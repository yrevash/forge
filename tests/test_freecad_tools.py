"""The tools around the session files: oracle, audit checks, manifest, baselines. No FreeCAD."""

import ast
import copy
import json
from collections import Counter
from pathlib import Path

from forge.freecad import audit, baselines, manifest, oracle, shards
from forge.freecad.load import view
from forge.freecad.teacher import teacher
from forge.freecad.valid import EMPTY_SNAPSHOT, valid_commands
from forge.system1.steps import Step

PLAN = [Step("block", {"length": 40.0, "width": 30.0, "height": 10.0}),
        Step("top_chamfer", {"size": 2.0})]
BODY = {"type": "body", "name": "Body", "tip": None, "active": True, "valid": True}
SKETCH = {"type": "sketch", "name": "Sketch", "body": "Body", "plane": "XY", "offset": 0.0,
          "shapes": [{"shape": "rectangle", "first": 0, "x": 0.0, "y": 0.0, "length": 40.0,
                      "width": 30.0, "fixed": ["length", "width", "x", "y"]}],
          "dof": 0, "closed": True, "used_by": "Pad", "valid": True, "n_geometry": 5,
          "n_constraints": 13}
PAD = {"type": "pad", "name": "Pad", "body": "Body", "sketch": "Sketch", "length": 10.0,
       "valid": True}
CHAMFER = {"type": "chamfer", "name": "Chamfer", "body": "Body", "on": "Pad", "edges": 4,
           "rule": "top_face", "size": 2.0, "valid": True}
SOLID = {"volume": 12000.0, "bbox": [-20.0, -15.0, 0.0, 20.0, 15.0, 10.0], "solids": 1,
         "valid": True}


def state(items: list[dict], tip: str | None = None, selection: dict | None = None,
          open_sketch: str | None = None, undo_depth: int = 5, finished: bool = False) -> dict:
    snapshot = copy.deepcopy(EMPTY_SNAPSHOT)
    snapshot["items"] = copy.deepcopy(items)
    snapshot["items"][0]["tip"] = tip
    snapshot["session"].update(document=True, active_body="Body", tip=tip, selection=selection,
                               open_sketch=open_sketch, undo_depth=undo_depth, finished=finished)
    snapshot["solid"] = SOLID if tip else None
    return snapshot


FINAL = state([BODY, SKETCH, PAD, CHAMFER], tip="Chamfer", finished=True)
EDGES = {"type": "edges", "rule": "top_face", "of": "Pad", "count": 4}


def half_drawn(fixed: list[str]) -> dict:
    sketch = copy.deepcopy(SKETCH)
    sketch["used_by"] = None
    sketch["shapes"][0]["fixed"] = fixed
    return sketch


STATES = [
    copy.deepcopy(EMPTY_SNAPSHOT),                                              # new_document
    state([BODY]),                                                              # select_plane
    state([BODY], selection={"type": "plane", "name": "XY"}),                   # new_sketch
    state([BODY, half_drawn(["x", "width"])], open_sketch="Sketch"),            # two dimensions
    state([BODY, half_drawn(["x", "width"])]),                                  # select_sketch
    state([BODY, half_drawn(["length", "width", "x", "y"])], open_sketch="Sketch"),     # leave
    state([BODY, SKETCH, PAD], tip="Pad"),                                      # select_edges
    state([BODY, SKETCH, PAD], tip="Pad", selection=EDGES),                     # chamfer
    state([BODY, SKETCH, PAD], tip="Pad", finished=True),                       # done too early
    state([BODY, SKETCH, {**PAD, "length": 11.0}], tip="Pad"),                  # a wrong number
    state([BODY, SKETCH, {**PAD, "length": 11.0}], tip="Pad", undo_depth=0),    # ... opened
    state([BODY, SKETCH, PAD, {**CHAMFER, "valid": False}], tip="Chamfer"),     # failed feature
    state([BODY, SKETCH, PAD, {**CHAMFER, "rule": "vertical"}], tip="Chamfer"),  # wrong edges
    state([BODY, SKETCH, PAD, CHAMFER], tip="Chamfer"),                         # done
    state([BODY, SKETCH, PAD, CHAMFER], tip="Chamfer", selection={"type": "plane", "name": "XY"}),
    FINAL,
]


# --- the oracle ------------------------------------------------------------------------------------

def test_the_oracle_shares_no_code_with_the_teacher_or_the_recipes():
    tree = ast.parse(Path(oracle.__file__).read_text())
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} \
        | {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
           for alias in node.names}
    assert imported == {"__future__"}       # it imports nothing of ours at all


def test_the_oracle_and_the_teacher_agree_on_every_kind_of_state():
    for snapshot in STATES:
        advice = teacher(PLAN, snapshot)
        on_plan, commands = oracle.oracle(snapshot, FINAL)
        targets = [target.to_json() for target in advice.targets]
        assert on_plan == advice.on_plan, snapshot["session"]
        assert oracle.agrees(commands, targets), (commands, targets)
    # And the cases are not all the same case.
    answers = {tuple(name for name, _ in oracle.oracle(snapshot, FINAL)[1]) for snapshot in STATES}
    assert {("undo",), ("new_document",), ("done",), ("clear_selection",), ("chamfer",),
            ("select_sketch",), ()} <= answers


def test_the_oracle_notices_a_wrong_label():
    snapshot = STATES[7]
    _, commands = oracle.oracle(snapshot, FINAL)
    right = [{"command": "chamfer", "args": {"size": 2.0}}]
    assert oracle.agrees(commands, right)
    assert not oracle.agrees(commands, [{"command": "chamfer", "args": {"size": 2.5}}])
    assert not oracle.agrees(commands, [{"command": "fillet", "args": {"radius": 2.0}}])
    assert not oracle.agrees(commands, [*right, {"command": "undo", "args": {}}])


# --- the audit's own checks ------------------------------------------------------------------------

def test_content_shared_between_splits_is_found():
    content = {"train": {("plan-a", "shape-a"), ("plan-b", "shape-b")},
               "iid": {("plan-c", "shape-c")}, "pairing": {("plan-d", "shape-d")}}
    assert audit.shared_content(content) == ([], {"train": 2, "iid": 1, "pairing": 1})
    content["iid"].add(("plan-a", "shape-z"))           # the same plan in train and iid
    content["pairing"].add(("plan-y", "shape-c"))       # the same geometry in iid and pairing
    problems, _ = audit.shared_content(content)
    assert problems == ["1 plans are in both train and iid",
                        "1 geometry fingerprints are in both iid and pairing"]


def test_a_plan_fingerprint_depends_on_kinds_numbers_and_order():
    plan = [{"kind": "block", "slots": {"length": 40.0, "width": 30.0, "height": 10.0}},
            {"kind": "hole", "slots": {"diameter": 6.0, "x": 1.0, "y": 2.0}}]
    same = [{"slots": {"width": 30.0, "length": 40.0, "height": 10.0}, "kind": "block"}, plan[1]]
    assert audit.plan_fingerprint(plan) == audit.plan_fingerprint(same)
    other = copy.deepcopy(plan)
    other[1]["slots"]["x"] = 1.5
    assert audit.plan_fingerprint(plan) != audit.plan_fingerprint(other)


# --- shards and the manifest -------------------------------------------------------------------------

def test_the_first_recording_is_frozen_and_the_second_has_its_own_seed():
    assert set(shards.FROZEN) | set(shards.RECORDED_NOW) == set(shards.SLICES)
    assert shards.RECORDED_NOW == ("iid_v2", "pairing_v2", "long_v2", "train_v2",
                                   "train_long_v2", "xlong_v2")
    # One session id per (part, seed): a slice of the same split must not reuse a seed.
    pairs = [(split, seed) for split, seed, _, _ in shards.SLICES.values()]
    assert len(pairs) == len(set(pairs))
    assert "train_v2" in shards.TRAIN_SLICES and "iid_v2" not in shards.TRAIN_SLICES


def _stats(folder: Path, name: str, number: int, **extra: object) -> None:
    (folder / name).mkdir(exist_ok=True)
    (folder / name / f"shard{number:03d}.jsonl.gz").write_bytes(b"")
    (folder / name / f"shard{number:03d}.stats.json").write_text(json.dumps(
        {"slice": name, "file": f"{name}/shard{number:03d}.jsonl.gz", "sha256": "x",
         "seconds": 1.0, "counts": {"sessions": 5, "steps": 50, "parts": 5, "end:done": 5,
                                    "noise:wrong_argument:ok:changed": 3,
                                    "noise:repeat:rejected:no change": 1,
                                    "target:undo": 5, "target:clear_selection": 1}, **extra}))


def test_the_manifest_says_which_code_made_which_shard(tmp_path):
    for number in (0, 1, 2, 5):
        _stats(tmp_path, "train_v2", number, code_hash="aaa", mix="v2")
    _stats(tmp_path, "iid_v2", 0, code_hash="bbb", mix="v2")
    written = manifest.write_manifest(tmp_path, {"mix": "v2", "code_hash": "bbb"}, {"iid_v2": 11})
    assert written["code_versions"]["aaa"]["shards"] == {"train_v2": "0-2, 5"}
    assert written["code_versions"]["aaa"]["sessions"] == 20
    assert written["code_versions"]["bbb"]["shards"] == {"iid_v2": "0"}
    assert written["slices"]["train_v2"]["mix"] == "v2" and written["slices"]["train"]["mix"] == "v1"
    totals = written["totals"]
    assert totals["noisy_steps_by_kind"] == {"repeat": 5, "wrong_argument": 15}
    assert totals["noisy_steps_share_by_kind"] == {"repeat": 0.25, "wrong_argument": 0.75}
    assert totals["undo_share"] == 0.1 and totals["clear_selection_share"] == 0.02
    assert json.loads((tmp_path / "manifest.json").read_text()) == written


def test_ranges_are_written_short():
    assert manifest._ranges([3, 0, 1, 2, 7, 9, 8]) == "0-3, 7-9"
    assert manifest._ranges([4]) == "4"


# --- the baselines -----------------------------------------------------------------------------------

def test_the_baselines_look_at_the_view_and_never_at_the_plan():
    snapshot = STATES[3]
    seen = view([{"kind": step.kind, "slots": step.slots} for step in PLAN], snapshot,
                valid_commands(snapshot))
    keys = baselines.keys_of(seen, "constrain_x")
    assert keys["previous_command"] == "constrain_x"
    assert keys["valid_list"] == tuple(valid_commands(snapshot))
    assert keys["plan_blind_state"][1:] == (None, "sketch", ("rectangle", 1, ("width", "x")), True)
    # Another plan, the same session: the keys do not move.
    other = view([{"kind": "block", "slots": {"length": 1.0, "width": 2.0, "height": 3.0}}],
                 snapshot, valid_commands(snapshot))
    assert baselines.keys_of(other, "constrain_x") == keys


def test_steps_are_sorted_into_the_four_groups():
    def record(on_plan: bool, noise: str | None) -> dict:
        return {"progress": {"on_plan": on_plan}, "executed": {"noise": noise}}

    assert baselines.groups_of(record(True, None), None, False) == \
        ["all steps", "other on-plan steps"]
    wrong_number = record(True, "wrong_argument")
    assert baselines.groups_of(record(False, None), wrong_number, True) == \
        ["all steps", "undo decisions", "right after a mistake", "wrong-number mistakes"]
    # A wrong command FreeCAD refused changed nothing: the next step is an ordinary one.
    assert baselines.groups_of(record(True, None), wrong_number, False) == \
        ["all steps", "other on-plan steps"]
    stray = record(True, "stray_click")
    assert baselines.groups_of(record(True, None), stray, True) == \
        ["all steps", "right after a mistake"]
    # Deep in a chain of undos: an undo decision, but not right after the mistake.
    assert baselines.groups_of(record(False, None), record(False, None), True) == \
        ["all steps", "undo decisions"]


def test_a_table_is_right_predictions_over_steps():
    scores = Counter({("all", "all steps", "n"): 4, ("all", "all steps", "valid_list"): 3,
                      ("all", "all steps", "previous_command"): 1,
                      ("all", "all steps", "plan_blind_state"): 4})
    assert baselines.table(scores) == {"all steps": {"steps": 4, "previous_command": 0.25,
                                                     "valid_list": 0.75, "plan_blind_state": 1.0}}
