"""Step accuracy of a trained Forge-S1 on whole test slices, next to the plan-blind floor.

    uv run python -m forge.s1.evaluate --checkpoint checkpoints/s1/s1-first-best.pt

For every step of iid_v2, pairing_v2 and long_v2 (and the long_pure subset of long_v2):
is the candidate the model scores highest one the teacher accepts? The
answer is counted for all steps and for each group of forge/freecad/baselines.py, and put
in one table beside the plan-blind rule's numbers on the SAME steps, read from
data/freecad/sessions/baselines_v2.log. If the step counts of the two do not agree, the
table says so: then they are not the same steps.

This reads test slices for scoring only. Nothing here trains or tunes.
The run folder (runs/<date>-<time>-s1-evaluate/) holds the table and the raw counts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

from forge.freecad.shards import OUT_DIR
from forge.runs import log_metrics, start_run
from forge.s1.data import StepData, read_manifest, row_ids
from forge.s1.loss import accuracy_by_group, chosen_is_right
from forge.s1.model import build_model
from forge.s1.prepare import PREPARED_DIR
from forge.s1.train import load_checkpoint, pick_device

GROUPS = ("all steps", "undo decisions", "right after a mistake", "wrong-number mistakes",
          "other on-plan steps")
BASELINE = "plan_blind_state"       # the strongest of the three lookups in baselines.py


def load_model(checkpoint: str | Path, device: torch.device | str) -> tuple[torch.nn.Module, dict]:
    """The trained model, ready to score, and the checkpoint's other contents."""
    state = load_checkpoint(checkpoint, device)       # data only: forge/s1/train.py
    model = build_model(state["config"]["model"], state["vocab"]).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    return model, state


def read_baselines(log: Path) -> dict[str, dict[str, tuple[int, float]]]:
    """baselines_v2.log -> slice name -> group -> (steps, plan-blind accuracy)."""
    found: dict[str, dict] = {}
    section = None
    for line in log.read_text().splitlines():
        if line and not line.startswith(" ") and "fit on" not in line and "run folder" not in line:
            section = line.strip()
            found[section] = {}
        elif section and line.startswith("  ") and not line.strip().startswith("group"):
            for group in GROUPS:
                if line.strip().startswith(group):
                    numbers = line.strip()[len(group):].split()
                    found[section][group] = (int(numbers[0]), float(numbers[3]))
    return found


@torch.no_grad()
def score_slice(model: torch.nn.Module, folder: Path, manifest: dict, name: str,
                device: torch.device, groups: dict[str, int], random_ids: bool,
                batch_size: int = 1024) -> dict[str, dict[str, tuple[int, int]]]:
    """{"all": group -> (right, steps), "pure": the same for long_pure sessions}."""
    totals = {"all": dict.fromkeys(GROUPS, (0, 0)), "pure": dict.fromkeys(GROUPS, (0, 0))}
    pure_bit = groups["long_pure"]
    scored = {name: bit for name, bit in groups.items() if name in GROUPS}
    generator = torch.Generator().manual_seed(0) if random_ids else None
    for note in manifest["slices"][name]["shards"]:        # one shard at a time: little memory
        print(f"  {note['file']}", flush=True)
        data = StepData.load(folder, [name], device, shards=[note["file"]])
        right, bits = [], []
        for start in range(0, len(data), batch_size):
            batch = data.batch(torch.arange(start, min(start + batch_size, len(data))), generator)
            right.append(chosen_is_right(model(batch), batch["is_candidate"], batch["is_target"]))
            bits.append(batch["group"])
        right, bits = torch.cat(right), torch.cat(bits)
        pure = (bits & pure_bit) > 0
        for subset, keep in (("all", torch.ones_like(pure)), ("pure", pure)):
            for group, (ok, n) in accuracy_by_group(right[keep], bits[keep], scored).items():
                before = totals[subset][group]
                totals[subset][group] = (before[0] + ok, before[1] + n)
    return totals


def table_text(results: dict, baselines: dict) -> str:
    """One markdown table: slice, group, steps, plan-blind rule, model, difference."""
    lines = ["| Slice | Group | Steps | Plan-blind rule | Forge-S1 | Difference |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for name, groups in results.items():
        for group in GROUPS:
            right, steps = groups[group]
            if not steps:
                continue
            base_steps, base = baselines.get(name, {}).get(group, (None, None))
            accuracy = right / steps
            if base is None:
                floor, gain = "not in the log", ""
            elif base_steps != steps:
                floor, gain = f"{base:.4f} (on {base_steps} steps: NOT the same steps)", ""
            else:
                floor, gain = f"{base:.4f}", f"{accuracy - base:+.4f}"
            lines.append(f"| {name} | {group} | {steps} | {floor} | {accuracy:.4f} | {gain} |")
    return "\n".join(lines) + "\n"


@torch.no_grad()
def held_out(args: argparse.Namespace, device: torch.device, run_dir: Path) -> None:
    """Step accuracy on every step of the checkpoint's held-out TRAINING shards, group by
    group of the config. Used to choose between identity and random row ids at test."""
    folder = Path(args.data)
    manifest = read_manifest(folder)
    model, state = load_model(args.checkpoint, device)
    data_cfg = state["config"]["data"]
    groups = dict(data_cfg.get("held_out_groups") or {})
    if data_cfg.get("held_out_shards"):
        groups = {"held_out": {"shards": data_cfg["held_out_shards"]}, **groups}
    lines = [("| Held-out training shards | Steps | All steps | Undo decisions | "
              "Label changed by the swap |"), "| --- | ---: | ---: | ---: | ---: |"]
    generator = torch.Generator().manual_seed(0) if args.random_ids else None
    for name, spec in groups.items():
        data = StepData.load(folder, data_cfg["train_slices"], device, shards=spec["shards"])
        right, bits = [], []
        for start in range(0, len(data), 1024):
            batch = data.batch(torch.arange(start, min(start + 1024, len(data))), generator)
            right.append(chosen_is_right(model(batch), batch["is_candidate"], batch["is_target"]))
            bits.append(batch["group"])
        found = accuracy_by_group(torch.cat(right), torch.cat(bits), manifest["group_bits"])
        log_metrics(run_dir, held_out=name, random_ids=args.random_ids,
                    checkpoint=args.checkpoint,
                    **{group: {"right": ok, "steps": n} for group, (ok, n) in found.items()})

        def share(group: str, found: dict = found) -> str:
            ok, n = found.get(group, (0, 0))
            return f"{ok / n:.4f}" if n else "-"
        lines.append(f"| {name} | {found['all steps'][1]} | {share('all steps')} | "
                     f"{share('undo decisions')} | {share('label changed by the swap')} |")
    text = (f"Checkpoint `{args.checkpoint}` (optimiser step {state['step']}), row ids: "
            f"{'random (one draw, seed 0)' if args.random_ids else 'identity'}. "
            f"Held-out TRAINING shards only; no test slice is read.\n\n"
            + "\n".join(lines) + "\n")
    (run_dir / "table.md").write_text(text)
    print(text + f"\nrun folder: {run_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--slices", nargs="+", default=["iid_v2", "pairing_v2", "long_v2"])
    parser.add_argument("--data", default=str(PREPARED_DIR))
    parser.add_argument("--baselines", default=str(OUT_DIR / "baselines_v2.log"))
    parser.add_argument("--random-ids", action="store_true",
                        help="random row ids at test instead of the identity")
    parser.add_argument("--device", default=None)
    parser.add_argument("--counterfactual-steps", type=int, default=20000,
                        help="steps per slice for the counterfactual table "
                             "(forge/s1/counterfactual.py); 0 leaves it out")
    parser.add_argument("--held-out", action="store_true",
                        help="score the checkpoint's own held-out TRAINING shards instead of "
                             "test slices (for choices that no test slice may decide)")
    args = parser.parse_args()
    torch.set_num_threads(2)            # the laptop is shared: never all cores (see drive.py too)
    device = torch.device(args.device) if args.device else pick_device()
    run_dir = start_run("s1-evaluate", vars(args))
    if args.held_out:
        held_out(args, device, run_dir)
        return

    folder = Path(args.data)
    manifest = read_manifest(folder)
    model, state = load_model(args.checkpoint, device)
    if state["vocab_hash"] != manifest["vocab_hash"]:
        raise SystemExit("the checkpoint was trained with another vocabulary than this data")
    assert row_ids(1, None, device)[0].shape[1] == manifest["vocab"]["max_plan_items"]

    results = {}
    for name in args.slices:
        totals = score_slice(model, folder, manifest, name, device, manifest["group_bits"],
                             args.random_ids)
        results[name] = totals["all"]
        if totals["pure"]["all steps"][1]:
            results[f"{name} (long_pure)"] = totals["pure"]
        right, steps = totals["all"]["all steps"]
        print(f"{name}: {right / steps:.4f} of {steps} steps", flush=True)
        log_metrics(run_dir, slice=name, checkpoint=args.checkpoint, trained_steps=state["step"],
                    random_ids=args.random_ids,
                    **{f"{subset}|{group}": {"right": ok, "steps": n}
                       for subset, groups in totals.items() for group, (ok, n) in groups.items()})
    text = table_text(results, read_baselines(Path(args.baselines)))
    header = (f"Checkpoint `{args.checkpoint}` (optimiser step {state['step']}, commit "
              f"{state.get('commit')}), row ids at test: "
              f"{'random' if args.random_ids else 'identity'}. Plan-blind rule: "
              f"`{BASELINE}` from `{args.baselines}`.\n\n")
    if args.counterfactual_steps:
        from forge.s1.counterfactual import table as counterfactual_table
        extra, counts = counterfactual_table({"Forge-S1": model}, args.slices,
                                             args.counterfactual_steps, 4, device,
                                             ("Forge-S1",) if args.random_ids else ())
        text += ("\nCounterfactual plans on recorded states of the same slices (about "
                 f"{args.counterfactual_steps} steps each; forge/s1/counterfactual.py):\n\n"
                 + extra)
        (run_dir / "counterfactual.json").write_text(json.dumps(counts, indent=1) + "\n")
    (run_dir / "table.md").write_text(header + text)
    (run_dir / "results.json").write_text(json.dumps(results, indent=1) + "\n")
    print("\n" + text)
    print(f"run folder: {run_dir}")


if __name__ == "__main__":
    main()
