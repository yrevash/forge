"""Noise, start states, the lean snapshot and recorded sessions (forge/freecad/).

The first tests need no FreeCAD. The rest play real sessions in a headless
FreeCAD and are skipped when it is not installed. No test crashes FreeCAD on
purpose: a lost worker is simulated by `break_worker`, which makes the worker
process exit by itself.
"""

import copy
import itertools
import json
import random
from collections import Counter

import pytest

from forge.freecad import sessions
from forge.freecad.audit import audit_session, replay_session
from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean, same_lean
from forge.freecad.locate import freecad_available
from forge.freecad.noise import (
    CHANCE_AT_TREATMENT,
    CHANCE_BEFORE_DONE,
    FLAVOURS,
    HEAVY_NOISE_LEVELS,
    NEVER_INJECTED,
    NOISE_KINDS,
    NOT_INJECTED,
    Moment,
    chance_at,
    follow_up,
    is_target,
    wrong_command,
    wrong_command_with_detail,
    wrong_value,
)
from forge.freecad.parts import sample_rows
from forge.freecad.play import HARD_EXTRA, HARD_FACTOR, play, rest_of_item
from forge.freecad.recipes import context_of
from forge.freecad.starts import START_KINDS, draw_start
from forge.freecad.teacher import Watch, plan_from_json, script_of, teacher
from forge.freecad.valid import EMPTY_SNAPSHOT, valid_commands
from forge.system1.steps import Step

needs_freecad = pytest.mark.skipif(not freecad_available(), reason="FreeCAD is not installed")

PLAN = [Step("cylinder", {"diameter": 50.0, "height": 12.0}),
        Step("hole", {"diameter": 6.0, "x": 10.0, "y": -5.0}),
        Step("boss", {"diameter": 8.0, "height": 5.0, "x": -12.0, "y": 0.0})]
PART = {"id": "test-cylinder", "family": "composed_cylinder", "source": "test", "license": "forge",
        "generator_version": "test",
        "params": {"base_diameter": 50.0, "base_height": 12.0}}


def body_snapshot() -> dict:
    snapshot = copy.deepcopy(EMPTY_SNAPSHOT)
    snapshot["items"] = [{"type": "body", "name": "Body", "tip": None, "active": True,
                          "valid": True}]
    snapshot["session"].update(document=True, active_body="Body", undo_depth=3,
                               selection={"type": "plane", "name": "XY"})
    return snapshot


# --- without FreeCAD -----------------------------------------------------------------------------

def finished_block_snapshot(selection: dict | None = None, finished: bool = False) -> dict:
    """A 40 x 30 x 10 block, complete: the state right before `done` for BLOCK_PLAN."""
    snapshot = copy.deepcopy(EMPTY_SNAPSHOT)
    snapshot["items"] = [
        {"type": "body", "name": "Body", "tip": "Pad", "active": True, "valid": True},
        {"type": "sketch", "name": "Sketch", "body": "Body", "plane": "XY", "offset": 0.0,
         "shapes": [{"shape": "rectangle", "first": 0, "x": 0.0, "y": 0.0, "length": 40.0,
                     "width": 30.0, "fixed": ["length", "width", "x", "y"]}],
         "dof": 0, "closed": True, "used_by": "Pad", "valid": True, "n_geometry": 5,
         "n_constraints": 13},
        {"type": "pad", "name": "Pad", "body": "Body", "sketch": "Sketch", "length": 10.0,
         "valid": True}]
    snapshot["session"].update(document=True, active_body="Body", tip="Pad", undo_depth=11,
                               selection=selection, finished=finished)
    snapshot["solid"] = {"volume": 12000.0, "bbox": [-20.0, -15.0, 0.0, 20.0, 15.0, 10.0],
                         "solids": 1, "valid": True}
    return snapshot


BLOCK_PLAN = [Step("block", {"length": 40.0, "width": 30.0, "height": 10.0})]
BLOCK_THEN_HOLE = [*BLOCK_PLAN, Step("hole", {"diameter": 6.0, "x": 5.0, "y": -4.0})]


def test_a_wrong_command_is_never_a_target_and_never_restarts_the_session():
    snapshot = body_snapshot()
    advice = teacher(PLAN, snapshot)
    assert advice.names() == ["new_sketch"]
    moment = Moment(PLAN, context_of(PLAN), advice, valid_commands(snapshot),
                    ("select_plane", {"plane": "XY"}))
    rng = random.Random(0)
    seen = Counter()
    for _ in range(600):
        name, args, kind = wrong_command(rng, moment)
        assert name not in NOT_INJECTED
        assert not is_target((name, args), advice)
        seen[kind] += 1
        if kind == "wrong_argument":    # the teacher's command, with another offset
            assert name == "new_sketch" and args["offset"] != 0.0
        if kind == "later_item":        # the hole's and the boss's sketches start at 12
            assert (name, args) in (("new_sketch", {"offset": 12.0}),
                                    ("select_plane", {"plane": "XY"}))
        if kind == "repeat":
            assert (name, args) == moment.previous
        if kind == "extra_undo":
            assert name == "undo"
        if kind == "stray_selection":   # new_sketch uses the selected plane
            assert name in ("select_plane", "clear_selection")
    # There is no solid yet: `done` is not valid, and there are no edges to round.
    assert set(seen) == set(NOISE_KINDS) - {"early_done", "failing_feature"}
    assert seen["wrong_argument"] > max(count for kind, count in seen.items()
                                        if kind != "wrong_argument")
    # The name forge.freecad_multi imports keeps its value.
    assert NEVER_INJECTED == ("done", "new_document")


def test_done_too_early_is_injected_and_the_teacher_answers_undo():
    snapshot = finished_block_snapshot()
    advice = teacher(BLOCK_THEN_HOLE, snapshot)
    assert advice.names() == ["select_plane"] and "done" in valid_commands(snapshot)
    moment = Moment(BLOCK_THEN_HOLE, context_of(BLOCK_THEN_HOLE), advice,
                    valid_commands(snapshot), None, item_start=True)
    rng = random.Random(1)
    kinds = Counter(wrong_command(rng, moment)[2] for _ in range(800))
    assert kinds["early_done"] > 40
    # What the runtime shows after that `done`: the session is marked finished, and only
    # undo and new_document are still valid. The teacher: off plan, undo.
    after = finished_block_snapshot(finished=True)
    assert valid_commands(after) == ["new_document", "undo"]
    answer = teacher(BLOCK_THEN_HOLE, after)
    assert not answer.on_plan and answer.names() == ["undo"]
    assert "finished too early" in answer.why
    # The same state on the complete plan is the end of a session, not a mistake.
    assert teacher(BLOCK_PLAN, after).targets == ()


def test_done_is_never_injected_when_the_plan_is_complete():
    """It would end the session without the teacher having said so (found in the first trial
    of the second mix: ten sessions in a thousand ended with nothing left to do)."""
    selected = finished_block_snapshot(selection={"type": "plane", "name": "XZ"})
    advice = teacher(BLOCK_PLAN, selected)
    assert advice.names() == ["clear_selection"] and "done" in valid_commands(selected)
    moment = Moment(BLOCK_PLAN, context_of(BLOCK_PLAN), advice, valid_commands(selected), None)
    rng = random.Random(4)
    made = [wrong_command(rng, moment) for _ in range(500)]
    assert all(name != "done" for name, _, _ in made)


def test_a_failing_feature_is_edges_then_a_treatment_no_part_has_room_for():
    snapshot = finished_block_snapshot()
    advice = teacher(BLOCK_THEN_HOLE, snapshot)
    moment = Moment(BLOCK_THEN_HOLE, context_of(BLOCK_THEN_HOLE), advice,
                    valid_commands(snapshot), None)
    rng = random.Random(5)
    first = [made for made in (wrong_command(rng, moment) for _ in range(600))
             if made[2] == "failing_feature"]
    assert {name for name, _, _ in first} == {"select_edges", "select_face"}
    for name, _, kind in first[:40]:
        (treatment, args), = follow_up(rng, BLOCK_THEN_HOLE, kind, name)
        assert treatment in (("fillet", "chamfer") if name == "select_edges" else ("thickness",))
        assert next(iter(args.values())) == 3 * 40.0        # three times the largest number
    # Only the selection has a second command; no other kind has one.
    assert follow_up(rng, BLOCK_THEN_HOLE, "failing_feature", "fillet") == []
    assert follow_up(rng, BLOCK_THEN_HOLE, "wrong_argument", "select_edges") == []


def test_before_done_a_stray_selection_is_the_usual_mistake_and_the_repair_is_clear_selection():
    snapshot = finished_block_snapshot()
    advice = teacher(BLOCK_PLAN, snapshot)
    assert advice.names() == ["done"]
    assert chance_at(0.1, advice) == CHANCE_BEFORE_DONE and chance_at(0.0, advice) == 0.0
    assert chance_at(0.7, advice) == 0.7
    moment = Moment(BLOCK_PLAN, context_of(BLOCK_PLAN), advice, valid_commands(snapshot), None)
    rng = random.Random(2)
    made = [wrong_command(rng, moment) for _ in range(400)]
    selections = [name for name, _, kind in made if kind == "stray_selection"]
    assert len(selections) > 200 and "clear_selection" not in selections
    selected = finished_block_snapshot(selection={"type": "plane", "name": "XZ"})
    assert teacher(BLOCK_PLAN, selected).names() == ["clear_selection"]


def test_the_chance_of_noise_is_raised_only_at_the_rare_moments():
    plan = [*BLOCK_PLAN, Step("top_chamfer", {"size": 2.0})]
    edges = finished_block_snapshot(selection={"type": "edges", "rule": "top_face", "of": "Pad",
                                               "count": 4})
    advice = teacher(plan, edges)
    assert advice.names() == ["chamfer"]
    assert chance_at(0.1, advice) == CHANCE_AT_TREATMENT
    assert chance_at(0.1, teacher(PLAN, body_snapshot())) == 0.1
    off_plan = teacher(BLOCK_PLAN, finished_block_snapshot(finished=True) | {"items": []})
    assert chance_at(0.2, off_plan) == 0.2


def test_wrong_numbers_are_near_misses_of_every_flavour_and_never_the_right_number():
    plan = [Step("block", {"length": 85.0, "width": 70.0, "height": 37.0}),
            Step("pocket", {"length": 12.0, "width": 9.0, "depth": 1.0, "x": 19.0, "y": -4.0}),
            Step("hole", {"diameter": 6.0, "x": 5.0, "y": -4.0})]
    rng = random.Random(3)
    seen: dict[str, set] = {}
    for _ in range(600):
        value, flavour = wrong_value(rng, plan, "constrain_x", "value", 19.0, 1, "slot:x")
        assert value != 19.0
        seen.setdefault(flavour, set()).add(value)
    assert seen["wrong_sign"] == {-19.0}
    assert seen["swapped"] <= {12.0, 9.0, 1.0, -4.0}        # the pocket's other slots
    assert seen["other_item"] == {5.0}                      # the hole's x
    assert seen["a_little_off"] & {18.0, 20.0, 91.0}        # one off, or digits swapped
    assert set(seen) == set(FLAVOURS) - {"too_big"}
    # A size is never zero or negative; a fillet also gets numbers no part can take.
    for _ in range(300):
        value, flavour = wrong_value(rng, plan, "fillet", "radius", 2.0, None, "slot:radius")
        assert value > 0 and value != 2.0
        seen.setdefault(flavour, set()).add(value)
    assert max(seen["too_big"]) >= 85.0
    # A word argument gets another word; a count another count.
    assert wrong_value(rng, plan, "mirror", "plane", "YZ", 1, "const")[0] in ("XY", "XZ")
    count, _ = wrong_value(rng, plan, "polar_pattern", "count", 6, None, "slot:count")
    assert isinstance(count, int) and count >= 2 and count != 6
    # The flavour travels with the wrong command, for the record.
    advice = teacher(PLAN, body_snapshot())
    moment = Moment(PLAN, context_of(PLAN), advice, valid_commands(body_snapshot()), None)
    details = {wrong_command_with_detail(rng, moment)[2:] for _ in range(200)}
    assert {detail for kind, detail in details if kind == "wrong_argument"} <= set(FLAVOURS)
    assert all(detail is None for kind, detail in details if kind != "wrong_argument")


def test_the_watch_stops_a_session_whose_own_command_keeps_failing():
    on_plan = teacher(BLOCK_THEN_HOLE, finished_block_snapshot())
    off_plan = teacher(BLOCK_THEN_HOLE, finished_block_snapshot(finished=True))
    assert on_plan.on_plan and not off_plan.on_plan
    watch = Watch()
    # Noise that leads off plan is the point of noise: never counted.
    assert not any(watch.stuck(on_plan, "done", {}, False, "ok", off_plan) for _ in range(5))
    # The teacher's own command, carried out, and the session is off plan: once is noted,
    # the second time at the same place the session is stuck.
    assert not watch.stuck(on_plan, "mirror", {"plane": "YZ"}, True, "ok", off_plan)
    assert not watch.stuck(on_plan, "pad", {"length": 4.0}, True, "ok", off_plan)
    assert not watch.stuck(on_plan, "select_plane", {"plane": "XY"}, True, "ok", on_plan)
    assert watch.stuck(on_plan, "mirror", {"plane": "YZ"}, True, "ok", off_plan)
    # A command of the teacher's that FreeCAD does not carry out counts the same way.
    watch = Watch()
    assert not watch.stuck(on_plan, "pad", {"length": 4.0}, True, "timeout", on_plan)
    assert watch.stuck(on_plan, "pad", {"length": 4.0}, True, "timeout", on_plan)


def test_carrying_on_issues_the_rest_of_the_item_after_the_wrong_command():
    plan = [*BLOCK_PLAN, Step("hole_pair", {"diameter": 6.0, "x": 9.0, "y": 0.0})]
    script = script_of(plan)
    snapshot = finished_block_snapshot(selection={"type": "plane", "name": "XY"})
    advice = teacher(plan, snapshot, script)
    assert advice.names() == ["new_sketch"] and advice.position is not None
    rest = rest_of_item(script, advice, "new_sketch", random.Random(0))
    names = [name for name, _ in rest]
    assert names[0] == "sketch_circle" and names[-4:] == ["leave_sketch", "pocket_through_all",
                                                          "select_tip", "mirror"]
    assert sorted(names[1:4]) == ["constrain_diameter", "constrain_x", "constrain_y"]
    # After a wrong SELECTION the command it was made for is still to come.
    before = teacher(plan, finished_block_snapshot(), script)
    assert before.names() == ["select_plane"]
    assert rest_of_item(script, before, "select_plane", random.Random(0))[0][0] == "new_sketch"
    # Off plan there is nothing to carry on with.
    assert rest_of_item(script, teacher(plan, finished_block_snapshot(finished=True), script),
                        "undo", random.Random(0)) == []


def test_start_states_are_deterministic_and_cover_every_kind():
    kinds = Counter()
    for number in range(600):
        first = draw_start(random.Random(number), PLAN)
        assert first == draw_start(random.Random(number), PLAN)
        kinds[first[0].removeprefix("opened_") if first[2] else first[0]] += 1
        assert first[2] == first[0].startswith("opened_")
    assert set(kinds) == {"empty", "document", "body", "partial", "stray_sketch", "stray_solid",
                          "wrong_partial"}
    assert abs(sum(START_KINDS.values()) - 1.0) < 1e-9
    assert 200 < kinds["empty"] < 310       # about four in ten start with nothing


def test_the_lean_snapshot_drops_the_bulk_and_keeps_what_decides():
    full = body_snapshot()
    full["items"][0]["solid"] = None
    full["items"].append({"type": "sketch", "name": "Sketch", "shapes": [], "valid": True,
                          "geometry": [{"kind": "line"}] * 4, "constraints": [{}] * 9})
    full["solid"] = {"volume": 100.00000012345, "bbox": [0, 0, 0, 1, 1, 1], "size": [1, 1, 1],
                     "solids": 1, "valid": True}
    short = lean(full)
    assert short["items"][1] == {"type": "sketch", "name": "Sketch", "shapes": [], "valid": True,
                                 "n_geometry": 4, "n_constraints": 9}
    assert "solid" not in short["items"][0] and "size" not in short["solid"]
    assert short["session"] == full["session"]
    other = copy.deepcopy(short)
    other["solid"]["volume"] += 1e-6                    # last-digit noise is the same state
    assert same_lean(short, other)
    other["solid"]["volume"] += 1.0
    assert not same_lean(short, other)
    other = copy.deepcopy(short)
    other["session"]["selection"] = None
    assert not same_lean(short, other)


# --- with FreeCAD ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fc():
    with FreeCADClient() as client:
        yield client


@pytest.fixture(scope="module")
def rows():
    return [{**row, "source": "gen:test", "license": "forge", "generator_version": "test",
             "geom_fingerprint": row["id"]} for row in sample_rows(per_base=3, long_per_base=1,
                                                                   seed=9)]


def seed_with(row: dict, wanted) -> int:
    """A seed whose session of this part satisfies `wanted(noise level, start kind)`."""
    from forge.freecad.noise import NOISE_LEVELS
    from forge.system1.steps import steps_of
    plan = steps_of(row["family"], row["params"])
    for seed in range(500):
        rng = random.Random(f"{seed}:{row['id']}")
        noise = rng.choice(NOISE_LEVELS)
        if wanted(noise, draw_start(rng, plan)[0]):
            return seed
    raise AssertionError("no such seed")


@needs_freecad
def test_noisy_sessions_end_done_on_the_stored_part_and_only_the_teacher_is_a_target(fc, rows):
    noisy_steps = 0
    for row in rows:
        seed = seed_with(row, lambda noise, start: noise >= 0.2)
        header, records, end = play(fc, row, seed, "train")
        assert end["end"] == "done" and end["problems"] == []
        assert len(records) < HARD_FACTOR * header["clean_length"] + HARD_EXTRA
        # What is stored survives JSON and passes the audit's own checks.
        header, records, end = json.loads(json.dumps([header, records, end]))
        problems = audit_session(header, records, end, row)
        assert problems["teacher"] == [] and problems["endings"] == []
        plan = plan_from_json(header["plan"])
        for record in records:
            assert record["target"] == [t.to_json() for t in teacher(plan, record["snapshot"]).targets]
            assert {t["command"] for t in record["target"]} <= set(record["valid"])
            noisy_steps += record["executed"]["noise"] is not None
        assert replay_session(fc, header, records, end, row) == []
    assert noisy_steps > 20


@needs_freecad
def test_heavy_noise_shows_the_new_mistakes_and_every_session_still_ends_right(fc, rows):
    """`done` too early, carrying on after a wrong number, a reopened document: each is
    repaired, labelled by the teacher alone, confirmed by the oracle, and replays."""
    kinds, repairs, reopened = Counter(), Counter(), 0
    for row in rows:
        for seed in range(100, 104):
            header, records, end = play(fc, row, seed, "train", HEAVY_NOISE_LEVELS)
            assert end["end"] == "done" and end["problems"] == []
            header, records, end = json.loads(json.dumps([header, records, end]))
            problems = audit_session(header, records, end, row)
            assert problems["teacher"] == [] and problems["oracle"] == []
            assert problems["endings"] == []
            assert replay_session(fc, header, records, end, row) == []
            for record, after in itertools.pairwise(records):
                done = record["executed"]
                kinds[done["noise"]] += 1
                reopened += "before" in record
                if done == {"command": "done", "args": {}, "noise": "early_done"} \
                        and record["reply"]["status"] == "ok":
                    # What a model sees after `done` on an unfinished part: FreeCAD accepted
                    # it, the session is marked finished, and nothing but undo (and
                    # new_document) is valid. If the undo history was lost right then, only
                    # new_document is left.
                    assert after["snapshot"]["session"]["finished"]
                    assert not after["progress"]["on_plan"]
                    repair = "new_document" if "before" in after else "undo"
                    assert after["valid"] == ["new_document", "undo"][:1 if "before" in after else 2]
                    assert [t["command"] for t in after["target"]] == [repair]
                    repairs[repair] += 1
                if "before" in after:       # the undo history was lost on an off-plan state
                    assert after["snapshot"]["session"]["undo_depth"] == 0
                    assert [t["command"] for t in after["target"]] == ["new_document"]
    assert kinds["early_done"] and kinds["carry_on"] and kinds["wrong_argument"]
    assert kinds["failing_feature"] and kinds["wrong_argument"] > kinds["random_valid"]
    assert set(repairs) <= {"undo", "new_document"} and repairs["undo"]
    assert reopened


@needs_freecad
def test_a_session_is_the_same_when_played_twice(fc, rows):
    row = rows[0]
    seed = seed_with(row, lambda noise, start: noise == 0.3)
    first, second = play(fc, row, seed, "train"), play(fc, row, seed, "train")
    assert json.dumps(first) == json.dumps(second)
    assert play(fc, row, seed + 1, "train")[0]["session"] != first[0]["session"]


@needs_freecad
@pytest.mark.parametrize("kind", ["document", "body", "partial", "stray_sketch", "stray_solid",
                                  "wrong_partial", "opened_stray_sketch", "opened_stray_solid",
                                  "opened_partial", "opened_wrong_partial"])
def test_every_start_state_is_cleaned_up_and_built(fc, rows, kind):
    row = rows[1]
    seed = seed_with(row, lambda noise, start: start == kind)
    header, records, end = play(fc, row, seed, "train")
    assert header["start"]["kind"] == kind and header["start"]["commands"]
    assert end["end"] == "done" and end["problems"] == []
    first = records[0]
    if kind.startswith("opened_stray"):     # junk that cannot be undone: start again
        assert first["snapshot"]["session"]["undo_depth"] == 0
        assert [t["command"] for t in first["target"]] == ["new_document"]
    elif kind.startswith("stray"):
        assert [t["command"] for t in first["target"]] == ["undo"]
        assert not first["progress"]["on_plan"]
    elif "wrong_partial" in kind:
        # A correct beginning with one wrong number in it. Usually that is off plan (undo, or
        # new_document when the file was "opened"); a wrong selection is simply replaced.
        repair = "new_document" if kind.startswith("opened") else "undo"
        assert first["progress"]["on_plan"] or [t["command"] for t in first["target"]] == [repair]
    elif "partial" in kind:
        assert first["progress"]["on_plan"] and len(first["snapshot"]["items"]) >= 1


@needs_freecad
def test_a_lost_worker_does_not_change_the_session(rows):
    row = rows[2]
    seed = seed_with(row, lambda noise, start: noise == 0.2 and start == "empty")
    with FreeCADClient() as steady:
        expected = play(steady, row, seed, "train")
    with FreeCADClient() as shaky:
        commands = shaky.command
        count = 0

        def command(name, **args):      # the worker exits by itself before the 15th command
            nonlocal count
            count += 1
            if count == 15:
                shaky.break_worker("crash")
            return commands(name, **args)

        shaky.command = command
        got = play(shaky, row, seed, "train")
        assert shaky.restarts == 1
    assert json.dumps(got) == json.dumps(expected)


@needs_freecad
def test_a_shard_is_written_read_back_and_counted(rows, tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "_client", None)
    with pytest.raises(ValueError, match="frozen"):     # the first recording is never redone
        sessions.write_shard(("train", 0, rows[:4], str(tmp_path)))
    result = sessions.write_shard(("train_v2", 0, rows[:4], str(tmp_path)))
    sessions._client.close()
    path = tmp_path / "train_v2" / "shard000.jsonl.gz"
    assert path.exists() and (tmp_path / "train_v2" / "shard000.stats.json").exists()
    assert not list(tmp_path.glob("train_v2/*.partial"))
    read = list(sessions.read_sessions(path))
    assert [header["part_id"] for header, _, _ in read] == [row["id"] for row in rows[:4]]
    assert result["counts"]["sessions"] == 4 and result["counts"]["end:done"] == 4
    assert result["counts"]["steps"] == sum(len(records) for _, records, _ in read)
    assert result["sha256"] == sessions.sha256(path)
    # Provenance: the code that made it, in the stats file and in every header.
    code = sessions.code_hash()
    stats = json.loads((tmp_path / "train_v2" / "shard000.stats.json").read_text())
    assert stats["code_hash"] == result["code_hash"] == code and stats["mix"] == "v2"
    for header, records, end in read:
        assert {"source", "license", "generator_version"} <= set(header)
        assert header["code_hash"] == code and header["mix"] == "v2"
        assert header["runtime"] == {"exact_undo": True}
        assert end["steps"] == len(records) == records[-1]["t"] + 1
    manifest = sessions.write_manifest(tmp_path, {"mix": "v2", "code_hash": code},
                                       {"train_v2": 1})
    assert manifest["totals"]["sessions"] == 4
    assert manifest["slices"]["train_v2"]["shards"] == 1
    assert manifest["slices"]["train_v2"]["shards_planned"] == 1
    assert manifest["code_versions"][code]["shards"] == {"train_v2": "0"}
    assert manifest["code_versions"][code]["sessions"] == 4
    assert sum(manifest["totals"]["steps_by_number_of_targets"].values()) == \
        manifest["totals"]["steps"]
    # A second call by another run keeps what the first one planned.
    again = sessions.write_manifest(tmp_path, None, {"iid_v2": 3})
    assert again["slices"]["train_v2"]["shards_planned"] == 1
    assert again["slices"]["iid_v2"]["shards_planned"] == 3


@needs_freecad
def test_a_refused_command_leaves_undo_working(fc):
    """A command that fails before changing anything used to leave FreeCAD one undo step ahead."""
    fc.reset()
    for name, args in (("new_document", {}), ("new_body", {}), ("select_plane", {"plane": "XY"}),
                       ("new_sketch", {"offset": 0.0}), ("sketch_slot", {}),
                       ("constrain_length", {"value": 5.0})):
        before = fc.command(name, **args)
    for name, args in (("constrain_width", {"value": 50.0}),        # wider than it is long
                       ("sketch_polygon", {"sides": 2})):
        refused = fc.command(name, **args)
        assert refused["status"] == "rejected" and refused["snapshot"] == before["snapshot"]
        undone = fc.command("undo")
        assert undone["status"] == "ok"
        assert fc.command("constrain_length", value=5.0)["snapshot"] == before["snapshot"]


@needs_freecad
def test_forgotten_undo_stays_forgotten_through_a_long_undo_chain(fc):
    """After forget_undo, undo stops at that point even when FreeCAD has to rebuild the document
    (more than 20 commands back)."""
    fc.reset()
    plan = [*PLAN, Step("hole", {"diameter": 4.0, "x": 0.0, "y": 15.0}),
            Step("blind_hole", {"diameter": 5.0, "depth": 3.0, "x": 0.0, "y": -15.0})]
    reply = None
    for target in iter(lambda: teacher(plan, lean(fc.snapshot())).targets, ()):
        reply = fc.command(target[0].command, **target[0].args)
        if reply["snapshot"]["session"]["tip"] == "Pad" and len(reply["snapshot"]["items"]) == 3 \
                and reply["snapshot"]["session"]["undo_depth"] == 9:
            base = fc.forget_undo()
    assert reply["snapshot"]["session"]["finished"]
    depth = reply["snapshot"]["session"]["undo_depth"]
    assert depth == 4 * 8 + 1 and base["snapshot"]["session"]["undo_depth"] == 0
    for _ in range(depth):
        reply = fc.command("undo")
        assert reply["status"] == "ok"
    assert same_lean(lean(reply["snapshot"]), lean(base["snapshot"]))
    assert "undo" not in reply["valid"]


@needs_freecad
def test_a_fillet_remembers_the_rule_that_chose_its_edges(fc):
    fc.reset()
    plan = [Step("block", {"length": 40.0, "width": 30.0, "height": 10.0}),
            Step("top_chamfer", {"size": 2.0})]
    for target in iter(lambda: teacher(plan, lean(fc.snapshot())).targets, ()):
        reply = fc.command(target[0].command, **target[0].args)
    chamfer = reply["snapshot"]["items"][-1]
    assert (chamfer["type"], chamfer["rule"], chamfer["edges"]) == ("chamfer", "top_face", 4)
    assert fc.command("undo")["status"] == "ok"                 # un-done
    assert fc.command("undo")["snapshot"]["items"][-1]["type"] == "pad"


# --- exact undo (the livelock of 6 Oct 2026) -------------------------------------------------------

BLOCK = [("new_document", {}), ("new_body", {}), ("select_plane", {"plane": "XY"}),
         ("new_sketch", {"offset": 0.0}), ("sketch_rectangle", {}),
         ("constrain_length", {"value": 85.0}), ("constrain_width", {"value": 70.0}),
         ("constrain_x", {"value": 0.0}), ("constrain_y", {"value": 0.0}), ("leave_sketch", {}),
         ("pad", {"length": 37.0})]
# Two rectangles drawn exactly on top of each other, padded: FreeCAD carries it out.
STRAY_PAD = [("select_plane", {"plane": "XY"}), ("new_sketch", {"offset": 37.0}),
             ("sketch_rectangle", {}), ("sketch_rectangle", {}), ("leave_sketch", {}),
             ("pad", {"length": 14.0})]
def pocket_pair(length: float, width: float, depth: float, x: float, y: float) -> list:
    return [("select_plane", {"plane": "XY"}), ("new_sketch", {"offset": 37.0}),
            ("sketch_rectangle", {}), ("constrain_length", {"value": length}),
            ("constrain_width", {"value": width}), ("constrain_x", {"value": x}),
            ("constrain_y", {"value": y}), ("leave_sketch", {}), ("pocket", {"depth": depth}),
            ("select_tip", {}), ("mirror", {"plane": "YZ"})]


# The shortest history found that shows it: a block with one mirrored pocket, then the stray
# pad. (On a bare block, or with the pocket but no mirror, the same stray pad does no harm.)
BEFORE_THE_STRAY_PAD = [*BLOCK, *pocket_pair(12.0, 9.0, 1.0, 19.0, 0.0)]
POCKET_PAIR = pocket_pair(11.0, 16.0, 10.0, 29.0, -16.0)


def _stray_pad_then_pocket_pair(client: FreeCADClient) -> tuple[dict, dict, dict]:
    """(shapes damaged after the undos, the snapshot before the stray pad, the last reply)."""
    client.reset()
    for name, args in BEFORE_THE_STRAY_PAD:
        before = client.command(name, **args)
    for name, args in STRAY_PAD:
        assert client.command(name, **args)["status"] == "ok"
    for _ in STRAY_PAD:
        undone = client.command("undo")
    assert same_lean(lean(undone["snapshot"]), lean(before["snapshot"]))    # nothing to see
    damaged = {name: value for name, value in client.tolerances().items() if value > 1e-6}
    for name, args in POCKET_PAIR:
        reply = client.command(name, **args)
        assert reply["status"] == "ok"
    return damaged, before, reply


@needs_freecad
def test_undo_leaves_nothing_behind_that_the_snapshot_does_not_show(fc):
    assert fc.exact
    damaged, _, reply = _stray_pad_then_pocket_pair(fc)
    assert damaged == {}
    mirror = reply["snapshot"]["items"][-1]
    assert mirror["type"] == "mirror" and mirror["valid"]
    assert reply["snapshot"]["solid"]["volume"] == pytest.approx(
        85 * 70 * 37 - 2 * 12 * 9 * 1 - 2 * 11 * 16 * 10)


@needs_freecad
def test_without_exact_undo_a_removed_pad_breaks_a_later_mirror_and_rebuild_repairs_it():
    """What FreeCAD's own undo does (FreeCAD 1.1.4). If this test fails after a FreeCAD update,
    FreeCAD may have fixed it; exact undo stays right either way."""
    with FreeCADClient(exact_undo=False) as old:
        assert not old.exact
        damaged, _, reply = _stray_pad_then_pocket_pair(old)
        assert damaged and max(damaged.values()) > 1.0      # a tolerance of millimetres
        assert not reply["snapshot"]["items"][-1]["valid"]  # the mirror: "Null shape"
        # The livelock: undo and mirror again gives the same failure, for ever.
        old.command("undo")
        assert not old.command("mirror", plane="YZ")["snapshot"]["items"][-1]["valid"]
        # The way out for a driver that is stuck: build the document again.
        undone = old.command("undo")
        rebuilt = old.rebuild()
        assert same_lean(lean(rebuilt["snapshot"]), lean(undone["snapshot"]))
        assert old.command("mirror", plane="YZ")["snapshot"]["items"][-1]["valid"]
        # Switching exact undo on works on a running worker too.
        old.set_exact_undo(True)
        damaged, _, reply = _stray_pad_then_pocket_pair(old)
        assert damaged == {} and reply["snapshot"]["items"][-1]["valid"]
