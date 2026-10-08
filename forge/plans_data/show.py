"""Print a few plans with their replies and resolved parts, in plain words.         [command]

    uv run python -m forge.plans_data.show --source structures --split train --n 3
    uv run python -m forge.plans_data.show --source random --split combo --n 2 --seed 7
    uv run python -m forge.plans_data.show --id 410aacda390db4b2

For reading the data with your own eyes. Nothing is computed here: it prints what the
record stores.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random

from forge.plans_data import config


def _n(value: float) -> str:
    return f"{value:g}"


def describe_part(part: dict) -> str:
    size = " x ".join(_n(v) for v in part["size"])
    place = (f"left {_n(part['low'][0])}, front {_n(part['low'][1])}, "
             f"bottom {_n(part['low'][2])}")
    words = f"    {part['name']}: {part['shape']}"
    if part.get("profile"):
        words += f" {part['profile']}"
    words += f", frame {size} (length x depth x height), {place}; made by line {part['line']}"
    if part.get("features"):
        words += "; features: " + ", ".join(f["name"] for f in part["features"])
    touches = list(part["touches"])
    if part["on_ground"]:
        touches.append("the ground")
    words += "; touches " + (", ".join(touches) if touches else "nothing")
    return words


def describe(record: dict) -> str:
    out = [f"=== {record['source']} / {record['split']} / {record['id']}"
           + (f" / {record['kind']}" if record.get("kind") else "")
           + (f" / {record['family']}" if record.get("family") else "")]
    replies = {reply["line"]: reply for reply in record["replies"]}
    for number, text in enumerate(record["lines"], start=1):
        reply = replies.get(number, {})
        mark = "ok " if reply.get("reply") == "built" else "NO "
        out.append(f"  {number:2d} {mark} {text}")
        if reply.get("reply") != "built":
            out.append(f"         -> {reply.get('reply')}")
        for note in reply.get("notes", []):
            out.append(f"         ({note})")
    if record.get("overall"):
        out.append(f"  resolved: {len(record['parts'])} parts, overall "
                   + " x ".join(_n(v) for v in record["overall"]["size"])
                   + (", complete" if record["complete"] else ", NOT complete (negative slice)"))
    for part in record["parts"]:
        out.append(describe_part(part))
    if record.get("held_out"):
        out.append(f"  held-out combinations: {', '.join(record['held_out'])}")
    if record.get("kernel"):
        problems = record["kernel"]["problems"]
        out.append("  kernel: built, " + ("every check passed" if not problems else f"PROBLEMS {problems}"))
    return "\n".join(out)


def read(source: str, split: str):
    for path in sorted((config.OUT_DIR / source / split).glob("shard*.jsonl.gz")):
        with gzip.open(path, "rt") as f:
            for line in f:
                yield json.loads(line)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print plans in plain words.")
    parser.add_argument("--source", choices=config.SOURCES, default="structures")
    parser.add_argument("--split", choices=config.SPLITS, default="train")
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--id")
    parser.add_argument("--brief", action="store_true", help="lines and replies only")
    args = parser.parse_args()
    if args.id:
        for source in config.SOURCES:
            for split in config.SPLITS:
                for record in read(source, split):
                    if record["id"] == args.id:
                        print(describe(record))
                        return
        raise SystemExit(f"no plan with id {args.id}")
    # Reservoir sampling: `n` plans chosen evenly from the whole split, without loading it.
    rng = random.Random(args.seed)
    chosen: list[dict] = []
    for count, record in enumerate(read(args.source, args.split)):
        if len(chosen) < args.n:
            chosen.append(record)
        elif (slot := rng.randint(0, count)) < args.n:
            chosen[slot] = record
    if not chosen:
        raise SystemExit(f"no plans in {args.source}/{args.split}")
    for record in chosen:
        if args.brief:
            record = {**record, "parts": [], "overall": None}
        print(describe(record))
        print()


if __name__ == "__main__":
    main()
