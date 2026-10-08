"""The loss and the accuracy of Forge-S1.

The model gives one score per row. Only candidate rows compete; the teacher accepts a SET
of them. Shapes below: B steps in the batch, R rows per step (padded).
"""

from __future__ import annotations

import math

import torch


def set_loss(scores: torch.Tensor, is_candidate: torch.Tensor, is_target: torch.Tensor,
             ) -> torch.Tensor:
    """L = -log( sum of softmax probability over the accepted candidates ).

    scores        [B, R] float   one score per row
    is_candidate  [B, R] bool    the rows that are valid commands (V)
    is_target     [B, R] bool    the candidates the teacher accepts (T, a subset of V)
    returns       [B]    float   the loss of every step

    log p(T) = logsumexp over T  -  logsumexp over V. A row outside the set gets the score
    -infinity first, and exp(-infinity) = 0, so it adds nothing to the sum.
    """
    scores = scores.float()             # full precision here, even under mixed precision
    minus_infinity = torch.finfo(scores.dtype).min
    over_valid = torch.logsumexp(scores.masked_fill(~is_candidate, minus_infinity), dim=1)
    over_target = torch.logsumexp(scores.masked_fill(~is_target, minus_infinity), dim=1)
    return over_valid - over_target


def chosen_is_right(scores: torch.Tensor, is_candidate: torch.Tensor, is_target: torch.Tensor,
                    ) -> torch.Tensor:
    """[B] bool, is the highest-scoring candidate one the teacher accepts?"""
    chosen = choose(scores, is_candidate)
    return is_target.gather(1, chosen[:, None])[:, 0]


def choose(scores: torch.Tensor, is_candidate: torch.Tensor) -> torch.Tensor:
    """[B] the row of the highest-scoring candidate (the first one, if several tie)."""
    scores = scores.float()
    return scores.masked_fill(~is_candidate, torch.finfo(scores.dtype).min).argmax(dim=1)


def accuracy_by_group(right: torch.Tensor, group_bits: torch.Tensor, groups: dict[str, int],
                      ) -> dict[str, tuple[int, int]]:
    """group name -> (steps answered right, steps) for "all steps" and each group bit."""
    table = {"all steps": (int(right.sum()), int(right.numel()))}
    for name, bit in groups.items():
        inside = (group_bits & bit) > 0
        table[name] = (int((right & inside).sum()), int(inside.sum()))
    return table


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """the 95% Wilson interval of a success rate (sound for small n)."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def closed_loop_success(outcomes: list[bool]) -> float:
    """the share of parts finished correctly. An outcome is True only when
    the session reached `done` and the document is the stored solid."""
    return sum(outcomes) / len(outcomes) if outcomes else 0.0
