"""The parts of forge/freecad_multi that need no FreeCAD: catalogue, rules, recipes, files."""

import itertools
import json
import math
import random
import zipfile
from xml.dom import minidom

import pytest

from forge.freecad import catalogue as base_catalogue
from forge.freecad import valid as base_valid
from forge.freecad_multi import sources, visible
from forge.freecad_multi.lean import lean, same_lean
from forge.freecad_multi.multi_catalogue import COMMANDS, MULTI_COMMANDS, check_args
from forge.freecad_multi.multi_valid import EMPTY_SNAPSHOT, valid_commands, why_not
from forge.freecad_multi.recipes import (
    AnyOrder,
    Unsupported,
    _cut,
    _Face,
    flatten,
    from_yaw_pitch_roll,
    placement_kind,
    script_of,
    split,
    structure_items,
    yaw_pitch_roll,
)
from forge.freecad_multi.teacher import clean_length, places_of, teacher
from forge.resolve import space as sp
from forge.resolve.bodies import Body, Cut

SOLID = {"volume": 1.0, "bbox": [0, 0, 0, 1, 1, 1], "size": [1, 1, 1], "solids": 1, "valid": True}


def snapshot(items=(), solid=None, **session) -> dict:
    """A hand-made snapshot: a document with a body, plus whatever the test sets."""
    base = {"document": True, "active_body": "Body", "tip": None, "open_sketch": None,
            "selection": None, "undo_depth": 3, "finished": False}
    return {"items": list(items), "session": {**base, **session}, "solid": solid,
            "structure": None}


def body_item(name="Body", solid=SOLID, active=True) -> dict:
    return {"type": "body", "name": name, "tip": None, "active": active, "solid": solid,
            "valid": True}


def box(name="a", size=(40.0, 20.0, 10.0), centre=(0.0, 0.0, 5.0), matrix=sp.IDENTITY,
        line=1, cuts=()) -> Body:
    return Body(name, name, line, "box", None,
                {"length": size[0], "depth": size[1], "height": size[2]}, matrix, centre,
                cuts=list(cuts))


# --- the catalogue ---------------------------------------------------------------------------

def test_the_catalogue_is_the_base_one_plus_eleven():
    assert len(base_catalogue.COMMANDS) == 37       # the base file is not changed by ours
    assert len(MULTI_COMMANDS) == 11 and len(COMMANDS) == 48
    assert list(COMMANDS)[:37] == list(base_catalogue.COMMANDS)
    assert all(COMMANDS[name] is base_catalogue.COMMANDS[name] for name in base_catalogue.COMMANDS)
    assert not set(MULTI_COMMANDS) & set(base_catalogue.COMMANDS)


@pytest.mark.parametrize(("command", "args", "accepted"), [
    ("move_body", {"x": 0.0, "y": -5.0, "z": 12}, True),
    ("move_body", {"x": 0.0, "y": -5.0}, False),
    ("turn_body", {"yaw": 90.0, "pitch": 0.0, "roll": -90.0}, True),
    ("activate_body", {"index": 2}, True),
    ("activate_body", {"index": 0}, False),
    ("activate_body", {"index": 1.0}, False),
    ("add_cone", {"bottom_diameter": 20.0, "top_diameter": 0.0, "height": 5.0}, True),
    ("add_cone", {"bottom_diameter": 20.0, "top_diameter": -1.0, "height": 5.0}, False),
    ("add_cone", {"bottom_diameter": 20.0, "top_diameter": 0.0, "height": 0.0}, False),
    ("sketch_outline", {"points": [[0, 0], [1, 0], [0, 1]]}, True),
    ("sketch_outline", {"points": [[0, 0], [1, 0]]}, False),
    ("sketch_outline", {"points": [[0, 0], [1, 0], [0, "1"]]}, False),
    ("select_side", {"side": "front"}, True),
    ("select_side", {"side": "inside"}, False),
    ("select_side_edges", {"side": "top", "rule": "around"}, True),
    ("pad", {"length": 3.0}, True),                 # a base command, checked by the base file
    ("pad", {"length": -3.0}, False),
])
def test_arguments_are_checked(command, args, accepted):
    assert (check_args(command, args) is None) == accepted


# --- valid commands ---------------------------------------------------------------------------

def test_the_empty_state_allows_only_a_new_document():
    assert valid_commands(EMPTY_SNAPSHOT) == ["new_document"]
    assert base_valid.EMPTY_SNAPSHOT.keys() == {"items", "session", "solid"}    # base untouched


def test_every_command_has_a_rule():
    state = snapshot([body_item()], SOLID)
    for command in COMMANDS:
        assert not (why_not(state, command) or "").startswith("no rule"), command


def test_activating_needs_a_second_body():
    one = snapshot([body_item()])
    two = snapshot([body_item(), body_item("Body001", active=False)])
    assert why_not(one, "activate_body") == "there is no other body"
    assert why_not(two, "activate_body") is None
    assert why_not(snapshot([body_item()], open_sketch="Sketch"), "activate_body") \
        == "leave the sketch first"


def test_a_primitive_is_only_the_first_shape_of_a_body():
    assert why_not(snapshot([body_item()]), "add_cone") is None
    assert why_not(snapshot([body_item()], tip="Pad"), "add_sphere") == "the body already has a shape"
    assert why_not(snapshot([], active_body=None), "add_dome") == "there is no body: use new_body"


def test_placing_needs_only_an_active_body():
    assert why_not(snapshot([body_item(solid=None)]), "move_body") is None
    assert why_not(snapshot([body_item()], finished=True), "turn_body") is not None


def test_a_side_selection_opens_sketching_and_hollowing():
    side = {"type": "side", "side": "front"}
    assert why_not(snapshot([body_item()], SOLID, selection=side), "new_sketch") is None
    assert why_not(snapshot([body_item()], SOLID, selection=side), "thickness") is None
    assert why_not(snapshot([body_item()], SOLID), "new_sketch") == "select a plane first"
    assert why_not(snapshot([body_item(solid=None)]), "select_side") == "there is no solid yet"


def test_an_outline_takes_no_dimension():
    sketch = {"type": "sketch", "name": "Sketch", "body": "Body", "geometry": [], "closed": True,
              "used_by": None, "valid": True,
              "shapes": [{"shape": "outline", "first": 0, "points": [], "fixed": []}]}
    state = snapshot([body_item(), sketch], open_sketch="Sketch")
    assert why_not(state, "constrain_length") == "an outline is fixed as it is drawn"
    assert why_not(state, "sketch_outline") is None and why_not(state, "leave_sketch") is None


def test_done_needs_every_body_to_hold_a_valid_solid():
    assert why_not(snapshot([body_item(), body_item("Body001", solid=None)], SOLID), "done") \
        == "body 2 has no valid solid yet"
    assert why_not(snapshot([body_item(), body_item("Body001")], SOLID), "done") is None
    assert why_not(snapshot([], active_body=None), "done") == "there is no body yet"


# --- placing: a matrix as yaw, pitch, roll -------------------------------------------------------

def quarter_turns() -> list[sp.Mat]:
    """All 24 ways a box can sit square to the axes."""
    found = []
    for a, b, c in itertools.product((0, 90, 180, 270), repeat=3):
        m = sp.multiply(sp.rotation(2, a), sp.multiply(sp.rotation(1, b), sp.rotation(0, c)))
        if all(any(abs(m[i][j] - other[i][j]) > 1e-9 for i in range(3) for j in range(3))
               for other in found):
            found.append(m)
    return found


def close(a: sp.Mat, b: sp.Mat, tolerance: float = 1e-9) -> bool:
    return all(abs(a[i][j] - b[i][j]) <= tolerance for i in range(3) for j in range(3))


def test_every_quarter_turn_is_whole_right_angles_and_comes_back():
    turns = quarter_turns()
    assert len(turns) == 24
    for turn in turns:
        angles = yaw_pitch_roll(turn)
        assert all(angle % 90 == 0 for angle in angles), angles
        assert close(from_yaw_pitch_roll(*angles), turn)


def test_odd_turns_and_tilts_come_back():
    rng = random.Random(4)
    for _ in range(300):
        turn = sp.multiply(sp.rotation(2, rng.uniform(-180, 180)), sp.multiply(
            sp.rotation(1, rng.choice((rng.uniform(-89, 89), 90.0, -90.0))),
            sp.rotation(0, rng.uniform(-180, 180))))
        assert close(from_yaw_pitch_roll(*yaw_pitch_roll(turn)), turn, 1e-8)


def test_a_mirror_image_is_drawn_mirrored_and_turned_by_what_is_left():
    for turn in quarter_turns()[:6]:
        mirrored = sp.multiply(turn, sp.mirror(0))
        rest, drawn_mirrored = split(mirrored)
        assert drawn_mirrored and abs(sp.determinant(rest) - 1) < 1e-12 and close(rest, turn)
        assert split(turn) == (turn, False)
    assert placement_kind(box(matrix=sp.mirror(0))) == "standing, mirror image"
    assert placement_kind(box(matrix=sp.rotation(1, 90))) == "quarter turn"
    assert placement_kind(box(matrix=sp.rotation(2, 30))) == "odd turn about the upright axis"
    assert placement_kind(box()) == "standing"


# --- recipes ------------------------------------------------------------------------------------

def test_a_box_recipe_copies_every_number_from_the_body():
    body = box(size=(40.0, 20.0, 10.0), centre=(3.0, -4.0, 55.0), matrix=sp.rotation(1, 90))
    (item,) = structure_items([body])
    commands = flatten(item.entries)
    assert [c.name for c in commands[:4]] == ["new_body", "select_plane", "new_sketch",
                                              "sketch_rectangle"]
    by_name = {c.name: c for c in commands}
    assert by_name["constrain_length"].args == {"value": 40.0}
    assert by_name["constrain_width"].args == {"value": 20.0}
    assert by_name["pad_symmetric"].args == {"length": 10.0}
    assert by_name["move_body"].args == {"x": 3.0, "y": -4.0, "z": 55.0}
    assert by_name["turn_body"].args == {"yaw": 0.0, "pitch": 90.0, "roll": 0.0}
    assert isinstance(item.entries[-1], AnyOrder)      # move and turn: either order
    # No number is worked out: each is a size, the centre, the turn or a constant.
    for command in commands:
        assert all(source.split(":")[0] in ("size", "body", "const")
                   for source in command.sources.values()), command


def test_a_body_at_the_origin_unturned_gets_no_placing_command():
    (item,) = structure_items([box(centre=(0.0, 0.0, 0.0))])
    assert not {"move_body", "turn_body"} & {c.name for c in flatten(item.entries)}


def test_bodies_are_numbered_in_plan_order_and_features_follow_their_line():
    hole = Cut("hole", "hole", {"diameter": 4.0}, origin=(0.0, 0.0, 5.0), through=10.0, line=3)
    first = box("first", line=1, cuts=[hole])
    second = box("second", line=2, centre=(0.0, 0.0, 15.0))
    items = structure_items([second, first])        # listed out of order on purpose
    assert [(item.kind, item.body, item.name) for item in items] == [
        ("part", 1, "first"), ("part", 2, "second"), ("feature", 1, "first: hole")]
    assert [item.source for item in items] == [1, 0, 1]
    script = script_of(items)
    names = [entry.name for _, entry in script if not isinstance(entry, AnyOrder)]
    assert names[0] == "new_document" and names[-1] == "done"
    # The feature is on body 1 while body 2 is the active one: it starts by activating body 1.
    activate = next(entry for _, entry in script
                    if not isinstance(entry, AnyOrder) and entry.name == "activate_body")
    assert activate.args == {"index": 1}
    assert clean_length(items) == len(flatten([entry for _, entry in script]))


def test_a_feature_position_is_the_plans_own_on_any_side():
    # A hole written 10 along the face's first direction and 3 along its second.
    def cut(normal, first, sign=1.0):
        return Cut("hole", "hole", {"diameter": 4.0}, origin=sp.scale(normal, 5.0), normal=normal,
                   first=first, second_sign=sign, through=10.0, spots=[(10.0, 3.0)])

    top = _Face(cut((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)), sp.IDENTITY)
    assert (top.side, top.offset, top.at((10.0, 3.0))) == ("top", 5.0, (10.0, 3.0))
    # The front face: first = length (x), second = height (z); x cross z points to the front.
    front = _Face(cut((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), 1.0), sp.IDENTITY)
    assert (front.side, front.at((10.0, 3.0))) == ("front", (10.0, 3.0))
    # The same top-face cut on a body lying along length: its top now faces right, and the
    # cut's first direction (the body's x) points down.
    lying = _Face(cut((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)), sp.rotation(1, 90))
    assert lying.side == "right"
    assert lying.at((10.0, 3.0)) == (3.0, -10.0) and lying.swapped


def test_a_closed_hollow_is_reported_not_built():
    shell = Cut("shell", "hollowed out", {"wall": 2.0}, open_normal=None)
    with pytest.raises(Unsupported, match="no open face"):
        _cut(shell, sp.IDENTITY)


# --- the teacher, where no FreeCAD is needed ------------------------------------------------------

def test_the_teacher_starts_with_a_document_and_knows_every_place():
    items = structure_items([box(), box("b", centre=(0.0, 0.0, 15.0), matrix=sp.rotation(2, 90),
                                        line=2)])
    advice = teacher(items, EMPTY_SNAPSHOT)
    assert advice.names() == ["new_document"] and advice.on_plan and advice.built == 0
    places = places_of(items)
    assert places[1] == ((0.0, 0.0, 5.0), sp.IDENTITY)
    assert places[2][0] == (0.0, 0.0, 15.0) and close(places[2][1], sp.rotation(2, 90))


# --- the lean snapshot ---------------------------------------------------------------------------

def test_the_lean_snapshot_keeps_where_bodies_are_and_tolerates_volume_noise():
    full = snapshot([{**body_item(), "placement": {"position": [0, 0, 5]}, "index": 1},
                     {"type": "sketch", "name": "Sketch", "body": "Body", "geometry": [1, 2, 3],
                      "constraints": [1], "shapes": []}], SOLID)
    full["structure"] = {"bodies": 1, "solids": 1, "volume": 1.0, "bbox": [0] * 6,
                         "size": [1, 1, 1], "valid": True, "touching": [], "overlapping": []}
    small = lean(full)
    assert small["items"][0]["solid"]["bbox"] == SOLID["bbox"]
    assert "geometry" not in small["items"][1] and small["items"][1]["n_geometry"] == 3
    noisy = json.loads(json.dumps(small))
    noisy["structure"]["volume"] += 1e-12
    assert same_lean(small, noisy)
    noisy["items"][0]["solid"]["bbox"][0] = 0.5
    assert not same_lean(small, noisy)


# --- structure rows --------------------------------------------------------------------------------

def test_a_structure_row_becomes_bodies_with_the_middle_as_centre():
    kinds = sources.structure_kinds()
    if not kinds:
        pytest.skip("data/structures has not been generated")
    row = next(sources.structure_rows(kinds[0], 1))
    bodies = sources.row_bodies(row)
    parts = [part for step in row["steps"] for part in step["parts"]]
    assert [body.name for body in bodies] == [part["name"] for part in parts]
    for body, part in zip(bodies, parts, strict=True):
        assert body.frame.low[2] == pytest.approx(part["at"][2])        # `at` is the bottom
        assert list(body.frame.size) == pytest.approx(part["size"])
        assert body.matrix == sp.IDENTITY
    assert sum(body.volume for body in bodies) == pytest.approx(row["measured"]["volume"])


# --- display settings for a saved file ---------------------------------------------------------------

def test_the_camera_looks_from_front_right_and_above():
    towards = visible._turned(visible.ISOMETRIC, (0.0, 0.0, 1.0))
    assert towards == pytest.approx((1 / math.sqrt(3), -1 / math.sqrt(3), 1 / math.sqrt(3)),
                                    abs=1e-5)
    text = visible.camera([-200.0, -200.0, 0.0, 200.0, 200.0, 900.0])
    position = [float(v) for v in text.split("position ")[1].split("\n")[0].split()]
    assert position[0] > 200 and position[1] < -200 and position[2] > 900
    height = float(text.split("height ")[1].split("\n")[0])
    assert height > math.dist((-200, -200, 0), (200, 200, 900))      # the whole thing fits


def test_display_settings_are_valid_xml_and_go_last_in_the_file(tmp_path):
    text = visible.gui_document(["Body", "Origin", "Sketch", "Pad"], {"Body", "Pad"},
                                [0.0, 0.0, 0.0, 10.0, 10.0, 10.0])
    document = minidom.parseString(text)
    shown = {node.getAttribute("name"): node.getElementsByTagName("Bool")[0].getAttribute("value")
             for node in document.getElementsByTagName("ViewProvider")}
    assert shown == {"Body": "true", "Origin": "false", "Sketch": "false", "Pad": "true"}
    assert "OrthographicCamera" in document.getElementsByTagName("Camera")[0].getAttribute(
        "settings")

    path = tmp_path / "saved.FCStd"
    objects = ('<Document><Objects Count="2"><Object type="PartDesign::Body" name="Body" id="1"/>'
               '<Object type="PartDesign::Pad" name="Pad" id="2"/></Objects><ObjectData/>'
               "</Document>")
    with zipfile.ZipFile(path, "w") as saved:
        saved.writestr("Document.xml", objects)
        saved.writestr("Pad.Shape.brp", "shape")
    state = snapshot([{**body_item(), "tip": "Pad"}])
    state["structure"] = {"bbox": [0.0, 0.0, 0.0, 10.0, 10.0, 10.0]}
    assert visible.make_visible(path, state) == ["Body", "Pad"]
    with zipfile.ZipFile(path) as saved:
        assert saved.namelist() == ["Document.xml", "Pad.Shape.brp", "GuiDocument.xml"]
    with pytest.raises(ValueError, match="already has display settings"):
        visible.make_visible(path, state)
