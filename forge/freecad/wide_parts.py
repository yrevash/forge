"""Generate WIDE parts: single parts whose plans are drawn far more freely.

What is free and what is still fixed is the table at the top of wide_sample.py. This file
turns the sampler into sets of verified parts on disk and proves that the sets are apart.

Sets (SETS below). Lengths count plan items, the base included.
    wide_train        1 to 13 items   for TRAINING (slice train_wide_v3)
    wide_train_long   14 to 16 items  for training only when asked for (train_wide_long_v3)
    wide_iid          1 to 13 items   TEST: same sampler, other parts
    wide_numbers      1 to 13 items   TEST: scales and decimal patterns no training part has
    wide_order        3 to 13 items   TEST: every plan holds a held-out ORDER of two items
    wide_starts       1 to 13 items   TEST: parts for the held-out START kinds
Every (base, length) cell of a set gets the same number of parts.

A part is kept only if all of these hold:
  - the CAD kernel builds every plan item into one valid solid that the item changed
    (wide_reference.Builder), and the stored program, run in the sandbox like every row of
    data/generated/, gives the same solid again and exports to STEP;
  - it holds none of the four held-out PAIRINGS (forge.system1.splits.HELD_OUT_PAIRS);
  - it holds a held-out ORDER (wide_sample.HELD_OUT_ORDERS) if and only if its set is wide_order;
  - its id, its plan and its geometry fingerprint are not those of any part already on disk:
    the 124,000 composed parts, the long and extra-long sets, and every other wide part.
That FreeCAD builds the same solid through the recipes is checked afterwards, for a sample
by wide_prove.py and for every part when its session is recorded (wide_sessions.py).

Run:    uv run python -m forge.freecad.wide_parts --set wide_iid [--per-cell 58] [--workers 6]
        (run again to carry on; what is there is kept)
        uv run python -m forge.freecad.wide_parts --check       # the leak proof only
Output: data/system1/wide_parts/<set>/wide_<base>.jsonl
        data/system1/wide_parts/leak_check.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import multiprocessing.connection
import random
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from forge.freecad.wide_sample import (
    BASES,
    HELD_OUT_SCALES,
    PROFILES,
    decimals_of,
    held_out_orders_in,
    sample_plan,
    scale_of,
)
from forge.generators.base import BBOX_TOLERANCE_MM, VOLUME_RELATIVE_TOLERANCE
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.system1.splits import held_out_pairs_in
from forge.system1.steps import Step

WIDE_DIR = PROJECT_ROOT / "data" / "system1" / "wide_parts"
# name -> (number profile, holds a held-out order?, fewest items, most items, parts per cell, seed)
SETS: dict[str, tuple[str, bool, int, int, int, int]] = {
    "wide_iid": ("train", False, 1, 13, 58, 31),
    "wide_numbers": ("numbers", False, 1, 13, 58, 32),
    "wide_order": ("train", True, 3, 13, 69, 33),
    "wide_starts": ("train", False, 1, 13, 58, 34),
    "wide_train": ("train", False, 1, 13, 1150, 35),
    "wide_train_long": ("train", False, 14, 16, 250, 36),
}
TRAIN_SETS = ("wide_train", "wide_train_long")
TEST_SETS = tuple(name for name in SETS if name not in TRAIN_SETS)
DRAWS = 8               # plans drawn for one serial number before it is given up
# A worker that takes longer than this on one part is killed (the kernel did not return):
TASK_SECONDS = 20       # seconds for any part ...
TASK_SECONDS_PER_ITEM = 2   # ... plus this many for every plan item
TRAIN_DECIMALS = 2      # no number of a training-profile part has more decimals than this

_sandbox = None


def plan_json(steps: list[Step]) -> list[dict]:
    return [{"kind": step.kind, "slots": dict(step.slots)} for step in steps]


def plan_fingerprint(plan: list[dict]) -> str:
    """The audit's plan fingerprint (forge.freecad.audit.plan_fingerprint), kept in step with it
    by a test: a hash of the plan's kinds and numbers, in order."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]


def part_id(family: str, plan: list[dict]) -> str:
    return hashlib.sha1(json.dumps([family, plan], sort_keys=True).encode()).hexdigest()[:16]


def generator_version() -> str:
    """A hash of everything that decides what a wide part is."""
    digest = hashlib.sha256()
    here = Path(__file__).parent
    for name in ("wide_sample.py", "wide_reference.py", "../generators/families/composed.py",
                 "../generators/families/_common.py", "../generators/base.py",
                 "../system1/steps.py", "../system1/splits.py"):
        digest.update(name.encode())
        digest.update((here / name).read_bytes())
    return digest.hexdigest()[:12]


# --- one part (runs in a worker process) --------------------------------------------------------

def make(task: tuple[str, str, int, int]) -> dict:
    """Draw, build and verify the part of one serial number. Returns {"task", "row" or "why"}."""
    global _sandbox
    from forge.sandbox import Sandbox

    name, base, items, serial = task
    profile, order, _, _, _, seed = SETS[name]
    rng = random.Random(f"{seed}:{name}:{base}:{items}:{serial}")
    began = time.perf_counter()
    made = None
    for _ in range(DRAWS):
        made = sample_plan(rng, base, items, PROFILES[profile], order)
        if made is not None:
            break
    if made is None:
        return {"task": task, "why": "no plan"}
    builder, facts = made
    code, final = builder.program(), builder.measures[-1]
    if _sandbox is None:
        _sandbox = Sandbox(timeout=30)
    drawn_at = time.perf_counter()
    reply = _sandbox.run(code, check_export=True)
    # How long the drawing (with every kernel try) and the second build in the sandbox took.
    facts["seconds"] = [round(drawn_at - began, 3), round(time.perf_counter() - drawn_at, 3)]
    if reply["status"] != "ok":
        return {"task": task, "why": f"sandbox {reply['status']}"}
    measure = reply["measure"]
    if not measure["one_valid_solid"] or not reply.get("step_export_ok") \
            or abs(measure["volume"] - final["volume"]) > VOLUME_RELATIVE_TOLERANCE * final["volume"] \
            or any(abs(a - b) > BBOX_TOLERANCE_MM
                   for a, b in zip(measure["bbox"], final["bbox"], strict=True)):
        return {"task": task, "why": "the stored program does not give the same solid"}
    plan = plan_json(builder.steps)
    family = f"wide_{base}"
    return {"task": task, "row": {
        "id": part_id(family, plan), "source": f"gen:{family}", "license": "forge",
        "generator_version": generator_version(), "family": family, "set": name,
        "serial": serial, "plan": plan, "params": builder.params, "code": code,
        "measured": {key: measure[key] for key in
                     ("volume", "area", "bbox", "n_faces", "n_edges", "cylinders")},
        "step_volumes": builder.volumes, "geom_fingerprint": measure["fingerprint"],
        "drawn": facts}}


# --- reading -----------------------------------------------------------------------------------

LIGHT = ("id", "source", "license", "generator_version", "family", "set", "serial", "plan",
         "measured", "step_volumes", "geom_fingerprint", "drawn")


def read_wide(name: str, full: bool = False) -> Iterator[dict]:
    """Every part of one wide set, base by base (without the program text unless `full`)."""
    for base in BASES:
        path = WIDE_DIR / name / f"wide_{base}.jsonl"
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                row = json.loads(line)
                yield row if full else {key: row[key] for key in LIGHT}


def steps_of_part(part: dict) -> list[Step]:
    """A wide part's plan as steps (a wide row holds its plan; nothing is read off names)."""
    return [Step(item["kind"], dict(item["slots"])) for item in part["plan"]]


# --- the leak proof ----------------------------------------------------------------------------

def facts_of(part: dict) -> dict:
    kinds = [item["kind"] for item in part["plan"]]
    numbers = [value for item in part["plan"] for slot, value in item["slots"].items()
               if slot != "count"]
    scale = scale_of(Step(kinds[0], part["plan"][0]["slots"]))
    return {"items": len(kinds), "pairing": bool(held_out_pairs_in(kinds)),
            "order": bool(held_out_orders_in(kinds)),
            "held_out_scale": any(low <= scale < high for low, high in HELD_OUT_SCALES),
            "most_decimals": max(decimals_of(value) for value in numbers)}


def old_parts() -> dict[str, dict[str, set]]:
    """split -> ids, plans and geometry fingerprints of every part made before the wide sets."""
    from forge.freecad.long_train_parts import on_disk

    return on_disk()


def leak_check() -> dict:
    """The proof, from the files alone."""
    old = old_parts()
    sets: dict[str, dict] = {}
    for name in SETS:
        rows = list(read_wide(name))
        facts = [facts_of(part) for part in rows]
        sets[name] = {"rows": rows, "facts": facts, "ids": {part["id"] for part in rows},
                      "plans": {plan_fingerprint(part["plan"]) for part in rows},
                      "shapes": {part["geom_fingerprint"] for part in rows}}
    report: dict = {"held_out_pairs": "forge.system1.splits.HELD_OUT_PAIRS",
                    "held_out_orders": "forge.freecad.wide_sample.HELD_OUT_ORDERS",
                    "held_out_scales_mm": [list(band) for band in HELD_OUT_SCALES],
                    "train_decimals_at_most": TRAIN_DECIMALS, "sets": {}}
    for name, found in sets.items():
        profile, order, low, high, _, _ = SETS[name]
        rows, facts = found["rows"], found["facts"]
        cells = Counter((part["family"].removeprefix("wide_"), fact["items"])
                        for part, fact in zip(rows, facts, strict=True))
        entry = {
            "parts": len(rows), "distinct_ids": len(found["ids"]),
            "distinct_plans": len(found["plans"]), "distinct_shapes": len(found["shapes"]),
            "with_a_held_out_pairing": sum(fact["pairing"] for fact in facts),
            "with_a_held_out_order": sum(fact["order"] for fact in facts),
            "with_a_held_out_scale": sum(fact["held_out_scale"] for fact in facts),
            "with_a_number_of_3_or_more_decimals":
                sum(fact["most_decimals"] > TRAIN_DECIMALS for fact in facts),
            "outside_the_lengths": sum(not low <= fact["items"] <= high for fact in facts),
            "by_plan_items": {str(items): {base: cells[(base, items)] for base in BASES}
                              for items in range(low, high + 1)},
            "shared_with": {},
        }
        others = {**{f"old:{split}": value for split, value in sorted(old.items())},
                  **{other: value for other, value in sets.items() if other != name}}
        for other, value in others.items():
            entry["shared_with"][other] = {
                "ids": len(found["ids"] & value["ids"]), "plans": len(found["plans"] & value["plans"]),
                "shapes": len(found["shapes"] & value["shapes"]), "parts_there": len(value["ids"])}
        shared = sum(count for other in entry["shared_with"].values()
                     for key, count in other.items() if key != "parts_there")
        n = len(rows)
        rules = {
            "nothing shared with any other set": shared == 0,
            "every id, plan and shape once": len(found["ids"]) == len(found["plans"])
            == len(found["shapes"]) == n,
            "no held-out pairing": entry["with_a_held_out_pairing"] == 0,
            "held-out order only in wide_order, and there in every plan":
                entry["with_a_held_out_order"] == (n if order else 0),
            "held-out scales only in wide_numbers, and there in every part":
                entry["with_a_held_out_scale"] == (n if profile == "numbers" else 0),
            "three or more decimals only in wide_numbers, and there in every part":
                entry["with_a_number_of_3_or_more_decimals"] == (n if profile == "numbers" else 0),
            "every length inside the set's range": entry["outside_the_lengths"] == 0,
        }
        entry["rules"] = rules
        entry["clean"] = all(rules.values())
        report["sets"][name] = entry
    report["passed"] = all(entry["clean"] for entry in report["sets"].values())
    WIDE_DIR.mkdir(parents=True, exist_ok=True)
    (WIDE_DIR / "leak_check.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def print_check(report: dict) -> None:
    for name, entry in report["sets"].items():
        print(f"{name}: {entry['parts']} parts, {entry['distinct_ids']} ids, "
              f"{entry['distinct_plans']} plans, {entry['distinct_shapes']} geometry fingerprints; "
              f"held-out pairing {entry['with_a_held_out_pairing']}, held-out order "
              f"{entry['with_a_held_out_order']}, held-out scale {entry['with_a_held_out_scale']}, "
              f"3+ decimals {entry['with_a_number_of_3_or_more_decimals']}")
        worst = {other: shared for other, shared in entry["shared_with"].items()
                 if shared["ids"] or shared["plans"] or shared["shapes"]}
        print(f"    shared with {len(entry['shared_with'])} other sets "
              f"({sum(s['parts_there'] for s in entry['shared_with'].values())} parts): "
              f"{worst or 'nothing'}")
        for rule, holds in entry["rules"].items():
            if not holds:
                print(f"    BROKEN: {rule}")
    print("LEAK CHECK PASSED" if report["passed"] else "LEAK CHECK FAILED")


# --- the command -------------------------------------------------------------------------------

def _serve(pipe) -> None:
    """A worker process: warm up, say so, then one task in, one answer out."""
    global _sandbox
    import cadquery  # noqa: F401 - slow to import; done before the first deadline starts

    from forge.sandbox import Sandbox

    _sandbox = Sandbox(timeout=30)
    _sandbox.run("result = None")       # starts the helper process now
    pipe.send("ready")
    while True:
        pipe.send(make(pipe.recv()))


class Worker:
    """One worker process that can be killed alone.

    Why not a process pool. The kernel sometimes never returns (seen: CadQuery's clean-up
    of a solid after a cut). A pool cannot stop one task; it can only be thrown away with
    everything it holds. Here a worker that passes its deadline is killed and replaced, its
    part is given up, and the others go on. A new worker gets no task before it says it is
    ready, so a slow start on a busy machine is never mistaken for a hang."""

    def __init__(self) -> None:
        self.pipe, other = multiprocessing.Pipe()
        self.process = multiprocessing.Process(target=_serve, args=(other,), daemon=True)
        self.process.start()
        self.ready = False
        self.task: tuple | None = None
        self.deadline = 0.0

    def give(self, task: tuple) -> None:
        self.task = task
        self.deadline = time.time() + TASK_SECONDS + TASK_SECONDS_PER_ITEM * task[2]
        self.pipe.send(task)

    def stop(self) -> None:
        self.process.kill()
        self.process.join()


def generate(name: str, per_cell: int, workers: int, max_minutes: float | None) -> None:
    _, _, low, high, _, _ = SETS[name]
    folder = WIDE_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    run_dir = start_run("freecad-wide-parts", {"set": name, "per_cell": per_cell,
                                               "workers": workers, "sets": SETS,
                                               "generator_version": generator_version()})
    seen = {"ids": set(), "plans": set(), "shapes": set()}
    for value in old_parts().values():
        for key in seen:
            seen[key] |= value[key]
    kept: Counter = Counter()
    next_serial: Counter = Counter()
    for other in SETS:
        for part in read_wide(other):
            seen["ids"].add(part["id"])
            seen["plans"].add(plan_fingerprint(part["plan"]))
            seen["shapes"].add(part["geom_fingerprint"])
            if other == name:
                cell = (part["family"].removeprefix("wide_"), len(part["plan"]))
                kept[cell] += 1
                next_serial[cell] = max(next_serial[cell], part["serial"] + 1)
    cells = [(base, items) for items in range(low, high + 1) for base in BASES]
    files = {base: (folder / f"wide_{base}.jsonl").open("a") for base in BASES}
    totals: Counter = Counter()
    flying: Counter = Counter()
    started = last_print = time.time()

    def next_task() -> tuple | None:
        """The next serial number of the cell that is furthest behind (None: nothing to do)."""
        if max_minutes is not None and time.time() - started > max_minutes * 60:
            return None
        behind = [cell for cell in cells if kept[cell] + flying[cell] < per_cell]
        if not behind:
            return None
        cell = min(behind, key=lambda cell: kept[cell] + flying[cell])
        next_serial[cell] += 1
        flying[cell] += 1
        return (name, cell[0], cell[1], next_serial[cell] - 1)

    def next_task_exists() -> bool:
        in_time = max_minutes is None or time.time() - started <= max_minutes * 60
        return in_time and any(kept[cell] + flying[cell] < per_cell for cell in cells)

    def take(result: dict) -> None:
        row = result.get("row")
        if row is None:
            totals[f"failed: {result['why']}"] += 1
            return
        fingerprint = plan_fingerprint(row["plan"])
        if row["id"] in seen["ids"] or fingerprint in seen["plans"] \
                or row["geom_fingerprint"] in seen["shapes"]:
            totals["already_seen"] += 1
            return
        seen["ids"].add(row["id"])
        seen["plans"].add(fingerprint)
        seen["shapes"].add(row["geom_fingerprint"])
        cell = (result["task"][1], result["task"][2])
        files[cell[0]].write(json.dumps(row) + "\n")
        files[cell[0]].flush()
        kept[cell] += 1
        totals["kept"] += 1

    crew = [Worker() for _ in range(workers)]
    while True:
        for worker in crew:
            if worker.ready and worker.task is None:
                task = next_task()
                if task is not None:
                    worker.give(task)
        busy = [worker for worker in crew if worker.task is not None]
        warming = [worker for worker in crew if not worker.ready]
        if not busy and (not warming or next_task_exists() is False):
            break
        ready = multiprocessing.connection.wait([worker.pipe for worker in busy + warming],
                                                timeout=2.0)
        for at, worker in enumerate(crew):
            if not worker.ready:
                if worker.pipe in ready:
                    worker.pipe.recv()
                    worker.ready = True
                continue
            if worker.task is None:
                continue
            cell = (worker.task[1], worker.task[2])
            if worker.pipe in ready:
                try:
                    take(worker.pipe.recv())
                except EOFError:        # the worker died by itself (the kernel crashed)
                    totals["failed: the worker died"] += 1
                    worker.stop()
                    crew[at] = Worker()
                    flying[cell] -= 1
                    continue
                worker.task = None
                flying[cell] -= 1
            elif time.time() > worker.deadline:
                totals["failed: the kernel did not return"] += 1
                worker.stop()
                crew[at] = Worker()
                flying[cell] -= 1
        if time.time() - last_print > 120:
            last_print = time.time()
            minutes = (last_print - started) / 60
            print(f"[{minutes:6.1f} min] {name}: {sum(kept.values())} of {per_cell * len(cells)} "
                  f"parts on disk; this run {dict(totals)} "
                  f"({totals['kept'] / max(minutes, 1e-9):.0f} parts/min)", flush=True)
            log_metrics(run_dir, on_disk=sum(kept.values()), **dict(totals))
    for worker in crew:
        worker.stop()
    for f in files.values():
        f.close()
    log_metrics(run_dir, final=True, on_disk=sum(kept.values()), **dict(totals))
    print(f"done: {name} has {sum(kept.values())} of {per_cell * len(cells)} parts in {folder} "
          f"after {(time.time() - started) / 60:.1f} min; this run {dict(totals)}; "
          f"run folder {run_dir}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--set", nargs="*", choices=list(SETS), default=[])
    parser.add_argument("--per-cell", type=int, default=None,
                        help="parts for every (base, number of plan items); default: SETS")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument("--check", action="store_true", help="only run the leak proof")
    args = parser.parse_args()
    for name in [] if args.check else args.set:
        generate(name, SETS[name][4] if args.per_cell is None else args.per_cell, args.workers,
                 args.max_minutes)
    report = leak_check()
    print_check(report)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
