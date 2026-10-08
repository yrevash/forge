"""The engine: apply, undo, the rule check, and build."""

import pytest

from forge.generators.base import check
from forge.sandbox import Sandbox
from forge.system1 import engine
from forge.system1.engine import State, apply, build, execute, expected, resolve, undo
from forge.system1.steps import DONE, UNDO, Step, steps_of
from tests.system1_helpers import sample_parts

BLOCK = Step("block", {"length": 100.0, "width": 60.0, "height": 20.0})
HOLE = Step("hole", {"diameter": 6.0, "x": 20.0, "y": 10.0})


def started(*steps: Step) -> State:
    state = State([])
    for step in (BLOCK, *steps):
        assert apply(state, step) == "ok", engine.check(state, step)
    return state


def reason(state: State, kind: str, **slots) -> str | None:
    return engine.check(state, Step(kind, slots))


# --- state, apply, undo ---------------------------------------------------------------------

def test_an_empty_state_then_steps_then_undo():
    state = State([])
    assert (state.built, state.last, state.steps) == ([], "none", 0)
    assert apply(state, BLOCK) == "ok" and apply(state, HOLE) == "ok"
    assert [b.kind for b in state.built] == ["block", "hole"] and state.steps == 2
    assert undo(state) == "undone" and [b.kind for b in state.built] == ["block"]
    assert undo(state) == "undone" and undo(state) == "rejected"     # nothing left to undo
    assert state.built == [] and state.steps == 5
    assert execute(state, DONE) == "ok" and execute(state, UNDO) == "rejected"


def test_a_rejected_step_changes_nothing_but_the_outcome_and_the_count():
    state = started(HOLE)
    before = state.copy()
    assert apply(state, HOLE) == "rejected"          # the same hole again sits on itself
    assert state.built == before.built
    assert (state.last, state.steps) == ("rejected", before.steps + 1)


def test_undo_gives_back_the_room_a_feature_took():
    state = started(HOLE)
    assert apply(state, HOLE) == "rejected"
    undo(state)
    assert apply(state, HOLE) == "ok"


def test_used_flags_and_provenance():
    mentions = [100.0, 60.0, 20.0, 6.0, 20.0, 10.0]
    state = State(mentions)
    apply(state, resolve("block", {"length": 0, "width": 1, "height": 2}, mentions))
    assert state.used == [True, True, True, False, False, False]
    apply(state, resolve("hole", {"diameter": 3, "x": 4, "y": 5}, mentions))
    assert state.used == [True] * 6
    assert state.built[1].sources == {"diameter": 3, "x": 4, "y": 5}
    assert state.built[1].slots == {"diameter": 6.0, "x": 20.0, "y": 10.0}
    undo(state)
    assert state.used == [True, True, True, False, False, False]
    again = State.from_json(state.to_json(), mentions)
    assert again.to_json() == state.to_json() and again.built == state.built


def test_a_count_must_come_from_a_whole_number():
    mentions = [4.0, 2.5, 5.0, 30.0]
    assert resolve("polar", {"count": 0, "hole_diameter": 2, "circle_diameter": 3},
                   mentions).slots["count"] == 4
    half = resolve("polar", {"count": 1, "hole_diameter": 2, "circle_diameter": 3}, mentions)
    cylinder = State([])
    apply(cylinder, Step("cylinder", {"diameter": 80.0, "height": 20.0}))
    assert "whole number" in engine.check(cylinder, half)


# --- the rule check, one rule at a time ------------------------------------------------------

def test_order_rules():
    empty = State([])
    assert "start with a base" in reason(empty, "hole", diameter=6.0, x=0.0, y=0.0)
    assert "start with a base" in reason(empty, "top_chamfer", size=1.0)
    assert "already exists" in reason(started(), "block", length=50.0, width=40.0, height=10.0)
    assert reason(started(), "top_chamfer", size=1.0) is None
    assert "straight after the start" in reason(started(HOLE), "top_chamfer", size=1.0)
    assert "not something to build" in reason(started(), "undo")
    assert "needs exactly the slots" in reason(started(), "hole", diameter=6.0)


def test_sizes_must_be_positive_and_fit():
    state = started()
    assert "must be positive" in reason(state, "hole", diameter=0.0, x=0.0, y=0.0)
    assert "must be positive" in reason(State([]), "block", length=60.0, width=-40.0, height=10.0)
    assert "smaller than the outer" in reason(State([]), "ring", outer_diameter=40.0,
                                              inner_diameter=40.0, height=10.0)
    assert "too large" in reason(state, "top_chamfer", size=6.0)      # at most 20% of 20 mm
    assert "too deep" in reason(state, "blind_hole", diameter=6.0, depth=19.0, x=0.0, y=0.0)
    assert reason(state, "blind_hole", diameter=6.0, depth=18.0, x=0.0, y=0.0) is None
    assert "wider than its hole" in reason(state, "counterbore", hole_diameter=8.0, diameter=8.0,
                                           depth=3.0, x=0.0, y=0.0)
    assert "longer than it is wide" in reason(state, "slot", length=5.0, width=5.0, depth=2.0,
                                              angle=0.0, x=0.0, y=0.0)
    assert "along X" in reason(state, "slot", length=20.0, width=5.0, depth=2.0, angle=45.0,
                               x=0.0, y=0.0)
    assert "greater than zero" in reason(state, "hole_pair", diameter=6.0, x=0.0, y=0.0)


def test_a_feature_stays_on_the_base_and_clear_of_edges_and_other_features():
    state = started(HOLE)       # block 100 x 60, hole 6 at (20, 10)
    off = "off the base"
    assert off in reason(state, "hole", diameter=6.0, x=60.0, y=0.0)        # outside
    assert off in reason(state, "hole", diameter=6.0, x=46.0, y=0.0)        # 1 mm from the edge
    assert reason(state, "hole", diameter=6.0, x=45.0, y=0.0) is None       # exactly 2 mm: fine
    assert off in reason(state, "hole", diameter=4.0, x=26.0, y=10.0)       # 1 mm from the hole
    assert reason(state, "hole", diameter=4.0, x=27.0, y=10.0) is None      # 2 mm from the hole
    assert off in reason(state, "pad", length=10.0, width=10.0, height=5.0, x=20.0, y=10.0)
    assert "too close to each other" in reason(state, "hole_pair", diameter=6.0, x=3.0, y=-20.0)


def test_what_goes_on_which_base():
    block, ring = started(), State([])
    apply(ring, Step("ring", {"outer_diameter": 100.0, "inner_diameter": 60.0, "height": 10.0}))
    assert "does not take" in reason(block, "polar", count=4, hole_diameter=5.0,
                                     circle_diameter=30.0)
    assert "does not take" in reason(ring, "row", count=3, hole_diameter=5.0, spacing=12.0, y=0.0)
    assert "does not take" in reason(ring, "shell", wall_thickness=2.0)
    assert reason(ring, "polar", count=4, hole_diameter=5.0, circle_diameter=80.0) is None
    assert "pitch circle" in reason(ring, "polar", count=4, hole_diameter=5.0,
                                    circle_diameter=64.0)                  # too near the bore
    assert "spacing" in reason(block, "row", count=5, hole_diameter=6.0, spacing=40.0, y=0.0)
    shelled = started(Step("shell", {"wall_thickness": 3.0}))
    assert "no cuts" in reason(shelled, "hole", diameter=6.0, x=0.0, y=0.0)
    assert reason(shelled, "boss", diameter=10.0, height=5.0, x=0.0, y=0.0) is None


# --- the rule check never refuses a real part, and build writes the real program -------------

def test_every_step_of_real_parts_is_accepted_and_the_program_is_the_generators():
    parts = sample_parts(500) + sample_parts(100, features=(6, 12))
    for part in parts:
        state = State([])
        for step in steps_of(part.family, part.params):
            assert apply(state, step) == "ok", (part.params, step, engine.check(state, step))
        assert build(state) == part.code
        made = expected(state)
        assert made.params == part.params
        assert made.expected_volume == part.expected_volume
        assert made.expected_bbox == part.expected_bbox
        assert made.expected_cylinders == part.expected_cylinders


def test_a_detour_through_a_wrong_step_and_undo_ends_at_the_same_program():
    for part in sample_parts(50, seed="detour"):
        state = State([])
        for step in steps_of(part.family, part.params):
            apply(state, step)
            extra = Step("boss", {"diameter": 5.0, "height": 3.0, "x": 0.0, "y": 0.0})
            if apply(state, extra) == "ok":      # a wrong step that fits: take it back
                undo(state)
        assert build(state) == part.code


def test_nothing_built_cannot_be_built():
    with pytest.raises(ValueError, match="nothing is built"):
        build(State([]))


def test_states_built_step_by_step_measure_right_on_the_kernel():
    """Every intermediate state, not only the last, is a valid solid of the expected size."""
    parts = sample_parts(2, seed="kernel") + sample_parts(1, seed="kernel", features=(8, 8))[:2]
    with Sandbox(timeout=30) as sandbox:
        for part in parts:
            state = State([])
            for step in steps_of(part.family, part.params):
                assert apply(state, step) == "ok"
                reply = sandbox.run(build(state))
                assert reply["status"] == "ok", (part.params, reply)
                assert check(expected(state), reply["measure"]) == [], (part.params, step)
            assert build(state) == part.code
