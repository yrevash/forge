"""Audit a built dataset. It trusts nothing the build step said about itself.

Run:    uv run python -m forge.data.audit --version v1 [--reexecute 1000]
                                                    [--reexecute-repairs 500]
Exit code 0 only if every check passes.

Checks:
 1. files match the hashes in the manifest;
 2. every pair has complete provenance and a non-empty prompt and program;
 3. pair ids are unique; no part appears in two splits;
 4. no shape and no program is shared between train/val and any test split;
 5. held-out families appear only in test_ood_family;
 6. every number in every prompt belongs to its part, and full prompts leave no
    dimension out;
 7. a random sample of programs is executed again in the sandbox and must
    measure exactly as recorded.

Only when the dataset has repair files (<split>_repair.parquet):
 8. every repair file on disk is in the manifest, and the other way round;
 9. every repair pair has complete provenance, a known mistake, and a unique id;
10. every repair pair's part, and its correct program, are in the same split;
11. no wrong or correct program in the train/val repair files is the program of
    a test part;
12. a random sample of wrong programs is executed again in the sandbox, the
    feedback is written again from the result, and it must equal the stored text.

Files are read in batches, so the dataset can be larger than memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from forge.data.mistakes import OPERATORS, REPAIR_PAIR_FIELDS, problems, stated_params
from forge.generators import FAMILIES
from forge.generators.base import BBOX_TOLERANCE_MM, VOLUME_RELATIVE_TOLERANCE
from forge.generators.captions import caption_problems
from forge.generators.prompts import prompt_problems
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.sandbox import Sandbox

TEST_SPLITS = ("test_id", "test_ood_family", "test_ood_params")
SPLITS = ("train", "val", *TEST_SPLITS)
PROVENANCE = ("id", "part_id", "source", "license", "generator_version", "caption_style",
              "caption_model", "geom_fingerprint", "split", "params")
KNOWN_MODELS = {"template"}


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def column(root, split: str, name: str) -> pa.ChunkedArray:
    return pq.read_table(root / f"{split}.parquet", columns=[name]).column(name)


def audit_repairs(root, manifest: dict, check, part_ids: dict, test_codes: pa.Array,
                  reexecute: int) -> int:
    """Checks 8 to 12, on the repair files. Returns the number of repair pairs."""
    on_disk = {path.name for path in root.glob("*_repair.parquet")}
    listed = {name for name in manifest["files"] if name.endswith("_repair.parquet")}
    expected = {f"{split}_repair.parquet" for split in SPLITS}
    check("8 repair files match manifest",
          [f"{name} is on disk but not in the manifest" for name in sorted(on_disk - listed)]
          + [f"{name} is in the manifest but not on disk" for name in sorted(listed - on_disk)]
          + [f"{name} is missing" for name in sorted(expected - on_disk)]
          + ["manifest has no repairs section"] * ("repairs" not in manifest))
    files = {split: root / f"{split}_repair.parquet" for split in SPLITS
             if (root / f"{split}_repair.parquet").exists()}

    # 9, and the sample for 12, in one pass.
    rng = random.Random(0)
    bad_provenance: list[str] = []
    ids: set[str] = set()
    sample: list[dict] = []
    total = 0
    for split, path in files.items():
        for batch in pq.ParquetFile(path).iter_batches(batch_size=20_000):
            for r in batch.to_pylist():
                total += 1
                operator = OPERATORS.get(r.get("mistake") or "")
                if (set(r) != set(REPAIR_PAIR_FIELDS) or any(not r[f] for f in REPAIR_PAIR_FIELDS)
                        or r["license"] != "forge" or not r["source"].startswith("gen:")
                        or r["split"] != split or r["caption_model"] not in KNOWN_MODELS
                        or r["feedback_model"] not in KNOWN_MODELS or operator is None
                        or operator.group != r["mistake_group"]
                        or r["wrong_code"] == r["code"] or r["id"] in ids):
                    bad_provenance.append(r.get("id") or "?")
                ids.add(r["id"])
                if len(sample) < reexecute:
                    sample.append(r)
                elif (j := rng.randrange(total)) < reexecute:
                    sample[j] = r  # reservoir sampling: every pair equally likely
    check("9 repair pairs: complete provenance, known mistake, unique ids", bad_provenance)

    # 10. A repair lives where its part lives, and fixes to that part's program.
    misplaced = []
    for split, path in files.items():
        table = pq.read_table(path, columns=["part_id", "code"])
        outside = len(table) - (pc.sum(pc.is_in(table["part_id"],
                                                value_set=part_ids[split])).as_py() or 0)
        other = len(table) - (pc.sum(pc.is_in(
            table["code"], value_set=pc.unique(column(root, split, "code")))).as_py() or 0)
        if outside:
            misplaced.append(f"{outside} {split} repair pairs belong to a part of another split")
        if other:
            misplaced.append(f"{other} {split} repair pairs fix to a program not in {split}")
    check("10 repair pairs are in their part's split", misplaced)

    # 11. Leakage: wrong programs count as programs too.
    leaks = []
    for split in ("train", "val"):
        if split in files:
            for name in ("wrong_code", "code"):
                values = pq.read_table(files[split], columns=[name]).column(name)
                n = pc.sum(pc.is_in(values, value_set=test_codes)).as_py() or 0
                if n:
                    leaks.append(f"{n} {split} repair pairs have a {name} that is a test program")
    check("11 no repair program in train/val is a test part's program", leaks)

    # 12. Run wrong programs again and write the feedback again.
    bad = []
    with Sandbox(timeout=30) as sandbox:
        for r in sample:
            reply = sandbox.run(r["wrong_code"])
            stated = stated_params(r["prompt"], json.loads(r["params"]))
            again = "\n".join(problems(stated, reply, r["wrong_code"]))
            if not again:
                bad.append(f"{r['id']}: the wrong program shows no problem when run again")
            elif again != r["feedback"]:
                bad.append(f"{r['id']}: feedback differs when written again")
    check(f"12 re-executed {len(sample)} wrong programs, feedback reproduced", bad)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--version", default="v1")
    parser.add_argument("--reexecute", type=int, default=1000)
    parser.add_argument("--reexecute-repairs", type=int, default=500)
    args = parser.parse_args()

    root = PROJECT_ROOT / "data" / "dataset" / args.version
    manifest = json.loads((root / "manifest.json").read_text())
    run_dir = start_run(f"audit-dataset-{args.version}", {"version": args.version,
                                                          "reexecute": args.reexecute})
    failures: list[str] = []
    report: dict[str, object] = {}

    def check(name: str, problems: list[str]) -> None:
        report[name] = "PASS" if not problems else f"FAIL ({len(problems)})"
        print(f"{'PASS' if not problems else 'FAIL'}  {name}"
              + ("" if not problems else f"  e.g. {problems[0]}"), flush=True)
        failures.extend(f"{name}: {p}" for p in problems[:20])

    # 1. File hashes.
    check("1 files match manifest", [
        name for name, digest in manifest["files"].items()
        if file_sha256(root / name) != digest])

    # 2, 5 and 6 in one pass over every pair.
    held = set(manifest["config"]["holdout_families"])
    bad_provenance, bad_holdout, bad_prompt = [], [], []
    pair_counts: Counter = Counter()
    for split in SPLITS:
        for batch in pq.ParquetFile(root / f"{split}.parquet").iter_batches(batch_size=20_000):
            for r in batch.to_pylist():
                pair_counts[split] += 1
                if (any(not r.get(f) for f in PROVENANCE) or not r["prompt"].strip()
                        or not r["code"].strip() or r["license"] != "forge"
                        or not r["source"].startswith("gen:") or r["split"] != split
                        or r["caption_model"] not in KNOWN_MODELS):
                    bad_provenance.append(r.get("id", "?"))
                if (r["family"] in held) != (split == "test_ood_family"):
                    bad_holdout.append(r["id"])
                params, designation = json.loads(r["params"]), json.loads(r["designation"])
                if r["caption_model"] == "template":
                    wrong = caption_problems(r["prompt"], params, designation)
                else:
                    wrong = prompt_problems(r["prompt"], params, designation,
                                            complete=r["caption_style"] != "designation")
                if wrong:
                    bad_prompt.append(f"{r['id']}: {wrong[0]}")
    check("2 complete provenance", bad_provenance)

    # 3. Unique pair ids; a part lives in exactly one split.
    ids = pa.chunked_array([c for s in SPLITS for c in column(root, s, "id").chunks])
    duplicates = len(ids) - pc.count_distinct(ids).as_py()
    part_ids = {s: pc.unique(column(root, s, "part_id")) for s in SPLITS}
    shared_parts = sum(
        pc.sum(pc.is_in(part_ids[a], value_set=part_ids[b])).as_py() or 0
        for i, a in enumerate(SPLITS) for b in SPLITS[i + 1:])
    check("3 ids unique, one split per part",
          [f"{duplicates} duplicate pair ids"] * bool(duplicates)
          + [f"{shared_parts} parts in two splits"] * bool(shared_parts))

    # 4. Leakage between training and test.
    leaks = []
    test_codes = None
    for name in ("geom_fingerprint", "code"):
        test_values = pc.unique(pa.chunked_array(
            [c for s in TEST_SPLITS for c in column(root, s, name).chunks]))
        test_codes = test_values  # after the loop: the programs of the test splits
        for split in ("train", "val"):
            n = pc.sum(pc.is_in(column(root, split, name), value_set=test_values)).as_py() or 0
            if n:
                leaks.append(f"{n} {split} pairs share a {name} with a test split")
    check("4 no shape or program shared with a test split", leaks)

    check("5 held-out families only in test_ood_family", bad_holdout)
    check("6 every prompt number belongs to its part; full prompts are complete", bad_prompt)

    # 7. Re-execute a random sample of parts and compare with what was recorded.
    rng = random.Random(0)
    sample: list[dict] = []
    seen = 0
    for batch in pq.ParquetFile(root / "parts.parquet").iter_batches(batch_size=20_000):
        for part in batch.to_pylist():
            seen += 1
            if len(sample) < args.reexecute:
                sample.append(part)
            elif (j := rng.randrange(seen)) < args.reexecute:
                sample[j] = part  # reservoir sampling: every part equally likely
    bad = []
    with Sandbox(timeout=30) as sandbox:
        for part in sample:
            reply = sandbox.run(part["code"])
            recorded = json.loads(part["measured"])
            if reply["status"] != "ok" or not reply["measure"]["one_valid_solid"]:
                bad.append(f"{part['id']} does not build")
                continue
            m = reply["measure"]
            if (abs(m["volume"] - recorded["volume"]) > VOLUME_RELATIVE_TOLERANCE * m["volume"]
                    or any(abs(a - b) > BBOX_TOLERANCE_MM
                           for a, b in zip(m["bbox"], recorded["bbox"], strict=True))
                    or m["fingerprint"] != part["geom_fingerprint"]):
                bad.append(f"{part['id']} measures differently from its record")
            if part["family"] not in FAMILIES:
                bad.append(f"{part['id']} has unknown family {part['family']}")
    check(f"7 re-executed {len(sample)} programs", bad)

    # 8-12. Repair files, only if the dataset has any (or its manifest says it should).
    has_repairs = "repairs" in manifest or any(root.glob("*_repair.parquet"))
    repair_pairs = 0
    if has_repairs:
        repair_pairs = audit_repairs(root, manifest, check, part_ids, test_codes,
                                     args.reexecute_repairs)

    total_pairs = sum(pair_counts.values())
    report["pairs"] = dict(pair_counts)
    report["parts"] = seen
    if has_repairs:
        report["repair_pairs"] = repair_pairs
    (root / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    metrics = {"repair_pairs": repair_pairs} if has_repairs else {}
    log_metrics(run_dir, final=True, failures=len(failures), parts=seen, pairs=total_pairs,
                **metrics)
    if failures:
        print(f"\nAUDIT FAILED: {len(failures)} problems")
        sys.exit(1)
    print(f"\nAUDIT PASSED: {seen} parts, {total_pairs} pairs"
          + (f", {repair_pairs} repair pairs" if has_repairs else ""))


if __name__ == "__main__":
    main()
