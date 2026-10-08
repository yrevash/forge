"""Proof that the interface recipes are right: built through FreeCAD's interface, a part is the stored solid.

For a sample of verified composed parts, every step is built in a hidden FreeCAD
window through its interface recipe, one interface action at a time, and compared
exactly as forge/freecad/prove.py compares command builds:

  - after every step, with the reference engine's arithmetic for the part so far;
  - at the end, with the stored kernel measurement of the part
    (bounding box within 0.001 mm, volume within one part in a million, one valid solid);
  - probe points, to show every feature is in the right place.

Inside every any-order group the order is drawn at random (seeded by the part id).

The run can be stopped and started again: every finished part is one line of
`--out`, and parts already there are skipped.

Run:    uv run python -m forge.freecad_ui.prove [--per-base 150] [--long-per-base 25]
            [--workers 1] [--max-minutes 15]
Exit code 0 only if no part differs.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from forge.freecad.parts import sample_rows
from forge.freecad_ui.build import against_stored, build_part
from forge.freecad_ui.client import UIClient
from forge.runs import PROJECT_ROOT, log_metrics, start_run

DEFAULT_OUT = PROJECT_ROOT / "data" / "freecad_ui" / "prove" / "results.jsonl"

_local = threading.local()
_clients: list[UIClient] = []
_write = threading.Lock()


def client() -> UIClient:
    """This thread's own hidden FreeCAD (a client must not be shared between threads)."""
    if not hasattr(_local, "client"):
        _local.client = UIClient()
        _local.client.start()
        _clients.append(_local.client)
    return _local.client


def prove_one(row: dict) -> dict:
    """Build one part through the interface; report its problems and what it exercised."""
    started = time.time()
    ui = client()
    result = build_part(ui, row["family"], row["params"], random.Random(row["id"]))
    problems = list(result.problems)
    if result.ok:
        problems += against_stored(result.solid, row["measured"])
    by_kind: Counter = Counter()
    for kind, _ in result.action_ms:
        by_kind[kind] += 1
    return {"id": row["id"], "family": row["family"], "long": row["long"], "problems": problems,
            "steps": result.steps, "actions": result.actions,
            "semantic_actions": result.semantic_actions, "action_kinds": dict(by_kind),
            "in_app_ms": round(sum(ms for _, ms in result.action_ms), 1),
            "seconds": round(time.time() - started, 2), "pid": ui.pid}


def summarize(results: list[dict]) -> dict:
    """The numbers of a run, from its result lines."""
    built: Counter = Counter()
    matched: Counter = Counter()
    in_failed: Counter = Counter()
    by_file: Counter = Counter()
    kinds: Counter = Counter()
    for r in results:
        by_file[f"{r['family']} {'long' if r['long'] else '1-5'} "
                f"{'same solid' if not r['problems'] else 'DIFFERENT'}"] += 1
        for kind, ok in r["steps"]:
            built[kind] += 1
            matched[kind] += ok
            in_failed[kind] += bool(r["problems"])
        kinds.update(r["action_kinds"])
    return {"parts": len(results), "long_parts": sum(r["long"] for r in results),
            "failures": sum(bool(r["problems"]) for r in results),
            "steps": sum(built.values()), "actions": sum(r["actions"] for r in results),
            "semantic_actions": sum(r["semantic_actions"] for r in results),
            "action_kinds": dict(kinds), "built": dict(built), "matched": dict(matched),
            "in_failed": dict(in_failed), "by_file": dict(sorted(by_file.items())),
            "failed_ids": [r["id"] for r in results if r["problems"]]}


def print_summary(summary: dict, results: list[dict]) -> None:
    print(f"{summary['parts']} parts ({summary['long_parts']} long), {summary['steps']} steps, "
          f"{summary['actions']} interface actions "
          f"({summary['semantic_actions']} semantic picks, "
          f"{summary['actions'] - summary['semantic_actions']} on real widgets)")
    print(f"parts that differ from the stored solid: {summary['failures']}")
    for name, count in summary["by_file"].items():
        print(f"  {name:45s} {count}")
    print("step kind         built  matched  in a failed part")
    for kind, count in summary["built"].items():
        print(f"  {kind:15s} {count:6d} {summary['matched'][kind]:8d} {summary['in_failed'][kind]:8d}")
    print("actions by element kind:", summary["action_kinds"])
    for r in [r for r in results if r["problems"]][:20]:
        print(f"  {r['id']} {r['family']}: {r['problems'][:3]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-base", type=int, default=150)
    parser.add_argument("--long-per-base", type=int, default=25)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-minutes", type=float, default=None,
                        help="start no new part after this many minutes (run again to go on)")
    parser.add_argument("--summary-only", action="store_true",
                        help="print the numbers of the results already in --out and stop")
    args = parser.parse_args()

    done: list[dict] = []
    if args.out.exists():
        done = [json.loads(line) for line in args.out.read_text().splitlines() if line.strip()]
    if args.summary_only:
        print_summary(summarize(done), done)
        raise SystemExit(1 if any(r["problems"] for r in done) else 0)

    run_dir = start_run("freecad-ui-prove", {**vars(args), "out": str(args.out)})
    rows = sample_rows(args.per_base, args.long_per_base, args.seed)
    have = {r["id"] for r in done}
    todo = [row for row in rows if row["id"] not in have]
    print(f"{len(rows)} parts in the sample, {len(have & {r['id'] for r in rows})} already done, "
          f"{len(todo)} to build", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    deadline = None if args.max_minutes is None else time.time() + 60 * args.max_minutes

    def work(row: dict) -> dict | None:
        if deadline is not None and time.time() > deadline:
            return None                 # out of time: left for the next run
        result = prove_one(row)
        with _write, args.out.open("a") as f:
            f.write(json.dumps(result) + "\n")
        return result

    started = time.time()
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            fresh = [result for result in pool.map(work, todo) if result is not None]
    finally:
        restarts = sum(c.restarts for c in _clients)
        planned = sum(c.planned_restarts for c in _clients)
        failures = Counter(kind for c in _clients for kind in c.failures)
        for c in _clients:
            c.close()
    seconds = time.time() - started

    wanted = {row["id"] for row in rows}
    results = [r for r in done if r["id"] in wanted] + fresh
    summary = summarize(results)
    print_summary(summary, results)
    if fresh:
        actions = sum(r["actions"] for r in fresh)
        print(f"this run: {len(fresh)} parts, {actions} actions in {seconds:.0f}s with "
              f"{args.workers} instance(s): {3600 * len(fresh) / seconds:.0f} parts/hour, "
              f"{actions / seconds:.1f} actions/s")
    left = len(rows) - len(results)
    if left:
        print(f"{left} parts of the sample are not built yet: run the same command again")
    print(f"instance restarts after a crash or hang: {restarts} {dict(failures)}; "
          f"planned restarts: {planned}")
    log_metrics(run_dir, final=True, **summary, seconds=round(seconds, 1), fresh=len(fresh),
                restarts=restarts, planned_restarts=planned, instance_failures=dict(failures))
    raise SystemExit(1 if summary["failures"] or left else 0)


if __name__ == "__main__":
    main()
