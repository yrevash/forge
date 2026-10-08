"""The FreeCAD runtime itself: worker, commands, snapshot, undo, recovery. Skipped without FreeCAD."""

import itertools
import math
import sys

import pytest

from forge.freecad.catalogue import COMMANDS
from forge.freecad.client import FreeCADClient
from forge.freecad.locate import freecad_available
from forge.freecad.undo_proof import same_state
from forge.freecad.valid import EMPTY_SNAPSHOT, find, valid_commands

pytestmark = pytest.mark.skipif(not freecad_available(), reason="FreeCAD is not installed")

# Arguments that pass the type check, for "is this command refused in this state?" tests.
SAMPLE_ARGS = {"mm": 5.0, "pos": 1.0, "deg": 90.0, "count": 3}


@pytest.fixture(scope="module")
def fc():
    with FreeCADClient() as client:
        yield client


def run(fc, *commands) -> dict:
    """Issue ("name", {args}) pairs or bare names; every one must be carried out."""
    reply = None
    for command in commands:
        name, args = (command, {}) if isinstance(command, str) else command
        reply = fc.command(name, **args)
        assert reply["status"] == "ok", (name, args, reply.get("reason"))
    return reply


def sketch_on(fc, offset: float, shape: str, **dimensions) -> dict:
    """A new XY sketch with one shape and its dimensions; left closed and selected."""
    name, args = (shape, {}) if isinstance(shape, str) else shape
    return run(fc, ("select_plane", {"plane": "XY"}), ("new_sketch", {"offset": offset}),
               (name, args), *[(f"constrain_{k}", {"value": v}) for k, v in dimensions.items()],
               "leave_sketch")


def block(fc, length=80.0, width=40.0, height=10.0) -> dict:
    run(fc, "new_document", "new_body")
    sketch_on(fc, 0.0, "sketch_rectangle", length=length, width=width, x=0.0, y=0.0)
    return run(fc, ("pad", {"length": height}))


def cylinder(fc, diameter=60.0, height=20.0) -> dict:
    run(fc, "new_document", "new_body")
    sketch_on(fc, 0.0, "sketch_circle", diameter=diameter, x=0.0, y=0.0)
    return run(fc, ("pad", {"length": height}))


def volume(reply) -> float:
    return reply["snapshot"]["solid"]["volume"]


# --- the process -----------------------------------------------------------------------------

def test_our_process_never_imports_freecad(fc):
    block(fc)
    assert "FreeCAD" not in sys.modules and "Part" not in sys.modules


def test_a_new_client_starts_empty():
    with FreeCADClient() as fresh:
        assert fresh.snapshot() == EMPTY_SNAPSHOT
        reply = fresh.command("pad", length=3.0)
        assert reply["status"] == "rejected" and "new_document" in reply["reason"]
        assert reply["valid"] == ["new_document"]


# --- snapshot ----------------------------------------------------------------------------------

def test_snapshot_of_a_block(fc):
    snapshot = block(fc)["snapshot"]
    assert [item["type"] for item in snapshot["items"]] == ["body", "sketch", "pad"]
    body, sketch, pad = snapshot["items"]
    assert body == {"type": "body", "name": "Body", "tip": "Pad", "active": True, "valid": True,
                    "solid": snapshot["solid"]}
    assert (sketch["plane"], sketch["offset"], sketch["dof"], sketch["closed"]) == ("XY", 0, 0, True)
    assert sketch["used_by"] == "Pad" and sketch["body"] == "Body"
    assert sketch["shapes"][0]["shape"] == "rectangle"
    assert sorted(sketch["shapes"][0]["fixed"]) == ["length", "width", "x", "y"]
    assert [g["kind"] for g in sketch["geometry"]] == ["line"] * 4 + ["point"]
    assert sketch["geometry"][0] == {"kind": "line", "from": [-40.0, -20.0], "to": [40.0, -20.0],
                                     "construction": False}
    named = {c["name"]: c["value"] for c in sketch["constraints"] if "name" in c}
    assert named == {"length": 80.0, "width": 40.0, "x": 0.0, "y": 0.0}
    assert pad == {"type": "pad", "name": "Pad", "body": "Body", "sketch": "Sketch",
                   "length": 10.0, "valid": True}
    assert snapshot["session"] == {"document": True, "active_body": "Body", "tip": "Pad",
                                   "open_sketch": None, "selection": None, "undo_depth": 10,
                                   "finished": False}
    assert snapshot["solid"] == {"volume": pytest.approx(32000.0, rel=1e-12),
                                 "bbox": [-40.0, -20.0, 0.0, 40.0, 20.0, 10.0],
                                 "size": [80.0, 40.0, 10.0], "solids": 1, "valid": True}


def test_the_reply_lists_the_valid_commands_and_they_match_the_snapshot(fc):
    reply = block(fc)
    assert reply["valid"] == valid_commands(reply["snapshot"])
    assert {"select_plane", "select_edges", "select_face", "select_tip", "undo", "done"} \
        <= set(reply["valid"])
    assert "pad" not in reply["valid"] and "sketch_circle" not in reply["valid"]


# --- sketch shapes: every order of the dimensions gives the same sketch ---------------------------

SHAPES = {
    "sketch_rectangle": {"length": 90.0, "width": 7.0, "x": -12.0, "y": 30.0},
    "sketch_circle": {"diameter": 44.0, "x": 5.0, "y": -6.0},
    ("sketch_polygon", (("sides", 6),)): {"across_flats": 40.0, "angle": 90.0, "x": 0.0, "y": 3.0},
    "sketch_slot": {"length": 120.0, "width": 35.0, "angle": 90.0, "x": 8.0, "y": -2.0},
}


@pytest.mark.parametrize("shape", list(SHAPES), ids=str)
def test_dimensions_can_come_in_any_order(fc, shape):
    dimensions = SHAPES[shape]
    draw = shape if isinstance(shape, str) else (shape[0], dict(shape[1]))
    orders = list(itertools.permutations(dimensions))
    results = []
    for order in orders[::max(1, len(orders) // 24)]:      # at most ~24 orders of each shape
        run(fc, "new_document", "new_body")
        reply = sketch_on(fc, 0.0, draw, **{name: dimensions[name] for name in order})
        sketch = reply["snapshot"]["items"][-1]
        assert sketch["dof"] == 0 and sketch["closed"]
        results.append((sketch["geometry"], {k: v for k, v in sketch["shapes"][0].items()
                                             if k != "fixed"}))
    assert all(result == results[0] for result in results)


def test_hexagon_has_flats_on_x_and_corners_on_y(fc):
    run(fc, "new_document", "new_body")
    sketch_on(fc, 0.0, ("sketch_polygon", {"sides": 6}), across_flats=40.0, angle=90.0, x=0.0,
              y=0.0)
    solid = run(fc, ("pad", {"length": 10.0}))["snapshot"]["solid"]
    assert solid["size"] == pytest.approx([40.0, 80.0 / math.sqrt(3), 10.0], abs=1e-9)
    assert solid["volume"] == pytest.approx(math.sqrt(3) / 2 * 40.0**2 * 10.0, rel=1e-12)


def test_a_slot_shorter_than_its_width_is_rejected_and_changes_nothing(fc):
    run(fc, "new_document", "new_body", ("select_plane", {"plane": "XY"}),
        ("new_sketch", {"offset": 0.0}), "sketch_slot", ("constrain_width", {"value": 30.0}))
    before = fc.snapshot()
    reply = fc.command("constrain_length", value=20.0)
    assert reply["status"] == "rejected" and "longer than it is wide" in reply["reason"]
    assert reply["snapshot"] == before


# --- features ----------------------------------------------------------------------------------

def test_pocket_hole_and_counterbore_volumes(fc):
    start = volume(block(fc))
    sketch_on(fc, 10.0, "sketch_circle", diameter=6.0, x=10.0, y=5.0)
    through = volume(run(fc, "pocket_through_all"))
    assert start - through == pytest.approx(math.pi * 9 * 10, rel=1e-9)
    sketch_on(fc, 10.0, "sketch_circle", diameter=8.0, x=-10.0, y=5.0)
    blind = volume(run(fc, ("hole_blind", {"diameter": 8.0, "depth": 4.0})))
    assert through - blind == pytest.approx(math.pi * 16 * 4, rel=1e-9)
    sketch_on(fc, 10.0, "sketch_circle", diameter=4.0, x=-25.0, y=-8.0)
    reply = run(fc, ("hole_counterbore", {"diameter": 4.0, "counterbore_diameter": 9.0,
                                          "counterbore_depth": 3.0}))
    removed = math.pi * 4 * 10 + math.pi * (4.5**2 - 2**2) * 3
    assert blind - volume(reply) == pytest.approx(removed, rel=1e-9)
    hole = reply["snapshot"]["items"][-1]
    assert (hole["type"], hole["through_all"], hole["counterbore_diameter"]) == ("hole", True, 9.0)
    sketch_on(fc, 10.0, "sketch_circle", diameter=5.0, x=25.0, y=8.0)
    assert volume(run(fc, ("hole_through", {"diameter": 5.0}))) == pytest.approx(
        volume(reply) - math.pi * 2.5**2 * 10, rel=1e-9)


def test_edges_are_selected_by_rule(fc):
    block(fc)
    counts = {rule: run(fc, ("select_edges", {"rule": rule}))["snapshot"]["session"]["selection"]
              for rule in ("vertical", "top_face", "bottom_face", "all")}
    assert {rule: s["count"] for rule, s in counts.items()} == {
        "vertical": 4, "top_face": 4, "bottom_face": 4, "all": 12}
    assert counts["vertical"] == {"type": "edges", "rule": "vertical", "of": "Pad", "count": 4}
    filleted = run(fc, ("select_edges", {"rule": "vertical"}), ("fillet", {"radius": 4.0}))
    assert volume(filleted) == pytest.approx((80 * 40 - (4 - math.pi) * 16) * 10, rel=1e-9)
    assert filleted["snapshot"]["session"]["selection"] is None     # a new solid: start afresh


def test_thickness_hollows_inwards(fc):
    cylinder(fc)
    reply = run(fc, ("select_face", {"rule": "top"}), ("thickness", {"value": 3.0}))
    assert volume(reply) == pytest.approx(math.pi * (30**2 * 20 - 27**2 * 17), rel=1e-9)
    assert reply["snapshot"]["solid"]["size"] == [60.0, 60.0, 20.0]


def test_patterns_and_mirror(fc):
    start = volume(cylinder(fc))
    one = math.pi * 2.5**2 * 20
    sketch_on(fc, 20.0, "sketch_circle", diameter=5.0, x=22.0, y=0.0)
    reply = run(fc, "pocket_through_all", "select_tip", ("polar_pattern", {"count": 5, "axis": "Z"}))
    assert start - volume(reply) == pytest.approx(5 * one, rel=1e-9)
    assert reply["snapshot"]["session"]["tip"] == "PolarPattern"
    assert reply["snapshot"]["items"][-1] == {
        "type": "polar_pattern", "name": "PolarPattern", "body": "Body", "of": "Pocket",
        "count": 5, "angle": 360.0, "axis": "Z", "valid": True}
    after_polar = volume(reply)
    sketch_on(fc, 20.0, "sketch_circle", diameter=5.0, x=-6.0, y=10.0)
    reply = run(fc, "pocket_through_all", "select_tip",
                ("linear_pattern", {"count": 3, "spacing": 6.0, "direction": "X"}))
    assert after_polar - volume(reply) == pytest.approx(3 * one, rel=1e-9)
    after_row = volume(reply)
    sketch_on(fc, 20.0, "sketch_circle", diameter=5.0, x=8.0, y=-12.0)
    reply = run(fc, ("pad", {"length": 7.0}), "select_tip", ("mirror", {"plane": "YZ"}))
    assert volume(reply) - after_row == pytest.approx(2 * math.pi * 2.5**2 * 7, rel=1e-9)
    assert fc.probe([[8, -12, 26], [-8, -12, 26], [8, 12, 26]]) == [True, True, False]


def test_revolution_about_the_sketch_axis(fc):
    run(fc, "new_document", "new_body", ("select_plane", {"plane": "XZ"}),
        ("new_sketch", {"offset": 0.0}), "sketch_rectangle",
        ("constrain_length", {"value": 5.0}), ("constrain_width", {"value": 10.0}),
        ("constrain_x", {"value": 2.5}), ("constrain_y", {"value": 5.0}), "leave_sketch")
    reply = run(fc, ("revolution", {"angle": 360.0, "axis": "V"}))
    assert volume(reply) == pytest.approx(math.pi * 25 * 10, rel=1e-9)
    assert reply["snapshot"]["items"][-1]["axis"] == "V"


def test_two_bodies_in_one_document(fc):
    block(fc)
    run(fc, "new_body")
    sketch_on(fc, 30.0, "sketch_circle", diameter=10.0, x=0.0, y=0.0)
    snapshot = run(fc, ("pad", {"length": 5.0}))["snapshot"]
    bodies = [item for item in snapshot["items"] if item["type"] == "body"]
    assert [(b["name"], b["active"], b["tip"]) for b in bodies] == [
        ("Body", False, "Pad"), ("Body001", True, "Pad001")]
    assert bodies[0]["solid"]["volume"] == pytest.approx(32000.0)
    assert snapshot["solid"]["bbox"][2::3] == [30.0, 35.0]      # the active body's own solid
    assert find(snapshot, "Sketch001")["body"] == "Body001"


# --- what is refused ---------------------------------------------------------------------------

def test_commands_that_are_not_valid_are_refused_and_change_nothing(fc):
    """In several states: every command the valid list leaves out is rejected by the worker."""
    block(fc)
    states = ["new_document", "new_body", ("select_plane", {"plane": "XY"}),
              ("new_sketch", {"offset": 0.0}), "sketch_circle",
              ("constrain_diameter", {"value": 9.0}), "leave_sketch", ("pad", {"length": 4.0}),
              ("select_edges", {"rule": "all"}), "select_tip", "done"]
    for command in states:
        reply = run(fc, command)
        before = reply["snapshot"]
        refused = [name for name in COMMANDS if name not in reply["valid"]]
        assert refused
        for name in refused:
            args = {arg: kind[0] if isinstance(kind, tuple) else SAMPLE_ARGS[kind]
                    for arg, kind in COMMANDS[name]["args"].items()}
            answer = fc.command(name, **args)
            assert answer["status"] == "rejected", (command, name)
            assert answer["snapshot"] == before


def test_bad_arguments_are_rejected(fc):
    reply = block(fc)
    run(fc, ("select_edges", {"rule": "vertical"}))
    for args in ({"radius": -1.0}, {"radius": "big"}, {}, {"size": 1.0}):
        answer = fc.command("fillet", **args)
        assert answer["status"] == "rejected"
        assert answer["snapshot"]["items"] == reply["snapshot"]["items"]


# --- undo ----------------------------------------------------------------------------------------

def test_undo_walks_back_through_every_snapshot_past_freecads_own_limit(fc):
    """35 document edits, then undo them all. FreeCAD itself remembers only 20."""
    trail = [run(fc, "new_document")["snapshot"]]
    commands = ["new_body"]
    for index in range(6):
        commands += [("select_plane", {"plane": "XY"}), ("new_sketch", {"offset": 2.0 * index}),
                     "sketch_circle", ("constrain_diameter", {"value": 30.0 - 3 * index}),
                     ("constrain_x", {"value": 0.0}), ("constrain_y", {"value": 0.0}),
                     "leave_sketch", ("pad", {"length": 2.0})]
    for command in commands:
        trail.append(run(fc, command)["snapshot"])
    edits = sum(COMMANDS[c if isinstance(c, str) else c[0]]["changes_document"] for c in commands)
    assert edits > 30
    for depth in range(len(commands), 0, -1):
        undone = run(fc, "undo")["snapshot"]
        # "volume noise" = equal but for the last bits of a re-computed volume
        assert same_state(undone, trail[depth - 1]) != "different", depth
    assert fc.command("undo")["status"] == "rejected"


def test_a_wrong_command_can_be_undone_with_nothing_left_behind(fc):
    before = block(fc)["snapshot"]
    objects = fc.objects()
    # A fillet far too large: FreeCAD carries the command out and marks the feature invalid.
    reply = run(fc, ("select_edges", {"rule": "all"}), ("fillet", {"radius": 500.0}))
    assert reply["snapshot"]["items"][-1]["valid"] is False
    run(fc, "undo", "undo")
    assert fc.snapshot() == before and fc.objects() == objects
    # A whole wrong feature (a boss nobody asked for), then abandoned half-way through the next.
    sketch_on(fc, 10.0, "sketch_circle", diameter=7.0, x=3.0, y=3.0)
    run(fc, ("pad", {"length": 50.0}), ("select_plane", {"plane": "XY"}),
        ("new_sketch", {"offset": 10.0}), "sketch_slot")
    assert len(fc.objects()) == len(objects) + 3
    for _ in range(11):
        run(fc, "undo")
    assert fc.snapshot() == before and fc.objects() == objects


# --- recovery --------------------------------------------------------------------------------

def test_the_client_recovers_from_a_crash_and_a_hang():
    with FreeCADClient(timeout=3.0) as client:
        block(client)
        before, first_pid = client.snapshot(), client.pid
        client.break_worker("crash")
        reply = client.command("select_edges", rule="vertical")     # finds the worker dead
        assert reply["status"] == "ok" and client.restarts == 1 and client.failures == ["crash"]
        assert client.pid != first_pid
        assert reply["snapshot"]["items"] == before["items"]        # the session was replayed
        client.break_worker("hang")
        reply = client.command("fillet", radius=4.0)                # no answer within 3 s
        assert reply["status"] == "ok" and client.failures == ["crash", "timeout"]
        assert reply["snapshot"]["items"][-1]["type"] == "fillet"
        assert reply["snapshot"]["solid"]["volume"] == pytest.approx(
            (80 * 40 - (4 - math.pi) * 16) * 10, rel=1e-9)


def test_the_worker_is_swapped_for_a_fresh_one_after_enough_documents():
    with FreeCADClient(recycle_after=2) as client:
        pids = []
        for _ in range(5):
            assert volume(block(client)) == pytest.approx(32000.0)
            pids.append(client.pid)
        assert pids[0] == pids[1] != pids[2] == pids[3] != pids[4]
        assert (client.recycles, client.restarts, client.failures) == (2, 0, [])


# --- saving ------------------------------------------------------------------------------------

def test_the_saved_document_reopens_to_the_same_solid(fc, tmp_path):
    reply = block(fc)
    run(fc, ("select_edges", {"rule": "top_face"}), ("chamfer", {"size": 1.0}))
    solid = fc.snapshot()["solid"]
    path = fc.save(tmp_path / "block.FCStd")
    assert path.stat().st_size > 1000
    assert path.read_bytes()[:2] == b"PK"           # an .FCStd file is a zip archive
    reopened = fc.measure_file(path)
    assert reopened["volume"] == pytest.approx(solid["volume"], rel=1e-12)
    assert reopened["size"] == solid["size"]
    assert solid["volume"] < volume(reply)
