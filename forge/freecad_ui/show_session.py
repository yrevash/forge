"""Print recorded interface sessions, one line per step, for reading by a person.

    uv run python -m forge.freecad_ui.show_session --shard data/freecad_ui/sessions/iid/shard000.jsonl.gz
        [--session <id>] [--first 3] [--noisy-only] [--brief]

Each line: step number, what was executed (`!kind` marks an injected wrong action),
FreeCAD's reply if it was not "ok", the teacher's targets when they are not simply the
executed action, and the teacher's reason (worked out again from the stored snapshot).
`--brief` prints only the steps around wrong actions and repairs.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from forge.freecad_ui.sessions import read_sessions
from forge.freecad_ui.teacher import plan_from_json, segments_of, teacher


def _value(value: object) -> str:
    return "" if value is None else f" = {value}"


def show(header: dict, records: list[dict], end: dict, brief: bool = False) -> None:
    plan = plan_from_json(header["plan"])
    segments = segments_of(plan)
    print(f"\nsession {header['session']}  part {header['part_id']} ({header['family']}, "
          f"{header['split']})  noise {header['noise_level']}  {len(records)} steps "
          f"(a clean build: {header['clean_length']})")
    print("  plan: " + "; ".join(f"{i}:{step['kind']} {step['slots']}"
                                 for i, step in enumerate(header["plan"])))
    keep = set(range(len(records)))
    if brief:
        hot = {i for i, r in enumerate(records)
               if r["executed"]["noise"] or not r["progress"]["on_plan"]
               or r["reply"]["status"] != "ok"}
        keep = {j for i in hot for j in range(i - 1, i + 3)} | {len(records) - 1}
    last = -1
    for i, record in enumerate(records):
        if i not in keep:
            continue
        if i != last + 1:
            print("       ...")
        last = i
        done = record["executed"]
        advice = teacher(plan, record["snapshot"], segments)
        mark = f"  !{done['noise']}" if done["noise"] else ""
        reply = "" if record["reply"]["status"] == "ok" else \
            f"  -> {record['reply']['status']}: {record['reply'].get('reason', '')[:60]}"
        targets = [f"{t['id']}{_value(t['value'])}" for t in record["target"]]
        plain = len(targets) == 1 and not done["noise"]
        wanted = "" if plain else f"  [teacher: {', '.join(targets)}]"
        state = "" if record["progress"]["on_plan"] else "  OFF PLAN"
        print(f"  {record['t']:4d} {done['id']}{_value(done['value'])}{mark}{reply}{wanted}"
              f"{state}  # {advice.why[:90]}")
    solid = end["snapshot"]["solid"] if end["snapshot"] else None
    print(f"  end: {end['end']}, problems {end['problems']}, solid "
          f"{None if solid is None else (solid['volume'], solid['bbox'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--shard", type=Path, required=True)
    parser.add_argument("--session", default=None)
    parser.add_argument("--first", type=int, default=3)
    parser.add_argument("--noisy-only", action="store_true")
    parser.add_argument("--brief", action="store_true")
    args = parser.parse_args()
    shown = 0
    for header, records, end in read_sessions(args.shard):
        if args.session and header["session"] != args.session:
            continue
        if args.noisy_only and not header["noise_level"]:
            continue
        show(header, records, end, args.brief)
        shown += 1
        if shown >= args.first and not args.session:
            break


if __name__ == "__main__":
    main()
