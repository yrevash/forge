"""Print recorded structure sessions step by step, for a person to read.

Run:    uv run python -m forge.freecad_multi.show_session --slice train --shard 0 --n 2
        uv run python -m forge.freecad_multi.show_session --session 1a2b... --all-steps
        uv run python -m forge.freecad_multi.show_session --slice combo --want feature --n 3

Per session: the plan as the model reads it (load.view_plan), then one line per step:

    t   on/OFF plan   what the teacher allows   ->  what was executed [the mistake]  reply

A run of steps in which the session simply did what the teacher said is folded into one
line (`--all-steps` prints them all). The state the model sees is printed at every step
that is off plan or follows a mistake (`--states` prints it at every step shown).

`--want` picks sessions that hold something: feature, shape (another shape than box or
cylinder), activate (the executor goes back to an earlier body), reopen (the undo
history was lost), or the name of a mistake kind (wrong_size, misplaced_body ...).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forge.freecad_multi.load import view_plan, view_state
from forge.freecad_multi.shards import OUT_DIR, complete_shards, read_sessions


def _args(args: dict) -> str:
    return ", ".join(f"{key}={value}" for key, value in args.items())


def _command(command: dict) -> str:
    return f"{command['command']}({_args(command['args'])})"


def holds(header: dict, records: list[dict], want: str | None) -> bool:
    if want is None:
        return True
    if want == "feature":
        return any(item["features"] for item in header["plan"])
    if want == "shape":
        return any(item.get("shape") not in ("box", "cylinder", "", None)
                   for item in header["plan"] if item["kind"] == "part")
    if want == "activate":
        return any(target["command"] == "activate_body" for r in records for target in r["target"])
    if want == "reopen":
        return any(r.get("before") == "forget_undo" for r in records)
    return any(r["executed"]["noise"] == want for r in records)


def show(header: dict, records: list[dict], end: dict, all_steps: bool, states: bool) -> None:
    print(f"\n=== session {header['session']}  slice {header['slice']}  noise "
          f"{header['noise_level']}  start {header['start']['kind']}  {header['parts']} bodies  "
          f"{len(records)} steps (clean build: {header['clean_length']})  ended {end['end']}"
          f"{'  PROBLEMS ' + str(end['problems']) if end['problems'] else ''}")
    for entry in view_plan(header["plan"]):
        print(f"  plan  {json.dumps(entry, separators=(',', ':'))}")
    folded: list[int] = []
    after_mistake = False

    def flush() -> None:
        if folded:
            names = [records[t]["executed"]["command"] for t in folded]
            print(f"  t={folded[0]}..{folded[-1]}  on plan, did what the teacher said: "
                  + " ".join(names[:12]) + (" ..." if len(names) > 12 else ""))
            folded.clear()

    for record in records:
        done, on_plan = record["executed"], record["progress"]["on_plan"]
        plain = on_plan and not done["noise"] and not after_mistake \
            and record.get("before") is None and record["reply"]["status"] == "ok"
        if plain and not all_steps:
            folded.append(record["t"])
            continue
        flush()
        if record.get("before"):
            print("        (the undo history was lost here: the file was closed and opened)")
        if states or not on_plan or after_mistake:
            print(f"        state {json.dumps(view_state(record['snapshot']), separators=(',', ':'))}")
        mistake = f"  [{done['noise']}{'/' + done['flavour'] if 'flavour' in done else ''}]" \
            if done["noise"] else ""
        print(f"  t={record['t']:<4d} {'on ' if on_plan else 'OFF'}  teacher: "
              f"{' | '.join(_command(target) for target in record['target'])}"
              f"   ->  {_command(done)}{mistake}  {record['reply']['status']}"
              + (f" ({record['reply'].get('reason')})" if record["reply"]["status"] != "ok" else ""))
        after_mistake = bool(done["noise"]) and record["reply"]["status"] == "ok"
    flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", default=str(OUT_DIR))
    parser.add_argument("--slice", default="train")
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--session", default=None)
    parser.add_argument("--want", default=None)
    parser.add_argument("--max-bodies", type=int, default=10 ** 6)
    parser.add_argument("--all-steps", action="store_true")
    parser.add_argument("--states", action="store_true")
    args = parser.parse_args()
    shown = skipped = 0
    for stats, path in complete_shards(Path(args.dir), (args.slice,)):
        if args.session is None and int(path.name[5:9]) < args.shard:
            continue
        for header, records, end in read_sessions(path):
            if args.session is not None and header["session"] != args.session:
                continue
            if header["parts"] > args.max_bodies or not holds(header, records, args.want):
                continue
            if skipped < args.skip:
                skipped += 1
                continue
            show(header, records, end, args.all_steps, args.states)
            shown += 1
            if shown >= args.n:
                return


if __name__ == "__main__":
    main()
