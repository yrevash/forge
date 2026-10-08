"""The teacher: one correct answer for any state."""

from forge.system1.engine import State, apply, resolve, undo
from forge.system1.mentions import prompt_and_plan
from forge.system1.steps import DONE, UNDO, Step
from forge.system1.teacher import matches, on_plan, teacher
from tests.system1_helpers import sample_parts

PARAMS = {"base_length": 100.0, "base_width": 60.0, "base_height": 20.0, "base_top_chamfer": 1.0,
          "hole_1_diameter": 6.0, "hole_1_x": 20.0, "hole_1_y": 10.0,
          "boss_2_diameter": 12.0, "boss_2_height": 8.0, "boss_2_x": -20.0, "boss_2_y": 0.0}


def setup():
    _, mentions, plan = prompt_and_plan("composed_block", PARAMS, "teacher-test", "spec")
    return plan, [m["value"] for m in mentions]


def test_a_clean_walk_is_start_treatment_features_in_order_then_done():
    plan, values = setup()
    state = State(values)
    said = []
    while (step := teacher(plan, state)) != DONE:
        said.append(step.kind)
        assert apply(state, step) == "ok"
    assert said == ["block", "top_chamfer", "hole", "boss"]
    assert teacher(plan, state) == DONE


def test_the_answer_depends_only_on_the_plan_and_the_state():
    """Two different histories that reach the same state get the same answer."""
    plan, values = setup()
    straight, roundabout = State(values), State(values)
    for step in plan[:3]:
        apply(straight, step)
        apply(roundabout, step)
    apply(roundabout, plan[3])
    undo(roundabout)
    assert straight.built == roundabout.built
    assert teacher(plan, straight) == teacher(plan, roundabout) == plan[3]


def test_a_wrong_last_item_is_undone():
    plan, values = setup()
    state = State(values)
    apply(state, plan[0])
    apply(state, plan[1])
    hole = plan[2]
    wrong_kind = resolve("boss", {**hole.sources, "height": hole.sources["diameter"]}, values)
    assert apply(state, wrong_kind) == "ok"
    assert teacher(plan, state) == UNDO
    undo(state)
    swapped = resolve("hole", {**hole.sources, "x": hole.sources["y"], "y": hole.sources["x"]},
                      values)
    assert apply(state, swapped) == "ok" and teacher(plan, state) == UNDO
    undo(state)
    assert apply(state, plan[3]) == "ok"            # a later item built early
    assert teacher(plan, state) == UNDO
    undo(state)
    assert teacher(plan, state) == plan[2]


def test_a_wrong_item_under_a_right_one_is_still_undone_all_the_way_down():
    """Rule 1 is worded about the LAST item; a buried wrong item must not slip by."""
    plan, values = setup()
    state = State(values)
    apply(state, plan[0])                       # block
    pad = Step("pad", {"length": 10.0, "width": 8.0, "height": 4.0, "x": -30.0, "y": -15.0})
    assert apply(state, pad) == "ok"            # wrong: the plan has the chamfer here
    assert apply(state, plan[2]) == "ok"        # the hole, which IS what the plan has third
    assert matches(state.built[2], plan[2])
    # Position 2 now holds exactly what the plan has at position 2; position 1 is wrong.
    assert not on_plan(plan, state)
    answers = []
    while teacher(plan, state) == UNDO:
        answers.append("undo")
        undo(state)
    assert answers == ["undo", "undo"] and [b.kind for b in state.built] == ["block"]
    assert teacher(plan, state) == plan[1]


def test_something_built_after_the_plan_is_complete_is_undone():
    plan, values = setup()
    state = State(values)
    for step in plan:
        apply(state, step)
    extra = resolve("hole", {"diameter": plan[2].sources["diameter"],
                             "x": plan[3].sources["y"], "y": plan[3].sources["y"]}, values)
    assert apply(state, extra) == "ok"
    assert teacher(plan, state) == UNDO
    undo(state)
    assert teacher(plan, state) == DONE


def test_the_same_value_from_another_mention_is_not_a_mistake():
    """A square block built with its two equal numbers swapped is the right block."""
    params = {"base_length": 40.0, "base_width": 40.0, "base_height": 10.0,
              "hole_1_diameter": 5.0, "hole_1_x": 0.0, "hole_1_y": 0.0}
    _, mentions, plan = prompt_and_plan("composed_block", params, "square", "request-0")
    values = [m["value"] for m in mentions]
    start = plan[0]
    crossed = resolve("block", {**start.sources, "length": start.sources["width"],
                                "width": start.sources["length"]}, values)
    state = State(values)
    assert apply(state, crossed) == "ok"
    assert teacher(plan, state) == plan[1]


def test_after_a_rejected_step_the_answer_is_unchanged():
    plan, values = setup()
    state = State(values)
    apply(state, plan[0])
    before = teacher(plan, state)
    assert apply(state, plan[0]) == "rejected"
    assert teacher(plan, state) == before == plan[1]


def test_following_the_teacher_builds_every_sampled_part():
    for part in sample_parts(150, seed="teacher"):
        _, mentions, plan = prompt_and_plan(part.family, part.params, part.id, "compact-0")
        state = State([m["value"] for m in mentions])
        for _ in range(len(plan)):
            assert apply(state, teacher(plan, state)) == "ok"
        assert teacher(plan, state) == DONE and on_plan(plan, state)
