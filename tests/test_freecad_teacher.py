"""The command-level teacher (forge/freecad/teacher.py).

The first tests need no FreeCAD: they hand the teacher snapshots written by hand.
The rest drive a real headless FreeCAD and are skipped when it is not installed.
"""

import copy
import random

import pytest

from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean
from forge.freecad.locate import freecad_available
from forge.freecad.parts import sample_rows
from forge.freecad.play import end_check
from forge.freecad.recipes import context_of, flatten, recipe
from forge.freecad.teacher import (
    clean_length,
    plan_from_json,
    plan_json,
    script_of,
    teacher,
)
from forge.freecad.valid import EMPTY_SNAPSHOT
from forge.system1.steps import Step, steps_of

needs_freecad = pytest.mark.skipif(not freecad_available(), reason="FreeCAD is not installed")

BLOCK = [Step("block", {"length": 80.0, "width": 40.0, "height": 10.0}),
         Step("corner_radius", {"radius": 4.0}),
         Step("hole", {"diameter": 6.0, "x": 10.0, "y": -5.0})]
RING = [Step("ring", {"outer_diameter": 60.0, "inner_diameter": 30.0, "height": 8.0}),
        Step("polar", {"count": 4, "hole_diameter": 4.0, "circle_diameter": 45.0})]


def names(plan, snapshot) -> list[str]:
    return teacher(plan, snapshot).names()


def session(**changes) -> dict:
    """A hand-written snapshot: an empty document unless told otherwise."""
    snapshot = copy.deepcopy(EMPTY_SNAPSHOT)
    snapshot["session"]["document"] = True
    snapshot["session"].update(changes.pop("session", {}))
    snapshot.update(changes)
    return snapshot


BODY = {"type": "body", "name": "Body", "tip": None, "active": True, "valid": True}


def sketch(name="Sketch", plane="XY", offset=0.0, shapes=(), used_by=None) -> dict:
    return {"type": "sketch", "name": name, "body": "Body", "plane": plane, "offset": offset,
            "shapes": list(shapes), "dof": 0, "closed": True, "used_by": used_by, "valid": True}


# --- without FreeCAD: hand-written snapshots ---------------------------------------------------

def test_the_script_is_every_recipe_in_plan_order():
    script = script_of(BLOCK)
    assert [index for index, _ in script] == sorted(index for index, _ in script)
    assert {index for index, _ in script} == {0, 1, 2}
    # block: 5 + 4 dimensions + 2; corner radius: 2; hole: 3 + 3 dimensions + 2; done: 1
    assert clean_length(BLOCK) == 11 + 2 + 8 + 1


def test_a_plan_survives_json():
    assert plan_from_json(plan_json(BLOCK)) == BLOCK


def test_no_document_then_no_body_then_the_plane():
    assert names(BLOCK, EMPTY_SNAPSHOT) == ["new_document"]
    assert names(BLOCK, session()) == ["new_body"]
    with_body = session(items=[BODY], session={"active_body": "Body", "undo_depth": 2})
    advice = teacher(BLOCK, with_body)
    assert advice.names() == ["select_plane"] and advice.targets[0].args == {"plane": "XY"}
    assert (advice.on_plan, advice.built, advice.active) == (True, 0, 0)


def test_a_wrong_selection_is_replaced_not_undone():
    base = {"active_body": "Body", "undo_depth": 3}
    right = session(items=[BODY], session={**base, "selection": {"type": "plane", "name": "XY"}})
    wrong = session(items=[BODY], session={**base, "selection": {"type": "plane", "name": "YZ"}})
    assert names(BLOCK, right) == ["new_sketch"]
    assert teacher(BLOCK, right).targets[0].args == {"offset": 0.0}
    assert names(BLOCK, wrong) == ["select_plane"]


def test_dimensions_in_any_order_and_only_the_missing_ones():
    shape = {"shape": "rectangle", "first": 0, "x": 0.0, "y": 0.0, "length": 80.0, "width": 10.0,
             "fixed": ["length", "y"]}
    state = session(items=[BODY, sketch(shapes=[shape])],
                    session={"active_body": "Body", "open_sketch": "Sketch", "undo_depth": 7})
    advice = teacher(BLOCK, state)
    assert sorted(advice.names()) == ["constrain_width", "constrain_x"]
    assert {t.command: t.args["value"] for t in advice.targets} == {"constrain_width": 40.0,
                                                                    "constrain_x": 0.0}
    assert {t.command: t.sources["value"] for t in advice.targets} == {
        "constrain_width": "slot:width", "constrain_x": "const"}


def test_a_wrong_number_means_undo_and_new_document_when_nothing_can_be_undone():
    shape = {"shape": "rectangle", "first": 0, "x": 0.0, "y": 0.0, "length": 81.0, "width": 10.0,
             "fixed": ["length"]}
    for depth, repair in ((6, "undo"), (0, "new_document")):
        state = session(items=[BODY, sketch(shapes=[shape])],
                        session={"active_body": "Body", "open_sketch": "Sketch",
                                 "undo_depth": depth})
        advice = teacher(BLOCK, state)
        assert advice.names() == [repair] and not advice.on_plan and advice.built == 0
        assert "length 81" in advice.why


@pytest.mark.parametrize("stray", [
    sketch(plane="YZ"),                                         # a sketch on the wrong plane
    sketch(offset=3.0),                                         # at the wrong height
    sketch(shapes=[{"shape": "circle", "first": 0, "x": 0.0, "y": 0.0, "diameter": 10.0,
                    "fixed": []}]),                             # the wrong shape
    {"type": "body", "name": "Body001", "tip": None, "active": False, "valid": True},
])
def test_anything_the_plan_does_not_have_means_undo(stray):
    state = session(items=[BODY, stray], session={"active_body": "Body", "undo_depth": 4})
    assert names(BLOCK, state) == ["undo"]


def test_an_unfinished_sketch_that_was_closed_is_opened_again():
    shape = {"shape": "rectangle", "first": 0, "x": 0.0, "y": 0.0, "length": 80.0, "width": 10.0,
             "fixed": ["length"]}
    closed = {"active_body": "Body", "undo_depth": 6}
    state = session(items=[BODY, sketch(shapes=[shape])], session=closed)
    assert names(BLOCK, state) == ["select_sketch"]
    state["session"]["selection"] = {"type": "sketch", "name": "Sketch"}
    assert names(BLOCK, state) == ["edit_sketch"]


# --- with FreeCAD ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fc():
    with FreeCADClient() as client:
        yield client


def follow(fc, plan, reply=None, stop_before: str | None = None, rng=None) -> dict:
    """Do what the teacher says until it has nothing to say (or is about to say `stop_before`)."""
    reply = reply or fc.reset()
    for _ in range(3 * clean_length(plan)):
        advice = teacher(plan, lean(reply["snapshot"]))
        if not advice.targets or advice.names() == [stop_before]:
            return reply
        assert set(advice.names()) <= set(reply["valid"])
        target = (rng or random).choice(advice.targets)
        reply = fc.command(target.command, **target.args)
        assert reply["status"] == "ok", (target, reply.get("reason"))
    raise AssertionError("the teacher did not finish")


def do(fc, *commands) -> dict:
    reply = None
    for command in commands:
        name, args = (command, {}) if isinstance(command, str) else command
        reply = fc.command(name, **args)
        assert reply["status"] == "ok", (name, reply.get("reason"))
    return reply


def advice_now(fc, plan):
    return teacher(plan, lean(fc.snapshot()))


@needs_freecad
def test_following_any_member_of_the_set_builds_stored_parts(fc):
    for row in sample_rows(per_base=2, long_per_base=1, seed=5):
        plan = steps_of(row["family"], row["params"])
        reply = follow(fc, plan, rng=random.Random(row["id"]))
        assert end_check(plan, reply["snapshot"], row["measured"]) == []
        # Exactly the clean number of commands (`new_document` cannot be undone, so minus one).
        assert reply["snapshot"]["session"]["undo_depth"] == clean_length(plan) - 1


@needs_freecad
def test_the_teacher_says_the_same_on_the_full_and_the_lean_snapshot(fc):
    reply = fc.reset()
    for _ in range(clean_length(RING)):
        full, short = teacher(RING, reply["snapshot"]), teacher(RING, lean(reply["snapshot"]))
        assert full == short
        reply = fc.command(full.targets[0].command, **full.targets[0].args)
    assert reply["snapshot"]["session"]["finished"]
    assert teacher(RING, reply["snapshot"]).targets == ()


@needs_freecad
def test_every_argument_comes_from_the_recipe_of_its_plan_item(fc):
    reply = fc.reset()
    context = context_of(BLOCK)
    for _ in range(clean_length(BLOCK)):
        target = teacher(BLOCK, lean(reply["snapshot"])).targets[0]
        if target.item is not None:
            step = BLOCK[target.item]
            assert any(c.name == target.command and c.args == target.args
                       and c.sources == target.sources for c in flatten(recipe(step, context)))
            for arg, source in target.sources.items():
                if source.startswith("slot:"):
                    assert target.args[arg] == step.slots[source.removeprefix("slot:")]
        reply = fc.command(target.command, **target.args)


@needs_freecad
def test_a_wrong_feature_is_undone_and_the_part_still_gets_built(fc):
    reply = follow(fc, BLOCK, stop_before="select_edges")       # the base is built
    do(fc, ("select_edges", {"rule": "top_face"}), ("fillet", {"radius": 4.0}))
    advice = advice_now(fc, BLOCK)      # right radius, wrong edges: the plan rounds the corners
    assert advice.names() == ["undo"] and advice.built == 1 and "vertical" in advice.why
    reply = follow(fc, BLOCK)
    assert reply["snapshot"]["session"]["finished"]
    assert [item["type"] for item in reply["snapshot"]["items"]] == [
        "body", "sketch", "pad", "fillet", "sketch", "pocket"]


@needs_freecad
def test_a_feature_that_failed_to_build_is_undone(fc):
    follow(fc, BLOCK, stop_before="select_edges")
    reply = do(fc, ("select_edges", {"rule": "vertical"}), ("fillet", {"radius": 500.0}))
    assert not reply["snapshot"]["items"][-1]["valid"]
    assert advice_now(fc, BLOCK).names() == ["undo"]


@needs_freecad
def test_a_second_body_and_an_early_done_are_undone(fc):
    follow(fc, BLOCK, stop_before="select_edges")
    do(fc, "new_body")
    assert advice_now(fc, BLOCK).names() == ["undo"]
    do(fc, "undo", "done")
    advice = advice_now(fc, BLOCK)
    assert advice.names() == ["undo"] and "finished too early" in advice.why


@needs_freecad
def test_sketch_left_too_early_or_opened_again(fc):
    reply = follow(fc, BLOCK, stop_before="leave_sketch")       # all four dimensions are given
    do(fc, "undo")                                              # one dimension is missing again
    do(fc, "leave_sketch")
    assert advice_now(fc, BLOCK).names() == ["edit_sketch"]
    do(fc, ("select_plane", {"plane": "XZ"}))
    assert advice_now(fc, BLOCK).names() == ["select_sketch"]
    reply = follow(fc, BLOCK, reply=do(fc, "select_sketch"), stop_before="pad")
    assert reply["snapshot"]["session"]["selection"] == {"type": "sketch", "name": "Sketch"}
    do(fc, "edit_sketch")               # a complete sketch opened for no reason: leave it
    assert advice_now(fc, BLOCK).names() == ["leave_sketch"]
    do(fc, "leave_sketch", "clear_selection")
    assert advice_now(fc, BLOCK).names() == ["select_sketch"]


@needs_freecad
def test_the_selection_is_cleared_before_done(fc):
    follow(fc, BLOCK, stop_before="done")
    do(fc, ("select_edges", {"rule": "all"}))
    assert advice_now(fc, BLOCK).names() == ["clear_selection"]
    do(fc, "clear_selection")
    assert advice_now(fc, BLOCK).names() == ["done"]
    do(fc, "done")
    assert advice_now(fc, BLOCK).targets == ()


@needs_freecad
def test_an_opened_document_is_continued_if_on_plan_and_replaced_if_not(fc):
    # On plan, with no undo history: carry on.
    follow(fc, RING, stop_before="pad")
    reply = fc.forget_undo()
    assert reply["snapshot"]["session"]["undo_depth"] == 0 and "undo" not in reply["valid"]
    assert advice_now(fc, RING).names() == ["pad"]
    assert follow(fc, RING, reply=reply)["snapshot"]["session"]["finished"]
    # Off plan, with no undo history: the only way out is a new document.
    fc.reset()
    do(fc, "new_document", "new_body", ("select_plane", {"plane": "YZ"}),
       ("new_sketch", {"offset": 7.0}), "sketch_slot")
    reply = fc.forget_undo()
    advice = teacher(RING, lean(reply["snapshot"]))
    assert advice.names() == ["new_document"] and not advice.on_plan
    assert follow(fc, RING, reply=reply)["snapshot"]["session"]["finished"]
