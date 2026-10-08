"""Put several closed-loop runs (forge/s1/drive.py) side by side: seeds, models, plan length.

    uv run python -m forge.s1.drive_report \\
        --model "model 1=runs/<a>,runs/<b>,runs/<c>" --model "model 2=runs/<d>,runs/<e>,runs/<f>"

Every run folder holds one seed of one model (episodes.jsonl). This file only counts what
is in them; it runs nothing. For every model, slice and condition it prints the parts built
right per seed, the pooled count over the seeds with its 95% Wilson interval
(forge/s1/loss.py), and the lowest and highest seed. Then the same by plan length.

The seeds of one model must be runs on DIFFERENT parts (drive.py --offset): the pooled
interval treats every episode as one independent part, and this file refuses a part that
occurs twice in one cell.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import yaml

from forge.s1.loss import wilson_interval


def read_run(folder: Path) -> tuple[dict, list[dict]]:
    config = yaml.safe_load((folder / "config.yaml").read_text())
    episodes = [json.loads(line) for line in (folder / "episodes.jsonl").read_text().splitlines()]
    return config, episodes


def condition(rate: float) -> str:
    return "clean start" if rate == 0 else f"messy start, mistakes at {rate}"


def cell(wins: int, parts: int) -> str:
    low, high = wilson_interval(wins, parts)
    return f"{wins}/{parts} = {wins / parts:.3f} ({low:.3f} to {high:.3f})"


def report(models: dict[str, list[Path]]) -> str:
    # model -> (slice, rate) -> seed -> episodes
    table: dict[str, dict[tuple, dict[int, list[dict]]]] = {}
    notes = []
    for name, folders in models.items():
        table[name] = defaultdict(dict)
        for folder in folders:
            config, episodes = read_run(folder)
            notes.append(f"- {name}, seed {config['seed']}, parts {config.get('offset', 0)} to "
                         f"{config.get('offset', 0) + config['parts'] - 1}"
                         + (", DRIVER RULE no-repeat-undo ON" if config.get("no_repeat_undo")
                            else "") + f": `{folder}` ({len(episodes)} episodes, "
                         f"{sum(1 for e in episodes if e.get('harness'))} harness failures)")
            for episode in episodes:
                lot = table[name][(episode["slice"], episode["mistakes"])]
                lot.setdefault(config["seed"], []).append(episode)
        for key, seeds in table[name].items():
            ids = [e["part_id"] for lot in seeds.values() for e in lot]
            if len(ids) != len(set(ids)):
                raise SystemExit(f"{name} {key}: a part occurs in two seeds; use --offset")
    names = list(models)
    cells = list(dict.fromkeys(key for name in names for key in table[name]))
    lines = ["## Closed loop: parts built right, pooled over the seeds (95% Wilson interval)", "",
             "| Slice | Condition | " + " | ".join(names) + " |",
             "| --- | --- | " + " | ".join("---" for _ in names) + " |"]
    for key in cells:
        row = []
        for name in names:
            lot = [e for seed in table[name].get(key, {}).values() for e in seed]
            row.append(cell(sum(e["success"] for e in lot), len(lot)) if lot else "not run")
        lines.append(f"| {key[0]} | {condition(key[1])} | " + " | ".join(row) + " |")
    lines += ["", "## The same, seed by seed (built right / parts; every seed has other parts)", "",
              "| Slice | Condition | " + " | ".join(names) + " |",
              "| --- | --- | " + " | ".join("---" for _ in names) + " |"]
    for key in cells:
        row = []
        for name in names:
            seeds = table[name].get(key, {})
            rates = [sum(e["success"] for e in lot) / len(lot) for lot in seeds.values()]
            row.append(", ".join(f"s{seed}: {sum(e['success'] for e in lot)}/{len(lot)}"
                                 for seed, lot in sorted(seeds.items()))
                       + (f"; range {min(rates):.2f} to {max(rates):.2f}" if rates else "not run"))
        lines.append(f"| {key[0]} | {condition(key[1])} | " + " | ".join(row) + " |")
    lines += ["", "## By plan length (plan items, the start included), seeds pooled", "",
              "| Slice | Condition | Plan items | " + " | ".join(names) + " |",
              "| --- | --- | ---: | " + " | ".join("---" for _ in names) + " |"]
    for key in cells:
        lengths = sorted({e["plan_items"] for name in names
                          for seed in table[name].get(key, {}).values() for e in seed
                          if "plan_items" in e})
        if not lengths or lengths[-1] < 7:      # only the slices with long plans
            continue
        for length in lengths:
            row = []
            for name in names:
                lot = [e for seed in table[name].get(key, {}).values() for e in seed
                       if e.get("plan_items") == length]
                row.append(f"{sum(e['success'] for e in lot)}/{len(lot)}" if lot else "-")
            lines.append(f"| {key[0]} | {condition(key[1])} | {length} | " + " | ".join(row) + " |")
    lines += ["", "## What the model did (means per part, seeds pooled)", "",
              ("| Slice | Condition | Model | Said done | Model mistakes | Steps | "
               "Undo refused by the driver rule |"),
              "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for key in cells:
        for name in names:
            lot = [e for seed in table[name].get(key, {}).values() for e in seed if "steps" in e]
            if lot:
                lines.append(
                    f"| {key[0]} | {condition(key[1])} | {name} | "
                    f"{sum(e['said_done'] for e in lot)}/{len(lot)} | "
                    f"{sum(e['model_mistakes'] for e in lot) / len(lot):.2f} | "
                    f"{sum(e['steps'] for e in lot) / len(lot):.1f} | "
                    f"{sum(e.get('undo_refused_by_rule', 0) for e in lot) / len(lot):.2f} |")
    return "\n".join([*lines, "", "## Runs", "", *notes]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", action="append", required=True,
                        help='"label=run folder,run folder,..." (one folder per seed)')
    parser.add_argument("--out", default=None, help="also write the tables to this file")
    args = parser.parse_args()
    models = {}
    for text in args.model:
        label, _, folders = text.partition("=")
        models[label.strip()] = [Path(folder) for folder in folders.split(",") if folder]
    text = report(models)
    if args.out:
        Path(args.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
