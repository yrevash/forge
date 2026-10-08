"""The parts of forge/freecad that need no FreeCAD: catalogue, valid commands, recipes."""

import random

import pytest

from forge.freecad.catalogue import COMMANDS, DIMENSION_COMMANDS, SHAPE_COMMANDS, check_args
from forge.freecad.probes import probes_of
from forge.freecad.recipes import AnyOrder, Cmd, context_of, flatten, orders, recipe
from forge.freecad.valid import EMPTY_SNAPSHOT, valid_commands, why_not
from forge.system1.steps import FEATURES, STARTS, TREATMENTS, Step, steps_of
from tests.system1_helpers import sample_parts


def snapshot(items=(), solid=None, **session) -> dict:
    """A hand-made snapshot: a document with a body, plus whatever the test sets."""
    base = {"document": True, "active_body": "Body", "tip": None, "open_sketch": None,
            "selection": None, "undo_depth": 3, "finished": False}
    return {"items": list(items), "session": {**base, **session}, "solid": solid}


def sketch(name="Sketch", shapes=(), closed=True, used_by=None, circle=False) -> dict:
    geometry = [{"kind": "circle", "construction": False}] if circle else []
    return {"type": "sketch", "name": name, "body": "Body", "shapes": list(shapes),
            "geometry": geometry, "closed": closed, "used_by": used_by, "valid": True}


SOLID = {"volume": 1.0, "bbox": [0] * 6, "size": [1, 1, 1], "solids": 1, "valid": True}


# --- catalogue -----------------------------------------------------------------------------

def test_every_command_has_a_group_help_and_typed_arguments():
    for name, command in COMMANDS.items():
        assert command["help"], name
        assert command["group"] in ("document", "select", "sketch", "feature", "dressup",
                                    "pattern", "control")
        for kind in command["args"].values():
            assert kind in ("mm", "pos", "deg", "count") or isinstance(kind, tuple)


@pytest.mark.parametrize(("command", "args", "ok"), [
    ("pad", {"length": 10.0}, True),
    ("pad", {"length": 0.0}, False),            # a size must be greater than zero
    ("pad", {"length": -3.0}, False),
    ("pad", {}, False),                         # a missing argument
    ("pad", {"length": 10.0, "extra": 1}, False),
    ("pad", {"length": "10"}, False),           # text is not a number
    ("pad", {"length": float("nan")}, False),
    ("constrain_x", {"value": -4.0}, True),     # a position may be negative
    ("constrain_x", {"value": 0}, True),
    ("polar_pattern", {"count": 5, "axis": "Z"}, True),
    ("polar_pattern", {"count": 5.0, "axis": "Z"}, False),   # a count is a whole number
    ("polar_pattern", {"count": 1, "axis": "Z"}, False),
    ("polar_pattern", {"count": True, "axis": "Z"}, False),
    ("select_plane", {"plane": "XY"}, True),
    ("select_plane", {"plane": "top"}, False),
    ("fly", {}, False),
])
def test_check_args(command, args, ok):
    assert (check_args(command, args) is None) == ok


# --- valid commands --------------------------------------------------------------------------

def test_with_no_document_only_new_document_is_valid():
    assert valid_commands(EMPTY_SNAPSHOT) == ["new_document"]


def test_a_fresh_document_needs_a_body_first():
    assert valid_commands(snapshot(active_body=None, undo_depth=0)) == ["new_document", "new_body"]


def test_geometry_commands_only_while_a_sketch_is_open():
    closed = snapshot()
    opened = snapshot([sketch()], open_sketch="Sketch")
    for command in SHAPE_COMMANDS:
        assert why_not(closed, command) == "no sketch is open"
        assert why_not(opened, command) is None
    # ...and inside a sketch nothing but sketch commands (and undo) works
    assert set(valid_commands(opened)) == {"new_document", *SHAPE_COMMANDS, "leave_sketch", "undo"}


def test_dimensions_follow_the_shape_drawn_last():
    circle = {"shape": "circle", "fixed": ["x"]}
    state = snapshot([sketch(shapes=[{"shape": "rectangle", "fixed": []}, circle])],
                     open_sketch="Sketch")
    offered = {DIMENSION_COMMANDS[c] for c in valid_commands(state) if c in DIMENSION_COMMANDS}
    assert offered == {"diameter", "y"}         # not length (a circle), not x (already fixed)


def test_a_polygon_takes_one_size_and_across_flats_needs_even_sides():
    hexagon = {"shape": "polygon", "sides": 6, "fixed": ["across_flats"]}
    state = snapshot([sketch(shapes=[hexagon])], open_sketch="Sketch")
    assert why_not(state, "constrain_diameter") == "its diameter is already fixed"
    pentagon = {"shape": "polygon", "sides": 5, "fixed": []}
    state = snapshot([sketch(shapes=[pentagon])], open_sketch="Sketch")
    assert why_not(state, "constrain_across_flats") == "across flats needs an even number of sides"
    assert why_not(state, "constrain_diameter") is None


def test_pad_needs_a_selected_closed_unused_sketch():
    selected = {"type": "sketch", "name": "Sketch"}
    assert why_not(snapshot([sketch()]), "pad") == "select a sketch first"
    assert why_not(snapshot([sketch()], selection=selected), "pad") is None
    assert "closed" in why_not(snapshot([sketch(closed=False)], selection=selected), "pad")
    assert "already used" in why_not(snapshot([sketch(used_by="Pad")], selection=selected), "pad")


def test_cuts_need_a_solid_and_holes_need_a_circle():
    selected = {"type": "sketch", "name": "Sketch"}
    assert why_not(snapshot([sketch()], selection=selected), "pocket") == \
        "there is no solid to cut into"
    with_solid = snapshot([sketch()], SOLID, selection=selected)
    assert why_not(with_solid, "pocket_through_all") is None
    assert why_not(with_solid, "hole_through") == "a hole needs a circle in the sketch"
    assert why_not(snapshot([sketch(circle=True)], SOLID, selection=selected),
                   "hole_counterbore") is None


def test_dressups_and_patterns_need_the_right_selection():
    pad = {"type": "pad", "name": "Pad", "valid": True}
    fillet = {"type": "fillet", "name": "Fillet", "valid": True}
    edges = {"type": "edges", "rule": "vertical", "of": "Pad", "count": 4}
    assert why_not(snapshot([pad], SOLID), "fillet") == "select edges first"
    assert why_not(snapshot([pad], SOLID, selection=edges), "chamfer") is None
    assert why_not(snapshot([pad], SOLID, selection=edges), "thickness") == "select a face first"
    tip = {"type": "feature", "name": "Pad"}
    assert why_not(snapshot([pad], SOLID, selection=tip), "mirror") is None
    assert why_not(snapshot([fillet], SOLID, selection={"type": "feature", "name": "Fillet"}),
                   "polar_pattern") == "a fillet cannot be copied"


def test_undo_done_and_finished():
    assert why_not(snapshot(undo_depth=0), "undo") == "there is nothing to undo"
    assert why_not(snapshot(), "done") == "there is no valid solid yet"
    assert why_not(snapshot(solid=SOLID), "done") is None
    finished = snapshot(solid=SOLID, finished=True)
    assert valid_commands(finished) == ["new_document", "undo"]


def test_every_command_has_a_rule():
    """`why_not` must never fall through to its 'no rule' line."""
    states = [EMPTY_SNAPSHOT, snapshot(), snapshot(solid=SOLID),
              snapshot([sketch()], open_sketch="Sketch")]
    for state in states:
        for command in COMMANDS:
            assert not (why_not(state, command) or "").startswith("no rule")


# --- recipes ---------------------------------------------------------------------------------

EXAMPLES = {
    "block": {"length": 80.0, "width": 40.0, "height": 10.0},
    "cylinder": {"diameter": 60.0, "height": 20.0},
    "hex": {"across_flats": 40.0, "height": 10.0},
    "ring": {"outer_diameter": 70.0, "inner_diameter": 30.0, "height": 8.0},
    "corner_radius": {"radius": 4.0}, "top_chamfer": {"size": 1.0},
    "top_fillet": {"radius": 1.0}, "shell": {"wall_thickness": 2.0},
    "hole": {"diameter": 6.0, "x": 10.0, "y": -5.0},
    "blind_hole": {"diameter": 6.0, "depth": 4.0, "x": 10.0, "y": -5.0},
    "counterbore": {"hole_diameter": 4.0, "diameter": 9.0, "depth": 3.0, "x": 1.0, "y": 2.0},
    "boss": {"diameter": 8.0, "height": 5.0, "x": 0.0, "y": 0.0},
    "pad": {"length": 12.0, "width": 6.0, "height": 4.0, "x": 3.0, "y": 4.0},
    "pocket": {"length": 12.0, "width": 6.0, "depth": 2.0, "x": 3.0, "y": 4.0},
    "slot": {"length": 20.0, "width": 6.0, "depth": 2.0, "angle": 90.0, "x": 0.0, "y": 0.0},
    "polar": {"count": 5, "hole_diameter": 4.0, "circle_diameter": 40.0},
    "row": {"count": 4, "hole_diameter": 4.0, "spacing": 12.0, "y": 5.0},
    "hole_pair": {"diameter": 6.0, "x": 10.0, "y": -5.0},
    "boss_pair": {"diameter": 8.0, "height": 5.0, "x": 12.0, "y": 0.0},
    "pocket_pair": {"length": 8.0, "width": 6.0, "depth": 2.0, "x": 12.0, "y": 4.0},
}
CONTEXT = context_of([Step("block", EXAMPLES["block"])])


def test_there_is_a_recipe_for_every_step_kind_of_steps_md():
    assert set(EXAMPLES) == set(STARTS) | set(TREATMENTS) | set(FEATURES)
    assert len(STARTS) == 4 and len(TREATMENTS) == 4 and len(FEATURES) == 12


@pytest.mark.parametrize("kind", list(EXAMPLES))
def test_recipe_commands_are_in_the_catalogue_with_valid_arguments(kind):
    commands = flatten(recipe(Step(kind, EXAMPLES[kind]), CONTEXT))
    assert commands
    for command in commands:
        assert check_args(command.name, command.args) is None, command
        assert set(command.sources) == set(command.args)        # every argument has a source
        for name, source in command.sources.items():
            if source.startswith("slot:"):                      # copied from the step as it is
                assert command.args[name] == EXAMPLES[kind][source.removeprefix("slot:")]


def test_only_the_two_patterns_need_arithmetic():
    derived = {kind for kind in EXAMPLES
               for command in flatten(recipe(Step(kind, EXAMPLES[kind]), CONTEXT))
               if any(source.startswith("derived") for source in command.sources.values())}
    assert derived == {"polar", "row"}


def test_any_order_groups_hold_only_dimensions_and_shuffling_keeps_the_rest_in_place():
    steps = recipe(Step("slot", EXAMPLES["slot"]), CONTEXT)
    groups = [entry for entry in steps if isinstance(entry, AnyOrder)]
    assert len(groups) == 1 and len(groups[0].commands) == 5
    assert all(c.name in DIMENSION_COMMANDS for c in groups[0].commands)
    assert orders(steps) == 120                                 # 5 dimensions: 5! orders
    plain = flatten(steps)
    seen = set()
    for seed in range(30):
        shuffled = flatten(steps, random.Random(seed))
        assert sorted(shuffled, key=repr) == sorted(plain, key=repr)
        fixed = [i for i, c in enumerate(plain) if c.name not in DIMENSION_COMMANDS]
        assert all(shuffled[i] == plain[i] for i in fixed)
        seen.add(tuple(c.name for c in shuffled))
    assert len(seen) > 10


def test_bosses_stand_on_the_floor_of_a_shelled_base_and_cuts_start_at_the_top():
    shelled = context_of([Step("block", EXAMPLES["block"]), Step("shell", EXAMPLES["shell"])])
    assert (shelled.height, shelled.floor) == (10.0, 2.0)
    boss = flatten(recipe(Step("boss", EXAMPLES["boss"]), shelled))
    assert Cmd("new_sketch", {"offset": 2.0}, {"offset": "floor"}) in boss
    hole = flatten(recipe(Step("hole", EXAMPLES["hole"]), CONTEXT))
    assert Cmd("new_sketch", {"offset": 10.0}, {"offset": "base:height"}) in hole


def test_recipes_and_probes_exist_for_every_step_of_real_parts():
    for part in sample_parts(40, seed="freecad-recipes"):
        steps = steps_of(part.family, part.params)
        context = context_of(steps)
        for step in steps:
            assert flatten(recipe(step, context))
        features = [step for step in steps if step.kind in FEATURES]
        assert bool(probes_of(steps)) == bool(features)
