"""Print sessions in plain words, so a person can check them by reading.

Run:    uv run python -m forge.system1.show --n 5 [--split train] [--noise 0.2] [--seed 0]

For each session: the prompt with every mention numbered ("[3]6" is mention 3,
the text "6"), the plan, then one block per step: the state in one line, what
was executed (marked NOISE when it was an injected wrong step), what the engine
said, and the teacher's target.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from forge.system1.sessions import OUT_DIR, read_sessions
from forge.system1.splits import SPLITS, unit_hash


def numbered_prompt(prompt: str, mentions: list[dict]) -> str:
    """The prompt with '[i]' put in front of the i-th mention."""
    pieces, at = [], 0
    for index, mention in enumerate(mentions):
        pieces.append(prompt[at:mention["start"]] + f"[{index}]")
        at = mention["start"]
    return "".join(pieces) + prompt[at:]


def say_step(step: dict, mentions: list[dict]) -> str:
    """'hole(diameter=[3] 6, x=[4] 10, y=[5] 5)' from a stored step."""
    if not step["slots"]:
        return step["kind"].upper()
    parts = []
    for slot, source in step["slots"].items():
        index = int(source.removeprefix("mention:"))
        parts.append(f"{slot}=[{index}] {mentions[index]['value']:g}")
    return f"{step['kind']}({', '.join(parts)})"


def say_state(state: dict) -> str:
    built = "; ".join(
        f"{item['kind']}({', '.join(f'{value:g}' for value in item['slots'].values())})"
        for item in state["built"]) or "nothing"
    return (f"built: {built} | last outcome: {state['last']} | "
            f"mentions used: {sum(state['used'])} of {len(state['used'])}")


def show(header: dict, records: list[dict]) -> str:
    mentions = header["mentions"]
    lines = [
        "=" * 100,
        (f"session {header['session']}   part {header['part_id']} ({header['family']})   "
         f"split {header['split']}   prompt {header['variant']}   "
         f"noise level {header['noise_level']}"),
        "PROMPT (mentions numbered):",
        *(f"    {line}" for line in numbered_prompt(header["prompt"], mentions).split("\n")),
        "PLAN:",
    ]
    for number, item in enumerate(header["plan"], start=1):
        lines.append(f"    {number}. {say_step({'kind': item['kind'], 'slots': item['sources']}, mentions)}")
    for record in records:
        done = record["executed"]
        what = f"NOISE ({done['noise_kind']})" if done["was_noise"] else "executed"
        lines += [
            f"step {record['t']}",
            f"    state:    {say_state(record['state'])}",
            f"    target:   {say_step(record['target'], mentions)}",
            f"    {what + ':':9s} {say_step(done, mentions)}  ->  {done['outcome']}",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--split", default="train", choices=SPLITS)
    parser.add_argument("--noise", type=float, default=None,
                        help="only sessions with this noise level (0, 0.1, 0.2 or 0.3)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--dir", default=str(OUT_DIR))
    args = parser.parse_args()

    shards = sorted((Path(args.dir) / args.split).glob("shard*.jsonl.gz"))
    if not shards:
        raise SystemExit(f"no sessions in {Path(args.dir) / args.split}: run forge.system1.sessions")
    # One shard chosen by the seed, then the sessions in it whose hash is smallest:
    # a different seed shows different sessions, the same seed the same ones.
    shard = shards[int(unit_hash(f"show:{args.seed}") * len(shards))]
    wanted = [(unit_hash(f"show:{args.seed}:{header['session']}"), header, records)
              for header, records in read_sessions(shard)
              if args.noise is None or header["noise_level"] == args.noise]
    for _, header, records in sorted(wanted, key=lambda item: item[0])[: args.n]:
        print(show(header, records))


if __name__ == "__main__":
    main()
