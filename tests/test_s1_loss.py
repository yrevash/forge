"""The Forge-S1 formulas against hand computations."""

import math

import torch
import torch.nn.functional as F

from forge.s1.loss import (
    accuracy_by_group,
    choose,
    chosen_is_right,
    closed_loop_success,
    set_loss,
    wilson_interval,
)


def mask(*rows: str) -> torch.Tensor:
    """"1100" -> [True, True, False, False]; one string per step."""
    return torch.tensor([[c == "1" for c in row] for row in rows])


def test_set_loss_hand_computation():
    # Four candidates with the same score: each has probability 1/4. Two are accepted, so
    # the mass on the set is 1/2 and the loss is -log(1/2) = log 2.
    loss = set_loss(torch.zeros(1, 4), mask("1111"), mask("1100"))
    assert loss.item() == torch.tensor(math.log(2)).item()


def test_set_loss_second_hand_computation():
    # Scores log 1, log 2, log 3, log 4: probabilities 0.1, 0.2, 0.3, 0.4. Targets are the
    # first and the third: mass 0.4, loss -log 0.4.
    scores = torch.tensor([[1.0, 2.0, 3.0, 4.0]]).log()
    loss = set_loss(scores, mask("1111"), mask("1010"))
    assert math.isclose(loss.item(), -math.log(0.4), rel_tol=1e-6)


def test_one_target_is_cross_entropy():
    torch.manual_seed(0)
    scores = torch.randn(8, 5)
    target = torch.randint(0, 5, (8,))
    ours = set_loss(scores, torch.ones(8, 5, dtype=torch.bool),
                    F.one_hot(target, 5).bool())
    assert torch.allclose(ours, F.cross_entropy(scores, target, reduction="none"), atol=1e-6)


def test_rows_that_are_not_candidates_do_not_count():
    # Rows 0 and 1 are not candidates (a plan row, a padding row). Whatever they score,
    # the loss is that of the three candidates alone.
    candidates, targets = mask("00111"), mask("00100")
    quiet = set_loss(torch.tensor([[0.0, 0.0, 1.0, 2.0, 3.0]]), candidates, targets)
    loud = set_loss(torch.tensor([[99.0, -99.0, 1.0, 2.0, 3.0]]), candidates, targets)
    alone = F.cross_entropy(torch.tensor([[1.0, 2.0, 3.0]]), torch.tensor([0]))
    assert torch.allclose(quiet, loud) and math.isclose(quiet.item(), alone.item(), rel_tol=1e-6)


def test_all_mass_on_the_set_gives_zero():
    scores = torch.tensor([[30.0, 30.0, -30.0, -30.0]])
    assert set_loss(scores, mask("1111"), mask("1100")).item() < 1e-6


def test_loss_is_computed_in_full_precision():
    half = torch.tensor([[0.0, 0.0, 0.0, 0.0]], dtype=torch.float16)
    assert set_loss(half, mask("1111"), mask("1100")).dtype == torch.float32


def test_accuracy_by_group_hand_count():
    # Step 0: best candidate is accepted. Step 1: not. Step 2: accepted. Step 3: a tie
    # between rows 1 and 2; the first one (row 1) is chosen, and it is not accepted.
    scores = torch.tensor([[0.0, 5.0, 1.0], [0.0, 5.0, 1.0], [9.0, 1.0, 2.0], [9.0, 3.0, 3.0]])
    candidates = mask("011", "011", "111", "011")
    targets = mask("010", "001", "100", "001")
    assert choose(scores, candidates).tolist() == [1, 1, 0, 1]
    right = chosen_is_right(scores, candidates, targets)
    assert right.tolist() == [True, False, True, False]
    groups = torch.tensor([1, 1, 2, 3])                 # bit 1 = group "a", bit 2 = group "b"
    table = accuracy_by_group(right, groups, {"a": 1, "b": 2})
    assert table == {"all steps": (2, 4), "a": (1, 3), "b": (1, 2)}


def test_wilson_interval_hand_computation():
    low, high = wilson_interval(8, 10)
    assert round(low, 3) == 0.490 and round(high, 3) == 0.943
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_closed_loop_success_counts_unfinished_parts_as_failures():
    # three parts: built right, never reached `done` (False), finished but wrong (False)
    assert closed_loop_success([True, False, False]) == 1 / 3
    assert closed_loop_success([]) == 0.0
