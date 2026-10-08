"""Speed, crash recovery and memory of the FreeCAD runtime.

Three measurements, each printed with what was run:

  SPEED     the same parts built with 1 worker and with 3: parts per second and
            commands per second (normal parts and long parts separately).
  RECOVERY  while parts are being built, the worker is killed, or made to hang,
            at a random command. The client must notice, start a new worker,
            replay the session and finish the part; the part must still match
            the stored measurement.
  MEMORY    one worker builds many parts in a row; its memory is read from the
            operating system every 100 parts.

Run:    uv run python -m forge.freecad.bench [--parts 400] [--long-parts 60] [--kills 40]
                                             [--memory-parts 2000]
"""

from __future__ import annotations

import argparse
import random
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from forge.freecad.build import against_stored, build_part
from forge.freecad.client import FreeCADClient
from forge.freecad.parts import sample_rows
from forge.runs import log_metrics, start_run

HANG_TIMEOUT = 3.0      # seconds the client waits before it calls a silent worker hung


def _build_all(rows: list[dict]) -> tuple[int, int]:
    """Build rows with one fresh worker. Returns (commands issued, parts that differ)."""
    commands = differ = 0
    with FreeCADClient() as fc:
        for row in rows:
            result = build_part(fc, row["family"], row["params"])
            commands += result.commands
            differ += bool(result.problems
                           or against_stored(result.snapshot["solid"], row["measured"]))
    return commands, differ


def speed(rows: list[dict], workers: int) -> dict:
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        done = list(pool.map(_build_all, [rows[i::workers] for i in range(workers)]))
    seconds = time.time() - started     # includes starting the workers (about 0.4 s each)
    commands = sum(c for c, _ in done)
    return {"workers": workers, "parts": len(rows), "commands": commands,
            "seconds": round(seconds, 1), "parts_per_s": round(len(rows) / seconds, 2),
            "commands_per_s": round(commands / seconds, 1), "differ": sum(d for _, d in done)}


def recovery(rows: list[dict], seed: int) -> dict:
    """Break the worker once per part, at a random command, and see the part still built."""
    rng = random.Random(seed)
    out = {"parts": len(rows), "crashes_injected": 0, "hangs_injected": 0, "differ": 0}
    slowest = 0.0
    with FreeCADClient() as fc:
        for number, row in enumerate(rows):
            how = "hang" if number % 4 == 3 else "crash"
            strike_at = rng.randint(2, 12)      # every part has more than 12 commands
            seen = 0

            def strike(step, command, reply, how=how, strike_at=strike_at) -> None:
                nonlocal seen
                seen += 1
                if seen == strike_at:
                    fc.timeout = HANG_TIMEOUT if how == "hang" else fc.timeout
                    fc.break_worker(how)        # the NEXT command will find it broken

            started = time.time()
            result = build_part(fc, row["family"], row["params"], on_command=strike)
            slowest = max(slowest, time.time() - started)
            fc.timeout = 30.0
            out[f"{'crashes' if how == 'crash' else 'hangs'}_injected"] += 1
            out["differ"] += bool(result.problems
                                  or against_stored(result.snapshot["solid"], row["measured"]))
        out.update(restarts=fc.restarts, crashes_seen=fc.failures.count("crash"),
                   timeouts_seen=fc.failures.count("timeout"),
                   slowest_part_seconds=round(slowest, 2))
    return out


def _rss_mb(pid: int) -> float:
    """Memory the process holds right now, as the operating system reports it."""
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True,
                         check=True)
    return int(out.stdout) / 1024       # ps prints kilobytes


def memory(rows: list[dict], every: int = 100) -> dict:
    readings = []
    with FreeCADClient(recycle_after=None) as fc:    # no planned swaps: measure the raw growth
        for number, row in enumerate(rows, start=1):
            build_part(fc, row["family"], row["params"])
            if number % every == 0 or number == 1:
                readings.append((number, round(_rss_mb(fc.pid), 1)))
        restarts = fc.restarts
    (first_n, first), (last_n, last) = readings[1], readings[-1]
    return {"parts": len(rows), "readings_mb": readings, "restarts": restarts,
            "growth_mb_per_1000_parts": round((last - first) / (last_n - first_n) * 1000, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--parts", type=int, default=400)
    parser.add_argument("--long-parts", type=int, default=60)
    parser.add_argument("--kills", type=int, default=40)
    parser.add_argument("--memory-parts", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=2)
    args = parser.parse_args()
    run_dir = start_run("freecad-bench", vars(args))

    normal = sample_rows(args.parts // 4, 0, args.seed)
    long = sample_rows(0, args.long_parts // 4, args.seed)
    for name, rows in (("normal (1-5 features)", normal), ("long (6-12 features)", long)):
        for workers in (1, 3):
            result = speed(rows, workers)
            print(f"speed, {name}: {result}")
            log_metrics(run_dir, test="speed", kind=name, **result)

    result = recovery(sample_rows(args.kills // 4, 0, args.seed + 1), args.seed)
    print(f"recovery: {result}")
    log_metrics(run_dir, test="recovery", **result)

    result = memory(sample_rows(args.memory_parts // 4, 0, args.seed + 2))
    print(f"memory: {result}")
    log_metrics(run_dir, test="memory", final=True, **result)


if __name__ == "__main__":
    main()
