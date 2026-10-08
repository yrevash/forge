"""Record shards from a task file, on any machine: the three recorders' own `write_shard`.

The recorders' commands (forge.freecad.sessions and friends) plan their shards from the
whole of data/, which is hundreds of megabytes and stays on the Mac. So the Mac plans
(forge/remote/bundle.py writes a task file: for each shard, exactly the parts or plans the
Mac would record), and the far machine only plays. The function that plays a shard is the
recorder's own, unchanged, so a shard made here is the shard the Mac would have made.

    python -m forge.remote.record --tasks tasks_wide.json --out out/wide --workers 4
                                  [--max-minutes 600] [--only 0 1 2]

Resumable by shard: a shard whose `.stats.json` exists in --out is skipped (the recorders
write that file last). `--max-minutes` starts no new shard after that time, so a kernel
with a time limit ends cleanly and keeps what it finished.

The task file: {"recorder": "single" | "wide" | "multi", "code_hash": "...", "tasks": [...]}.
A task is the recorder's task tuple without its output folder. If this machine's copy of
the code does not hash to `code_hash`, nothing is recorded: the shards would claim to be
something they are not.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from forge.runs import PROJECT_ROOT

# recorder -> (its module, digits in a shard's file name)
RECORDERS = {"single": ("forge.freecad.sessions", 3),
             "wide": ("forge.freecad.wide_sessions", 3),
             "multi": ("forge.freecad_multi.sessions", 4)}


def full_task(recorder: str, task: list, out_dir: Path) -> tuple:
    """The tuple the recorder's `write_shard` takes: the stored task plus where to write."""
    if recorder == "single":
        name, number, parts = task
        return name, number, parts, str(out_dir)
    if recorder == "wide":
        name, number, parts, others = task
        return name, number, parts, others, str(out_dir)
    name, number, plans_file, chosen = task
    return name, number, str(PROJECT_ROOT / plans_file), str(out_dir), chosen


def stats_file(recorder: str, task: list, out_dir: Path) -> Path:
    return out_dir / task[0] / f"shard{task[1]:0{RECORDERS[recorder][1]}d}.stats.json"


def _prepare(recorder: str) -> None:
    """Runs once in every worker process. On Linux the structure recorder's kernel judge
    gets the CadQuery helper without the macOS sandbox (forge/remote/linux.py)."""
    if recorder == "multi" and sys.platform == "linux":
        from forge.freecad_multi import sessions
        from forge.remote.linux import LinuxSandbox
        from forge.resolve.judge import JUDGE_TIMEOUT, KernelJudge
        sessions._judge = KernelJudge(LinuxSandbox(timeout=JUDGE_TIMEOUT))


def record(tasks_file: Path, out_dir: Path, workers: int, max_minutes: float | None = None,
           only: list[int] | None = None) -> dict:
    """Record the shards of one task file that are not in `out_dir` yet. Returns the counts."""
    stored = json.loads(tasks_file.read_text())
    recorder = stored["recorder"]
    module = importlib.import_module(RECORDERS[recorder][0])
    if module.code_hash() != stored["code_hash"]:
        raise RuntimeError(f"this copy of the {recorder} recorder hashes to "
                           f"{module.code_hash()}, the tasks were planned for "
                           f"{stored['code_hash']}")
    tasks = [task for at, task in enumerate(stored["tasks"]) if only is None or at in only]
    todo = [task for task in tasks if not stats_file(recorder, task, out_dir).exists()]
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    shards = []
    with ProcessPoolExecutor(max_workers=workers, initializer=_prepare,
                             initargs=(recorder,)) as pool:
        waiting, running = iter(todo), set()
        while True:
            late = max_minutes is not None and time.time() - started > max_minutes * 60
            while len(running) < workers and not late:
                task = next(waiting, None)
                if task is None:
                    break
                running.add(pool.submit(module.write_shard, full_task(recorder, task, out_dir)))
            if not running:
                break
            finished, running = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                shard = future.result()
                shards.append(shard)
                print(f"[{(time.time() - started) / 60:6.1f} min] {shard['file']} "
                      f"{shard['counts'].get('sessions', 0)} sessions in "
                      f"{shard['seconds']:.0f}s", flush=True)
    seconds = time.time() - started
    sessions = sum(shard["counts"].get("sessions", 0) for shard in shards)
    steps = sum(shard["counts"].get("steps", 0) for shard in shards)
    left = sum(1 for task in tasks if not stats_file(recorder, task, out_dir).exists())
    return {"recorder": recorder, "workers": workers, "shards": len(shards),
            "shards_left": left, "sessions": sessions, "steps": steps,
            "seconds": round(seconds, 1),
            "sessions_per_second": round(sessions / max(seconds, 1e-9), 3),
            "seconds_in_shards": round(sum(shard["seconds"] for shard in shards), 1),
            "ended": {key[4:]: sum(shard["counts"].get(key, 0) for shard in shards)
                      for shard in shards for key in shard["counts"] if key.startswith("end:")},
            "worker_restarts": sum(shard["counts"].get("worker_restarts", 0)
                                   for shard in shards)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument("--only", type=int, nargs="*", default=None,
                        help="positions in the task file (default: every task)")
    args = parser.parse_args()
    print(json.dumps(record(Path(args.tasks), Path(args.out), args.workers, args.max_minutes,
                            args.only), indent=1))


if __name__ == "__main__":
    main()
