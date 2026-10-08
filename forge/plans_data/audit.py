"""Audit data/plans. Exit code 0 only if every check passes.                       [command]

    uv run python -m forge.plans_data.audit [--workers 3] [--resolve-rate 1.0] [--kernel-plans 60]

The six checks:

  1 reader     every plan is read again from its text. A kept plan must be accepted in
               full; in the negative slice exactly the lines stored as `not understood`
               may be refused. The structured plan read now equals the stored one.
  2 resolver   the plan is resolved again from its text and the result (every reply,
               every part's size and place, what it touches, the overall frame) must
               equal the stored result. Arithmetic plans are resolved with a judge that
               counts any question to the kernel as a failure; single parts with the
               same stand-in the build used; kernel-tier plans on the real kernel, for a
               sample of `--kernel-plans`.
  3 kernel     the stored kernel verdicts: at least config.MIN_KERNEL_BUILT plans across the
               three sources were built on the kernel, with no disagreement (the target,
               config.TARGET_KERNEL_BUILT, is reported).
  4 coverage   the cells of every plan are counted again; every required cell reaches
               its floor in the train split (config.MIN_*; the targets are reported).
  5 splits     every plan's split is worked out again and must be the stored one; no
               train or iid plan holds a held-out combination, is long, or is of a
               held-out kind; the longest train plan is shorter than the shortest long one.
  6 records    every record has its provenance fields, ids are unique within a source,
               every file's hash is the one in the manifest.

`--resolve-rate` below 1 checks only that share of the plans in check 2 (chosen by a
hash of the id); the report says so. One plan in twenty is resolved WITHOUT the
snapshot short-cut the build used (forge/plans_data/fast.py), to show the short-cut
changes nothing.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from forge.plans_data import config, coverage, fast, splits
from forge.plans_data import table as coverage_table
from forge.runs import log_metrics, start_run

REQUIRED = ("id", "source", "license", "generator_version", "caption_style", "caption_model",
            "geom_fingerprint", "split", "lines", "plan", "replies", "parts", "cells", "provenance")
PART_MAX_TRAIN_LINES = 7        # base + at most 5 items + done (forge.system1.splits)
PLAIN_RATE = 0.05
MAX_LISTED = 5


def _json(item):
    return json.loads(json.dumps(item))


def audit_file(job: tuple) -> dict:
    path, resolve_rate = job
    from forge.plan import parse_plan
    from forge.plans_data.parts import TrustStored
    from forge.plans_data.record import NoKernel, line_dict, solution
    from forge.resolve.resolver import resolve_plan

    relative = Path(path).relative_to(config.OUT_DIR)
    source, split = relative.parts[0], relative.parts[1]
    out = {"file": str(relative), "source": source, "split": split, "plans": 0, "resolved": 0,
           "resolved_plain": 0, "kernel_tier": 0, "problems": Counter(), "examples": [],
           "cells": Counter(), "ids": [], "lengths": Counter(), "kernel": Counter(),
           "kernel_ids": [], "fingerprints": []}

    def bad(what: str, record: dict) -> None:
        out["problems"][what] += 1
        if len(out["examples"]) < MAX_LISTED:
            out["examples"].append(f"{what}: {record.get('id')}")

    out["sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    with gzip.open(path, "rt") as f:
        for raw in f:
            record = json.loads(raw)
            out["plans"] += 1
            out["ids"].append(record.get("id"))
            out["fingerprints"].append(record.get("geom_fingerprint"))
            if any(record.get(key) in (None, "") for key in REQUIRED):
                bad("6 a provenance field is missing", record)
                continue
            if record["source"] != source or record["split"] != split:
                bad("6 the record is in the wrong folder", record)
            text = "\n".join(record["lines"]) + "\n"
            stored_replies = [reply["reply"] for reply in record["replies"]]

            # 1 reader
            plan = parse_plan(text)
            refused = [line.number for line in plan.lines if not line.accepted]
            stored_refused = [reply["line"] for reply in record["replies"]
                              if reply["reply"] == "rejected: not understood"]
            if source == "negatives":
                if refused != stored_refused or plan.problems:
                    bad("1 the reader refuses other lines than the stored ones", record)
                if record["complete"]:
                    bad("1 a plan with nothing rejected is in the negative slice", record)
            elif not plan.accepted or not record["complete"] or record["negative"]:
                bad("1 the reader does not accept the plan in full", record)
            if _json([line_dict(line) for line in plan.lines]) != record["plan"]:
                bad("1 the structured plan read now differs from the stored one", record)

            # 4 coverage and 5 splits, recomputed from the plan alone
            cells = coverage.cells_of(plan.lines, stored_replies)
            if cells != record["cells"]:
                bad("4 the cells counted now differ from the stored ones", record)
            if split == "train" and source != "negatives":
                out["cells"].update(cells)
            elif split == "train":
                out["cells"].update(c for c in cells if c.startswith("reply:") and c != "reply:built")
            n_lines = len(record["lines"])
            out["lengths"][n_lines] += 1
            combos = splits.held_out_combinations(plan.lines, stored_replies)
            kind = record.get("kind")
            if source == "parts":
                if combos and split in ("train", "iid"):
                    bad("5 a held-out combination is in train or iid", record)
                if split in ("train", "iid", "combo") and n_lines > PART_MAX_TRAIN_LINES:
                    bad("5 a long part is outside the long split", record)
            else:
                want, _ = splits.assign(source, record["id"], plan.lines, stored_replies,
                                        kind=kind, held_out_kinds=config.HELD_OUT_KINDS)
                if want != split:
                    bad(f"5 the split worked out now is {want}", record)
                if split in ("train", "iid") and (combos or splits.is_long(n_lines)
                                                  or kind in config.HELD_OUT_KINDS):
                    bad("5 a held-out plan is in train or iid", record)

            # 3 kernel verdicts stored with the record
            if record.get("kernel"):
                out["kernel"]["built"] += 1
                out["kernel_ids"].append(record["id"])
                if record["kernel"]["problems"] and record["kernel"].get("explained"):
                    out["kernel"]["explained"] += 1
                elif record["kernel"]["problems"]:
                    out["kernel"]["disagreements"] += 1
                    bad("3 the kernel disagreed with the resolver", record)

            # 2 resolver
            tier = record["provenance"].get("tier")
            if tier == "kernel":
                out["kernel_tier"] += 1
                continue                    # sampled by the main process, on the real kernel
            if config.unit_hash(f"audit:{record['id']}") >= resolve_rate:
                continue
            plain = config.unit_hash(f"plain:{record['id']}") < PLAIN_RATE
            fast.copy_bodies() if plain else fast.share_bodies()
            judge = TrustStored() if source == "parts" else NoKernel()
            solved = _json(solution(resolve_plan(plan, judge)))
            out["resolved"] += 1
            out["resolved_plain"] += plain
            if getattr(judge, "asked", 0):
                bad("2 an arithmetic plan needed the kernel", record)
            for key in ("replies", "parts", "overall", "complete"):
                if solved.get(key) != record.get(key):
                    bad(f"2 the resolver's {key} differ from the stored ones", record)
                    break
    out["problems"], out["cells"] = dict(out["problems"]), dict(out["cells"])
    out["lengths"], out["kernel"] = dict(out["lengths"]), dict(out["kernel"])
    return out


def kernel_tier_sample(wanted: int) -> dict:
    """Resolve a sample of kernel-tier plans again on the real kernel; compare with the store."""
    from forge.plans_data.record import solution_of
    from forge.resolve.judge import KernelJudge
    from forge.sandbox import Sandbox

    found, checked, differ = [], 0, []
    for source in ("random", "negatives"):
        for path in sorted((config.OUT_DIR / source).glob("*/shard*.jsonl.gz")):
            with gzip.open(path, "rt") as f:
                for raw in f:
                    record = json.loads(raw)
                    if record["provenance"].get("tier") == "kernel":
                        found.append((config.unit_hash(f"kernel-audit:{record['id']}"), record))
    found.sort(key=lambda pair: pair[0])
    if found and wanted:
        with Sandbox(timeout=120) as sandbox:
            for _, record in found[:wanted]:
                _, _, solved = solution_of("\n".join(record["lines"]) + "\n", KernelJudge(sandbox))
                checked += 1
                solved = _json(solved)
                if any(solved.get(key) != record.get(key)
                       for key in ("replies", "parts", "overall", "complete")):
                    differ.append(record["id"])
    return {"kernel_tier_plans": len(found), "resolved_again_on_the_kernel": checked,
            "differ": differ}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit data/plans.")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--resolve-rate", type=float, default=1.0)
    parser.add_argument("--kernel-plans", type=int, default=60)
    args = parser.parse_args()
    started = time.time()
    run_dir = start_run("plans-audit", vars(args))
    manifest = json.loads((config.OUT_DIR / "manifest.json").read_text())
    paths = sorted(str(p) for source in config.SOURCES
                   for p in (config.OUT_DIR / source).glob("*/shard*.jsonl.gz"))
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(audit_file, [(path, args.resolve_rate) for path in paths]))

    problems: Counter = Counter()
    examples: list[str] = []
    cells: Counter = Counter()
    plans: dict[str, Counter] = {}
    lengths: dict[str, Counter] = {}
    kernel: dict[str, Counter] = {}
    resolved = plain = kernel_tier = 0
    ids: dict[str, list] = {}
    prints: dict[tuple[str, str], set] = {}
    for result in results:
        prints.setdefault((result["source"], result["split"]), set()).update(result["fingerprints"])
        problems.update(result["problems"])
        examples += result["examples"]
        cells.update(result["cells"])
        plans.setdefault(result["source"], Counter())[result["split"]] += result["plans"]
        lengths.setdefault(f"{result['source']}/{result['split']}", Counter()).update(
            {int(k): v for k, v in result["lengths"].items()})
        kernel.setdefault(result["source"], Counter()).update(result["kernel"])
        resolved += result["resolved"]
        plain += result["resolved_plain"]
        kernel_tier += result["kernel_tier"]
        ids.setdefault(result["source"], []).extend(result["ids"])
        listed = manifest["files"].get(result["file"])
        if listed is None or listed["sha256"] != result["sha256"] or listed["rows"] != result["plans"]:
            problems["6 a file does not match the manifest"] += 1
    if len(results) != len(manifest["files"]):
        problems["6 the manifest lists other files than are on disk"] += 1
    for source, found in ids.items():
        if len(found) != len(set(found)):
            problems[f"6 ids repeat within {source}"] += 1

    # Leakage, for the report: geometry fingerprints a test split shares with train. A plan's
    # id is a hash of its TEXT, so two different texts that build the same geometry can sit on
    # both sides; this counts them. (Single parts are split by fingerprint: always 0.)
    shared = {f"{source}/{split}": len(found & prints.get((source, "train"), set()))
              for (source, split), found in sorted(prints.items()) if split != "train"}

    # 5 lengths: the longest train plan is shorter than the shortest long plan, per source
    for source in config.SOURCES:
        short = [n for split in ("train", "iid", "combo") for n in lengths.get(f"{source}/{split}", {})]
        long = list(lengths.get(f"{source}/long", {}))
        if short and long and max(short) >= min(long):
            problems[f"5 {source}: a train plan is as long as a long plan"] += 1

    # 4 coverage minimums, on the train split
    table = coverage_table.rows(cells, cells)
    under = {cell: (row["train"], row["floor"]) for cell, row in table.items()
             if row["train"] < row["floor"]}
    at_target = sum(row["train"] >= row["target"] for row in table.values())
    if under:
        problems["4 cells under their floor in train"] = len(under)

    # 3 the kernel sample
    built = {source: count.get("built", 0) for source, count in kernel.items()}
    explained = sum(count.get("explained", 0) for count in kernel.values())
    total_built = sum(built.values())
    if total_built < config.MIN_KERNEL_BUILT:
        problems[f"3 fewer than {config.MIN_KERNEL_BUILT} plans were built on the kernel"] = \
            config.MIN_KERNEL_BUILT - total_built
    for source in ("random", "structures", "parts"):
        if not built.get(source):
            problems[f"3 no plan of {source} was built on the kernel"] += 1

    again = kernel_tier_sample(args.kernel_plans)
    if again["differ"]:
        problems["2 kernel-tier plans resolve differently now"] = len(again["differ"])

    total = sum(sum(c.values()) for c in plans.values())
    checks = {
        "1 reader": not any(k.startswith("1") for k in problems),
        "2 resolver": not any(k.startswith("2") for k in problems),
        "3 kernel": not any(k.startswith("3") for k in problems),
        "4 coverage": not any(k.startswith("4") for k in problems),
        "5 splits": not any(k.startswith("5") for k in problems),
        "6 records": not any(k.startswith("6") for k in problems),
    }
    report = {
        "plans": total, "by_source": {s: dict(c) for s, c in plans.items()},
        "resolve_rate": args.resolve_rate,
        "resolved_again_by_arithmetic": resolved, "of_those_without_the_shortcut": plain,
        "kernel_tier_plans": kernel_tier, **again,
        "built_on_the_kernel": built, "built_on_the_kernel_total": total_built,
        "kernel_disagreements_explained": explained,
        "geometry_fingerprints_shared_with_train": shared,
        "cells_required": len(table), "cells_at_target": at_target, "cells_under_floor": under,
        "checks": checks, "problems": dict(problems), "examples": examples[:40],
        "seconds": round(time.time() - started, 1),
    }
    (config.OUT_DIR / "audit.json").write_text(json.dumps(report, indent=1))
    log_metrics(run_dir, **{k: v for k, v in report.items() if k not in ("cells_under_floor",)})
    print(f"audited {total} plans in {report['seconds']}s: { {s: dict(c) for s, c in plans.items()} }")
    print(f"  resolved again by arithmetic: {resolved} (rate {args.resolve_rate}); "
          f"{plain} of them without the snapshot short-cut")
    print(f"  kernel-tier plans: {kernel_tier}; resolved again on the kernel: "
          f"{again['resolved_again_on_the_kernel']}, differing: {len(again['differ'])}")
    print(f"  built on the kernel (stored verdicts): {built}, total {total_built} "
          f"(floor {config.MIN_KERNEL_BUILT}, target {config.TARGET_KERNEL_BUILT}); "
          f"{explained} disagreement(s) explained in forge/plans_data/kernel_explained.json")
    print(f"  geometry fingerprints shared with train (not a check): {shared}")
    print(f"  coverage in train: {len(table) - len(under)} of {len(table)} required cells at their "
          f"floor; {at_target} at their target")
    for cell, (count, minimum) in sorted(under.items())[:30]:
        print(f"      under: {cell}: {count} < {minimum}")
    for name, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    for what, count in sorted(problems.items()):
        print(f"      {count:7d}  {what}")
    for example in examples[:10]:
        print(f"      e.g. {example}")
    if not all(checks.values()):
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
