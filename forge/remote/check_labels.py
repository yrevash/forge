"""The audits' plain-Python checks on far shards: teacher, oracle, splits, endings.

The recorders' audit commands expect whole slices under data/. A probe holds a few cut
shards somewhere else, so this file runs the audits' own `audit_session` on every session
of a folder of far shards, with the parts taken from the task file that planned them (for
structures: from the selection files on this Mac). No FreeCAD is started; the structure
audit starts the CadQuery helper for plans whose resolution needs the kernel.

    uv run python -m forge.remote.check_labels --recorder wide --far <folder>/wide \
        --tasks <folder>/tasks_wide.json

Check 3 of the audits (replay in FreeCAD) is NOT here: that is the recorders' audit on the
Mac, see README.md.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from forge.runs import PROJECT_ROOT


def _single(far: Path, tasks: dict):
    from forge.freecad.audit import audit_session
    from forge.freecad.shards import read_sessions
    parts = {part["id"]: part for task in tasks["tasks"] for part in task[2]}
    for path in sorted(far.glob("*/shard*.jsonl.gz")):
        for header, records, end in read_sessions(path):
            yield header, records, end, audit_session(header, records, end,
                                                      parts[header["part_id"]])


def _wide(far: Path, tasks: dict):
    from forge.freecad.shards import read_sessions
    from forge.freecad.wide_audit import audit_session
    parts = {part["id"]: part for task in tasks["tasks"] for part in task[2]}
    for path in sorted(far.glob("*/shard*.jsonl.gz")):
        for header, records, end in read_sessions(path):
            yield header, records, end, audit_session(header, records, end,
                                                      parts[header["part_id"]])


def _multi(far: Path, tasks: dict):
    from forge.freecad_multi.audit import audit_session, units_of
    from forge.freecad_multi.shards import read_sessions
    plans = {(task[0], task[1]): PROJECT_ROOT / task[2] for task in tasks["tasks"]}
    for path in sorted(far.glob("*/shard*.jsonl.gz")):
        number = int(path.name[len("shard"):].split(".")[0])
        units = units_of(plans[(path.parent.name, number)])
        for header, records, end in read_sessions(path):
            unit, row = units[header["plan_id"]]
            yield header, records, end, audit_session(header, records, end, unit, row)


def check(recorder: str, far: Path, tasks: dict) -> dict:
    counts: Counter = Counter()
    examples = []
    for header, records, end, problems in {"single": _single, "wide": _wide,
                                           "multi": _multi}[recorder](far, tasks):
        counts["sessions"] += 1
        counts["steps"] += len(records)
        counts[f"ended_{end['end']}"] += 1
        for name, found in problems.items():
            if found:
                counts[f"sessions_failing_{name}"] += 1
                if len(examples) < 10:
                    examples.append([header["session"], name, found[0]])
    return {"recorder": recorder, **dict(counts), "examples": examples}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--recorder", required=True, choices=("single", "wide", "multi"))
    parser.add_argument("--far", required=True)
    parser.add_argument("--tasks", required=True)
    args = parser.parse_args()
    report = check(args.recorder, Path(args.far), json.loads(Path(args.tasks).read_text()))
    print(json.dumps(report, indent=1))
    raise SystemExit(1 if any(key.startswith("sessions_failing_") for key in report) else 0)


if __name__ == "__main__":
    main()
