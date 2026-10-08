"""The counterfactual table: does the model read the plan, or only recognise the session?

    uv run python -m forge.s1.counterfactual --checkpoint checkpoints/s1/s1-first-best.pt

forge/s1/evaluate.py prints this table after its step table. It can be run by itself too.

Take recorded states of a TEST slice. Keep the state; change only the plan; ask the
teacher again (forge/s1/augment.py says why that is sound). Three versions of each state:
    recorded               the plan as recorded (a check: the teacher must give the recorded label)
    two features swapped   two feature items of the plan change places
    one number changed     one number of one plan item is moved by 1.5, 3.5 or 7.5
    every length scaled    plan AND state multiplied by one factor between 0.1 and 10 (drawn
                           evenly on a log scale; augment.scaled). The answer must NOT
                           change: it is the same session on a smaller or larger part, with
                           sizes that are not whole numbers. Its "Right" column against the
                           recorded row shows how much the model leans on raw magnitudes.
For each, on the steps where the right answer CHANGED:
    right                  the model's choice is in the teacher's set for the changed plan
    still the old answer   the model's choice is in the recorded set and not in the new one:
                           it answered for the plan it was not given
and on the steps where the answer did not change, how often the model is still right.

A model that merely continues what the session looks like scores high on recorded states
and low here. First measured on the first model (7 Oct 2026).

Test slices are read for scoring only. Nothing here trains or tunes.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

import torch

from forge.freecad.load import history_before, usable, view
from forge.freecad.shards import OUT_DIR, complete_shards, read_sessions
from forge.runs import log_metrics, start_run
from forge.s1.augment import (
    change_one_number,
    relabel,
    scaled,
    scaled_plan,
    swap_two_features,
)
from forge.s1.encode import encode
from forge.s1.live import steps_data
from forge.s1.loss import choose
from forge.s1.train import on_device
from forge.system1.splits import unit_hash

VARIANTS = ("recorded", "two features swapped", "one number changed", "every length scaled")
SCALE_TEST_RANGE = (0.1, 10.0)
SEED = 0


def states(name: str, wanted: int, shards: int) -> dict[str, list[tuple[tuple, set, set]]]:
    """variant -> [(what a view is made of: plan, snapshot, valid commands, the two commands
    carried out before; the teacher's commands for it; the recorded commands)]
    for about `wanted` steps of the first `shards` shards of a slice (old or wide)."""
    from forge.freecad import wide_sessions
    folder = wide_sessions.OUT_DIR if name in wide_sessions.SLICES else OUT_DIR
    found = complete_shards(folder, (name,))[:shards]
    total = sum(stats["counts"].get("steps", 0) for stats, _ in found)
    share = min(1.0, wanted / max(1, total))
    made: dict[str, list] = {variant: [] for variant in VARIANTS}
    for _, path in found:
        for header, records, end in read_sessions(path):
            if not usable(end):
                continue
            for record in records:
                key = f"{SEED}:{header['session']}:{record['t']}"
                if unit_hash(f"cf-test:{key}") >= share:
                    continue
                rng = random.Random(f"cf-test:{key}")
                recorded = {target["command"] for target in record["target"]}
                plans = {"recorded": header["plan"],
                         "two features swapped": swap_two_features(header["plan"], rng),
                         "one number changed": change_one_number(header["plan"], rng)}
                low, high = SCALE_TEST_RANGE
                factor = math.exp(rng.uniform(math.log(low), math.log(high)))
                plans["every length scaled"] = scaled_plan(header["plan"], factor)
                for variant, plan in plans.items():
                    snapshot = scaled(record["snapshot"], factor) \
                        if variant == "every length scaled" else record["snapshot"]
                    advice = None if plan is None else relabel(plan, snapshot, record["valid"])
                    if advice is None:
                        continue
                    if variant == "recorded" and set(advice.names()) != recorded:
                        raise RuntimeError(f"the teacher does not give the recorded label: "
                                           f"{header['session']} t={record['t']}")
                    before = history_before(records, record["t"], 2)
                    made[variant].append(((plan, snapshot, record["valid"], before),
                                          set(advice.names()), recorded))
    return made


@torch.no_grad()
def choices(model: torch.nn.Module, seen: list[tuple], device: torch.device | str,
            batch_size: int = 256, random_ids: bool = False) -> list[str]:
    """The command the model picks in each view (row ids: identity, or one random draw).
    A third model gets its own encoding, with the previous commands; the first
    two get theirs. Only the COMMAND is compared here, for all three."""
    third = hasattr(model, "kind_logits")
    picked = []
    generator = torch.Generator().manual_seed(0) if random_ids else None
    for start in range(0, len(seen), batch_size):
        if third:
            from forge.s1.third.live import steps_data as third_data
            chunk = [encode(view(plan, snapshot, valid, before), third=True)
                     for plan, snapshot, valid, before in seen[start:start + batch_size]]
            own = torch.Generator().manual_seed(0) \
                if getattr(model, "trained_with_random_ids", False) else None
            batch = on_device(third_data(chunk, "cpu").batch(torch.arange(len(chunk)), own),
                              device)
            scores = model(batch)[0]
        else:
            chunk = [encode(view(plan, snapshot, valid))
                     for plan, snapshot, valid, _ in seen[start:start + batch_size]]
            batch = steps_data(chunk, device).batch(torch.arange(len(chunk)), generator)
            scores = model(batch)
        rows = choose(scores, batch["is_candidate"]).tolist()
        picked += [enc.candidates[row - (enc.n_rows - len(enc.candidates))]
                   for row, enc in zip(rows, chunk, strict=True)]
    return picked


def score(picked: list[str], lot: list[tuple[tuple, set, set]]) -> dict[str, int]:
    out = {"steps": len(lot), "right": 0, "changed": 0, "changed_right": 0,
           "changed_old_answer": 0, "unchanged": 0, "unchanged_right": 0}
    for pick, (_, now, recorded) in zip(picked, lot, strict=True):
        right = pick in now
        out["right"] += right
        if now != recorded:
            out["changed"] += 1
            out["changed_right"] += right
            out["changed_old_answer"] += pick in recorded and not right
        else:
            out["unchanged"] += 1
            out["unchanged_right"] += right
    return out


def ratio(a: int, b: int) -> str:
    return f"{a / b:.4f}" if b else "-"


def table(models: dict[str, torch.nn.Module], slices: list[str], steps: int, shards: int,
          device: torch.device | str, random_ids: tuple[str, ...] = ()) -> tuple[str, dict]:
    """The markdown table and the raw counts, for every model on the same states.
    `random_ids` names the models that get random row ids instead of the identity."""
    lines = [("| Slice | Plan | Model | Steps | Right | Answer changed | Right when changed | "
              "Still the old answer | Right when unchanged |"),
             "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    counts: dict = {}
    for name in slices:
        made = states(name, steps, shards)
        for variant in VARIANTS:
            for label, model in models.items():
                s = score(choices(model, [enc for enc, _, _ in made[variant]], device,
                                  random_ids=label in random_ids), made[variant])
                counts[f"{name}|{variant}|{label}"] = s
                lines.append(
                    f"| {name} | {variant} | {label} | {s['steps']} | "
                    f"{ratio(s['right'], s['steps'])} | {s['changed']} | "
                    f"{ratio(s['changed_right'], s['changed'])} | "
                    f"{ratio(s['changed_old_answer'], s['changed'])} | "
                    f"{ratio(s['unchanged_right'], s['unchanged'])} |")
    return "\n".join(lines) + "\n", counts


def main() -> None:
    from forge.s1.third.drive import load_any_model
    from forge.s1.train import pick_device
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", action="append", required=True,
                        help="label=file, or just a file; may be given several times")
    parser.add_argument("--slices", nargs="+", default=["iid_v2", "pairing_v2", "long_v2"])
    parser.add_argument("--steps", type=int, default=20000, help="about this many per slice")
    parser.add_argument("--shards", type=int, default=4, help="read the first so many shards")
    parser.add_argument("--device", default=None)
    parser.add_argument("--random-ids", nargs="*", default=[],
                        help="labels of the checkpoints that get random row ids at test")
    args = parser.parse_args()
    torch.set_num_threads(2)            # the laptop is shared: never all cores
    device = torch.device(args.device) if args.device else pick_device()
    run_dir = start_run("s1-counterfactual", vars(args))
    models = {}
    for text in args.checkpoint:
        label, _, file = text.rpartition("=")
        models[label or Path(file).stem] = load_any_model(file)[0].to(device)
    text, counts = table(models, args.slices, args.steps, args.shards, device,
                         tuple(args.random_ids))
    (run_dir / "table.md").write_text(
        f"Counterfactual plans on recorded test states (forge/s1/counterfactual.py), about "
        f"{args.steps} steps of the first {args.shards} shards of each slice. Checkpoints: "
        f"{args.checkpoint}.\n\n" + text)
    (run_dir / "counts.json").write_text(json.dumps(counts, indent=1) + "\n")
    for key, s in counts.items():
        log_metrics(run_dir, cell=key, **s)
    print(text + f"\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
