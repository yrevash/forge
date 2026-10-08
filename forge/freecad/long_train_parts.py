"""New long composed parts: for TRAINING (6 to 12 features) and an extra-long test (13 to 15).

Why. The first Forge-S1 model was trained on plans of 2 to 6 items and fails on
plans of 7 to 13. The `long` test parts must stay a test, so the model cannot be shown
them. This file makes OTHER long parts with the same generator and the same check, and
proves they are other parts.

Two sets, each in a folder of its own (forge/system1/parts.py ADDED_DIRS):
    train_long   6 to 12 items after the start (plans of 7 to 13 items), for training
    xlong        13 to 15 items after the start (plans of 14 to 16 items), TEST ONLY

A fresh part is kept only if all of these hold:
  - it has no held-out pairing (forge.system1.splits.HELD_OUT_PAIRS). For train_long that is
    the training rule. For xlong it makes the test a test of length alone;
  - its id, its PLAN (kinds and numbers, the audit's plan fingerprint) and its GEOMETRY
    fingerprint are not those of any part already on disk: the 120,000 stored parts, the
    4,000 long test parts, the other added set, and the parts kept so far;
  - the CAD kernel builds it in the sandbox and the measured bounding box, volume and round
    features equal the generator's arithmetic (`forge.data.generate.verify`), exactly as
    for data/generated/.

Lengths and bases are balanced: the same number of parts for every (base, length).

Run:    uv run python -m forge.freecad.long_train_parts --set train_long --per-cell 400
        uv run python -m forge.freecad.long_train_parts --set xlong --per-cell 50
        (run again with a larger --per-cell to add more; what is there is kept)
        uv run python -m forge.freecad.long_train_parts --check     # the leak proof only
Output: data/system1/long_train_parts/composed_<base>.jsonl   (row format of data/generated/)
        data/system1/xlong_parts/composed_<base>.jsonl
        data/system1/long_train_parts/leak_check.json          what --check found
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from forge.data.generate import generator_version, row, verify
from forge.freecad.audit import plan_fingerprint
from forge.freecad.teacher import plan_json
from forge.generators import FAMILIES
from forge.runs import log_metrics, start_run
from forge.system1.parts import ADDED_DIRS, BASES, read_parts
from forge.system1.splits import ADDED_SPLITS, held_out_pairs_in, split_of
from forge.system1.steps import steps_of

BATCH = 100
SEEDS = {"train_long": 11, "xlong": 12}     # default seeds; the long TEST parts used seed 0


def plan_of(part: dict) -> tuple[int, str, list[str]]:
    """(items after the start, plan fingerprint, step kinds) of a part row."""
    steps = steps_of(part["family"], part["params"])
    return len(steps) - 1, plan_fingerprint(plan_json(steps)), [step.kind for step in steps]


def on_disk(skip: str | None = None) -> dict[str, dict[str, set]]:
    """split -> {"ids", "plans", "shapes"} of every part on disk. `skip` leaves one added set
    out (the one being checked against the rest)."""
    found: dict[str, dict[str, set]] = {}
    for part in read_parts(include_added=True):
        if part.get("set") == skip and skip is not None:
            continue
        split = split_of(part)
        entry = found.setdefault(split, {"ids": set(), "plans": set(), "shapes": set()})
        entry["ids"].add(part["id"])
        entry["plans"].add(plan_of(part)[1])
        entry["shapes"].add(part["geom_fingerprint"])
    return found


def leak_check() -> dict:
    """The proof, from the files alone: each added set against every other part on disk."""
    report: dict = {"held_out_pairs": "forge.system1.splits.HELD_OUT_PAIRS", "sets": {}}
    for name in ADDED_DIRS:
        others = on_disk(skip=name)
        rows = [part for part in read_parts(include_added=True) if part.get("set") == name]
        ids = {part["id"] for part in rows}
        plans, with_pairing, cells = set(), 0, Counter()
        for part in rows:
            items, fingerprint, kinds = plan_of(part)
            plans.add(fingerprint)
            with_pairing += bool(held_out_pairs_in(kinds))
            cells[(part["family"].removeprefix("composed_"), items)] += 1
        shapes = {part["geom_fingerprint"] for part in rows}
        low, high = ADDED_SPLITS[name]
        entry = {
            "parts": len(rows), "distinct_ids": len(ids), "distinct_plans": len(plans),
            "distinct_shapes": len(shapes), "parts_with_a_held_out_pairing": with_pairing,
            "parts_outside_the_lengths": sum(n for (_, items), n in cells.items()
                                             if not low <= items <= high),
            "by_items_after_the_start": {str(items): {base: cells[(base, items)]
                                                      for base in BASES}
                                         for items in range(low, high + 1)},
            "shared_with": {split: {"ids": len(ids & other["ids"]),
                                    "plans": len(plans & other["plans"]),
                                    "shapes": len(shapes & other["shapes"]),
                                    "parts_there": len(other["ids"])}
                            for split, other in sorted(others.items())},
        }
        shared = sum(count for other in entry["shared_with"].values()
                     for key, count in other.items() if key != "parts_there")
        entry["clean"] = not (shared or with_pairing or entry["parts_outside_the_lengths"]
                              or len(ids) != len(rows) or len(plans) != len(rows)
                              or len(shapes) != len(rows))
        report["sets"][name] = entry
    report["passed"] = all(entry["clean"] for entry in report["sets"].values())
    folder = ADDED_DIRS["train_long"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "leak_check.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def print_check(report: dict) -> None:
    for name, entry in report["sets"].items():
        print(f"{name}: {entry['parts']} parts, {entry['distinct_ids']} ids, "
              f"{entry['distinct_plans']} plans, {entry['distinct_shapes']} geometry "
              f"fingerprints; with a held-out pairing: {entry['parts_with_a_held_out_pairing']}; "
              f"outside the lengths: {entry['parts_outside_the_lengths']}")
        for items, bases in entry["by_items_after_the_start"].items():
            print(f"    {items:>2} items after the start: {bases}")
        for split, shared in entry["shared_with"].items():
            print(f"    shared with {split:10s} ({shared['parts_there']:6d} parts): "
                  f"ids {shared['ids']}, plans {shared['plans']}, geometry {shared['shapes']}")
    print("LEAK CHECK PASSED" if report["passed"] else "LEAK CHECK FAILED")


def generate(name: str, per_cell: int, seed: int, workers: int) -> None:
    folder = ADDED_DIRS[name]
    folder.mkdir(parents=True, exist_ok=True)
    low, high = ADDED_SPLITS[name]
    version = generator_version()
    run_dir = start_run("freecad-long-train-parts",
                        {"set": name, "per_cell": per_cell, "seed": seed, "workers": workers,
                         "generator_version": version, "items_after_the_start": [low, high]})
    seen = {"ids": set(), "plans": set(), "shapes": set()}
    for entry in on_disk().values():            # everything on disk, this set's own rows included
        for key in seen:
            seen[key] |= entry[key]
    have: Counter = Counter()
    files = {base: folder / f"composed_{base}.jsonl" for base in BASES}
    for base, path in files.items():
        for line in path.read_text().splitlines() if path.exists() else []:
            have[(base, plan_of(json.loads(line))[0])] += 1
    totals = {"kept": 0, "failed": 0, "already_seen": 0}
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Length by length, base by base, so that stopping early leaves a balanced set.
        for items in range(low, high + 1):
            for base in BASES:
                family = FAMILIES[f"composed_{base}"]
                rng = random.Random(f"{seed}:{name}:{base}:{items}:{have[(base, items)]}")
                while have[(base, items)] < per_cell:
                    batch: dict[str, object] = {}
                    wanted = min(BATCH, per_cell - have[(base, items)])
                    while len(batch) < wanted:
                        part = family.sample(rng, features=(items, items))
                        steps = steps_of(part.family, part.params)
                        if part.id in seen["ids"] or part.id in batch or len(steps) - 1 != items \
                                or held_out_pairs_in([step.kind for step in steps]):
                            continue
                        batch[part.id] = part
                    kept = []
                    for part, measure, problems in pool.map(verify, batch.values()):
                        if problems:
                            totals["failed"] += 1
                            continue
                        made = row(part, measure, version)
                        fingerprint = plan_of(made)[1]
                        if made["geom_fingerprint"] in seen["shapes"] \
                                or fingerprint in seen["plans"] \
                                or split_of({**made, "long": False, "set": name}) != name:
                            totals["already_seen"] += 1
                            continue
                        seen["ids"].add(part.id)
                        seen["plans"].add(fingerprint)
                        seen["shapes"].add(made["geom_fingerprint"])
                        kept.append(made)
                    with files[base].open("a") as f:
                        f.write("".join(json.dumps(r) + "\n" for r in kept))
                    have[(base, items)] += len(kept)
                    totals["kept"] += len(kept)
                print(f"[{(time.time() - started) / 60:5.1f} min] {name} composed_{base} "
                      f"{items} items: {have[(base, items)]} on disk  (this run: {totals})",
                      flush=True)
                log_metrics(run_dir, base=base, items=items, on_disk=have[(base, items)], **totals)
    log_metrics(run_dir, final=True, **totals)
    print(f"done: {sum(have.values())} parts in {folder}; this run {totals}; run folder {run_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--set", choices=list(ADDED_DIRS), default="train_long")
    parser.add_argument("--per-cell", type=int, default=400,
                        help="parts for every (base, number of items)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--check", action="store_true", help="only run the leak proof")
    args = parser.parse_args()
    if not args.check:
        generate(args.set, args.per_cell, SEEDS[args.set] if args.seed is None else args.seed,
                 args.workers)
    report = leak_check()
    print_check(report)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
