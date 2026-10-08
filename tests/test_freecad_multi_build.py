"""Structures built in FreeCAD against their references; the teacher, a session, a saved file.

Skipped without FreeCAD. One worker for the whole file; the two tests that need the
reference kernel start one sandbox each.
"""

import json
import random
import zipfile
from pathlib import Path

import pytest

from forge.freecad.locate import freecad_available
from forge.freecad_multi import sources
from forge.freecad_multi.audit import audit_session
from forge.freecad_multi.build import (
    build_structure,
    compare_with_kernel,
    compare_with_resolver,
    compare_with_row,
    run_script,
)
from forge.freecad_multi.client import MultiClient
from forge.freecad_multi.lean import lean
from forge.freecad_multi.play import play
from forge.freecad_multi.recipes import flatten, script_of, structure_items
from forge.freecad_multi.save import save_structure
from forge.freecad_multi.shards import pack, unpack
from forge.freecad_multi.teacher import clean_length, teacher
from forge.resolve import resolve_text
from forge.resolve import space as sp
from forge.resolve.bodies import Body
from forge.resolve.program import write_program
from forge.runs import PROJECT_ROOT
from forge.sandbox import Sandbox

pytestmark = pytest.mark.skipif(not freecad_available(), reason="FreeCAD is not installed")

PLAN = """\
deck: box 120 by 80 by 10, above ground, bottom at height 40
post: box 20 by 20 by rest, under deck, down to ground
ramp: wedge 40 by 30 by 20 pointing left, on top of deck, flush with deck's left
rail: bar L 20 by 20 by 3 by 80 lying along depth, on top of deck, flush with deck's right
ball: sphere 30, on top of deck
on deck's front: hole diameter 6
on deck: pocket length 20, width 10, depth 4, 15 from deck's right
done
"""


@pytest.fixture(scope="module")
def fc():
    with MultiClient() as client:
        yield client


def shaped(name: str, shape: str, sizes: dict, profile: str | None = None, **more) -> Body:
    return Body(name, name, 1, shape, profile, sizes, sp.IDENTITY, (0.0, 0.0, 0.0), **more)


SHAPES = [
    shaped("box", "box", {"length": 50.0, "depth": 30.0, "height": 20.0}),
    shaped("cylinder", "cylinder", {"diameter": 30.0, "height": 40.0}),
    shaped("tube", "tube", {"outer diameter": 40.0, "inner diameter": 30.0, "height": 25.0}),
    shaped("cone", "cone", {"bottom diameter": 40.0, "top diameter": 10.0, "height": 30.0}),
    shaped("point", "cone", {"bottom diameter": 40.0, "top diameter": 0.0, "height": 30.0}),
    shaped("sphere", "sphere", {"diameter": 30.0}),
    shaped("dome", "dome", {"diameter": 60.0, "height": 20.0}),
    shaped("hex", "prism", {"sides": 6, "across": 40.0, "height": 15.0}),
    shaped("five", "prism", {"sides": 5, "across": 40.0, "height": 15.0}),
    shaped("three", "prism", {"sides": 3, "across": 40.0, "height": 15.0}),
    shaped("wedge", "wedge", {"length": 40.0, "depth": 30.0, "height": 20.0}, pointing="left",
           flat="bottom"),
    shaped("wedge down", "wedge", {"length": 40.0, "depth": 30.0, "height": 20.0},
           pointing="down", flat="back"),
    shaped("taper", "tapered box", {"bottom length": 60.0, "bottom depth": 40.0,
                                    "top length": 20.0, "top depth": 0.0, "height": 30.0}),
    shaped("bar l", "bar", {"profile width": 30.0, "profile depth": 20.0, "thickness": 4.0,
                            "length": 80.0}, "l"),
    shaped("bar t", "bar", {"profile width": 30.0, "profile depth": 20.0, "thickness": 4.0,
                            "length": 80.0}, "t"),
    shaped("bar u", "bar", {"profile width": 30.0, "profile depth": 20.0, "thickness": 4.0,
                            "length": 80.0}, "u"),
    shaped("bar i", "bar", {"profile width": 30.0, "profile depth": 20.0, "thickness": 4.0,
                            "length": 80.0}, "i"),
    shaped("pipe", "bar", {"outer diameter": 30.0, "wall": 3.0, "length": 80.0}, "tube"),
]
TURNS = [sp.IDENTITY, sp.rotation(1, 90), sp.rotation(0, -90), sp.rotation(0, 180),
         sp.mirror(0), sp.multiply(sp.rotation(2, 90), sp.mirror(0)), sp.rotation(2, 37.0),
         sp.multiply(sp.rotation(2, 20.0), sp.rotation(1, 55.0))]


def test_every_shape_in_every_kind_of_place_matches_the_resolver(fc):
    """18 shapes x 8 turns, set out on a grid: frame, volume, one solid each, nothing overlaps."""
    bodies = []
    for row, shape in enumerate(SHAPES):
        for column, turn in enumerate(TURNS):
            if shape.shape == "wedge" and turn not in (sp.IDENTITY, sp.mirror(0)):
                continue                # the plan language never turns a wedge
            name = f"{shape.name} {column}"
            bodies.append(Body(name, name, 1 + len(bodies), shape.shape, shape.profile,
                               shape.sizes, turn, (200.0 * column, 200.0 * row, 100.0),
                               pointing=shape.pointing, flat=shape.flat))
    result = build_structure(fc, bodies, touching=set(), rng=random.Random(1))
    assert result.problems == []
    assert result.snapshot["structure"]["bodies"] == len(bodies) == 132
    assert {item.placement for item in result.items} == {
        "standing", "quarter turn", "standing, mirror image", "quarter turn, mirror image",
        "odd turn about the upright axis", "tilted"}


def test_the_example_chair_builds_and_matches(fc):
    resolution = resolve_text((PROJECT_ROOT / "forge/plan/examples/chair.txt").read_text())
    assert resolution.complete
    result = build_structure(fc, resolution.bodies, resolution.touching, random.Random(2))
    assert result.problems == []
    whole = sp.around([body.frame for body in resolution.bodies])
    assert result.snapshot["structure"]["size"] == pytest.approx(list(whole.size), abs=1e-9)
    assert result.snapshot["structure"]["bodies"] == len(resolution.bodies) == 16
    assert result.snapshot["structure"]["overlapping"] == []
    assert result.snapshot["session"]["finished"]


def test_structure_rows_match_their_stored_kernel_measurement(fc):
    for kind in sources.structure_kinds()[:3]:
        row = next(sources.structure_rows(kind, 1))
        bodies = sources.row_bodies(row)
        result = build_structure(fc, bodies)
        assert result.problems == [], kind
        assert compare_with_row(row, result.snapshot) == [], kind


def test_a_plan_with_features_is_the_reference_solid_for_solid(fc):
    """Wedge, L bar, ball, a hole in a side, a pocket: each FreeCAD solid shares all of its
    volume with the reference kernel's. A build of the WRONG wedge is noticed."""
    resolution = resolve_text(PLAN)
    assert resolution.complete, [(reply.text, reply.reply) for reply in resolution.replies]
    bodies = resolution.bodies
    result = build_structure(fc, bodies, resolution.touching)
    assert result.problems == []
    with Sandbox() as sandbox:
        reply = sandbox.run(write_program(bodies, export=True), timeout=120, structure=True)
        work_dir = Path(sandbox._work_dir)
        assert compare_with_kernel(fc, bodies, result.items, result.snapshot, reply,
                                   work_dir) == []

        # The same structure with the ramp sloping the other way: same frame, same volume,
        # so the resolver's arithmetic cannot tell; the shared volume can.
        wrong = [body if body.name != "ramp" else Body(
            body.name, body.owner, body.line, body.shape, body.profile, body.sizes, body.matrix,
            body.centre, pointing="right", flat=body.flat) for body in bodies]
        mistaken = run_script(fc, structure_items(wrong))
        assert compare_with_resolver(bodies, mistaken.items, mistaken.snapshot) == []
        found = compare_with_kernel(fc, bodies, mistaken.items, mistaken.snapshot, reply, work_dir)
        assert len(found) == 1 and found[0].startswith("ramp (wedge, standing): FreeCAD's solid")


def test_a_saved_structure_is_complete_and_carries_display_settings(fc, tmp_path):
    row = next(sources.structure_rows("stool", 1))
    report = save_structure(fc, sources.row_bodies(row), None, tmp_path / "stool.FCStd")
    assert report["reopened_same"] and report["bodies"] == len(sources.row_bodies(row))
    with zipfile.ZipFile(report["path"]) as saved:
        names = saved.namelist()
        gui = saved.read("GuiDocument.xml").decode()
        document = saved.read("Document.xml").decode()
    assert names[0] == "Document.xml" and names[-1] == "GuiDocument.xml"
    assert gui.count('<Bool value="true"/>') == 2 * report["bodies"]     # each body and its tip
    assert "OrthographicCamera" in gui
    first = sources.row_bodies(row)[0].name
    assert f'<String value="{first}"/>' in document         # the body carries the plan's name


# --- the teacher -----------------------------------------------------------------------------------

def small_row() -> dict:
    return next(sources.structure_rows("stool", 1))


def plan_of(row: dict) -> tuple[list[Body], list]:
    bodies = sources.row_bodies(row)
    return bodies, structure_items(bodies)


def drive(fc, items, stop_after: int | None = None) -> dict:
    """Follow the teacher from an empty FreeCAD; returns the last reply."""
    reply = fc.reset()
    script = script_of(items)
    for step in range(10 * clean_length(items)):
        advice = teacher(items, lean(reply["snapshot"]), script)
        assert advice.on_plan, advice.why
        if not advice.targets or step == stop_after:
            return reply
        target = advice.targets[0]
        assert target.command in reply["valid"]
        reply = fc.command(target.command, **target.args)
        assert reply["status"] == "ok", (target, reply.get("reason"))
    raise AssertionError("the teacher never finished")


def test_following_the_teacher_builds_the_structure_in_the_clean_number_of_steps(fc):
    row = small_row()
    bodies, items = plan_of(row)
    reply = drive(fc, items)
    assert reply["snapshot"]["session"]["finished"]
    assert compare_with_resolver(bodies, items, reply["snapshot"]) == []
    assert compare_with_row(row, reply["snapshot"]) == []
    assert reply["snapshot"]["session"]["undo_depth"] == clean_length(items) - 1


def test_the_teacher_builds_a_plan_with_features_sides_and_a_return_to_an_earlier_body(fc):
    """The two feature lines come after four more parts: the teacher must go back to body 1."""
    resolution = resolve_text(PLAN)
    items = structure_items(resolution.bodies)
    assert [item.kind for item in items] == ["part"] * 5 + ["feature"] * 2
    reply = fc.reset()
    script, said = script_of(items), []
    while True:
        advice = teacher(items, lean(reply["snapshot"]), script)
        assert advice.on_plan, advice.why
        if not advice.targets:
            break
        target = advice.targets[-1]         # the last of an any-order set, for a change
        said.append(target.command)
        reply = fc.command(target.command, **target.args)
        assert reply["status"] == "ok", (target, reply.get("reason"))
    assert said.count("activate_body") == 1 and "select_side" in said and "pocket" in said
    assert len(said) == clean_length(items)
    assert compare_with_resolver(resolution.bodies, items, reply["snapshot"],
                                 resolution.touching) == []


def test_the_teacher_calls_undo_for_a_misplaced_a_missized_and_an_extra_body(fc):
    _, items = plan_of(small_row())
    first_part = len(flatten(items[0].entries))
    reply = drive(fc, items, stop_after=1 + first_part + 1)     # body 1 done, body 2 just made

    def says(reply: dict) -> tuple[list[str], bool]:
        advice = teacher(items, lean(reply["snapshot"]))
        return advice.names(), advice.on_plan

    on_plan = says(reply)
    assert on_plan[1]
    # A wrongly placed body: the active one is moved to where nothing should be.
    moved = fc.command("move_body", x=123.0, y=45.0, z=678.0)
    assert says(moved) == (["undo"], False)
    assert says(fc.command("undo")) == on_plan
    # An earlier body moved: found although it is not the active one any more.
    fc.command("activate_body", index=1)
    moved = fc.command("move_body", x=0.0, y=0.0, z=-50.0)
    assert says(moved) == (["undo"], False)
    undone = fc.command("undo")
    # The wrong body is still the active one: that is repaired by activating, not by undo.
    advice = teacher(items, lean(undone["snapshot"]))
    assert advice.on_plan and advice.names() == ["activate_body"]
    assert advice.targets[0].args == {"index": 2}
    back = fc.command("activate_body", index=2)
    assert says(back)[1]
    # An extra body nobody asked for.
    assert says(fc.command("new_body")) == (["undo"], False)
    assert says(fc.command("undo"))[1]


def test_a_wrong_size_is_off_plan_at_once(fc):
    _, items = plan_of(small_row())
    reply = drive(fc, items, stop_after=5)      # in the first sketch, shape drawn
    advice = teacher(items, lean(reply["snapshot"]))
    target = next(t for t in advice.targets if t.command.startswith("constrain_")
                  and t.args["value"] > 0)
    wrong = fc.command(target.command, value=target.args["value"] + 7.0)
    assert wrong["status"] == "ok"
    after = teacher(items, lean(wrong["snapshot"]))
    assert not after.on_plan and after.names() == ["undo"]


def test_a_noisy_session_ends_on_the_right_structure_and_passes_its_audit(fc):
    row = small_row()
    unit = sources.unit_from_row(row)
    header, records, end = play(fc, unit, seed=7, noise_levels=(0.3,))
    assert end["end"] == "done" and end["problems"] == []
    assert header["noise_level"] == 0.3 and header["source"] == row["source"]
    noisy = [record for record in records if record["executed"]["noise"]]
    assert len(noisy) > 10
    assert any(not record["progress"]["on_plan"] for record in records)
    assert all(record["target"] for record in records)
    # What is stored is what the audit recomputes (it reads JSON, so compare as JSON). The
    # audit's split check is about data/plans slices; a bare row has none, so leave it out.
    stored = [json.loads(json.dumps(line)) for line in (header, *records, end)]
    problems = audit_session({**stored[0], "slice": "train", "split": "train",
                              "plans_split": None}, stored[1:-1], stored[-1], unit,
                             {"s1_split": "train", "split": None})
    assert problems["teacher"] == [] and problems["oracle"] == [] and problems["endings"] == []
    # The file form (a delta per step) gives every snapshot back exactly.
    assert unpack(json.loads(json.dumps(pack(stored[1:-1], stored[-1])))) == (stored[1:-1],
                                                                              stored[-1])


def test_a_stored_plan_resolves_to_its_stored_parts_and_plays_a_session(fc):
    files = sources.plan_files("structures", "iid")
    if not files:
        pytest.skip("data/plans has not been generated")
    record = next(r for r in sources.plan_records(files[0]) if 2 <= len(r["parts"]) <= 8)
    unit = sources.unit_from_record(record, files[0])
    assert (unit.split, unit.source, unit.license) == ("iid", "gen:plans:structures", "forge")
    header, records, end = play(fc, unit, seed=0, noise_levels=(0.2,), slice_name="iid")
    assert end["end"] == "done" and end["problems"] == []
    stored = [json.loads(json.dumps(line)) for line in (header, *records, end)]
    assert audit_session({**stored[0], "plans_split": "iid"}, stored[1:-1], stored[-1], unit,
                         {"s1_split": "iid", "split": "iid"}) == {
        name: [] for name in ("teacher", "oracle", "replay", "splits", "content", "coverage",
                              "endings")}
    # A record whose stored parts are not what its lines resolve to is refused.
    moved = json.loads(json.dumps(record))
    moved["parts"][0]["low"][0] += 1.0
    with pytest.raises(ValueError, match="is not where the record says"):
        sources.unit_from_record(moved, files[0])
