"""Step accuracy by group on test slices, for any of the three models.

    uv run python -m forge.s1.third.evaluate --checkpoint third=checkpoints/s1/s1-third-best.pt \
        --checkpoint first=checkpoints/s1/s1-first-best.pt --max-shards 4

Every model is scored on the SAME recorded steps: the first `--max-shards` shards of each
slice (shards of a slice are balanced samples, so a prefix is a fair sample).

    a third model     reads its prepared arrays (data/s1/v3) and gets two numbers per group:
                      "command" (the first two models' measure) and "command and binding"
    a first or second model   is given the same sessions through ITS OWN encoding
                      (forge/s1/prepare.arrays_of_shard), made on the spot: its arrays in
                      data/s1/v1 do not hold the wide slices

Test slices are only ever read here, by counterfactual.py and by the drivers.
"""

from __future__ import annotations

import argparse
import json

import torch

from forge.freecad import wide_sessions
from forge.freecad.shards import OUT_DIR, complete_shards
from forge.runs import log_metrics, start_run
from forge.s1 import prepare as first_prepare
from forge.s1 import vocab
from forge.s1.data import StepData, read_manifest
from forge.s1.third.data import BindData
from forge.s1.third.drive import load_any_model
from forge.s1.third.prepare import ALL_GROUPS, PREPARED_DIR, TEST_SLICES
from forge.s1.third.train import evaluate as evaluate_third
from forge.s1.train import evaluate as evaluate_first
from forge.s1.train import pick_device

GROUPS = ("all steps", "undo decisions", "right after a mistake", "wrong-number mistakes",
          "other on-plan steps")


def score(model: torch.nn.Module, third: bool, name: str, max_shards: int,
          device: torch.device) -> dict:
    """One model on the first shards of one slice: {"steps", "command", "both" (third only)}."""
    if third:
        manifest = read_manifest(PREPARED_DIR)
        files = [note["file"] for note in manifest["slices"][name]["shards"]][:max_shards]
        found = evaluate_third(model, BindData.load(PREPARED_DIR, [name], "cpu", shards=files),
                               ALL_GROUPS,
                               random_ids=0 if model.trained_with_random_ids else None)
        return {"steps": found["steps"], "command": found["command_only"],
                "both": found["accuracy"]}
    folder = wide_sessions.OUT_DIR if name in wide_sessions.SLICES else OUT_DIR
    listing = folder / "long_pure.json"
    pure = frozenset(entry["session"] for entry in json.loads(listing.read_text())
                     .get(name, {}).get("pure", [])) if listing.exists() else frozenset()
    roles = [table == "doc" for _, table in vocab.as_json()["roles"]]
    right, steps = {}, {}
    for _, path in complete_shards(folder, (name,))[:max_shards]:
        data = StepData(first_prepare.arrays_of_shard(path, pure), "cpu", roles)
        found = evaluate_first(model, data, first_prepare.GROUP_BITS)
        for group, n in found["steps"].items():
            steps[group] = steps.get(group, 0) + n
            right[group] = right.get(group, 0) + round((found["accuracy"][group] or 0) * n)
    return {"steps": steps, "command": {g: right[g] / n if n else None for g, n in steps.items()}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", action="append", required=True, help="label=file")
    parser.add_argument("--slices", nargs="+", default=list(TEST_SLICES))
    parser.add_argument("--max-shards", type=int, default=4)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()
    torch.set_num_threads(2)            # the laptop is shared: never all cores
    device = torch.device(args.device) if args.device else pick_device()
    run_dir = start_run("s1-steps3", vars(args))
    lines = ["| Slice | Group | Steps | " + " | ".join(
        text.partition("=")[0] for text in args.checkpoint) + " |",
        "| --- | --- | ---: |" + " ---: |" * len(args.checkpoint)]
    cells: dict[tuple[str, str], list[str]] = {}
    for text in args.checkpoint:
        label, _, file = text.partition("=")
        model, third = load_any_model(file)
        model = model.to(device)
        for name in args.slices:
            found = score(model, third, name, args.max_shards, device)
            log_metrics(run_dir, model=label, checkpoint=file, slice=name, **found)
            for group in GROUPS:
                value = found["command"].get(group)
                cell = "-" if value is None else f"{value:.4f}"
                if third and value is not None:
                    cell += f" (with binding {found['both'][group]:.4f})"
                cells.setdefault((name, group), [str(found["steps"].get(group, 0))]).append(cell)
            print(f"{label} {name}: done", flush=True)
    for (name, group), row in cells.items():
        lines.append(f"| {name} | {group} | " + " | ".join(row) + " |")
    table = "\n".join(lines) + "\n"
    (run_dir / "table.md").write_text(
        f"Step accuracy, command only, on the first {args.max_shards} shards of "
        f"each slice; for a third model also 'command and binding'.\n\n{table}")
    print(table + f"\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
