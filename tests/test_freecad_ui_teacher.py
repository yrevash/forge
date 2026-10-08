"""The interface teacher and the noise maker on hand-made snapshots: no FreeCAD needed."""

from __future__ import annotations

import random

from forge.freecad_ui.noise import WRONG_BUTTONS, Moment, is_target, wrong_action
from forge.freecad_ui.recipes import context_of, flatten, recipe
from forge.freecad_ui.teacher import (
    CANCEL,
    DONE,
    LEAVE,
    UNDO,
    clean_length,
    expected_shape,
    segments_of,
    teacher,
    volumes_of,
)
from forge.system1.steps import Step

PLAN = [Step("block", {"length": 60, "width": 40, "height": 10}),
        Step("top_fillet", {"radius": 2}),
        Step("hole", {"diameter": 6, "x": 12, "y": -5})]
BODY = {"type": "body", "name": "Body", "tip": None, "valid": True}
SKETCH = {"type": "sketch", "name": "Sketch", "on": "XY_Plane", "plane": "XY", "height": 0.0,
          "shapes": [{"shape": "rectangle", "x": 0.0, "y": 0.0, "length": 60.0, "width": 40.0}],
          "dof": 0, "closed": True, "used_by": "Pad", "valid": True}
PAD = {"type": "pad", "name": "Pad", "body": "Body", "sketch": "Sketch", "length": 10.0,
       "valid": True}
FILLET = {"type": "fillet", "name": "Fillet", "body": "Body", "on": "Pad", "edges": 4,
          "radius": 2.0, "rule": "top_face", "valid": True}


SKETCHER = (UNDO, LEAVE, "button:Sketcher_CreateRectangle_Center", "button:Sketcher_CreateCircle")


def snapshot(items=(), *, document=True, dialog=None, sketch_open=None, tool=None, selection=(),
             elements=(), buttons=SKETCHER, undo=5, built=0, volume=None) -> dict:
    # `built`: the solid has the volume the plan gives after that many items.
    if built:
        volume = volumes_of(PLAN)[built - 1]
    solid = None if volume is None else {"volume": volume, "bbox": [0] * 6, "solids": 1,
                                         "valid": True}
    return {"buttons": list(buttons), "elements": list(elements), "items": list(items),
            "solid": solid,
            "context": {"workbench": "PartDesignWorkbench", "document": document,
                        "dialog": dialog, "sketch_open": sketch_open, "tool": tool,
                        "selection": list(selection), "tip": None, "undo": undo}}


def chosen(name, rule=None, edges=0, faces=0) -> dict:
    return {"object": name, "faces": faces, "edges": edges, "rule": rule}


def ids(advice) -> list[str]:
    return [target.id for target in advice.targets]


def test_the_plan_becomes_segments_that_cover_every_action():
    segments = segments_of(PLAN)
    assert [s.kind for s in segments] == [
        "new_document", "body", "sketch_xy", "shape", "leave", "dialog",     # block
        "dialog",                                                            # top fillet
        "datum", "sketch_on_datum", "shape", "leave", "dialog"]              # hole
    context = context_of(PLAN)
    assert clean_length(PLAN) == 1 + sum(len(flatten(recipe(step, context))) for step in PLAN)
    assert expected_shape(segments[3]) == {"shape": "rectangle", "x": 0.0, "y": 0.0,
                                           "length": 60, "width": 40}


def test_a_clean_build_is_followed_step_by_step():
    assert ids(teacher(PLAN, snapshot(document=False))) == ["button:Std_New"]
    assert ids(teacher(PLAN, snapshot())) == ["button:PartDesign_Body"]
    assert ids(teacher(PLAN, snapshot([BODY], selection=[chosen("Body")]))) \
        == ["button:PartDesign_NewSketch"]
    # A plane is selected: "new sketch" would use it unasked, so the body is selected first.
    assert ids(teacher(PLAN, snapshot([BODY], selection=[chosen("XZ_Plane")]))) == ["tree:Body"]
    # The pad is confirmed: the fillet needs its edges, then its button.
    built = [BODY, SKETCH, PAD]
    assert ids(teacher(PLAN, snapshot(built, built=1))) == ["pick:edges:top_face"]
    picked = [chosen("Pad", "top_face", edges=4)]
    assert ids(teacher(PLAN, snapshot(built, selection=picked, built=1))) \
        == ["button:PartDesign_Fillet"]
    # Every number reads right but the solid is not the plan's: undo.
    advice = teacher(PLAN, snapshot(built, selection=picked, volume=23000.0))
    assert ids(advice) == [UNDO]
    assert "volume" in advice.why


def test_an_open_dialog_is_filled_in_then_confirmed():
    built = [BODY, SKETCH, PAD, {**FILLET, "radius": 1.0}]
    field = {"kind": "field", "id": "field:filletRadius", "role": "Radius", "value": 1.0}
    advice = teacher(PLAN, snapshot(built, dialog="TaskFilletParameters", elements=[field]))
    assert [(t.id, t.value, t.item, t.source) for t in advice.targets] \
        == [("field:filletRadius", 2, 1, "slot:radius")]
    advice = teacher(PLAN, snapshot(built, dialog="TaskFilletParameters",
                                    elements=[{**field, "value": 2.0}]))
    assert ids(advice) == ["dialog:OK"]
    assert advice.on_plan
    assert advice.built == 1


def test_a_wrong_dialog_is_cancelled_and_a_wrong_feature_is_undone():
    chamfer = {"type": "chamfer", "name": "Chamfer", "body": "Body", "on": "Pad", "edges": 4,
               "size": 1.0, "rule": "top_face", "valid": True}
    advice = teacher(PLAN, snapshot([BODY, SKETCH, PAD, chamfer], dialog="TaskChamferParameters"))
    assert ids(advice) == [CANCEL]
    assert not advice.on_plan
    # Confirmed with the wrong radius: undo.
    advice = teacher(PLAN, snapshot([BODY, SKETCH, PAD, {**FILLET, "radius": 5.0}]))
    assert ids(advice) == [UNDO]
    assert not advice.on_plan
    # ... and with nothing to undo the teacher says so instead of inventing an action.
    advice = teacher(PLAN, snapshot([BODY, SKETCH, PAD, {**FILLET, "radius": 5.0}], buttons=()))
    assert advice.targets == ()
    # A pad of the wrong length under a good fillet costs the fillet too.
    assert ids(teacher(PLAN, snapshot([BODY, SKETCH, {**PAD, "length": 12.0}, FILLET]))) == [UNDO]


def test_a_dialog_or_sketch_that_is_not_needed_is_closed_first():
    built = [BODY, SKETCH, PAD]
    advice = teacher(PLAN, snapshot(built, dialog="TaskFeaturePick"))
    assert ids(advice) == [CANCEL]
    assert advice.on_plan              # tidying, not a repair of the document
    advice = teacher(PLAN, snapshot(built, sketch_open="Sketch", dialog="TaskSketcherConstraints"))
    assert ids(advice) == [LEAVE]


def test_drawing_follows_the_tool_and_its_fields():
    empty = {**SKETCH, "shapes": [], "used_by": None, "closed": False}
    state = {"sketch_open": "Sketch", "dialog": "TaskSketcherConstraints"}
    press = ["button:Sketcher_CreateRectangle_Center"]
    assert ids(teacher(PLAN, snapshot([BODY, empty], **state))) == press
    assert ids(teacher(PLAN, snapshot([BODY, empty], tool="Sketcher_CreateCircle", **state))) == press

    def field(role, value=None):
        return {"kind": "view_field", "id": f"view:{role}", "role": role, "value": value,
                "entered": value is not None}

    tool = {"tool": "Sketcher_CreateRectangle_Center", **state}
    assert ids(teacher(PLAN, snapshot([BODY, empty], elements=[field("x"), field("y")], **tool))) \
        == ["view:x", "view:y"]
    assert ids(teacher(PLAN, snapshot([BODY, empty], elements=[field("x", 0.0), field("y")],
                                      **tool))) == ["view:y"]
    # A number entered wrong: take the tool again, which starts the shape afresh.
    assert ids(teacher(PLAN, snapshot([BODY, empty], elements=[field("x", 7.0), field("y")],
                                      **tool))) == press
    # A wrong shape in the open sketch is undone there; a complete sketch is left.
    wrong = {**empty, "shapes": [{**SKETCH["shapes"][0], "length": 61.0}]}
    assert ids(teacher(PLAN, snapshot([BODY, wrong], **tool))) == [UNDO]
    assert ids(teacher(PLAN, snapshot([BODY, {**SKETCH, "used_by": None}], **tool))) == [LEAVE]
    # The sketcher's toolbars are gone (it happens after an Undo in a sketch): the panel's
    # Close is the way out, whatever was wanted.
    for items in ([BODY, empty], [BODY, {**SKETCH, "used_by": None}]):
        assert ids(teacher(PLAN, snapshot(items, buttons=(UNDO,), **tool))) == ["dialog:Close"]


def test_done_only_when_everything_is_built():
    datum = {"type": "datum_plane", "name": "DatumPlane", "on": "XY_Plane", "plane": "XY",
             "offset": 10.0, "valid": True}
    sketch = {"type": "sketch", "name": "Sketch001", "on": "DatumPlane", "plane": None,
              "height": 10.0, "shapes": [{"shape": "circle", "x": 12.0, "y": -5.0, "diameter": 6.0}],
              "dof": 0, "closed": True, "used_by": "Pocket", "valid": True}
    pocket = {"type": "pocket", "name": "Pocket", "body": "Body", "sketch": "Sketch001",
              "through_all": True, "depth": None, "valid": True}
    whole = [BODY, SKETCH, PAD, FILLET, datum, sketch, pocket]
    advice = teacher(PLAN, snapshot(whole, built=3))
    assert ids(advice) == [DONE]
    assert ids(teacher(PLAN, snapshot(whole, volume=1.0))) == [UNDO]
    assert advice.built == 3
    assert ids(teacher(PLAN, snapshot([*whole[:-1], {**pocket, "through_all": False,
                                                      "depth": 5.0}]))) == [UNDO]
    assert ids(teacher(PLAN, snapshot([*whole, {**datum, "name": "DatumPlane001"}]))) == [UNDO]
    assert ids(teacher(PLAN, snapshot(whole[:-1], selection=[chosen("XY_Plane")], built=2))) \
        == ["tree:Sketch001"]


def test_noise_is_never_a_target_and_never_a_forbidden_button():
    assert not {"button:Std_Open", "button:Std_Save", "button:Std_New",
                "button:PartDesign_Body"} & set(WRONG_BUTTONS)
    built = [BODY, SKETCH, PAD, {**FILLET, "radius": 1.0}]
    elements = [{"kind": "field", "id": "field:filletRadius", "role": "Radius", "value": 1.0},
                {"kind": "dialog_button", "id": "dialog:OK", "role": "OK", "value": None},
                {"kind": "dialog_button", "id": CANCEL, "role": "Cancel", "value": None},
                {"kind": "tree", "id": "tree:Pad", "role": "Pad", "value": None, "type": "pad",
                 "selected": False}]
    state = snapshot(built, dialog="TaskFilletParameters", elements=elements,
                     buttons=(UNDO, "button:Std_Open", "button:PartDesign_Pad"))
    advice = teacher(PLAN, state)
    rng = random.Random(0)
    kinds = set()
    for _ in range(300):
        element, value, kind = wrong_action(rng, Moment(PLAN, state, advice))
        kinds.add(kind)
        assert not is_target((element, value), advice)
        assert element not in ("button:Std_Open", DONE)
    assert {"wrong_value", "ok_too_early", "stray_click", "extra_undo", "wrong_button"} <= kinds
