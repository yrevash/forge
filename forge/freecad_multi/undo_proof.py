"""Proof that undo is exact for structures, and that a wrong body can always be taken back.

For each sampled structure, while it is being built in FreeCAD:

  A. UNDO AND REDO. After every command (also `move_body`, `turn_body`, `activate_body`
     and the primitives): undo it and check the snapshot is the one from before the
     command; issue it again and check the snapshot is the one from after it.
  B. WRONG BODIES. After a part, with some probability, do something wrong on purpose,
     then undo it command by command:
         misplaced body       the body just built is moved somewhere else
         misturned body       ... or turned
         wrong-size body      a whole extra body of the wrong size, placed into the structure
         wrong primitive      an extra cone, turned
         earlier body moved   another body is made active and moved
         feature on a side    a hole drilled into a side of the body just built
     The snapshot must be what it was, and the document's full object list (origins
     included) must be the same: nothing of the wrong body is left behind.
  C. STILL THE RIGHT STRUCTURE. The finished structure must match the resolver.
  D. DEEP UNDO. Undo the whole build, one command at a time, back to the empty document,
     checking every snapshot on the way. A structure takes hundreds of commands and
     FreeCAD remembers 20, so this runs the base session's rebuild path many times, with
     several bodies in the document.

"The same snapshot" means equal dicts, floats included, except that a measured VOLUME
may differ in its last binary digits (the kernel sums faces in memory order); every such
case is counted.

Run:    uv run python -m forge.freecad_multi.undo_proof [--rows-per-kind 5] [--random 50] [--workers 2]
Exit code 0 only if nothing differed.
"""

from __future__ import annotations

import argparse
import copy
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from forge.freecad_multi import sources
from forge.freecad_multi.build import compare_with_resolver
from forge.freecad_multi.client import MultiClient
from forge.freecad_multi.recipes import Cmd, flatten, script_of, structure_items
from forge.resolve import resolve_text
from forge.resolve.bodies import Body
from forge.resolve.random_plans import random_plan
from forge.runs import log_metrics, start_run

WRONG_CHANCE = 0.4      # how often a wrong sequence follows a part
MOST_PARTS = 30         # larger structures are left to the proof; here every command costs three
VOLUME_NOISE = 1e-12    # relative

_local = threading.local()
_clients: list[MultiClient] = []


def client() -> MultiClient:
    """This thread's own FreeCAD worker."""
    if not hasattr(_local, "client"):
        _local.client = MultiClient()
        _local.client.start()
        _clients.append(_local.client)
    return _local.client


def _volumes_out(snapshot: dict) -> tuple[dict, list[float]]:
    """The snapshot with every measured volume taken out, and those volumes."""
    bare = copy.deepcopy(snapshot)
    solids = [bare["solid"], *(item.get("solid") for item in bare["items"])]
    volumes = [solid.pop("volume") for solid in solids if solid]
    if bare.get("structure"):
        volumes.append(bare["structure"].pop("volume"))
        volumes += [pair.pop() for pair in bare["structure"]["overlapping"]]
    return bare, volumes


def same_state(a: dict, b: dict) -> str:
    """'identical', 'volume noise' (equal but for the last bits of a volume) or 'different'."""
    if a == b:
        return "identical"
    (bare_a, volumes_a), (bare_b, volumes_b) = _volumes_out(a), _volumes_out(b)
    if bare_a == bare_b and len(volumes_a) == len(volumes_b) and all(
            abs(x - y) <= VOLUME_NOISE * max(abs(x), 1.0) for x, y in zip(volumes_a, volumes_b)):
        return "volume noise"
    return "different"


WRONG_KINDS = ("misplaced body", "misturned body", "wrong-size body", "wrong primitive",
               "earlier body moved", "feature on a side")


def _value(rng: random.Random) -> float:
    return float(rng.choice((5, 12, 20, 35, 60, 150, 400)))


def wrong_commands(kind: str, bodies: int, rng: random.Random) -> list[Cmd]:
    """A plausible wrong sequence. `bodies`: how many bodies the document holds."""
    def value() -> float:
        return _value(rng)

    move = Cmd("move_body", {"x": value() - 50, "y": -value(), "z": value()})
    turn = Cmd("turn_body", {"yaw": rng.choice((0.0, 90.0, 37.0)), "pitch": rng.choice((0.0, 90.0)),
                             "roll": rng.choice((90.0, 180.0, -90.0))})
    if kind == "misplaced body":
        return [move]
    if kind == "misturned body":
        return [turn]
    if kind == "wrong-size body":
        return [Cmd("new_body"), Cmd("select_plane", {"plane": "XY"}),
                Cmd("new_sketch", {"offset": 0.0}), Cmd("sketch_rectangle"),
                Cmd("constrain_length", {"value": value()}),
                Cmd("constrain_width", {"value": value()}),
                Cmd("constrain_x", {"value": 0.0}), Cmd("constrain_y", {"value": 0.0}),
                Cmd("leave_sketch"), Cmd("pad_symmetric", {"length": value()}), move]
    if kind == "wrong primitive":
        return [Cmd("new_body"),
                Cmd("add_cone", {"bottom_diameter": value(), "top_diameter": 0.0,
                                 "height": value()}), turn, move]
    if kind == "earlier body moved":
        if bodies < 2:
            return [move]
        return [Cmd("activate_body", {"index": rng.randint(1, bodies - 1)}), move]
    return [Cmd("select_side", {"side": rng.choice(("top", "front", "left", "bottom"))}),
            Cmd("new_sketch", {"offset": 2.0}), Cmd("sketch_circle"),
            Cmd("constrain_diameter", {"value": 3.0}), Cmd("leave_sketch"),
            Cmd("pocket", {"depth": value()})]


def do_wrong(fc: MultiClient, rng: random.Random, stats: Counter, problems: list[str]) -> None:
    """Part B: do something wrong, undo it, and check nothing is left of it."""
    before, objects = fc.snapshot(), fc.objects()
    kind = rng.choice(WRONG_KINDS)
    carried_out = 0
    for command in wrong_commands(kind, before["structure"]["bodies"], rng):
        reply = fc.command(command.name, **command.args)
        if reply["status"] != "ok":
            stats[f"wrong {kind}: stopped by '{reply['status']}'"] += 1
            break
        carried_out += 1
    else:
        stats[f"wrong {kind}: carried out"] += 1
        if reply["snapshot"]["structure"]["overlapping"] != before["structure"]["overlapping"]:
            stats["wrong bodies that overlapped the structure"] += 1
    reply = None
    for _ in range(carried_out):
        reply = fc.command("undo")
    stats["wrong sequences"] += 1
    stats["wrong commands undone"] += carried_out
    if carried_out:
        verdict = same_state(reply["snapshot"], before)
        stats[f"wrong, after undo: {verdict}"] += 1
        if verdict == "different":
            problems.append(f"after undoing {kind}: the snapshot is not what it was")
    if fc.objects() != objects:
        problems.append(f"after undoing {kind}: stray objects in the document")


def undo_one(job: tuple[str, list[Body], set | None, bool]) -> dict:
    name, bodies, touching, deep = job
    fc = client()
    rng = random.Random(f"undo:{name}")
    stats: Counter = Counter()
    problems: list[str] = []
    trail: list[dict] = []      # trail[d] = the snapshot when d commands could be undone
    after = None
    items = structure_items(bodies)
    script = script_of(items)
    for position, (index, entry) in enumerate(script):
        for command in flatten([entry], rng):
            after = fc.command(command.name, **command.args)
            if after["status"] != "ok":
                problems.append(f"{command.name}: {after['status']} {after.get('reason')}")
                return {"id": name, "problems": problems, "stats": stats}
            if command.name == "new_document":
                trail = [after["snapshot"]]
                continue
            # A: undo, compare with before; issue again, compare with after.
            undone = fc.command("undo")
            again = fc.command(command.name, **command.args)
            stats["undo+redo checks"] += 1
            stats[f"undo+redo checks: {command.name}"] += 1
            back, forth = (same_state(undone["snapshot"], trail[-1]),
                           same_state(again["snapshot"], after["snapshot"]))
            stats[f"after undo: {back}"] += 1
            stats[f"after issuing again: {forth}"] += 1
            if back == "different":
                problems.append(f"undo of {command.name}: not the state before it")
            if forth == "different":
                problems.append(f"redo of {command.name}: not the state after it")
            trail.append(after["snapshot"])
        # B: after the last entry of a part, sometimes something wrong.
        last_of_item = index is not None and script[position + 1][0] != index
        if last_of_item and items[index].kind == "part" and rng.random() < WRONG_CHANCE:
            do_wrong(fc, rng, stats, problems)

    # C: the finished structure is still the right structure.
    problems += compare_with_resolver(bodies, items, fc.snapshot(), touching)
    stats["structures"] += 1
    stats["bodies"] += len(bodies)

    if deep:    # D: all the way back to the empty document
        for depth in range(len(trail) - 1, 0, -1):
            reply = fc.command("undo")
            stats["deep undos"] += 1
            verdict = same_state(reply["snapshot"], trail[depth - 1])
            stats[f"deep undo: {verdict}"] += 1
            if verdict == "different":
                problems.append(f"deep undo to depth {depth - 1}: not the recorded state")
                break
        stats["structures undone to empty"] += 1
        stats["longest undo chain"] = len(trail) - 1
    return {"id": name, "problems": problems, "stats": stats}


def sample(rows_per_kind: int, random_plans: int, seed: int) -> list[tuple]:
    """(name, bodies, touching) for a mix of example plans, structure rows and random plans."""
    jobs = []
    # The example and tuning plans bring the features, the sides and `activate_body`.
    for path in [*sources.example_plans(), *sources.tuning_plans()]:
        resolution = resolve_text(path.read_text())
        if resolution.complete:
            jobs.append((f"{path.parent.name}/{path.name}", resolution.bodies,
                         resolution.touching))
    for kind in sources.structure_kinds():
        for row in sources.structure_rows(kind, rows_per_kind):
            jobs.append((f"{kind}/{row['id']}", sources.row_bodies(row), None))
    for number in range(seed, seed + random_plans):
        resolution = resolve_text(random_plan(number))
        if len(resolution.bodies) >= 2:
            jobs.append((f"random seed {number}", resolution.bodies, resolution.touching))
    return [job for job in jobs if len(job[1]) <= MOST_PARTS]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rows-per-kind", type=int, default=5)
    parser.add_argument("--random", type=int, default=50)
    parser.add_argument("--deep-every", type=int, default=2,
                        help="one structure in this many is undone all the way")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--seed", type=int, default=5000)
    args = parser.parse_args()
    run_dir = start_run("freecad-multi-undo-proof", vars(args))

    jobs = sample(args.rows_per_kind, args.random, args.seed)
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(undo_one, [(*job, number % args.deep_every == 0)
                                           for number, job in enumerate(jobs)]))
    total: Counter = Counter()
    for r in results:
        longest = max(total["longest undo chain"], r["stats"].pop("longest undo chain", 0))
        total.update(r["stats"])
        total["longest undo chain"] = longest
    failures = [r for r in results if r["problems"]]
    print(f"{len(results)} structures, {time.time() - started:.1f}s")
    for name, count in sorted(total.items()):
        print(f"  {name:60s} {count}")
    print(f"structures with any difference: {len(failures)}")
    for r in failures[:20]:
        print(f"  {r['id']}: {r['problems'][:3]}")
    restarts = sum(c.restarts for c in _clients)
    print(f"worker restarts: {restarts}")
    log_metrics(run_dir, final=True, structures_sampled=len(results), failures=len(failures),
                restarts=restarts, seconds=round(time.time() - started, 1),
                counts=dict(sorted(total.items())))
    for c in _clients:
        c.close()
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
