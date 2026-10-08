"""Train the third model. The loop is forge/s1/train.py; this file supplies what differs.

    uv run python -m forge.s1.third.train --config configs/s1_third.yaml
    uv run python -m forge.s1.third.train --resume runs/<run>/last.pt

    loss of a step = set loss of the command + binding loss

In training the binding head is asked about the TEACHER's commands and is given the
teacher's plan item when it names the kinds (teacher forcing). In `evaluate` it is given
the item it pointed at itself, as when it drives FreeCAD.

"all steps" in the evaluation, which picks best.pt, is the strict measure: the command AND
its binding are right. "command only" is the first two models' measure, beside it.
"""

from __future__ import annotations

import torch

from forge.s1.loss import accuracy_by_group, set_loss
from forge.s1.third.data import BindData
from forge.s1.third.loss import binding_is_right, binding_loss, step_is_right
from forge.s1.third.model import build_model
from forge.s1.train import Kit, main, on_device


def losses(model: torch.nn.Module, batch: dict, use_amp: bool = False,
           ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """(command loss [B], binding loss [B], command right [B], command and binding right [B])."""
    with torch.autocast("cuda", dtype=torch.float16, enabled=use_amp):
        scores, h = model(batch)
        step, row, item = batch["bind_step"], batch["bind_row"], batch["bind_item"]
        items = model.item_scores(h, step, row, batch["n_plan"])                # [Q, P]
        kinds = model.kind_logits(h, step, row, item)                           # [Q, A, K]
    command = set_loss(scores, batch["is_candidate"], batch["is_target"])
    binding = binding_loss(items, kinds, item, batch["bind_kinds"], step, batch["n_steps"])
    with torch.no_grad():
        own = model.kind_logits(h, step, row, items.argmax(dim=1)) if len(item) else kinds
        right = binding_is_right(items, own, item, batch["bind_kinds"], model.kind_needs_item)
        command_right, both_right = step_is_right(
            scores, batch["is_candidate"], batch["is_target"], right, step, row)
    return command, binding, command_right, both_right


@torch.no_grad()
def evaluate(model: torch.nn.Module, data: BindData, groups: dict[str, int],
             batch_size: int = 1024, random_ids: int | None = None) -> dict[str, dict]:
    """Accuracy by group (command and binding) and the two mean losses on every step."""
    model.eval()
    device = next(model.parameters()).device
    parts: dict[str, list] = {"command": [], "binding": [], "right": [], "both": [], "bits": []}
    # Row ids: the identity, or (for a model trained with random ids) one seeded random draw.
    generator = None if random_ids is None else torch.Generator().manual_seed(random_ids)
    for start in range(0, len(data), batch_size):
        batch = on_device(data.batch(torch.arange(start, min(start + batch_size, len(data))),
                                     generator), device)
        command, binding, right, both = losses(model, batch)
        for name, value in zip(parts, (command, binding, right, both, batch["group"]),
                               strict=True):
            parts[name].append(value)
    model.train()
    joined = {name: torch.cat(values) for name, values in parts.items()}
    table = accuracy_by_group(joined["both"], joined["bits"], groups)
    names = accuracy_by_group(joined["right"], joined["bits"], groups)
    return {"loss": float((joined["command"] + joined["binding"]).mean()),
            "command_loss": float(joined["command"].mean()),
            "binding_loss": float(joined["binding"].mean()),
            "accuracy": {name: (ok / n if n else None) for name, (ok, n) in table.items()},
            "command_only": {name: (ok / n if n else None) for name, (ok, n) in names.items()},
            "steps": {name: n for name, (_, n) in table.items()}}


class ThirdKit(Kit):
    data = BindData
    build_model = staticmethod(build_model)
    evaluate = staticmethod(evaluate)

    @staticmethod
    def loss(model: torch.nn.Module, batch: dict, use_amp: bool) -> torch.Tensor:
        command, binding, _, _ = losses(model, batch, use_amp)
        return (command + binding).mean()


if __name__ == "__main__":
    main(ThirdKit)
