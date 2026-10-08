"""Generate verified structure rows for every kind of structure.

For every structure: build it (arithmetic), run its program in the sandbox,
compare the kernel's measurements with the arithmetic (check.py), write its
prompts with mentions (prompts.py). Only structures that pass every check are
written. A failure is a bug in a generator, so it is reported loudly and kept
in a separate file.

Run:    uv run python -m forge.generators.structures.generate [--per-kind 3000] [--workers 2]
Output: data/structures/<kind>.jsonl        one row per verified structure
        data/structures/<kind>.failures.jsonl   only if something failed
        data/structures/summary.json        the variety numbers printed at the end
        runs/<date>-generate-structures/    config, command, metrics

Kinds can be generated a few at a time (`--kinds bed cart`): the summary keeps the
numbers of kinds generated earlier.

`--resume` keeps the rows a kind already has (same generator version) and only
verifies the structures that are missing. The same seed always draws the same
structures in the same order, so a run of 1,500 is the first half of a run of
3,000 and can be topped up later; a run that was interrupted loses at most the
last few hundred structures, because the file is saved as it grows.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import itertools
import json
import random
import re
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from forge.generators.families import _common
from forge.generators.structures import KINDS, defaults
from forge.generators.structures.check import check
from forge.generators.structures.draft import Structure
from forge.generators.structures.prompts import prompts_for
from forge.generators.structures.sampling import REJECTED, sample
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.sandbox import Sandbox

OUT_DIR = PROJECT_ROOT / "data" / "structures"
SAVE_EVERY = 100          # rows between two saves of a kind's file while it is being generated
_local = threading.local()
_numbers = itertools.count()      # gives each worker thread its own number: 0, 1, 2 ...
_throttle: Path | None = None     # see --throttle-file


def _wait_for_a_turn() -> None:
    """Let a long run give up workers while something else needs the processor.

    If the throttle file holds a number N, only the worker threads numbered below
    N keep working; the others wait here and look again every few seconds.
    """
    if not hasattr(_local, "number"):
        _local.number = next(_numbers)
    while _throttle is not None and _throttle.exists():
        try:
            allowed = int(_throttle.read_text().strip() or 0)
        except ValueError:
            return
        if _local.number < allowed:
            return
        time.sleep(3)


# Everything every kind depends on. defaults.py is left out on purpose: a kind
# depends only on the rules it uses, and those are added to its hash one by one.
_SHARED = ("vocabulary.py", "formula.py", "solids.py", "draft.py", "sampling.py", "prompts.py",
           "check.py")


def generator_version(kind_name: str) -> str:
    """A hash of everything one kind's rows depend on.

    The shared machinery, the kernel-side measuring, the kind's own file, the
    helper files it imports from kinds/, and the source of every default rule
    those files use. Adding a new kind or a new rule therefore leaves the version
    of every other kind unchanged, and changing anything a kind uses changes it.
    """
    package = Path(__file__).resolve().parent
    files = [package / name for name in _SHARED]
    files.append(PROJECT_ROOT / "forge" / "assembly_geometry.py")
    files.append(PROJECT_ROOT / "forge" / "generators" / "prompts.py")    # the wording machinery
    own = [package / "kinds" / f"{kind_name}.py"]
    helpers = re.findall(r"\b(_[a-z]+)\b", own[0].read_text().split("\nNAME = ")[0])
    own += [package / "kinds" / f"{name}.py" for name in sorted(set(helpers))
            if (package / "kinds" / f"{name}.py").exists()]
    digest = hashlib.sha256()
    for path in files + own:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    used = sorted(set(re.findall(r"\brules\.([a-z_]+)", "".join(p.read_text() for p in own))))
    for name in used:
        digest.update(inspect.getsource(getattr(defaults, name)).encode())
    # Helpers the rules themselves call.
    for helper in (defaults.round_to, defaults._clamp, _common.floor_to):
        digest.update(inspect.getsource(helper).encode())
    return digest.hexdigest()[:12]


def structures_for(kind_name: str, wanted: int, seed: int) -> list[Structure]:
    """`wanted` different random structures of one kind. The same seed gives the same list."""
    rng = random.Random(f"{seed}:structure:{kind_name}")
    found: dict[str, Structure] = {}
    misses = 0
    while len(found) < wanted and misses < 2000:
        structure = sample(KINDS[kind_name], rng)
        if structure.id in found:
            misses += 1
        else:
            found[structure.id] = structure
    return list(found.values())


def verify(structure: Structure) -> tuple[Structure, dict | None, list[str]]:
    """Run one structure's program in this thread's sandbox and check the result."""
    _wait_for_a_turn()
    if not hasattr(_local, "sandbox"):
        _local.sandbox = Sandbox(timeout=60)
    reply = _local.sandbox.run(structure.code, check_export=True, structure=True)
    problems = check(structure, reply)
    if reply.get("status") == "ok" and not reply.get("step_export_ok"):
        problems.append("STEP export failed")
    return structure, reply.get("measure"), problems


def already_verified(path: Path, version: str) -> dict[str, dict]:
    """Rows a kind already has from an earlier run of the SAME generator version, by id."""
    if not path.exists():
        return {}
    rows = (json.loads(line) for line in path.read_text().splitlines() if line)
    return {r["id"]: r for r in rows if r.get("generator_version") == version}


def _save(path: Path, rows: list[dict]) -> None:
    """Write the rows, replacing the file in one step so a kill cannot leave half a file."""
    partial = path.with_suffix(".jsonl.partial")
    partial.write_text("".join(json.dumps(r) + "\n" for r in rows))
    partial.replace(path)


def shape_of(structure: Structure) -> str:
    """The structure's make-up, sizes aside: its steps and how many parts each adds.

    Two structures with the same shape differ only in dimensions. Counting
    distinct shapes says how many different things a kind really produces.
    """
    return " | ".join(f"{step.name}:{step.placement}:{step.shape}x{len(step.parts)}"
                      for step in structure.steps)


def row(structure: Structure, measure: dict, prompts: list[dict], version: str) -> dict:
    parts = measure["structure"]
    return {
        "id": structure.id,
        "kind": structure.kind,
        "source": f"gen:structure:{structure.kind}",
        "license": "forge",
        "generator_version": version,
        "level": structure.level,
        "variant": structure.choices,
        "given": structure.given,              # build(variant, given) rebuilds this structure
        "stated": structure.stated(),          # parameter -> headline / count / override
        "steps": [{"name": step.name, "role": step.role, "placement": step.placement,
                   "shape": step.shape,
                   "slots": {name: slot.as_dict() for name, slot in step.slots.items()},
                   "parts": [{"name": p.name, "shape": p.shape, "size": list(p.size),
                              "at": list(p.at)} for p in step.parts]}
                  for step in structure.steps],
        "params": structure.params,
        "program": structure.code,
        "expected": {"bbox": structure.expected_bbox, "volume": structure.expected_volume,
                     "n_parts": len(structure.solids)},
        "measured": {**{k: measure[k] for k in ("volume", "area", "bbox", "n_faces", "n_edges")},
                     "n_parts": parts["n_parts"], "n_touching_pairs": len(parts["touching"]),
                     "n_overlaps": len(parts["overlaps"]), "n_groups": parts["n_groups"]},
        "geom_fingerprint": measure["fingerprint"],
        "prompts": prompts,
        "split": None,                         # filled by a later step
    }


def summarise(rows: list[dict]) -> dict:
    """The variety numbers for one kind."""
    lengths = [len(r["program"]) for r in rows]
    return {
        "structures": len(rows),
        "levels": dict(sorted(Counter(r["level"] for r in rows).items())),
        "distinct_variants": len({json.dumps(r["variant"], sort_keys=True) for r in rows}),
        "distinct_shapes": len({" | ".join(
            f"{s['name']}:{s['placement']}:{s['shape']}x{len(s['parts'])}" for s in r["steps"])
            for r in rows}),
        "parts": _spread([r["expected"]["n_parts"] for r in rows]),
        "steps": _spread([len(r["steps"]) for r in rows]),
        "program_chars": {"min": min(lengths), "median": int(statistics.median(lengths)),
                          "max": max(lengths)},
        "prompts": sum(len(r["prompts"]) for r in rows),
        "slot_sources": dict(sorted(Counter(
            slot["source"] for r in rows for s in r["steps"] for slot in s["slots"].values()
        ).items())),
    }


def _spread(values: list[int]) -> dict:
    return {"min": min(values), "median": int(statistics.median(values)), "max": max(values),
            "histogram": dict(sorted(Counter(values).items()))}


def one_kind(name: str, args: argparse.Namespace, version: str, path: Path,
             pool: ThreadPoolExecutor) -> tuple[list[dict], list[dict], int, int]:
    """Verify one kind's structures. Returns the good rows, the failures, how many prompt
    drafts were thrown away and how many rows were kept from an earlier run."""
    wanted = structures_for(name, args.per_kind, args.seed)
    kept = already_verified(path, version) if args.resume else {}
    fresh: dict[str, dict] = {}
    bad, discarded = [], 0

    def in_order() -> list[dict]:
        """Every verified row so far, in the order the structures were drawn."""
        found = {**kept, **fresh}
        return [found[s.id] for s in wanted if s.id in found]

    todo = [structure for structure in wanted if structure.id not in kept]
    for structure, measure, problems in pool.map(verify, todo):
        prompts, thrown = prompts_for(KINDS[name], structure)
        discarded += thrown
        if not prompts:
            problems = [*problems, "no prompt passed its check"]
        if problems:
            bad.append({"id": structure.id, "variant": structure.choices,
                        "given": structure.given, "problems": problems})
        else:
            fresh[structure.id] = row(structure, measure, prompts, version)
            if len(fresh) % SAVE_EVERY == 0:      # save as it grows: a kill loses little
                _save(path, in_order())
    good = in_order()
    _save(path, good)
    return good, bad, discarded, len(kept)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-kind", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--kinds", nargs="*", default=list(KINDS))
    parser.add_argument("--resume", action="store_true",
                        help="keep rows already verified with this generator version")
    parser.add_argument("--throttle-file", type=Path,
                        help="a file holding how many workers may run right now (optional)")
    args = parser.parse_args()
    global _throttle
    _throttle = args.throttle_file

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    versions = {name: generator_version(name) for name in args.kinds}
    run_dir = start_run("generate-structures", {
        **vars(args), "throttle_file": str(args.throttle_file or ""),
        "generator_versions": versions})
    # Kinds generated by an earlier run keep their numbers in the summary.
    summary_path = OUT_DIR / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    summary = {name: found for name, found in summary.items()
               if name in KINDS and (OUT_DIR / f"{name}.jsonl").exists()
               and found.get("generator_version") == generator_version(name)}
    total_ok, total_bad, total_discarded = 0, 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for name in args.kinds:
            started = time.time()
            version = versions[name]
            before = dict(REJECTED)
            path = OUT_DIR / f"{name}.jsonl"
            good, bad, discarded, kept = one_kind(name, args, version, path, pool)
            failures = OUT_DIR / f"{name}.failures.jsonl"
            if bad:
                failures.write_text("".join(json.dumps(b) + "\n" for b in bad))
            elif failures.exists():
                failures.unlink()
            seconds = round(time.time() - started, 1)
            total_ok, total_bad = total_ok + len(good), total_bad + len(bad)
            total_discarded += discarded
            summary[name] = summarise(good) if good else {}
            # Random draws thrown away before building, by reason (sampling.REJECTED).
            thrown_away = {why: n - before.get((kind, why), 0)
                           for (kind, why), n in sorted(REJECTED.items()) if kind == name}
            summary[name].update(
                failed=len(bad), prompt_drafts_discarded=discarded, generator_version=version,
                seed=args.seed, draws_rejected_before_building=thrown_away)
            print(f"{name:10s} {len(good):6d} verified ({kept} kept from before)  "
                  f"{len(bad):4d} FAILED  {discarded:3d} prompt drafts discarded  {seconds}s",
                  flush=True)
            log_metrics(run_dir, kind=name, verified=len(good), failed=len(bad),
                        prompt_drafts_discarded=discarded, seconds=seconds)
            # Saved after every kind, so a run that is stopped keeps what it finished.
            kinds_done = [kind for kind in KINDS if summary.get(kind)]
            summary["_all"] = {
                "verified": sum(summary[kind].get("structures", 0) for kind in kinds_done),
                "failed": sum(summary[kind]["failed"] for kind in kinds_done),
                "kinds": len(kinds_done)}
            summary_path.write_text(json.dumps(summary, indent=2))

    print(f"\nthis run: {total_ok} verified, {total_bad} failed, "
          f"{total_discarded} prompt drafts discarded")
    print(f"{'kind':10s} {'variants':>8s} {'shapes':>7s} {'parts min/med/max':>18s} "
          f"{'steps min/med/max':>18s} {'program chars min/med/max':>26s}")
    for name in args.kinds:
        found = summary[name]
        if found.get("structures"):
            print(f"{name:10s} {found['distinct_variants']:8d} {found['distinct_shapes']:7d} "
                  f"{_three(found['parts']):>18s} {_three(found['steps']):>18s} "
                  f"{_three(found['program_chars']):>26s}")
            print(f"{'':10s} draws thrown away: "
                  f"{found['draws_rejected_before_building'] or 'none'}")
    log_metrics(run_dir, final=True, verified=total_ok, failed=total_bad)
    if total_bad:
        raise SystemExit(f"{total_bad} structures failed: see data/structures/*.failures.jsonl")


def _three(spread: dict) -> str:
    return f"{spread['min']}/{spread['median']}/{spread['max']}"


if __name__ == "__main__":
    main()
