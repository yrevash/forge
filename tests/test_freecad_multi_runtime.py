"""The structure runtime in real FreeCAD: bodies, placing, sides, snapshot, undo, recovery.

Skipped without FreeCAD. One worker for the whole file.
"""

import math

import pytest

from forge.freecad.catalogue import COMMANDS as BASE_COMMANDS
from forge.freecad.client import FreeCADClient
from forge.freecad.locate import freecad_available
from forge.freecad_multi.client import MultiClient
from forge.freecad_multi.multi_catalogue import COMMANDS
from forge.freecad_multi.multi_valid import valid_commands
from forge.freecad_multi.undo_proof import same_state

pytestmark = pytest.mark.skipif(not freecad_available(), reason="FreeCAD is not installed")


@pytest.fixture(scope="module")
def fc():
    with MultiClient() as client:
        yield client


def run(fc, *commands) -> dict:
    """Issue ("name", {args}) pairs or bare names; every one must be carried out."""
    reply = None
    for command in commands:
        name, args = (command, {}) if isinstance(command, str) else command
        reply = fc.command(name, **args)
        assert reply["status"] == "ok", (name, args, reply.get("reason"))
    return reply


def box(fc, length=80.0, width=40.0, height=10.0) -> dict:
    """A new body holding a box with its middle on the body's origin."""
    return run(fc, "new_body", ("select_plane", {"plane": "XY"}), ("new_sketch", {"offset": 0.0}),
               "sketch_rectangle", ("constrain_length", {"value": length}),
               ("constrain_width", {"value": width}), ("constrain_x", {"value": 0.0}),
               ("constrain_y", {"value": 0.0}), "leave_sketch",
               ("pad_symmetric", {"length": height}))


def bodies(snapshot: dict) -> list[dict]:
    return [item for item in snapshot["items"] if item["type"] == "body"]


def circle_on(fc, side: str, offset: float, diameter: float, x: float, y: float) -> dict:
    return run(fc, ("select_side", {"side": side}), ("new_sketch", {"offset": offset}),
               "sketch_circle", ("constrain_diameter", {"value": diameter}),
               ("constrain_x", {"value": x}), ("constrain_y", {"value": y}), "leave_sketch")


# --- the worker ---------------------------------------------------------------------------------

def test_the_worker_serves_all_commands_and_reports_the_valid_ones(fc):
    reply = run(fc, "new_document")
    assert reply["valid"] == valid_commands(reply["snapshot"])
    assert reply["snapshot"]["structure"] == {
        "bodies": 0, "solids": 0, "volume": 0.0, "bbox": None, "size": None, "valid": False,
        "touching": [], "overlapping": []}
    reply = box(fc)
    assert reply["valid"] == valid_commands(reply["snapshot"])
    assert set(reply["valid"]) <= set(COMMANDS)
    assert {"move_body", "turn_body", "select_side", "select_side_edges"} <= set(reply["valid"])
    assert "activate_body" not in reply["valid"]            # there is only one body


def test_the_base_client_is_untouched_beside_ours(fc):
    run(fc, "new_document")
    with FreeCADClient() as base:
        reply = base.command("new_document")
        assert set(reply["valid"]) <= set(BASE_COMMANDS)
        assert base.command("move_body", x=1.0, y=0.0, z=0.0)["status"] == "rejected"
        assert "structure" not in reply["snapshot"]


# --- drawing parts centred --------------------------------------------------------------------------

def test_a_symmetric_pad_puts_the_middle_of_the_box_on_the_origin(fc):
    run(fc, "new_document")
    solid = box(fc, 80.0, 40.0, 10.0)["snapshot"]["solid"]
    assert solid["bbox"] == [-40.0, -20.0, -5.0, 40.0, 20.0, 5.0]
    assert solid["volume"] == pytest.approx(32000.0, rel=1e-12)
    pad = next(i for i in reply_items(fc) if i["type"] == "pad")
    assert pad["symmetric"] is True and pad["length"] == 10.0


def reply_items(fc) -> list[dict]:
    return fc.snapshot()["items"]


@pytest.mark.parametrize(("command", "args", "size", "volume"), [
    ("add_cone", {"bottom_diameter": 40.0, "top_diameter": 16.0, "height": 30.0},
     (40.0, 40.0, 30.0), math.pi * 30 / 12 * (40 ** 2 + 40 * 16 + 16 ** 2)),
    ("add_cone", {"bottom_diameter": 0.0, "top_diameter": 40.0, "height": 30.0},
     (40.0, 40.0, 30.0), math.pi * 30 / 12 * 40 ** 2),
    ("add_sphere", {"diameter": 30.0}, (30.0, 30.0, 30.0), math.pi * 30 ** 3 / 6),
    ("add_dome", {"diameter": 100.0, "height": 30.0}, (100.0, 100.0, 30.0),
     math.pi * 30 ** 2 * (3 * ((2500 + 900) / 60) - 30) / 3),
    ("add_dome", {"diameter": 100.0, "height": 50.0}, (100.0, 100.0, 50.0),
     math.pi * 100 ** 3 / 12),
    ("add_tapered_box", {"bottom_length": 80.0, "bottom_depth": 60.0, "top_length": 40.0,
                         "top_depth": 20.0, "height": 50.0}, (80.0, 60.0, 50.0),
     50 / 6 * (80 * 60 + 40 * 20 + 4 * 60 * 40)),
    ("add_tapered_box", {"bottom_length": 80.0, "bottom_depth": 60.0, "top_length": 0.0,
                         "top_depth": 0.0, "height": 50.0}, (80.0, 60.0, 50.0),
     80 * 60 * 50 / 3),
])
def test_primitives_are_exact_and_centred(fc, command, args, size, volume):
    reply = run(fc, "new_document", "new_body", (command, args))
    solid = reply["snapshot"]["solid"]
    assert solid["solids"] == 1 and solid["valid"]
    assert solid["volume"] == pytest.approx(volume, rel=1e-9)
    for axis in range(3):
        assert solid["bbox"][axis] == pytest.approx(-size[axis] / 2, abs=1e-6)
        assert solid["bbox"][axis + 3] == pytest.approx(size[axis] / 2, abs=1e-6)
    item = reply["snapshot"]["items"][-1]
    assert item["type"] == command.removeprefix("add_") and item["valid"]
    assert {key: item[key] for key in args} == pytest.approx(args)     # read back off the document
    assert "add_cone" not in reply["valid"]             # a body takes one primitive


def test_an_outline_is_fixed_as_drawn_and_pads_to_a_wedge(fc):
    reply = run(fc, "new_document", "new_body", ("select_plane", {"plane": "XZ"}),
                ("new_sketch", {"offset": 0.0}),
                ("sketch_outline", {"points": [[-20.0, -15.0], [20.0, -15.0], [20.0, 15.0]]}))
    sketch = reply["snapshot"]["items"][-1]
    assert sketch["dof"] == 0 and sketch["closed"]
    assert sketch["shapes"][0]["shape"] == "outline"
    assert "constrain_length" not in reply["valid"]
    solid = run(fc, "leave_sketch", ("pad_symmetric", {"length": 10.0}))["snapshot"]["solid"]
    # An XZ sketch: its x is the body's X and its y the body's Z; the pad runs along Y.
    assert solid["bbox"] == pytest.approx([-20.0, -5.0, -15.0, 20.0, 5.0, 15.0], abs=1e-9)
    assert solid["volume"] == pytest.approx(40 * 30 * 10 / 2, rel=1e-12)


# --- placing ----------------------------------------------------------------------------------------

def test_move_and_turn_do_not_disturb_each_other(fc):
    move = ("move_body", {"x": 100.0, "y": -50.0, "z": 30.0})
    turn = ("turn_body", {"yaw": 0.0, "pitch": 90.0, "roll": 0.0})      # lying along length
    run(fc, "new_document")
    box(fc, 80.0, 40.0, 10.0)
    one = run(fc, move, turn)["snapshot"]
    run(fc, "new_document")
    box(fc, 80.0, 40.0, 10.0)
    other = run(fc, turn, move)["snapshot"]
    assert bodies(one)[0]["solid"] == bodies(other)[0]["solid"]
    assert bodies(one)[0]["placement"] == bodies(other)[0]["placement"]
    # The written height (10) now runs left to right, the length (80) top to bottom.
    assert bodies(one)[0]["solid"]["bbox"] == pytest.approx(
        [95.0, -70.0, -10.0, 105.0, -30.0, 70.0], abs=1e-9)
    assert bodies(one)[0]["placement"]["position"] == [100.0, -50.0, 30.0]
    assert bodies(one)[0]["placement"]["matrix"] == [[0.0, 0.0, 1.0], [0.0, 1.0, 0.0],
                                                     [-1.0, 0.0, 0.0]]


def test_two_bodies_are_measured_where_they_sit_and_found_touching(fc):
    run(fc, "new_document")
    box(fc, 80.0, 40.0, 10.0)
    run(fc, ("move_body", {"x": 0.0, "y": 0.0, "z": 5.0}))
    box(fc, 20.0, 20.0, 30.0)
    reply = run(fc, ("move_body", {"x": 30.0, "y": 0.0, "z": 25.0}))
    first, second = bodies(reply["snapshot"])
    assert (first["index"], second["index"]) == (1, 2)
    assert (first["active"], second["active"]) == (False, True)
    assert first["solid"]["bbox"] == [-40.0, -20.0, 0.0, 40.0, 20.0, 10.0]
    assert second["solid"]["bbox"] == [20.0, -10.0, 10.0, 40.0, 10.0, 40.0]
    whole = reply["snapshot"]["structure"]
    assert whole["bodies"] == whole["solids"] == 2 and whole["valid"]
    assert whole["bbox"] == [-40.0, -20.0, 0.0, 40.0, 20.0, 40.0]
    assert whole["size"] == [80.0, 40.0, 40.0]
    assert whole["volume"] == pytest.approx(32000.0 + 12000.0)
    assert whole["touching"] == [[1, 2]] and whole["overlapping"] == []
    assert reply["snapshot"]["solid"] == second["solid"]        # the active body's

    # Sunk 4 into the first body: they now share 20 x 20 x 4.
    sunk = run(fc, ("move_body", {"x": 30.0, "y": 0.0, "z": 21.0}))["snapshot"]["structure"]
    assert sunk["touching"] == [[1, 2]]
    assert [pair[:2] for pair in sunk["overlapping"]] == [[1, 2]]
    assert sunk["overlapping"][0][2] == pytest.approx(1600.0)
    # Lifted clear: neither.
    apart = run(fc, ("move_body", {"x": 30.0, "y": 0.0, "z": 26.0}))["snapshot"]["structure"]
    assert apart["touching"] == [] and apart["overlapping"] == []


def test_features_go_on_the_active_body(fc):
    run(fc, "new_document")
    box(fc, 80.0, 40.0, 10.0)
    box(fc, 20.0, 20.0, 30.0)
    run(fc, ("move_body", {"x": 0.0, "y": 0.0, "z": 20.0}))
    reply = run(fc, ("activate_body", {"index": 1}))
    assert reply["snapshot"]["session"]["active_body"] == "Body"
    assert fc.command("activate_body", index=1)["status"] == "rejected"     # already active
    assert fc.command("activate_body", index=3)["status"] == "rejected"     # no third body
    circle_on(fc, "top", 5.0, 6.0, 20.0, 0.0)
    reply = run(fc, ("pocket", {"depth": 10.0}))
    first, second = bodies(reply["snapshot"])
    assert first["solid"]["volume"] == pytest.approx(32000.0 - math.pi * 9 * 10)
    assert second["solid"]["volume"] == pytest.approx(12000.0)
    assert reply["snapshot"]["items"][-1]["body"] == "Body"


# --- sides of a placed body ------------------------------------------------------------------------

def test_a_side_is_the_side_as_the_body_sits(fc):
    """A boss on the 'left' of a body that lies along length grows out to the left."""
    run(fc, "new_document")
    box(fc, 40.0, 20.0, 100.0)                                 # written standing, 100 high
    run(fc, ("turn_body", {"yaw": 0.0, "pitch": 90.0, "roll": 0.0}),    # top end to the right
        ("move_body", {"x": 0.0, "y": 0.0, "z": 20.0}))
    before = fc.snapshot()["solid"]["bbox"]
    assert before == pytest.approx([-50.0, -10.0, 0.0, 50.0, 10.0, 40.0], abs=1e-9)
    # The left side: first direction = depth (y), second = height (z). A boss 3 towards the
    # back and 5 up from the middle of that face.
    circle_on(fc, "left", 50.0, 8.0, 3.0, 5.0)
    reply = run(fc, ("pad", {"length": 7.0}))
    sketch = next(i for i in reply["snapshot"]["items"] if i["type"] == "sketch"
                  and i.get("side"))
    assert (sketch["side"], sketch["plane"], sketch["offset"]) == ("left", None, 50.0)
    after = reply["snapshot"]["solid"]
    assert after["bbox"] == pytest.approx([-57.0, -10.0, 0.0, 50.0, 10.0, 40.0], abs=1e-9)
    assert after["volume"] == pytest.approx(80000.0 + math.pi * 16 * 7)

    # A blind hole in the same place cuts INTO the body instead.
    run(fc, "undo")
    reply = run(fc, ("pocket", {"depth": 7.0}))
    assert reply["snapshot"]["solid"]["bbox"] == pytest.approx(before, abs=1e-9)
    assert reply["snapshot"]["solid"]["volume"] == pytest.approx(80000.0 - math.pi * 16 * 7)


@pytest.mark.parametrize("side", ["top", "bottom", "left", "right", "front", "back"])
def test_a_boss_grows_out_of_every_side_and_a_pocket_cuts_into_it(fc, side):
    run(fc, "new_document")
    box(fc, 60.0, 40.0, 20.0)
    run(fc, ("move_body", {"x": 0.0, "y": 0.0, "z": 10.0}))
    half = {"top": 10.0, "bottom": 10.0, "left": 30.0, "right": 30.0, "front": 20.0,
            "back": 20.0}[side]
    axis, end = {"left": (0, -1), "right": (0, 1), "front": (1, -1), "back": (1, 1),
                 "bottom": (2, -1), "top": (2, 1)}[side]
    plain = [-30.0, -20.0, 0.0, 30.0, 20.0, 20.0]
    circle_on(fc, side, half, 6.0, 2.0, 1.0)
    grown = run(fc, ("pad", {"length": 4.0}))["snapshot"]["solid"]
    wanted = list(plain)
    wanted[axis + (3 if end > 0 else 0)] += 4.0 * end
    assert grown["bbox"] == pytest.approx(wanted, abs=1e-9)
    assert grown["volume"] == pytest.approx(48000.0 + math.pi * 9 * 4)
    run(fc, "undo")
    cut = run(fc, ("pocket", {"depth": 4.0}))["snapshot"]["solid"]
    assert cut["bbox"] == pytest.approx(plain, abs=1e-9)
    assert cut["volume"] == pytest.approx(48000.0 - math.pi * 9 * 4)


def test_a_body_turned_by_an_odd_angle_has_no_sides(fc):
    run(fc, "new_document")
    box(fc)
    run(fc, ("turn_body", {"yaw": 30.0, "pitch": 0.0, "roll": 0.0}))
    reply = fc.command("select_side", side="front")
    assert reply["status"] == "rejected" and "odd angle" in reply["reason"]


def test_side_edges_and_hollowing_use_the_side_as_it_sits(fc):
    run(fc, "new_document")
    box(fc, 60.0, 40.0, 20.0)
    run(fc, ("turn_body", {"yaw": 0.0, "pitch": 0.0, "roll": 180.0}))       # upside down
    reply = run(fc, ("select_side_edges", {"side": "top", "rule": "around"}))
    assert reply["snapshot"]["session"]["selection"] == {
        "type": "edges", "rule": "around", "side": "top", "of": "Pad", "count": 4}
    reply = run(fc, ("fillet", {"radius": 2.0}))
    fillet = reply["snapshot"]["items"][-1]
    assert (fillet["type"], fillet["rule"], fillet["side"], fillet["edges"]) == (
        "fillet", "around", "top", 4)
    run(fc, ("select_side", {"side": "bottom"}))
    # (A wall thicker than the rounded edge's radius cannot be offset inwards: FreeCAD then
    # leaves the feature invalid. 1.5 is inside the 2 mm fillet.)
    hollow = run(fc, ("thickness", {"value": 1.5}))["snapshot"]
    item = hollow["items"][-1]
    assert (item["type"], item["side"], item["value"], item["valid"]) == (
        "thickness", "bottom", 1.5, True)
    assert hollow["solid"]["bbox"] == pytest.approx([-30.0, -20.0, -10.0, 30.0, 20.0, 10.0],
                                                    abs=1e-9)
    assert hollow["solid"]["volume"] < 48000.0 / 2


def test_a_dome_is_hollowed_through_its_flat_base(fc):
    """FreeCAD's Thickness tool fails on a moved slice of a ball; the runtime cuts the hollow."""
    reply = run(fc, "new_document", "new_body", ("add_dome", {"diameter": 90.0, "height": 45.0}),
                ("select_side", {"side": "bottom"}), ("thickness", {"value": 1.5}))
    item = reply["snapshot"]["items"][-1]
    assert (item["type"], item["on"], item["side"], item["value"], item["valid"]) == (
        "thickness", "Dome", "bottom", 1.5, True)
    assert reply["snapshot"]["solid"]["volume"] == pytest.approx(
        2 / 3 * math.pi * (45 ** 3 - 43.5 ** 3), rel=1e-9)
    assert reply["snapshot"]["solid"]["solids"] == 1


# --- undo --------------------------------------------------------------------------------------------

def test_undo_takes_back_a_move_a_turn_an_activation_and_a_whole_body(fc):
    run(fc, "new_document")
    box(fc, 80.0, 40.0, 10.0)
    trail = [fc.snapshot()]
    for command in (("move_body", {"x": 5.0, "y": 6.0, "z": 7.0}),
                    ("turn_body", {"yaw": 90.0, "pitch": 0.0, "roll": 90.0}),
                    "new_body", ("add_sphere", {"diameter": 20.0}),
                    ("move_body", {"x": 0.0, "y": 0.0, "z": 50.0}),
                    ("activate_body", {"index": 1}),
                    ("move_body", {"x": -100.0, "y": 0.0, "z": 0.0})):
        trail.append(run(fc, command)["snapshot"])
    objects = fc.objects()
    assert len(bodies(trail[-1])) == 2
    for wanted in reversed(trail[:-1]):
        assert same_state(run(fc, "undo")["snapshot"], wanted) != "different"
    assert len(bodies(fc.snapshot())) == 1 and len(fc.objects()) < len(objects)


def test_undo_reaches_past_freecads_twenty_steps_with_several_bodies(fc):
    run(fc, "new_document")
    trail = [fc.snapshot()]
    for number in range(4):                 # 4 bodies x 11 commands, well past 20
        for command in ("new_body", ("select_plane", {"plane": "XY"}),
                        ("new_sketch", {"offset": 0.0}), "sketch_circle",
                        ("constrain_diameter", {"value": 10.0 + number}),
                        ("constrain_x", {"value": 0.0}), ("constrain_y", {"value": 0.0}),
                        "leave_sketch", ("pad_symmetric", {"length": 30.0}),
                        ("move_body", {"x": 40.0 * number, "y": 0.0, "z": 15.0}),
                        ("turn_body", {"yaw": 0.0, "pitch": 0.0, "roll": 90.0})):
            trail.append(run(fc, command)["snapshot"])
    assert len(trail) - 1 == 44 and trail[-1]["structure"]["bodies"] == 4
    for wanted in reversed(trail[:-1]):
        assert same_state(run(fc, "undo")["snapshot"], wanted) != "different"
    assert fc.snapshot()["structure"]["bodies"] == 0
    assert "undo" not in valid_commands(fc.snapshot())


# --- recovery -----------------------------------------------------------------------------------------

def test_a_killed_worker_is_replaced_and_the_structure_is_still_there(fc):
    run(fc, "new_document")
    box(fc, 80.0, 40.0, 10.0)
    run(fc, ("move_body", {"x": 0.0, "y": 0.0, "z": 5.0}))
    run(fc, "new_body", ("add_cone", {"bottom_diameter": 20.0, "top_diameter": 0.0,
                                      "height": 30.0}))
    before = run(fc, ("turn_body", {"yaw": 0.0, "pitch": 90.0, "roll": 0.0}))["snapshot"]
    restarts = fc.restarts
    fc.break_worker("crash")                # os._exit in the worker: no crash dialog
    reply = run(fc, ("move_body", {"x": 55.0, "y": 0.0, "z": 20.0}))
    assert fc.restarts == restarts + 1
    assert reply["snapshot"]["structure"]["bodies"] == 2
    assert same_state(run(fc, "undo")["snapshot"], before) != "different"
