"""Play far shards again in THIS machine's FreeCAD: the audits' own replay, on every session.

The real test that a shard recorded elsewhere is one this machine stands behind. Each
recorder's audit has a `replay_shard` that re-issues every executed command of a session in
a fresh FreeCAD and requires the recorded snapshot, valid commands and reply at every step,
then the stored solid at the end. The audit commands sample from whole slices under data/;
this file points the same function at a folder of far shards and replays ALL of them, with
the parts taken from the task file that planned the shards.

    uv run python -m forge.remote.replay --recorder single --far <folder>/single \
        --tasks <folder>/tasks_single.json --workers 3

Starts FreeCAD (one per worker). Exit code 0 only if every session replays.
Not run yet when this was written (8 Oct 2026): the Mac was busy recording.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from forge.runs import PROJECT_ROOT


def _ids(recorder: str, path: Path) -> list[str]:
    if recorder == "multi":
        from forge.freecad_multi.shards import read_sessions
    else:
        from forge.freecad.shards import read_sessions
    return [header["session"] for header, _, _ in read_sessions(path)]


def _replay(job: tuple[str, str, str | None, dict | None]) -> dict:
    """One shard, in a worker process. `parts` (id -> part) for the part recorders."""
    recorder, path, plans_file, parts = job
    wanted = _ids(recorder, Path(path))
    if recorder == "multi":
        from forge.freecad_multi import audit
        return audit.replay_shard((path, plans_file, wanted)) | {"file": path}
    if recorder == "wide":
        from forge.freecad import wide_audit as audit
    else:
        from forge.freecad import audit
    audit._load_parts(parts)
    return audit.replay_shard((path, wanted)) | {"file": path}


def jobs(recorder: str, far: Path, tasks: dict) -> list[tuple]:
    by_shard = {(task[0], task[1]): task for task in tasks["tasks"]}
    found = []
    for path in sorted(far.glob("*/shard*.jsonl.gz")):
        task = by_shard[(path.parent.name, int(path.name[len("shard"):].split(".")[0]))]
        if recorder == "multi":
            found.append((recorder, str(path), str(PROJECT_ROOT / task[2]), None))
        else:
            found.append((recorder, str(path), None, {part["id"]: part for part in task[2]}))
    return found


def replay(recorder: str, far: Path, tasks: dict, workers: int) -> dict:
    counts: Counter = Counter()
    bad = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(_replay, jobs(recorder, far, tasks)):
            counts["shards"] += 1
            counts["sessions"] += len(_ids(recorder, Path(result["file"])))
            counts["steps"] += result["steps"]
            counts["worker_restarts"] += result["restarts"]
            counts["sessions_not_reproduced"] += len(result["bad"])
            bad += result["bad"]
    reasons = Counter(line.split(": ", 1)[1].split(":")[-1].strip()[:60] for line in bad)
    return {"recorder": recorder, **dict(counts), "reasons": dict(reasons.most_common(8)),
            "examples": bad[:20]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--recorder", required=True, choices=("single", "wide", "multi"))
    parser.add_argument("--far", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    report = replay(args.recorder, Path(args.far), json.loads(Path(args.tasks).read_text()),
                    args.workers)
    print(json.dumps(report, indent=1))
    raise SystemExit(1 if report.get("sessions_not_reproduced") else 0)


if __name__ == "__main__":
    main()
