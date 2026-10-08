"""The third model's loss and step accuracy.

Shapes: B steps, R rows, Q binding queries (data.py), P plan rows, A arguments, K kinds.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F

from forge.s1.loss import choose


def binding_loss(item_scores: torch.Tensor, kind_logits: torch.Tensor, item: torch.Tensor,
                 kinds: torch.Tensor, step: torch.Tensor, n_steps: int) -> torch.Tensor:
    """Returns [B]: the binding loss of every step.

    item_scores [Q, P]     kind_logits [Q, A, K]
    item [Q] the teacher's plan item     kinds [Q, A] the teacher's kinds, 0 = no argument
    step [Q] which step a query is in

    One query:  -log p(item)  +  the mean over its arguments of  -log p(kind of argument).
    One step:   the mean over its queries (0 when it has none: `undo` needs no binding).
    """
    out = torch.zeros(n_steps, device=item.device)
    if len(item) == 0:
        return out
    of_item = F.cross_entropy(item_scores, item, reduction="none")                  # [Q]
    has = kinds > 0                                                                 # [Q, A]
    of_kind = F.cross_entropy(kind_logits.flatten(0, 1), kinds.flatten(), reduction="none")
    of_kind = (of_kind.view_as(kinds) * has).sum(dim=1) / has.sum(dim=1).clamp(min=1)
    queries = torch.zeros(n_steps, device=item.device).index_add_(
        0, step, torch.ones_like(of_item))
    return out.index_add_(0, step, of_item + of_kind) / queries.clamp(min=1)


def binding_is_right(item_scores: torch.Tensor, kind_logits_at_chosen_item: torch.Tensor,
                     item: torch.Tensor, kinds: torch.Tensor, needs_item: torch.Tensor,
                     ) -> torch.Tensor:
    """[Q] bool: are the kinds of every argument the teacher's, and, when one
    of them reads a plan item, is the item the teacher's?

    kind_logits_at_chosen_item  [Q, A, K]: the kinds given the item the MODEL pointed at
    needs_item  [K] bool: does this kind read the pointed item (a slot, not a constant)?
    """
    kinds_right = ((kind_logits_at_chosen_item.argmax(dim=-1) == kinds) | (kinds == 0)).all(dim=1)
    item_right = (item_scores.argmax(dim=1) == item) | ~needs_item[kinds].any(dim=1)
    return kinds_right & item_right


def step_is_right(scores: torch.Tensor, is_candidate: torch.Tensor, is_target: torch.Tensor,
                  query_right: torch.Tensor, step: torch.Tensor, row: torch.Tensor,
                  ) -> tuple[torch.Tensor, torch.Tensor]:
    """([B] the command is accepted, [B] the command AND its binding are right).

    A step is fully right when the highest-scoring command is one the teacher accepts and,
    if that command has a binding query, the model's binding for it is the teacher's."""
    chosen = choose(scores, is_candidate)                                           # [B] a row
    command_right = is_target.gather(1, chosen[:, None])[:, 0]
    spoiled = torch.zeros(len(chosen), dtype=torch.bool, device=chosen.device)
    wrong = (row == chosen[step]) & ~query_right        # the chosen command, bound wrongly
    spoiled[step[wrong]] = True
    return command_right, command_right & ~spoiled
