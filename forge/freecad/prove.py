"""Proof that the recipes are right: FreeCAD builds the same solid as our reference engine.

For a sample of verified composed parts, every step is built in a headless
FreeCAD through its recipe, one command at a time, and the result is compared:

  - after every step, with the reference engine's arithmetic for the part so far;
  - at the end, with the stored kernel measurement of the part
    (bounding box within 0.001 mm, volume within one part in a million, one valid solid);
  - probe points, to show every feature is in the right place.

The dimensions of each sketch shape are issued in a random order (seeded by the
part id), so the any-order groups of the recipes are exercised too.

Run:    uv run python -m forge.freecad.prove [--per-base 500] [--long-per-base 150] [--workers 3]
Exit code 0 only if no part differs.
"""

from __future__ import annotations

import argparse
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from forge.freecad.build import against_stored, build_part
from forge.freecad.client import FreeCADClient
from forge.freecad.parts import sample_rows
from forge.runs import log_metrics, start_run

_local = threading.local()
_clients: list[FreeCADClient] = []


def client() -> FreeCADClient:
    """This thread's own FreeCAD worker (a client must not be shared between threads)."""
    if not hasattr(_local, "client"):
        _local.client = FreeCADClient()
        _local.client.start()
        _clients.append(_local.client)
    return _local.client


def prove_one(row: dict) -> dict:
    """Build one part; report its problems and which step kinds it exercised."""
    result = build_part(client(), row["family"], row["params"], random.Random(row["id"]))
    problems = list(result.problems)
    if result.ok:
        problems += against_stored(result.snapshot["solid"], row["measured"])
    return {"id": row["id"], "family": row["family"], "long": row["long"], "problems": problems,
            "steps": result.steps, "commands": result.commands}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-base", type=int, default=500)
    parser.add_argument("--long-per-base", type=int, default=150)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    run_dir = start_run("freecad-prove", vars(args))

    rows = sample_rows(args.per_base, args.long_per_base, args.seed)
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(prove_one, rows))
    seconds = time.time() - started

    built: Counter = Counter()      # step kind -> how many were built
    matched: Counter = Counter()    # step kind -> how many matched the reference
    in_failed: Counter = Counter()  # step kind -> how many sat in a part that failed
    by_file: Counter = Counter()
    failures = [r for r in results if r["problems"]]
    for r in results:
        by_file[(r["family"], "long" if r["long"] else "1-5", not r["problems"])] += 1
        for kind, ok in r["steps"]:
            built[kind] += 1
            matched[kind] += ok
            in_failed[kind] += bool(r["problems"])

    commands = sum(r["commands"] for r in results)
    print(f"{len(results)} parts, {sum(built.values())} steps, {commands} commands, "
          f"{seconds:.1f}s with {args.workers} workers "
          f"({len(results) / seconds:.1f} parts/s, {commands / seconds:.0f} commands/s)")
    print(f"parts that differ from the reference: {len(failures)}")
    for (family, length, ok), count in sorted(by_file.items()):
        print(f"  {family:18s} {length:5s} {'same solid' if ok else 'DIFFERENT'} {count}")
    print("step kind         built  matched  in a failed part")
    for kind, count in built.items():
        print(f"  {kind:15s} {count:6d} {matched[kind]:8d} {in_failed[kind]:8d}")
    for r in failures[:20]:
        print(f"  {r['id']} {r['family']}: {r['problems'][:3]}")
    restarts = sum(c.restarts for c in _clients)
    worker_failures = Counter(kind for c in _clients for kind in c.failures)
    print(f"worker restarts: {restarts} {dict(worker_failures)}")

    log_metrics(run_dir, final=True, parts=len(results), long_parts=sum(r["long"] for r in results),
                failures=len(failures), commands=commands, seconds=round(seconds, 1),
                built=dict(built), matched=dict(matched), restarts=restarts,
                worker_failures=dict(worker_failures),
                failed_ids=[r["id"] for r in failures])
    for c in _clients:
        c.close()
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
