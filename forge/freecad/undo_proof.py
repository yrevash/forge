"""Proof that undo is exact, and that a wrong command can always be taken back cleanly.

For each sampled part, while it is being built in FreeCAD:

  A. UNDO AND REDO. After every command: undo it and check the snapshot is
     exactly the one from before the command; issue the command again and
     check the snapshot is exactly the one from after it.
  B. WRONG COMMANDS. After a step, with some probability, do something
     deliberately wrong (a wrong number or a wrong feature), then undo it
     command by command. The snapshot must be exactly what it was, and the
     document's full object list (origins included) must be the same, so no
     stray sketch or broken feature is left behind.
  C. STILL THE RIGHT PART. After all that, the finished solid must match the
     stored measurement.
  D. DEEP UNDO (some parts). Undo the whole build, one command at a time, back
     to the empty document, checking every snapshot on the way. This goes past
     the 20 transactions FreeCAD remembers, so it also tests the rebuild path
     of inside/session.py.

  E. NOISE-LIKE HISTORIES (`--histories`). Parts A to D never did what a noisy session
     does. E does: while a part is built, between any two commands, a random wrong
     chain is carried out (histories.py: stray sketches with shapes on top of each
     other, stray pads and pockets, patterns and fillets on them, random walks) and
     then undone command by command. Checked after every chain:
       - the snapshot is what it was, and no object is left behind (as in B);
       - HIDDEN STATE: no shape carries a tolerance above 0.00001 mm. FreeCAD's undo
         removes a feature but not what computing it did to the shapes underneath
         (inside/session.py, "Exact undo"); the snapshot cannot show that;
     and at the end the part must still build to the stored solid with every
     feature valid. `--exact-undo off` runs the runtime as it was before the fix,
     to count how common the hidden state is; `on` (the default) must find none.

"Exactly" means the snapshot dicts are equal, floats included. One number is
allowed to differ, and every time it does is counted and reported: the measured
VOLUME may change in its last binary digits (a few parts in 10^16) when FreeCAD
works a solid out a second time. The geometry kernel adds up a solid's faces in
an order that depends on where they sit in memory, and floating-point sums
depend on order. Everything else must be equal exactly.

Run:    uv run python -m forge.freecad.undo_proof [--per-base 60] [--long-per-base 15] [--workers 3]
        uv run python -m forge.freecad.undo_proof --histories --per-base 125 --long-per-base 20
        uv run python -m forge.freecad.undo_proof --histories --exact-undo off   (the old runtime)
Exit code 0 only if nothing differed.
"""

from __future__ import annotations

import argparse
import copy
import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from forge.freecad.build import against_stored
from forge.freecad.catalogue import COMMANDS
from forge.freecad.client import FreeCADClient
from forge.freecad.histories import CHAIN_KINDS, run_chain
from forge.freecad.parts import sample_rows
from forge.freecad.prove import _clients, client
from forge.freecad.recipes import Cmd, Context, context_of, flatten, recipe
from forge.freecad.valid import valid_commands
from forge.runs import log_metrics, start_run
from forge.system1.steps import Step, steps_of

WRONG_CHANCE = 0.5      # how often a wrong command follows a step
DEEP_EVERY = 4          # one normal part in this many is undone all the way
VOLUME_NOISE = 1e-12    # relative; rounding noise in a re-computed volume is ~1e-16
CHAIN_CHANCE = 0.12     # part E: how often a wrong chain follows a command
# mm. Clean builds of 580 parts (6 Oct 2026): the largest tolerance in any shape was 1e-7 for
# most, at most 1.03e-6 (a boss fused next to a fillet). Ten times that is not a clean build.
# The damage this looks for is of another order: 14 mm and 24 mm in the livelock session.
HEALTHY_TOLERANCE = 1e-5


def _volumes_out(snapshot: dict) -> tuple[dict, list[float]]:
    """The snapshot with every measured volume taken out, and those volumes."""
    bare = copy.deepcopy(snapshot)
    solids = [bare["solid"], *(item.get("solid") for item in bare["items"])]
    return bare, [solid.pop("volume") for solid in solids if solid]


def same_state(a: dict, b: dict) -> str:
    """'identical', 'volume noise' (equal but for the last bits of a volume) or 'different'."""
    if a == b:
        return "identical"
    (bare_a, volumes_a), (bare_b, volumes_b) = _volumes_out(a), _volumes_out(b)
    if bare_a == bare_b and len(volumes_a) == len(volumes_b) and all(
            abs(x - y) <= VOLUME_NOISE * abs(x) for x, y in zip(volumes_a, volumes_b)):
        return "volume noise"
    return "different"


def differences(a: object, b: object, path: str = "") -> list[str]:
    """Where two snapshots differ, as 'path: one | other' lines (for the report)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return [line for key in sorted(set(a) | set(b))
                for line in differences(a.get(key), b.get(key), f"{path}/{key}")]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [line for number, (x, y) in enumerate(zip(a, b, strict=True))
                for line in differences(x, y, f"{path}[{number}]")]
    return [] if a == b else [f"{path}: {str(a)[:80]} | {str(b)[:80]}"]


def wrong_commands(kind: str, context: Context, rng: random.Random) -> list[Cmd]:
    """A deliberately wrong thing to do after a step."""
    plane = Cmd("select_plane", {"plane": "XY"})
    if kind == "huge_fillet":           # wrong number: no solid has room for this radius
        return [Cmd("select_edges", {"rule": "all"}), Cmd("fillet", {"radius": 500.0})]
    if kind == "huge_chamfer":
        return [Cmd("select_edges", {"rule": "top_face"}), Cmd("chamfer", {"size": 500.0})]
    if kind == "pocket_in_air":         # wrong number: sketched 50 mm above the part
        return [plane, Cmd("new_sketch", {"offset": context.height + 50}), Cmd("sketch_circle"),
                Cmd("constrain_diameter", {"value": 5.0}), Cmd("leave_sketch"),
                Cmd("pocket", {"depth": 1.0})]
    if kind == "abandoned_sketch":      # wrong number, noticed before leaving the sketch
        return [plane, Cmd("new_sketch", {"offset": context.height}), Cmd("sketch_slot"),
                Cmd("constrain_width", {"value": rng.choice([3.0, 70.0])}),
                Cmd("constrain_angle", {"value": 37.0})]
    # wrong feature: something the plan never asked for, with numbers that do build
    x, y = float(rng.randint(-9, 9)), float(rng.randint(-9, 9))
    step = rng.choice([
        Step("boss", {"diameter": 7.0, "height": 9.0, "x": x, "y": y}),
        Step("pad", {"length": 9.0, "width": 5.0, "height": 4.0, "x": x, "y": y}),
        Step("hole_pair", {"diameter": 3.0, "x": abs(x) + 3, "y": y}),
        Step("slot", {"length": 9.0, "width": 3.0, "depth": 1.0, "angle": 90.0, "x": x, "y": y}),
        Step("counterbore", {"hole_diameter": 3.0, "diameter": 6.0, "depth": 1.5, "x": x, "y": y}),
        Step("row", {"count": 3, "hole_diameter": 3.0, "spacing": 6.0, "y": y}),
        Step("polar", {"count": 5, "hole_diameter": 3.0, "circle_diameter": 16.0}),
    ])
    return flatten(recipe(step, context), rng)


WRONG_KINDS = ("huge_fillet", "huge_chamfer", "pocket_in_air", "abandoned_sketch",
               "wrong_feature")


def do_wrong(fc: FreeCADClient, context: Context, rng: random.Random, stats: Counter,
             problems: list[str]) -> None:
    """Part B: do something wrong, undo it, and check nothing is left of it."""
    before, objects = fc.snapshot(), fc.objects()
    kind = rng.choice(WRONG_KINDS)
    carried_out = 0
    for command in wrong_commands(kind, context, rng):
        reply = fc.command(command.name, **command.args)
        if reply["status"] != "ok":
            stats[f"wrong {kind}: stopped by '{reply['status']}'"] += 1
            break
        carried_out += 1
        if any(not item["valid"] for item in reply["snapshot"]["items"]):
            stats[f"wrong {kind}: left an invalid feature"] += 1
    else:
        stats[f"wrong {kind}: built"] += 1
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


def undo_one(job: tuple[dict, bool]) -> dict:
    row, deep = job
    fc = client()
    rng = random.Random(f"undo:{row['id']}")
    steps = steps_of(row["family"], row["params"])
    context = context_of(steps)
    stats: Counter = Counter()
    problems: list[str] = []
    trail: list[dict] = []      # trail[d] = the snapshot when d commands could be undone
    after = None

    for step in steps:
        for command in flatten(recipe(step, context), rng):
            after = fc.command(command.name, **command.args)
            if after["status"] != "ok":
                problems.append(f"{command.name}: {after['status']} {after.get('reason')}")
                return {"id": row["id"], "problems": problems, "stats": stats}
            if command.name == "new_document":
                trail = [after["snapshot"]]
                continue
            # A: undo, compare with before; issue again, compare with after.
            undone = fc.command("undo")
            again = fc.command(command.name, **command.args)
            stats["undo+redo checks"] += 1
            back, forth = (same_state(undone["snapshot"], trail[-1]),
                           same_state(again["snapshot"], after["snapshot"]))
            stats[f"after undo: {back}"] += 1
            stats[f"after issuing again: {forth}"] += 1
            if back == "different":
                problems.append(f"undo of {command.name}: not the state before it")
            if forth == "different":
                problems.append(f"redo of {command.name}: not the state after it")
            trail.append(after["snapshot"])
        if rng.random() < WRONG_CHANCE:
            do_wrong(fc, context, rng, stats, problems)

    # C: the finished part is still the right part.
    problems += against_stored(fc.snapshot()["solid"], row["measured"])

    if deep:    # D: all the way back to the empty document
        for depth in range(len(trail) - 1, 0, -1):
            reply = fc.command("undo")
            stats["deep undos"] += 1
            verdict = same_state(reply["snapshot"], trail[depth - 1])
            stats[f"deep undo: {verdict}"] += 1
            if verdict == "different":
                problems.append(f"deep undo to depth {depth - 1}: not the recorded state")
                break
        stats["parts undone to empty"] += 1
        stats["longest undo chain"] = len(trail) - 1
    return {"id": row["id"], "problems": problems, "stats": stats}


# --- part E: noise-like histories ----------------------------------------------------------------

def _damaged(fc: FreeCADClient) -> dict[str, float]:
    """Shapes whose tolerance is not the healthy one: state the snapshot does not show."""
    return {name: value for name, value in fc.tolerances().items() if value > HEALTHY_TOLERANCE}


def do_chain(fc: FreeCADClient, numbers: list[float], rng: random.Random, stats: Counter,
             problems: list[str], hidden: list[str]) -> None:
    """One wrong chain, then undo all of it; the three checks of part E."""
    before, objects, damaged_before = fc.snapshot(), fc.objects(), _damaged(fc)
    restarts = fc.restarts
    kinds = [kind for kind in CHAIN_KINDS
             if (kind == "in_the_sketch") == (before["session"]["open_sketch"] is not None)
             or kind == "random_walk"]
    kind = rng.choice(kinds)
    carried_out, reply = run_chain(fc, rng, kind, numbers, valid_commands(before))
    if not carried_out:
        return
    stats["chains"] += 1
    stats[f"chain {kind}"] += 1
    stats["chain commands undone"] += len(carried_out)
    stats["longest chain undone"] = max(stats["longest chain undone"], len(carried_out))
    if any(not item["valid"] for item in reply["snapshot"]["items"]):
        stats["chains that left an invalid feature"] += 1
    for _ in carried_out:
        reply = fc.command("undo")
        if reply["status"] != "ok":
            problems.append(f"undo refused after {kind}: {reply.get('reason')}")
            return
    verdict = same_state(reply["snapshot"], before)
    stats[f"chain, after undo: {verdict}"] += 1
    stats["chains during which the worker was replaced"] += fc.restarts > restarts
    if verdict == "different":
        problems.append(f"after undoing a {kind} chain ({len(carried_out)} commands, worker "
                        f"replaced {fc.restarts - restarts} times, {fc.failures[-2:]}): "
                        f"{differences(reply['snapshot'], before)[:4]}; the chain: "
                        f"{[name for name, _ in carried_out]}")
    if fc.objects() != objects:
        problems.append(f"after undoing a {kind} chain: stray objects in the document")
    # Only damage this chain made: what an earlier chain left is counted there.
    damaged = {name: value for name, value in _damaged(fc).items()
               if damaged_before.get(name) != value}
    if damaged:
        stats["chains that left hidden state"] += 1
        features = [name for name, _ in carried_out if COMMANDS[name]["group"] in
                    ("feature", "dressup", "pattern")]
        hidden.append(f"{kind}: {', '.join(features) or 'no feature'} -> "
                      f"{max(damaged.values()):.3g} mm on {sorted(damaged)}")


def history_one(job: tuple[dict, bool]) -> dict:
    """Build one part with wrong chains between its commands (part E)."""
    row, exact = job
    fc = client()
    if fc.exact_undo is not exact:
        fc.set_exact_undo(exact)
    rng = random.Random(f"history:{row['id']}")
    steps = steps_of(row["family"], row["params"])
    context = context_of(steps)
    numbers = [value for step in steps for value in step.slots.values()]
    stats: Counter = Counter()
    problems: list[str] = []
    hidden: list[str] = []
    for step in steps:
        for command in flatten(recipe(step, context), rng):
            reply = fc.command(command.name, **command.args)
            invalid = [item["name"] for item in reply.get("snapshot", {}).get("items", [])
                       if not item["valid"]]
            if reply["status"] != "ok" or invalid:
                stats["parts whose own command failed after a chain"] += 1
                problems.append(f"{command.name}: {reply['status']} {reply.get('reason')}, "
                                f"invalid {invalid}")
                return {"id": row["id"], "problems": problems, "stats": stats, "hidden": hidden}
            if command.name != "new_document" and rng.random() < CHAIN_CHANCE:
                do_chain(fc, numbers, rng, stats, problems, hidden)
    final = against_stored(fc.snapshot()["solid"], row["measured"])
    stats["parts that did not end at the stored solid"] += bool(final)
    stats["parts with hidden state at some point"] += bool(hidden)
    return {"id": row["id"], "problems": problems + final, "stats": stats, "hidden": hidden}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-base", type=int, default=60)
    parser.add_argument("--long-per-base", type=int, default=15)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--histories", action="store_true",
                        help="part E: noise-like wrong chains instead of parts A to D")
    parser.add_argument("--exact-undo", choices=("on", "off"), default="on",
                        help="off = the runtime as it was before 6 Oct 2026 (to measure)")
    args = parser.parse_args()
    run_dir = start_run("freecad-undo-proof", vars(args))

    rows = sample_rows(args.per_base, args.long_per_base, args.seed)
    normal = [row for row in rows if not row["long"]]
    deep_ids = {row["id"] for row in normal[::DEEP_EVERY]}
    exact = args.exact_undo == "on"
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        if args.histories:
            results = list(pool.map(history_one, [(row, exact) for row in rows]))
        else:
            results = list(pool.map(undo_one, [(row, row["id"] in deep_ids) for row in rows]))

    total: Counter = Counter()
    for r in results:
        longest = {key: max(total[key], r["stats"].pop(key, 0))
                   for key in ("longest undo chain", "longest chain undone")}
        total.update(r["stats"])
        total.update({key: value - total[key] for key, value in longest.items() if value})
    failures = [r for r in results if r["problems"]]
    hidden = [text for r in results for text in r.get("hidden", [])]
    if args.histories:
        print(f"exact undo: {args.exact_undo}")
        causes = Counter(text.split(" -> ")[0] for text in hidden)
        print(f"chains that left hidden state, by what the chain made ({len(hidden)}):")
        for cause, count in causes.most_common(12):
            print(f"  {count:5d}  {cause}")
        if exact and hidden:
            failures += [{"id": "hidden state", "problems": hidden[:3]}]
    print(f"{len(results)} parts ({sum(row['long'] for row in rows)} long), "
          f"{time.time() - started:.1f}s")
    for name, count in sorted(total.items()):
        print(f"  {name:55s} {count}")
    print(f"parts with any difference: {len(failures)}")
    for r in failures[:20]:
        print(f"  {r['id']}: {r['problems'][:3]}")
    restarts = sum(c.restarts for c in _clients)
    print(f"worker restarts: {restarts}")
    log_metrics(run_dir, final=True, parts=len(results), failures=len(failures),
                restarts=restarts, **{k.replace(" ", "_"): v for k, v in total.items()})
    for c in _clients:
        c.close()
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
