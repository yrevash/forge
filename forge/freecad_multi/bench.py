"""Speed, crash recovery and memory of the FreeCAD runtime when it builds structures.

Three measurements, each printed with what was run:

  SPEED     the same structures built with 1 worker and with 2: structures, parts and
            commands per second. A full snapshot (every body, the whole structure, who
            touches whom) is taken and sent after every command, as a model would need.
  RECOVERY  while a structure is being built the worker is killed (os._exit, so no macOS
            crash dialog) or made to hang, at a random command. The client must notice,
            start a new worker, replay the session and finish; the structure must still
            match the resolver.
  MEMORY    one worker builds many structures in a row, never swapped; its memory is
            read from the operating system every 25 structures.

The structures are rows of data/structures (furniture, 12 to 40 parts each), the same
number from every kind.

Run:    uv run python -m forge.freecad_multi.bench [--per-kind 12] [--kills 24] [--memory-per-kind 40]
"""

from __future__ import annotations

import argparse
import random
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from forge.freecad_multi import sources
from forge.freecad_multi.build import build_structure
from forge.freecad_multi.client import MultiClient
from forge.resolve.bodies import Body
from forge.runs import log_metrics, start_run

HANG_TIMEOUT = 3.0      # seconds the client waits before it calls a silent worker hung


def structures(per_kind: int, skip: int = 0) -> list[list[Body]]:
    """`per_kind` rows of every kind, kinds interleaved; `skip` rows are passed over first."""
    by_kind = [[sources.row_bodies(row) for row in list(sources.structure_rows(
        kind, skip + per_kind))[skip:]] for kind in sources.structure_kinds()]
    return [bodies for group in zip(*by_kind) for bodies in group]


def _build_all(batch: list[list[Body]]) -> tuple[int, int, float]:
    """Build with one fresh worker: (commands, structures that differ, seconds inside FreeCAD)."""
    commands = differ = 0
    inside = 0.0
    with MultiClient() as fc:
        for bodies in batch:
            result = build_structure(fc, bodies)
            commands += result.commands
            differ += not result.ok
            inside += result.seconds_in_freecad
    return commands, differ, inside


def speed(batch: list[list[Body]], workers: int) -> dict:
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        done = list(pool.map(_build_all, [batch[i::workers] for i in range(workers)]))
    seconds = time.time() - started     # includes starting the workers (about 0.4 s each)
    commands, parts = sum(c for c, _, _ in done), sum(len(bodies) for bodies in batch)
    return {"workers": workers, "structures": len(batch), "parts": parts, "commands": commands,
            "seconds": round(seconds, 1),
            "structures_per_s": round(len(batch) / seconds, 2),
            "parts_per_s": round(parts / seconds, 1),
            "commands_per_s": round(commands / seconds, 1),
            "share_of_time_inside_freecad": round(sum(i for _, _, i in done) / seconds / workers,
                                                  2),
            "differ": sum(d for _, d, _ in done)}


def recovery(batch: list[list[Body]], seed: int) -> dict:
    """Break the worker once per structure, at a random command; the structure must still build."""
    rng = random.Random(seed)
    out = {"structures": len(batch), "crashes_injected": 0, "hangs_injected": 0, "differ": 0}
    slowest = 0.0
    with MultiClient() as fc:
        for number, bodies in enumerate(batch):
            how = "hang" if number % 4 == 3 else "crash"
            strike_at = rng.randint(5, 100)     # every structure here has more than 100 commands
            seen = 0

            def strike(index, command, reply, how=how, strike_at=strike_at) -> None:
                nonlocal seen
                seen += 1
                if seen == strike_at:
                    fc.timeout = HANG_TIMEOUT if how == "hang" else fc.timeout
                    fc.break_worker(how)        # the NEXT command will find it broken

            started = time.time()
            result = build_structure(fc, bodies, on_command=strike)
            slowest = max(slowest, time.time() - started)
            fc.timeout = 30.0
            out[f"{'crashes' if how == 'crash' else 'hangs'}_injected"] += 1
            out["differ"] += not result.ok
        out.update(restarts=fc.restarts, crashes_seen=fc.failures.count("crash"),
                   timeouts_seen=fc.failures.count("timeout"),
                   slowest_structure_seconds=round(slowest, 2))
    return out


def _rss_mb(pid: int) -> float:
    """Memory the process holds right now, as the operating system reports it."""
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True,
                         check=True)
    return int(out.stdout) / 1024       # ps prints kilobytes


def memory(batch: list[list[Body]], every: int = 25) -> dict:
    readings = []
    parts = 0
    with MultiClient(recycle_after=None) as fc:     # no planned swaps: measure the raw growth
        for number, bodies in enumerate(batch, start=1):
            build_structure(fc, bodies)
            parts += len(bodies)
            if number % every == 0 or number == 1:
                readings.append((number, parts, round(_rss_mb(fc.pid), 1)))
        restarts = fc.restarts
    (first_n, first_parts, first), (last_n, last_parts, last) = readings[1], readings[-1]
    return {"structures": len(batch), "parts": parts, "readings_mb": readings,
            "restarts": restarts,
            "growth_mb_per_1000_structures": round((last - first) / (last_n - first_n) * 1000, 1),
            "growth_mb_per_1000_parts": round((last - first) / (last_parts - first_parts) * 1000,
                                              1)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-kind", type=int, default=12, help="structures per kind for speed")
    parser.add_argument("--kills", type=int, default=24, help="structures broken on purpose")
    parser.add_argument("--memory-per-kind", type=int, default=40)
    parser.add_argument("--seed", type=int, default=2)
    args = parser.parse_args()
    run_dir = start_run("freecad-multi-bench", vars(args))

    batch = structures(args.per_kind, skip=300)     # rows the proof did not use
    for workers in (1, 2):
        result = speed(batch, workers)
        print(f"speed: {result}")
        log_metrics(run_dir, test="speed", **result)

    result = recovery(structures(args.kills // 8, skip=320), args.seed)
    print(f"recovery: {result}")
    log_metrics(run_dir, test="recovery", **result)

    result = memory(structures(args.memory_per_kind, skip=340))
    print(f"memory: {result}")
    log_metrics(run_dir, test="memory", final=True, **result)


if __name__ == "__main__":
    main()
