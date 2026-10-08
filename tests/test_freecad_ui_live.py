"""The interface runtime against a real, HIDDEN FreeCAD window.

Skipped when FreeCAD.app is not installed, and when FORGE_UI_LIVE is not set to 1:
a hidden FreeCAD window uses about 1 GB and a full core, so these tests run only
when asked for.   FORGE_UI_LIVE=1 uv run pytest tests/test_freecad_ui_live.py
"""

from __future__ import annotations

import os
import random

import pytest

from forge.freecad_ui.launch import freecad_gui_available

pytestmark = pytest.mark.skipif(
    not freecad_gui_available() or os.environ.get("FORGE_UI_LIVE") != "1",
    reason="needs FreeCAD.app and FORGE_UI_LIVE=1 (starts one hidden FreeCAD window)")

BLOCK = {"base_length": 60, "base_width": 40, "base_height": 10,
         "hole_1_diameter": 6, "hole_1_x": 12, "hole_1_y": -5}
VOLUME = 60 * 40 * 10 - 3.141592653589793 * 9 * 10


@pytest.fixture(scope="module")
def ui():
    from forge.freecad_ui.client import UIClient
    with UIClient() as client:
        yield client
    assert client.instance is None      # the hidden FreeCAD was killed


def build_block(ui):
    from forge.freecad_ui.build import build_part
    return build_part(ui, "composed_block", BLOCK, random.Random(0))


def test_a_block_with_a_hole_is_exact(ui):
    result = build_block(ui)
    assert result.problems == []
    assert result.solid["volume"] == pytest.approx(VOLUME, rel=1e-9)
    assert result.solid["size"] == [60.0, 40.0, 10.0]
    assert result.semantic_actions == 0


def test_the_pad_dialog_is_read_as_typed_elements(ui):
    build_block(ui)
    ui.act("pick:plane:XY")
    reply = ui.act("button:PartDesign_NewSketch")
    reply = ui.act("button:Sketcher_CreateCircle")
    assert [e["id"] for e in reply["elements"] if e["kind"] == "view_field"] == ["view:x", "view:y"]
    for name, value in (("view:x", 0), ("view:y", 0), ("view:diameter", 8)):
        assert ui.act(name, value)["status"] == "ok"
    ui.act("button:Sketcher_LeaveSketch")
    before = ui.document()
    reply = ui.act("button:PartDesign_Pad")
    assert reply["context"]["dialog"] == "TaskPadPocketParameters"
    by_id = {e["id"]: e for e in reply["elements"]}
    assert by_id["field:lengthEdit"]["kind"] == "field"
    assert by_id["field:lengthEdit"]["role"] == "Length"
    assert "Through all" not in by_id["dropdown:changeMode"]["entries"]    # a pad has "To last"
    assert {"dialog:OK", "dialog:Cancel"} <= set(by_id)
    # Cancel leaves nothing behind.
    assert ui.act("dialog:Cancel")["status"] == "ok"
    after = ui.document()
    assert after["items"] == before["items"]
    assert after["context"]["undo"] == before["context"]["undo"]


def test_a_refusal_by_freecad_is_reported_not_hung(ui):
    ui.new_part()
    ui.act("button:Std_New")
    ui.act("button:PartDesign_Body")
    reply = ui.act("button:PartDesign_Pocket")
    assert reply["status"] == "refused"
    assert "no solid" in reply["reason"]
    assert ui.act("button:No_Such_Command")["status"] == "rejected"
    assert ui.act("field:lengthEdit", 5)["status"] == "rejected"


def test_a_wrong_confirmed_pad_is_undone_exactly(ui):
    build_block(ui)
    before = ui.document()
    ui.act("pick:edges:vertical")
    ui.act("button:PartDesign_Fillet")
    ui.act("field:filletRadius", 3)
    assert ui.act("dialog:OK")["status"] == "ok"
    assert ui.document()["solid"]["volume"] < before["solid"]["volume"]
    assert ui.act("button:Std_Undo")["status"] == "ok"
    after = ui.document()
    assert after["items"] == before["items"]
    assert after["solid"] == before["solid"]


def test_a_killed_instance_is_replaced_and_the_part_replayed(ui):
    result = build_block(ui)
    assert result.ok
    old = ui.pid
    ui.kill_instance()
    ui.act("pick:edges:vertical")
    reply = ui.act("button:PartDesign_Fillet")
    assert reply["status"] == "ok"
    assert ui.restarts == 1
    assert ui.pid != old
    ui.act("dialog:Cancel")
    assert ui.document()["solid"]["volume"] == pytest.approx(VOLUME, rel=1e-9)
