"""Print recorded FreeCAD sessions in plain words, so a person can check them by reading.

    uv run python -m forge.freecad.show_session --n 3 --noise 0.2
    uv run python -m forge.freecad.show_session --n 2 --start stray_sketch --split long
    uv run python -m forge.freecad.show_session --session 0123456789abcdef

For each session: the plan, how the session started, then one block per step:
    the session in one line (what exists, what is open, what is selected),
    the commands the teacher accepts (the TARGET), with the plan item they belong to,
    what was executed (marked NOISE when it was an injected wrong command), FreeCAD's reply.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from forge.freecad.describe import describe_shape
from forge.freecad.shards import OUT_DIR, SLICES, read_sessions
from forge.system1.splits import unit_hash


def _n(value: object) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def say_command(name: str, args: dict) -> str:
    return " ".join([name, *(f"{key}={_n(value)}" for key, value in args.items())])


def say_item(item: dict) -> str:
    """One document object, short: 'Pad(10)', 'Sketch001[circle diameter=6 (x, y free)]'."""
    mark = "" if item["valid"] else "!INVALID"
    if item["type"] == "sketch":
        drawn = "; ".join(describe_shape(shape) for shape in item["shapes"]) or "empty"
        where = "" if item["plane"] == "XY" else f" on {item['plane']}"
        return f"{item['name']}@{_n(item['offset'])}{where}[{drawn}]{mark}"
    skip = ("type", "name", "body", "valid", "sketch", "on", "of", "edges", "faces")
    numbers = ",".join(_n(value) for key, value in item.items()
                       if key not in skip and value is not None and value is not False)
    return f"{item['name']}({numbers}){mark}"


def say_snapshot(snapshot: dict) -> str:
    session = snapshot["session"]
    if not session["document"]:
        return "no document"
    objects = [say_item(item) for item in snapshot["items"] if item["type"] != "body"]
    bodies = sum(item["type"] == "body" for item in snapshot["items"])
    selection = session["selection"]
    if selection is None:
        selected = "nothing"
    elif "rule" in selection:
        selected = f"{selection['count']} {selection['type']} ({selection['rule']})"
    else:
        selected = f"{selection['type']} {selection['name']}"
    shown = objects if len(objects) <= 4 else [f"... {len(objects) - 3} earlier", *objects[-3:]]
    return (f"{bodies} body | {' '.join(shown) or 'no objects'} | open {session['open_sketch'] or '-'}"
            f" | selected {selected} | undo {session['undo_depth']}"
            + (" | FINISHED" if session["finished"] else ""))


def say_targets(targets: list[dict]) -> str:
    said = " | ".join(say_command(target["command"], target["args"]) for target in targets)
    items = {target["item"] for target in targets if target["item"] is not None}
    return said + (f"   (plan item {min(items) + 1})" if items else "")


def show(header: dict, records: list[dict], end: dict) -> str:
    start = header["start"]
    lines = [
        "=" * 110,
        (f"session {header['session']}   part {header['part_id']} ({header['family']})   "
         f"split {header['split']}   noise level {header['noise_level']}   "
         f"clean length {header['clean_length']}, took {end['steps']} steps, ended {end['end']}"),
        "PLAN:",
        *(f"    {number}. {item['kind']}  "
          + " ".join(f"{slot}={_n(value)}" for slot, value in item["slots"].items())
          for number, item in enumerate(header["plan"], start=1)),
        f"START: {start['kind']}" + ("  (undo history forgotten)" if start["forget_undo"] else ""),
        *(f"    before step 0: {say_command(name, args)}" for name, args in start["commands"]),
    ]
    for record in records:
        done, reply = record["executed"], record["reply"]
        flavour = f": {done['flavour']}" if "flavour" in done else ""
        what = f"NOISE ({done['noise']}{flavour})" if done["noise"] else "executed"
        answer = reply["status"] + (f" ({reply['reason']})" if "reason" in reply else "")
        plan_state = "" if record["progress"]["on_plan"] else "   [OFF PLAN]"
        if record.get("before") == "forget_undo":
            lines.append("    ~~ the document was closed and opened again: nothing can be undone ~~")
        lines += [
            f"step {record['t']}{plan_state}",
            f"    session:  {say_snapshot(record['snapshot'])}",
            f"    target:   {say_targets(record['target'])}",
            f"    {what + ':':9s} {say_command(done['command'], done['args'])}  ->  {answer}",
        ]
    lines.append(f"END: {end['end']}; {say_snapshot(end['snapshot']) if end['snapshot'] else '-'}"
                 + (f"; PROBLEMS {end['problems']}" if end["problems"] else
                    "; the solid is the stored one and the document is clean"))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--split", default="train_v2", choices=list(SLICES),
                        help="the slice to read from (train_v2, iid_v2, ...; or a first-mix "
                             "slice: train, train_heavy, ...)")
    parser.add_argument("--kind", default=None,
                        help="only sessions with a wrong command of this kind (early_done, "
                             "carry_on, wrong_argument, ...) or 'reopen'")
    parser.add_argument("--noise", type=float, default=None,
                        help="only sessions with this noise level (0, 0.1, 0.2 or 0.3)")
    parser.add_argument("--start", default=None,
                        help="only sessions whose start kind contains this (stray, opened, ...)")
    parser.add_argument("--session", default=None, help="one session, by its id")
    parser.add_argument("--max-steps", type=int, default=None, help="only sessions this short")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dir", default=str(OUT_DIR))
    args = parser.parse_args()

    shards = sorted((Path(args.dir) / args.split).glob("shard*.jsonl.gz"))
    if args.session:
        shards = sorted(Path(args.dir).glob("*/shard*.jsonl.gz"))
    if not shards:
        raise SystemExit(f"no sessions in {Path(args.dir) / args.split}: run forge.freecad.sessions")
    # One shard chosen by the seed, then the sessions in it whose hash is smallest:
    # a different seed shows different sessions, the same seed the same ones.
    if not args.session:
        shards = [shards[int(unit_hash(f"show:{args.seed}") * len(shards))]]
    wanted = []
    for shard in shards:
        for header, records, end in read_sessions(shard):
            if args.session:
                if header["session"] != args.session:
                    continue
            elif (args.noise is not None and header["noise_level"] != args.noise) \
                    or (args.start and args.start not in header["start"]["kind"]) \
                    or (args.max_steps and end["steps"] > args.max_steps) \
                    or (args.kind and not any(
                        record["executed"]["noise"] == args.kind
                        or (args.kind == "reopen" and "before" in record) for record in records)):
                continue
            wanted.append((unit_hash(f"show:{args.seed}:{header['session']}"), header, records,
                           end))
    for _, header, records, end in sorted(wanted, key=lambda item: item[0])[: args.n]:
        print(show(header, records, end))


if __name__ == "__main__":
    main()
