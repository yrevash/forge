"""Proof that following the teacher builds the part: any member of its set, at every step.

For a sample of verified composed parts, a clean FreeCAD session is driven by
the teacher alone. At every step ONE member of the teacher's set is picked at
random and carried out. The proof passes only if, for every part:

  - every command the teacher offers is in the runtime's valid list;
  - no command is refused;
  - the session takes exactly as many steps as the plan's recipes have commands;
  - the finished solid is the stored one (bounding box within 0.001 mm, volume within
    one part in a million, one valid solid), and probe points find every feature in place;
  - the teacher itself finds the finished document complete, on plan and clean.

Run:    uv run python -m forge.freecad.teacher_proof [--per-base 500] [--long-per-base 150]
                                                    [--workers 4]
Exit code 0 only if no part fails.
"""

from __future__ import annotations

import argparse
import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from forge.freecad.lean import lean
from forge.freecad.parts import sample_rows
from forge.freecad.play import end_check
from forge.freecad.probes import failed, probes_of
from forge.freecad.prove import _clients, client
from forge.freecad.teacher import clean_length, script_of, teacher
from forge.runs import log_metrics, start_run
from forge.system1.steps import steps_of


def drive_one(row: dict) -> dict:
    """Drive one part from an empty FreeCAD with random picks from the teacher's set."""
    fc = client()
    rng = random.Random(f"teacher-proof:{row['id']}")
    plan = steps_of(row["family"], row["params"])
    script, length = script_of(plan), clean_length(plan)
    problems: list[str] = []
    stats: Counter = Counter()
    reply = fc.reset()
    steps = 0
    while steps <= 2 * length:
        advice = teacher(plan, lean(reply["snapshot"]), script)
        if not advice.targets:
            break
        not_valid = [name for name in advice.names() if name not in reply["valid"]]
        if not advice.on_plan or not_valid:
            problems.append(f"step {steps}: on_plan={advice.on_plan}, not valid: {not_valid}, "
                            f"{advice.why}")
            break
        stats[f"choices:{len(advice.targets)}"] += 1
        target = rng.choice(advice.targets)
        reply = fc.command(target.command, **target.args)
        steps += 1
        stats[f"command:{target.command}"] += 1
        if reply["status"] != "ok":
            problems.append(f"step {steps}: {target.command} {target.args} -> {reply['status']} "
                            f"{reply.get('reason')}")
            break
    if not problems:
        if steps != length:
            problems.append(f"{steps} steps, the plan's recipes have {length}")
        problems += end_check(plan, reply["snapshot"], row["measured"])
        probes = probes_of(plan)
        if probes:
            problems += failed(probes, fc.probe([point for point, _, _ in probes]))
    return {"id": row["id"], "family": row["family"], "long": row["long"], "items": len(plan),
            "steps": steps, "problems": problems, "stats": stats}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-base", type=int, default=500)
    parser.add_argument("--long-per-base", type=int, default=150)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    run_dir = start_run("freecad-teacher-proof", vars(args))

    rows = sample_rows(args.per_base, args.long_per_base, args.seed)
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(drive_one, rows))
    seconds = time.time() - started

    failures = [r for r in results if r["problems"]]
    stats: Counter = Counter()
    by_file: Counter = Counter()
    for r in results:
        stats.update(r["stats"])
        by_file[(r["family"], "long" if r["long"] else "1-5", not r["problems"])] += 1
    steps = sum(r["steps"] for r in results)
    print(f"{len(results)} parts ({sum(r['long'] for r in results)} long, up to "
          f"{max(r['items'] for r in results)} plan items), {steps} commands, {seconds:.1f}s "
          f"with {args.workers} workers")
    print(f"parts that failed: {len(failures)}")
    for (family, length, ok), count in sorted(by_file.items()):
        print(f"  {family:18s} {length:5s} {'built, same solid' if ok else 'FAILED'} {count}")
    print("steps by number of acceptable commands: "
          + ", ".join(f"{key.split(':')[1]}: {count}" for key, count in sorted(stats.items())
                      if key.startswith("choices:")))
    for r in failures[:20]:
        print(f"  {r['id']} {r['family']}: {r['problems'][:3]}")
    restarts = sum(c.restarts for c in _clients)
    print(f"worker restarts: {restarts}")
    log_metrics(run_dir, final=True, parts=len(results), failures=len(failures), steps=steps,
                seconds=round(seconds, 1), restarts=restarts, stats=dict(stats),
                failed_ids=[r["id"] for r in failures])
    for c in _clients:
        c.close()
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
