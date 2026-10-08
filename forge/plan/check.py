"""Check plans against the plan language and count what is missing.   [command]

    uv run python -m forge.plan.check FILE_OR_DIR [--json PATH] [--echo] [--note TEXT]

For every plan (one plan per text file) it prints each line as accepted or rejected with
the reason, then a summary: plans fully accepted, lines accepted, a count per rejection
reason, and a count per missing phrase. The summary is also written as JSON.

This is the "paper test" of the plan language: it needs no CAD kernel.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from forge.plan.echo import echo_line
from forge.plan.model import MISSING_MOVE_REASONS, Plan
from forge.plan.parser import parse_plan

DEFAULT_JSON = Path("runs/plan-check/summary.json")
PLAN_SUFFIXES = (".txt", ".plan")


def plan_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.suffix in PLAN_SUFFIXES)
    return [path]


def phrase_pattern(fragment: str, names: list[str]) -> str:
    """Turn a rejected fragment into a pattern, so the same missing move is counted once.

    "top flush with seat's bottom" -> "top flush with X's bottom"; "tilted 15" -> "tilted N".
    """
    pattern = fragment.lower().replace("’", "'")
    for name in sorted(names, key=len, reverse=True):     # longest first: "top rail" before "rail"
        pattern = re.sub(rf"\b{re.escape(name)}\b", "X", pattern)
    return re.sub(r"\d+(?:\.\d+)?(?: ?mm)?", "N", pattern)


def report(name: str, plan: Plan, show_echo: bool) -> tuple[str, dict]:
    """The printed block for one plan, and its entry in the JSON summary."""
    out = [f"== {name}: {'ACCEPTED' if plan.accepted else 'NOT ACCEPTED'} =="]
    names = [line.name for line in plan.lines if line.name]
    rejections = []
    for line in plan.lines:
        mark = "ok      " if line.accepted else "REJECTED"
        out.append(f"{line.number:>4} {mark} {line.text.strip()}")
        if show_echo and line.accepted:
            out.append(f"{'':>14}read as: {echo_line(line)}")
        for rejection in line.rejections:
            detail = f"  ({rejection.detail})" if rejection.detail else ""
            out.append(f'{"":>14}{rejection.reason}: "{rejection.fragment}"{detail}')
            rejections.append({"line": line.number, "text": line.text.strip(),
                               **asdict(rejection),
                               "phrase": phrase_pattern(rejection.fragment, names)})
        out.extend(f"{'':>14}note: {note}" for note in line.notes)
    for problem in plan.problems:
        out.append(f'{"":>5}PLAN     {problem.reason}: "{problem.fragment}"  ({problem.detail})')
        rejections.append({"line": None, "text": "", **asdict(problem),
                           "phrase": phrase_pattern(problem.fragment, names)})
    entry = {"plan": name, "accepted": plan.accepted, "lines": len(plan.lines),
             "lines_accepted": sum(line.accepted for line in plan.lines),
             "rejections": rejections}
    return "\n".join(out), entry


def summarise(entries: list[dict]) -> dict:
    reasons: Counter[str] = Counter()
    phrases: Counter[str] = Counter()
    for entry in entries:
        for rejection in entry["rejections"]:
            reasons[rejection["reason"]] += 1
            if rejection["reason"] in MISSING_MOVE_REASONS:
                phrases[f'{rejection["reason"]}: {rejection["phrase"]}'] += 1
    return {
        "plans": len(entries),
        "plans_fully_accepted": sum(entry["accepted"] for entry in entries),
        "lines": sum(entry["lines"] for entry in entries),
        "lines_accepted": sum(entry["lines_accepted"] for entry in entries),
        "rejections_by_reason": dict(reasons.most_common()),
        "missing_phrases": dict(phrases.most_common()),
        "per_plan": entries,
    }


def summary_table(summary: dict) -> str:
    out = ["", "SUMMARY",
           f"  plans fully accepted   {summary['plans_fully_accepted']} of {summary['plans']}",
           f"  lines accepted         {summary['lines_accepted']} of {summary['lines']}",
           "  rejections by reason"]
    out += [f"    {n:>4}  {reason}" for reason, n in summary["rejections_by_reason"].items()]
    out.append("  missing phrases (X = a part name, N = a number)")
    out += [f"    {n:>4}  {phrase}" for phrase, n in summary["missing_phrases"].items()]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("path", type=Path, help="a plan file, or a folder of .txt / .plan files")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="where to write the summary")
    parser.add_argument("--echo", action="store_true", help="also print how each line was read")
    parser.add_argument("--note", default="", help="a sentence stored in the JSON summary")
    args = parser.parse_args(argv)

    files = plan_files(args.path)
    if not files or not all(f.is_file() for f in files):
        print(f"no plan files found at {args.path}", file=sys.stderr)
        return 2
    entries = []
    for file in files:
        text, entry = report(file.name, parse_plan(file.read_text()), args.echo)
        print(text + "\n")
        entries.append(entry)
    summary = summarise(entries)
    summary["input"] = str(args.path)
    if args.note:
        summary["note"] = args.note
    print(summary_table(summary))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(f"\nsummary written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
