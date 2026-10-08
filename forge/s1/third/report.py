"""Closed-loop runs of forge/s1/third/drive.py side by side: seeds pooled, by plan length.

    uv run python -m forge.s1.third.report \
        --model "third, teacher-free=runs/<a>,runs/<b>,runs/<c>" \
        --model "first, teacher arguments=runs/<d>,runs/<e>,runs/<f>"

Every row says in its label which of the two closed-loop numbers it is. The
runs of one model are pooled (their seeds share no part). Three tables:

    1. built right per slice and condition, with the 95% Wilson interval
    2. the same by plan length (2-6, 7-10, 11-13, 14-16 items), slices pooled
    3. recovery: of the episodes in which the model made at least one mistake of its own,
       how many it still built right; and how many failures were exact state cycles
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forge.s1.loss import wilson_interval

LENGTHS = ((1, 6), (7, 10), (11, 13), (14, 16))


def episodes(folders: str) -> list[dict]:
    found = []
    for folder in folders.split(","):
        with (Path(folder) / "episodes.jsonl").open() as f:
            found += [json.loads(line) for line in f]
    return found


def cell(lot: list[dict]) -> str:
    if not lot:
        return "-"
    wins = sum(e["success"] for e in lot)
    low, high = wilson_interval(wins, len(lot))
    return f"{wins}/{len(lot)} = {wins / len(lot):.3f} ({low:.3f} to {high:.3f})"


def tables(models: dict[str, list[dict]]) -> str:
    names = list(models)
    slices = list(dict.fromkeys(e["slice"] for lot in models.values() for e in lot))
    rates = sorted({e["mistakes"] for lot in models.values() for e in lot})
    head = " | ".join(names)
    rule = " --- |" * len(names)
    out = [f"### Built right, per slice and condition\n\n| Slice | Condition | {head} |",
           f"| --- | --- |{rule}"]
    for name in slices:
        for rate in rates:
            condition = "clean" if rate == 0 else f"messy, mistakes at {rate}"
            out.append(f"| {name} | {condition} | " + " | ".join(
                cell([e for e in models[m] if e["slice"] == name and e["mistakes"] == rate])
                for m in names) + " |")
    out += [(f"\n### Built right, by plan length (all slices pooled)\n\n"
             f"| Plan items | Condition | {head} |"), f"| --- | --- |{rule}"]
    for low, high in LENGTHS:
        for rate in rates:
            condition = "clean" if rate == 0 else f"messy, mistakes at {rate}"
            out.append(f"| {low} to {high} | {condition} | " + " | ".join(
                cell([e for e in models[m]
                      if low <= e["plan_items"] <= high and e["mistakes"] == rate])
                for m in names) + " |")
    out += [(f"\n### Recovery from the model's own mistakes (all slices and conditions)\n\n"
             f"| Measure | {head} |"), f"| --- |{rule}"]
    scored = {m: [e for e in models[m] if "model_mistakes" in e] for m in names}
    erred = {m: [e for e in scored[m] if e["model_mistakes"] > 0] for m in names}
    failed = {m: [e for e in models[m] if not e["success"]] for m in names}
    out.append("| Episodes with no own mistake: built right | " + " | ".join(
        cell([e for e in scored[m] if e["model_mistakes"] == 0]) for m in names) + " |")
    out.append("| Episodes with at least one own mistake: built right | " + " | ".join(
        cell(erred[m]) for m in names) + " |")
    out.append("| Own mistakes per episode (command / argument) | " + " | ".join(
        f"{sum(e.get('command_mistakes', e['model_mistakes']) for e in scored[m]) / max(1, len(scored[m])):.3f}"
        f" / {sum(e.get('argument_mistakes', 0) for e in scored[m]) / max(1, len(scored[m])):.3f}"
        for m in names) + " |")
    out.append("| Failures that are exact state cycles | " + " | ".join(
        f"{sum(1 for e in failed[m] if e.get('cycle'))}/{len(failed[m])}" for m in names) + " |")
    out.append("| Harness failures (FreeCAD lost twice) | " + " | ".join(
        str(sum(1 for e in models[m] if e.get("harness"))) for m in names) + " |")
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", action="append", required=True,
                        help='"label=run folder,run folder,..."')
    parser.add_argument("--out", default=None, help="also write the tables to this file")
    args = parser.parse_args()
    models = {}
    for text in args.model:
        label, _, folders = text.partition("=")
        models[label] = episodes(folders)
    text = tables(models)
    if args.out:
        Path(args.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
