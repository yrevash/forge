"""Proof: a structure built in FreeCAD, command by command, is the reference structure.

    uv run python -m forge.freecad_multi.prove --structures-per-kind 260 --random 1100 --workers 2

For every structure: resolve it (plans), write its recipe, issue the commands to a
FreeCAD worker (any-order groups shuffled), and compare (build.py):

    every part            one valid solid; its bounding box IN THE STRUCTURE within 0.001 mm
                          of the resolver's frame; its volume within 1e-6 (parts without
                          features, by formula)
    the structure         part count; overall bounding box; no pair shares volume unless the
                          plan declared it (`sunk`, a span's two ends); the touching pairs
                          are exactly the resolver's
    structure rows        also against the row's stored kernel measurement: total volume,
                          overall size, number of touching pairs, no overlap, one group
    kernel sample         the reference CadQuery program is built in the sandbox and each
    (--kernel-every N)    FreeCAD solid is compared with its reference solid: volume, box,
                          and the volume the two share (so a wedge pointing the wrong way,
                          or a hole in the wrong place, cannot pass)

Every example and tuning plan gets the kernel comparison; of the structure rows, the
random plans and the stored plans every N-th does. A random plan need not be complete:
whatever the resolver accepted is a structure, and it is built.

`--sources plans` adds the multi-part plans of data/plans (forge/plans_data), the same
number from every split of `structures` and `random`: each is resolved again from its
text, must give the parts its record stores, and is then built and compared like the rest.

Each structure's result is one line of <run>/records.jsonl; `--resume <run>` skips the
ones already there.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import random
import time
from collections import Counter
from pathlib import Path

from forge.freecad_multi import sources
from forge.freecad_multi.build import (
    compare_with_kernel,
    compare_with_resolver,
    compare_with_row,
    run_script,
)
from forge.freecad_multi.client import MultiClient
from forge.freecad_multi.recipes import Unsupported, structure_items
from forge.resolve import resolve_text
from forge.resolve.judge import KernelJudge
from forge.resolve.program import write_program
from forge.resolve.random_plans import random_plan
from forge.resolve.verify import BUILD_TIMEOUT
from forge.runs import log_metrics, start_run
from forge.sandbox import Sandbox

SOURCES = ("examples", "tuning", "structures", "random", "plans")
_state: dict = {}       # this worker process's FreeCAD client and sandbox


def _start() -> None:
    _state["client"] = MultiClient(recycle_after=150)
    _state["client"].start()
    _state["sandbox"] = Sandbox()
    _state["sandbox"].__enter__()
    _state["judge"] = KernelJudge(_state["sandbox"])


def prove_one(task: dict) -> dict:
    """Build one structure and report every difference from its references."""
    client, sandbox = _state["client"], _state["sandbox"]
    record = {"source": task["source"], "id": task["id"], "problems": [], "parts": 0,
              "commands": 0, "kernel": False, "complete": None, "unsupported": None}
    started = time.time()
    row, touching = None, None
    if task["source"] == "structures":
        row = task["row"]
        bodies = sources.row_bodies(row)
    elif task["source"] == "plans":
        try:
            unit = sources.unit_from_record(task["record"], Path(task["path"]), _state["judge"])
        except ValueError as error:
            record["problems"] = [str(error)]
            return record
        bodies, touching, record["complete"] = unit.bodies, unit.touching, True
    else:
        text = random_plan(task["seed"]) if task["source"] == "random" \
            else Path(task["path"]).read_text()
        resolution = resolve_text(text, _state["judge"])
        bodies, touching = resolution.bodies, resolution.touching
        record["complete"] = resolution.complete
    record["resolve_seconds"] = round(time.time() - started, 3)
    record["parts"] = len(bodies)
    if len(bodies) < task.get("least_parts", 1):
        record["skipped"] = "fewer parts than asked for"
        return record
    try:
        items = structure_items(bodies)
    except Unsupported as error:
        record["unsupported"] = str(error)
        return record
    parts = [item for item in items if item.kind == "part"]
    record["shapes"] = dict(Counter(item.shape for item in parts))
    record["placements"] = dict(Counter(item.placement for item in parts))
    record["features"] = dict(Counter(kind for item in items for kind in item.cuts))
    built = time.time()
    result = run_script(client, items, random.Random(str(task["id"])))
    record["commands"] = result.commands
    record["build_seconds"] = round(time.time() - built, 3)
    record["freecad_seconds"] = round(result.seconds_in_freecad, 3)
    problems = list(result.problems)
    if not problems:
        problems += compare_with_resolver(bodies, items, result.snapshot, touching)
        if row is not None:
            problems += compare_with_row(row, result.snapshot)
        if task["kernel"]:
            reply = sandbox.run(write_program(bodies, export=True), timeout=BUILD_TIMEOUT,
                                structure=True)
            problems += compare_with_kernel(client, bodies, items, result.snapshot, reply,
                                            Path(sandbox._work_dir))
            for leftover in Path(sandbox._work_dir).glob("*.brep"):
                leftover.unlink()
            record["kernel"] = True
    record["problems"] = problems
    if problems:        # which shapes and placements sit in a failed structure
        record["failed_parts"] = [f"{item.shape} | {item.placement}" for item in parts
                                  if any(problem.startswith(item.name + " (")
                                         for problem in problems)]
    record["restarts"] = client.restarts
    return record


PLAN_SPLITS = (("structures", "train"), ("structures", "iid"), ("structures", "kinds"),
               ("random", "train"), ("random", "iid"), ("random", "combo"))


def plan_tasks(per_split: int, every: int) -> list[dict]:
    """The first `per_split` multi-part plans of every split of data/plans."""
    tasks = []
    for source, split in PLAN_SPLITS:
        taken = 0
        for path in sources.plan_files(source, split):
            for record in sources.plan_records(path):
                if taken < per_split and record["complete"] and len(record["parts"]) >= 2:
                    tasks.append({"source": "plans", "id": f"{source}/{split}/{record['id']}",
                                  "record": record, "path": str(path),
                                  "kernel": bool(every) and taken % every == 0})
                    taken += 1
    return tasks


def tasks_for(args: argparse.Namespace) -> list[dict]:
    every = args.kernel_every
    tasks = []
    if "examples" in args.sources:
        tasks += [{"source": "examples", "id": f"{path.parent.parent.name}/{path.name}",
                   "path": str(path), "kernel": True} for path in sources.example_plans()]
    if "tuning" in args.sources:
        tasks += [{"source": "tuning", "id": path.stem, "path": str(path), "kernel": True}
                  for path in sources.tuning_plans()]
    if "structures" in args.sources:
        for kind in sources.structure_kinds():
            for number, row in enumerate(sources.structure_rows(kind, args.structures_per_kind)):
                tasks.append({"source": "structures", "id": f"{kind}/{row['id']}", "row": row,
                              "kernel": bool(every) and number % every == 0})
    if "random" in args.sources:
        tasks += [{"source": "random", "id": f"seed {seed}", "seed": seed, "least_parts": 2,
                   "kernel": bool(every) and seed % every == 0}
                  for seed in range(args.seed, args.seed + args.random)]
    if "plans" in args.sources:
        tasks += plan_tasks(args.plans_per_split, every)
    return tasks


def summarise(records: list[dict]) -> dict:
    """Counts per source, per shape and per placement kind."""
    by_source: dict[str, Counter] = {}
    shapes, placements, features = Counter(), Counter(), Counter()
    bad_shapes, bad_placements = Counter(), Counter()
    for record in records:
        counts = by_source.setdefault(record["source"], Counter())
        if record.get("skipped"):
            counts["skipped (one part or none)"] += 1
            continue
        if record["unsupported"]:
            counts["not buildable yet"] += 1
            continue
        counts["structures"] += 1
        counts["parts"] += record["parts"]
        counts["commands"] += record["commands"]
        counts["mismatched"] += bool(record["problems"])
        counts["with kernel comparison"] += record["kernel"]
        counts["complete plans"] += bool(record["complete"])
        shapes.update(record["shapes"])
        placements.update(record["placements"])
        features.update(record["features"])
        for part in record.get("failed_parts", []):
            shape, placement = part.split(" | ")
            bad_shapes[shape] += 1
            bad_placements[placement] += 1
    return {"by_source": {source: dict(counts) for source, counts in by_source.items()},
            "shapes": dict(shapes), "placements": dict(placements), "features": dict(features),
            "mismatched_shapes": dict(bad_shapes), "mismatched_placements": dict(bad_placements)}


def report(summary: dict, records: list[dict], seconds: float, workers: int) -> None:
    print(f"{'source':12s} {'structures':>10s} {'parts':>8s} {'commands':>9s} {'kernel':>7s} "
          f"{'mismatched':>10s} {'unbuildable':>11s} {'skipped':>7s}")
    for source, counts in summary["by_source"].items():
        print(f"{source:12s} {counts.get('structures', 0):10d} {counts.get('parts', 0):8d} "
              f"{counts.get('commands', 0):9d} {counts.get('with kernel comparison', 0):7d} "
              f"{counts.get('mismatched', 0):10d} {counts.get('not buildable yet', 0):11d} "
              f"{counts.get('skipped (one part or none)', 0):7d}")
    for title, key, bad in (("shape", "shapes", "mismatched_shapes"),
                            ("placement", "placements", "mismatched_placements")):
        print(f"\n{title:40s} {'parts':>8s} {'mismatched':>10s}")
        for name, count in sorted(summary[key].items(), key=lambda pair: -pair[1]):
            print(f"  {name:38s} {count:8d} {summary[bad].get(name, 0):10d}")
    print("\nfeatures built:", dict(sorted(summary["features"].items())))
    built = [r for r in records if r["commands"]]
    parts, commands = sum(r["parts"] for r in built), sum(r["commands"] for r in built)
    print(f"\n{len(built)} structures, {parts} parts, {commands} commands in {seconds:.0f} s "
          f"with {workers} workers (resolving and kernel comparisons included)")
    for record in records:
        if record["unsupported"]:
            print(f"  NOT BUILDABLE {record['source']} {record['id']}: {record['unsupported']}")
        for problem in record["problems"][:6]:
            print(f"  MISMATCH {record['source']} {record['id']}: {problem}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--structures-per-kind", type=int, default=260)
    parser.add_argument("--random", type=int, default=1100, help="how many random plan seeds")
    parser.add_argument("--seed", type=int, default=0, help="the first random plan seed")
    parser.add_argument("--kernel-every", type=int, default=10,
                        help="kernel comparison for every N-th structure row and random plan")
    parser.add_argument("--sources", nargs="*", choices=SOURCES,
                        default=["examples", "tuning", "structures", "random"])
    parser.add_argument("--plans-per-split", type=int, default=150,
                        help="with `--sources plans`: multi-part plans per split of data/plans")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", help="a run folder to continue")
    args = parser.parse_args()
    run_dir = Path(args.resume) if args.resume else start_run("freecad-multi-prove", vars(args))
    records_path = run_dir / "records.jsonl"
    records = ([json.loads(line) for line in records_path.read_text().splitlines()]
               if records_path.exists() else [])
    done = {(record["source"], record["id"]) for record in records}
    tasks = [task for task in tasks_for(args) if (task["source"], task["id"]) not in done]
    print(f"{len(tasks)} structures to build ({len(done)} already in {records_path})", flush=True)

    started = time.time()
    with multiprocessing.get_context("spawn").Pool(args.workers, initializer=_start) as pool, \
            records_path.open("a") as out:
        for count, record in enumerate(pool.imap_unordered(prove_one, tasks), start=1):
            records.append(record)
            out.write(json.dumps(record) + "\n")
            out.flush()
            if count % 100 == 0:
                bad = sum(bool(r["problems"]) for r in records)
                print(f"  {count}/{len(tasks)} built, {bad} mismatched, "
                      f"{time.time() - started:.0f} s", flush=True)
    seconds = time.time() - started
    summary = summarise(records)
    report(summary, records, seconds, args.workers)
    log_metrics(run_dir, final=True, seconds=round(seconds, 1), workers=args.workers, **summary,
                restarts=max((r.get("restarts", 0) for r in records), default=0),
                mismatched_ids=[[r["source"], r["id"]] for r in records if r["problems"]])
    mismatched = sum(bool(r["problems"]) or bool(r["unsupported"]) for r in records)
    raise SystemExit(1 if mismatched else 0)


if __name__ == "__main__":
    main()
