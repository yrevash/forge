"""Interface recipes and the pure helpers: no FreeCAD needed."""

from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from forge.freecad_ui import launch
from forge.freecad_ui.recipes import Act, AnyOrder, Context, flatten, recipe, resolve
from forge.system1.steps import FEATURES, STARTS, TREATMENTS, Step

INSIDE = Path(launch.__file__).parent / "inside"
sys.path.insert(0, str(INSIDE))
from numbers_text import number_text
from tools import TOOL_PHASES, Tool

CONTEXT = Context("block", 10.0, 10.0)
SHELLED = Context("block", 10.0, 2.0)
EXAMPLES = {
    "block": {"length": 80, "width": 40, "height": 10},
    "cylinder": {"diameter": 50, "height": 10},
    "hex": {"across_flats": 30, "height": 10},
    "ring": {"outer_diameter": 60, "inner_diameter": 30, "height": 10},
    "corner_radius": {"radius": 3}, "top_chamfer": {"size": 1}, "top_fillet": {"radius": 2},
    "shell": {"wall_thickness": 2},
    "hole": {"diameter": 6, "x": 10, "y": -5},
    "blind_hole": {"diameter": 6, "depth": 4, "x": 10, "y": -5},
    "counterbore": {"hole_diameter": 4, "diameter": 8, "depth": 3, "x": 10, "y": -5},
    "boss": {"diameter": 8, "height": 5, "x": 10, "y": -5},
    "pad": {"length": 12, "width": 6, "height": 4, "x": 10, "y": -5},
    "pocket": {"length": 12, "width": 6, "depth": 4, "x": 10, "y": -5},
    "slot": {"length": 16, "width": 6, "depth": 4, "angle": 90, "x": -9, "y": 0},
    "polar": {"count": 6, "hole_diameter": 4, "circle_diameter": 40},
    "row": {"count": 4, "hole_diameter": 4, "spacing": 12, "y": 5},
    "hole_pair": {"diameter": 6, "x": 10, "y": -5},
    "boss_pair": {"diameter": 8, "height": 5, "x": 10, "y": -5},
    "pocket_pair": {"length": 12, "width": 6, "depth": 4, "x": 10, "y": -5},
}


def actions(kind: str, context: Context = CONTEXT) -> list[Act]:
    return flatten(recipe(Step(kind, EXAMPLES[kind]), context))


def value_of(kind: str, element_id: str, context: Context = CONTEXT):
    return next(a.value for a in actions(kind, context) if a.id == element_id)


def test_every_step_kind_has_an_interface_recipe():
    kinds = [*STARTS, *TREATMENTS, *FEATURES]
    assert len(kinds) == 20
    assert set(kinds) == set(EXAMPLES)
    for kind in kinds:
        assert actions(kind)[-1].id == "dialog:OK", kind


def test_only_edge_and_face_picks_are_semantic():
    semantic = {kind: [a.id for a in actions(kind) if a.semantic] for kind in EXAMPLES}
    assert {k: v for k, v in semantic.items() if v} == {
        "corner_radius": ["pick:edges:vertical"], "top_chamfer": ["pick:edges:top_face"],
        "top_fillet": ["pick:edges:top_face"], "shell": ["pick:face:top"]}


def test_every_typed_number_says_where_it_comes_from():
    for kind, slots in EXAMPLES.items():
        for action in actions(kind):
            if action.value is None:
                continue
            assert action.source is not None, (kind, action.id)
            if action.source.startswith("slot:"):
                assert action.value == slots[action.source.removeprefix("slot:")], (kind, action)
            else:
                assert action.source.split(":")[0] in ("const", "base", "floor", "derived")


def test_derived_numbers():
    assert value_of("hex", "view:corner_radius") == pytest.approx(30 / math.sqrt(3))
    assert value_of("hex", "view:angle") == 90.0
    assert value_of("polar", "view:x") == 20.0
    assert value_of("row", "view:x") == -18.0       # -(4 - 1) * 12 / 2
    # A 16 x 6 slot along Y centred on (-9, 0): its ends are 10 apart, 5 either side.
    assert (value_of("slot", "view:x"), value_of("slot", "view:y")) == (-9, -5.0)
    assert value_of("slot", "view:centre_distance") == 10
    assert value_of("slot", "view:end_radius") == 3.0


def test_cuts_are_sketched_on_top_and_bosses_on_the_floor():
    assert value_of("hole", "field:attachmentOffsetZ", SHELLED) == 10.0
    assert value_of("boss", "field:attachmentOffsetZ", SHELLED) == 2.0
    assert value_of("pad", "field:attachmentOffsetZ", SHELLED) == 2.0


def test_any_order_groups_shuffle_but_keep_their_members():
    import random
    entries = recipe(Step("counterbore", EXAMPLES["counterbore"]), CONTEXT)
    assert any(isinstance(entry, AnyOrder) for entry in entries)
    plain = [a.id for a in flatten(entries)]
    shuffled = [a.id for a in flatten(entries, random.Random(3))]
    assert sorted(plain) == sorted(shuffled)


def test_resolve_fills_in_the_newest_datum_plane_and_the_tip():
    reply = {"context": {"tip": "Pocket002"},
             "elements": [{"kind": "tree", "id": "tree:DatumPlane", "type": "datum_plane"},
                          {"kind": "tree", "id": "tree:DatumPlane002", "type": "datum_plane"},
                          {"kind": "tree", "id": "tree:DatumPlane001", "type": "datum_plane"}]}
    assert resolve(Act("tree:{datum}"), reply).id == "tree:DatumPlane002"
    assert resolve(Act("tree:{tip}"), reply).id == "tree:Pocket002"
    with pytest.raises(LookupError):
        resolve(Act("tree:{tip}"), {"context": {"tip": None}, "elements": []})


def test_numbers_are_typed_plainly():
    assert number_text(40) == "40"
    assert number_text(40.0) == "40"
    assert number_text(-7.5) == "-7.5"
    assert number_text(30 / math.sqrt(3)) == "17.3205080757"
    assert "e" not in number_text(0.00001)
    with pytest.raises(ValueError, match="not a number"):
        number_text("40")
    with pytest.raises(ValueError, match="not a number"):
        number_text(True)


def test_a_drawing_tool_moves_through_its_phases():
    tool = Tool()
    tool.start("Sketcher_CreateSlot")
    assert tool.roles() == ["x", "y"]
    assert tool.enter("y") == "more"
    assert tool.enter("x") == "next_phase"
    assert tool.roles() == ["centre_distance", "angle"]
    assert tool.enter("angle") == "more"
    assert tool.enter("centre_distance") == "next_phase"
    assert tool.enter("end_radius") == "shape_done"
    assert tool.roles() == ["x", "y"]               # ready for another slot
    assert set(TOOL_PHASES) >= {"Sketcher_CreateCircle", "Sketcher_CreateRectangle_Center",
                                "Sketcher_CreateHexagon", "Sketcher_CreateSlot"}


def test_the_isolated_settings_ask_for_english_and_no_start_page():
    root = ET.fromstring(launch._user_cfg())
    texts = {node.get("Name"): node.text for node in root.iter("FCText")}
    flags = {node.get("Name"): node.get("Value") for node in root.iter("FCBool")}
    assert texts["Language"] == "English"
    assert texts["AutoloadModule"] == "PartDesignWorkbench"
    assert flags["ShowOnStartup"] == "0"
    assert flags["AutoSaveEnabled"] == "0"
