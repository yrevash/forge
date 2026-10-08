"""Three proofs for the wide data. Each prints its numbers and writes a run folder.

  --recipes N   FreeCAD builds the wide plans exactly. For N parts of each chosen set, every
                plan item is built in a headless FreeCAD through its recipe (the dimensions
                of each sketch in a shuffled order) and compared with the CadQuery reference:
                the volume after EVERY item, and at the end the stored measurement (bounding
                box within 0.001 mm, volume within one part in a million, one valid solid).
                With --accepted only parts that have a recorded session are taken: for
                those the answer must be "0 differ" (exit code 1 otherwise). Without it the
                run measures how many freshly generated parts FreeCAD builds differently.
  --starts N    the teacher finishes from every start kind. For N parts and EVERY start
                kind of wide_starts.py (also with the undo history gone), FreeCAD is put in
                that start state and the teacher alone drives, one random member of its set
                per step. Passes only if every session ends with `done` on the stored solid
                and a clean document (exit code 1 otherwise).
  --orders N    how often does the strict-order rule call an equivalent build wrong? For N
                parts of each chosen source, every two NEIGHBOURING plan items after the base
                are swapped and the swapped plan is built by the kernel. Counted: the swap
                gives the same solid (the teacher's `undo` there is stricter than geometry
                needs), another solid, or no valid solid at all.

Run:    uv run python -m forge.freecad.wide_prove --recipes 250 --sets wide_iid wide_train --workers 3
        uv run python -m forge.freecad.wide_prove --starts 40 --sets wide_iid --workers 3
        uv run python -m forge.freecad.wide_prove --orders 500 --sets wide_train composed --workers 4
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from forge.freecad.lean import lean
from forge.freecad.play import HARD_EXTRA, HARD_FACTOR, end_check, run_start
from forge.freecad.prove import _clients, client
from forge.freecad.shards import complete_shards, read_sessions
from forge.freecad.teacher import clean_length, script_of, teacher
from forge.freecad.wide_build import build_plan
from forge.freecad.wide_parts import SETS, read_wide, steps_of_part
from forge.freecad.wide_starts import MAKERS, START_KINDS, make_start
from forge.runs import log_metrics, start_run
from forge.system1.steps import FEATURES, Step, steps_of


def sample(names: list[str], per_set: int, seed: int, accepted: set[str] | None = None,
           ) -> list[dict]:
    """`per_set` parts of each set, spread over its bases and lengths by a seeded shuffle."""
    rows = []
    for name in names:
        parts = [part for part in read_wide(name) if accepted is None or part["id"] in accepted]
        random.Random(f"{seed}:{name}").shuffle(parts)
        rows += parts[:per_set]
    return rows


def accepted_parts() -> set[str]:
    """The ids of the parts that have a finished session on disk."""
    from forge.freecad.wide_sessions import OUT_DIR

    found = set()
    for _, path in complete_shards(OUT_DIR):
        for header, _, end in read_sessions(path):
            if end["end"] == "done" and not end["problems"]:
                found.add(header["part_id"])
    return found


# --- 1. the recipes ------------------------------------------------------------------------------

def prove_one(part: dict) -> dict:
    result = build_plan(client(), steps_of_part(part), part["step_volumes"], part["measured"],
                        random.Random(part["id"]))
    return {"id": part["id"], "set": part["set"], "base": part["plan"][0]["kind"],
            "items": len(part["plan"]), "problems": result.problems, "steps": result.steps,
            "commands": result.commands}


def recipes(args: argparse.Namespace) -> int:
    accepted = accepted_parts() if args.accepted else None
    rows = sample(args.sets, args.recipes, args.seed, accepted)
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(prove_one, rows))
    built, matched, where = Counter(), Counter(), Counter()
    failures = [r for r in results if r["problems"]]
    for r in results:
        where[(r["set"], "same solid" if not r["problems"] else "DIFFERENT")] += 1
        for kind, ok in r["steps"]:
            built[kind] += 1
            matched[kind] += ok
    print(f"{len(results)} parts ({'with a recorded session' if args.accepted else 'as generated'}), "
          f"{sum(built.values())} plan items, {sum(r['commands'] for r in results)} commands, "
          f"{time.time() - started:.0f}s")
    print(f"parts FreeCAD builds differently from the reference: {len(failures)}")
    for key, count in sorted(where.items()):
        print(f"  {key[0]:16s} {key[1]:11s} {count}")
    print("by length:", dict(sorted(Counter(r["items"] for r in results).items())))
    print("plan item        built  matched")
    for kind, count in sorted(built.items()):
        print(f"  {kind:14s} {count:6d} {matched[kind]:8d}")
    last = Counter(r["steps"][-1][0] for r in failures if r["steps"])
    print("item at which a different part first differs:", dict(last))
    for r in failures[:8]:
        print(f"  {r['id']} {r['base']} {r['items']} items: {r['problems'][0][:160]}")
    log_metrics(start_run("freecad-wide-prove-recipes", vars(args)), final=True,
                parts=len(results), different=len(failures), built=dict(built),
                matched=dict(matched), first_differs_at=dict(last),
                restarts=sum(c.restarts for c in _clients),
                different_ids=[r["id"] for r in failures])
    return 1 if failures and args.accepted else 0


# --- 2. the teacher from every start -------------------------------------------------------------

def drive(task: tuple[dict, dict, str, bool]) -> dict:
    """Put FreeCAD in one start state and let the teacher alone drive to `done`."""
    part, other, kind, forget = task
    fc = client()
    plan = steps_of_part(part)
    rng = random.Random(f"wide-starts:{part['id']}:{kind}:{forget}")
    script, length = script_of(plan), clean_length(plan)
    commands = make_start(rng, kind, plan, steps_of_part(other))
    reply, carried_out = run_start(fc, commands, forget)
    first = None
    steps, problems, undos = 0, [], 0
    for _ in range(HARD_FACTOR * length + HARD_EXTRA + len(carried_out)):
        advice = teacher(plan, lean(reply["snapshot"]), script)
        if not advice.targets:
            break
        first = first or "+".join(sorted(set(advice.names())))
        target = rng.choice(advice.targets)
        if target.command not in reply["valid"]:
            problems.append(f"{target.command} is not valid")
            break
        reply = fc.command(target.command, **target.args)
        steps += 1
        undos += target.command == "undo"
        if reply["status"] != "ok":
            problems.append(f"{target.command} was refused: {reply.get('reason')}")
            break
    if not problems:
        if not reply["snapshot"]["session"]["finished"]:
            problems.append("the session did not reach done")
        else:
            problems += end_check(plan, reply["snapshot"], part["measured"])
    return {"kind": f"opened_{kind}" if forget else kind, "id": part["id"], "problems": problems,
            "steps": steps, "undos": undos, "first": first, "start_commands": len(carried_out)}


def starts(args: argparse.Namespace) -> int:
    rows = sample(args.sets, 4 * args.starts, args.seed)
    # Only parts FreeCAD builds as the reference does can be judged (see wide_sessions.py).
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        good = [part for part, result in zip(rows, pool.map(prove_one, rows), strict=True)
                if not result["problems"]][:args.starts]
        tasks = [(part, good[(at + 1) % len(good)], kind, forget)
                 for at, part in enumerate(good) for kind in START_KINDS
                 for forget in ((False, True) if kind in MAKERS else (False,))]
        started = time.time()
        results = list(pool.map(drive, tasks))
    failures = [r for r in results if r["problems"]]
    by_kind: dict[str, list[dict]] = {}
    for r in results:
        by_kind.setdefault(r["kind"], []).append(r)
    print(f"{len(good)} parts x {len(by_kind)} start kinds = {len(results)} sessions driven by "
          f"the teacher alone ({time.time() - started:.0f}s); did not finish correctly: "
          f"{len(failures)}")
    print("start kind                 sessions finished  mean steps  mean undos  first answer")
    table = {}
    for kind, found in by_kind.items():
        finished = sum(not r["problems"] for r in found)
        first = Counter(r["first"] for r in found)
        table[kind] = {"sessions": len(found), "finished": finished,
                       "mean_steps": round(sum(r["steps"] for r in found) / len(found), 1),
                       "mean_undos": round(sum(r["undos"] for r in found) / len(found), 1),
                       "first_answer": dict(first.most_common(4))}
        print(f"  {kind:26s} {len(found):6d} {finished:8d} {table[kind]['mean_steps']:10.1f} "
              f"{table[kind]['mean_undos']:10.1f}  {dict(first.most_common(3))}")
    for r in failures[:10]:
        print(f"  FAILED {r['kind']} {r['id']}: {r['problems'][0][:160]}")
    log_metrics(start_run("freecad-wide-prove-starts", vars(args)), final=True,
                parts=len(good), sessions=len(results), failed=len(failures), by_kind=table,
                restarts=sum(c.restarts for c in _clients))
    return 1 if failures else 0


# --- 3. strict order -----------------------------------------------------------------------------

def _same_solid(a: dict, b: dict) -> bool:
    return abs(a["volume"] - b["volume"]) <= 1e-6 * a["volume"] \
        and abs(a["area"] - b["area"]) <= 1e-6 * a["area"] and a["n_faces"] == b["n_faces"] \
        and all(abs(x - y) <= 1e-3 for x, y in zip(a["bbox"], b["bbox"], strict=True))


def swaps_of(plan_json: list[dict]) -> list[tuple[str, str]]:
    """For one plan: (which two kinds of item were swapped, what the swap gives)."""
    from forge.freecad.wide_reference import Builder, Rejected

    plan = [Step(item["kind"], dict(item["slots"])) for item in plan_json]
    try:
        final = Builder.of_plan(plan).measures[-1]
    except Rejected:
        return []
    found = []
    for at in range(1, len(plan) - 1):
        swapped = [*plan[:at], plan[at + 1], plan[at], *plan[at + 2:]]
        both = "two features" if all(step.kind in FEATURES for step in plan[at:at + 2]) \
            else "a feature and an edge treatment" \
            if any(step.kind in FEATURES for step in plan[at:at + 2]) else "two edge treatments"
        try:
            other = Builder.of_plan(swapped).measures[-1]
            found.append((both, "same solid" if _same_solid(final, other) else "another solid"))
        except Rejected:
            found.append((both, "does not build"))
    return found


def _batch(plans: list[list[dict]], path: str) -> None:
    found = [list(swap) for plan in plans for swap in swaps_of(plan)]
    with open(path, "w") as f:
        json.dump(found, f)


def _swaps_in_batches(plans: list[list[dict]], workers: int, size: int = 2,
                      seconds: float = 60.0) -> tuple[Counter, int]:
    """Count the swaps of many plans, a few plans per process. A swapped plan can crash the
    kernel or hang it; then only that small batch is lost (and counted), not the run."""
    import multiprocessing
    import tempfile

    counts: Counter = Counter()
    lost = 0
    batches = [plans[at:at + size] for at in range(0, len(plans), size)]
    with tempfile.TemporaryDirectory() as folder:
        running: list[tuple] = []
        while batches or running:
            while batches and len(running) < workers:
                batch = batches.pop()
                path = f"{folder}/{len(batches)}.json"
                process = multiprocessing.Process(target=_batch, args=(batch, path), daemon=True)
                process.start()
                running.append((process, path, time.time(), len(batch)))
            time.sleep(0.5)
            for entry in list(running):
                process, path, began, n = entry
                if process.is_alive() and time.time() - began < seconds:
                    continue
                if process.is_alive():
                    process.kill()
                process.join()
                running.remove(entry)
                try:
                    with open(path) as f:
                        counts.update(tuple(swap) for swap in json.load(f))
                except (OSError, ValueError):
                    lost += n
    return counts, lost


def orders(args: argparse.Namespace) -> int:
    from forge.freecad.parts import session_parts
    from forge.system1.splits import split_of

    run_dir = start_run("freecad-wide-prove-orders", vars(args))
    report = {}
    for source in args.sets:
        if source == "composed":        # the training parts of the composed generator
            parts = [part for part in session_parts() if split_of(part) == "train"]
            random.Random(f"{args.seed}:composed").shuffle(parts)
            plans = [[{"kind": step.kind, "slots": dict(step.slots)}
                      for step in steps_of(part["family"], part["params"])]
                     for part in parts[:args.orders]]
        else:
            plans = [part["plan"] for part in sample([source], args.orders, args.seed)]
        counts, lost = _swaps_in_batches(plans, args.workers)
        if lost:
            print(f"  {lost} plans were lost: the kernel crashed or did not return on a swapped plan")
        total = sum(counts.values())
        print(f"{source}: {len(plans)} plans, {total} swaps of two neighbouring plan items",
              flush=True)
        report[source] = {"plans": len(plans), "swaps": total, "by_pair": {}}
        for both in ("two features", "a feature and an edge treatment", "two edge treatments"):
            row = {what: counts[(both, what)]
                   for what in ("same solid", "another solid", "does not build")}
            n = sum(row.values())
            if n:
                print(f"  {both:32s} {n:6d}: same solid {row['same solid']} "
                      f"({row['same solid'] / n:.1%}), another solid {row['another solid']}, "
                      f"does not build {row['does not build']}")
            report[source]["by_pair"][both] = row
        same = sum(value for (_, what), value in counts.items() if what == "same solid")
        report[source]["same_solid_share"] = round(same / max(total, 1), 4)
        print(f"  all swaps: the swapped order gives the SAME solid in {same} of {total} "
              f"({same / max(total, 1):.1%}); there the strict-order teacher says `undo` "
              f"although the build is geometrically right")
    (run_dir / "orders.json").write_text(json.dumps(report, indent=1) + "\n")
    log_metrics(run_dir, final=True, **{f"{name}_same_share": value["same_solid_share"]
                                        for name, value in report.items()})
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--recipes", type=int, default=0, help="parts per set")
    parser.add_argument("--starts", type=int, default=0, help="parts (each gets every start kind)")
    parser.add_argument("--orders", type=int, default=0, help="plans per source")
    parser.add_argument("--sets", nargs="*", default=["wide_iid"],
                        choices=[*SETS, "composed"])
    parser.add_argument("--accepted", action="store_true",
                        help="--recipes: only parts that have a recorded session")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    code = 0
    if args.recipes:
        code |= recipes(args)
    if args.starts:
        code |= starts(args)
    if args.orders:
        code |= orders(args)
    for c in _clients:
        c.close()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
