"""The structure reader (load.py), the packing (shards.py), the oracle and the plan facts.

No FreeCAD is needed: the records are real ones, recorded on 6 Oct 2026 and kept in
tests/data/freecad_multi_sessions.jsonl.gz (eight sessions of the trial: other shapes,
features on sides, a return to an earlier body, every kind of mistake that occurred).
"""

import copy
import json
from pathlib import Path

import pytest

from forge.freecad_multi import load, oracle, plan_facts, selection
from forge.freecad_multi.load import leaves, view, view_plan, view_state
from forge.freecad_multi.play import plan_json
from forge.freecad_multi.recipes import flatten, script_of, structure_items
from forge.freecad_multi.shards import pack, read_sessions, unpack
from forge.resolve import space as sp
from forge.resolve.bodies import Body, Cut

SAMPLE = Path(__file__).parent / "data" / "freecad_multi_sessions.jsonl.gz"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="the sample sessions are missing")


@pytest.fixture(scope="module")
def sessions() -> list[tuple[dict, list[dict], dict]]:
    return list(read_sessions(SAMPLE))


def _strings(value) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*map(_strings, value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*map(_strings, value)) if value else set()
    return set()


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | (set().union(*map(_keys, value.values())) if value else set())
    if isinstance(value, list):
        return set().union(*map(_keys, value)) if value else set()
    return set()


# --- real records --------------------------------------------------------------------------------

# Every dictionary key a view may hold, at any depth. A new key has to be added here by
# hand, which is the point.
ALLOWED_KEYS = {
    "plan", "state", "valid",
    # a plan item and a feature of the plan
    "kind", "body", "shape", "sizes", "at", "turn", "features", *load.SIZE_SLOTS,
    *load.DRAWN_FIELDS, *load.FEATURE_FIELDS,
    # the state
    "document", "finished", "can_undo", "active", "open_sketch", "selection", "bodies",
    "is", "objects", "shapes", "fixed", *load.SELECTION_FIELDS, *load.COMMON, *load.SHAPE_FIELDS,
    *(field for fields in load.ITEM_FIELDS.values() for field in fields),
    "outer diameter", "inner diameter", "depth", "height", "outline",
}


def test_the_view_of_every_real_record_is_closed(sessions):
    """Every string is a word of the vocabulary or an opaque id, every key is a listed
    one, and nothing that says what the structure is gets through."""
    steps = 0
    for header, records, _ in sessions:
        names = {item["name"] for item in header["plan"]}
        secrets = {header["plan_id"], header["where"], header["source"], header["session"],
                   header["generator_version"], *(names - load.VOCABULARY)}
        if header["kind"]:
            secrets.add(header["kind"])
        for example in load.examples_of(header, records):
            steps += 1
            found = _strings(example.view)
            assert not found & secrets
            assert all(text in load.VOCABULARY or (text[0] == "#" and text[1:].isdigit())
                       for text in found)
            assert _keys(example.view) <= ALLOWED_KEYS, _keys(example.view) - ALLOWED_KEYS
            assert not _keys(example.view) & load.FORBIDDEN_KEYS
            # FreeCAD's object names never appear: "Body003", "Sketch012", "Pad".
            assert not any(text.startswith(("Body", "Sketch", "Pad", "Pocket", "Fillet"))
                           for text in found)
    assert steps > 1000


def test_the_label_is_the_teachers_set_and_nothing_else(sessions):
    for header, records, _ in sessions:
        for record, example in zip(records, load.examples_of(header, records)):
            assert [t["command"] for t in example.label] == sorted(
                t["command"] for t in record["target"])
            assert all(set(t) == {"command", "args"} for t in example.label)
            done = record["executed"]
            if done["noise"]:           # a wrong command is executed, never a label
                assert {"command": done["command"], "args": done["args"]} not in example.label


def test_a_finished_body_is_one_short_entry_and_the_worked_one_is_in_detail(sessions):
    short = detailed = 0
    for header, records, _ in sessions:
        for record in records:
            state = view_state(record["snapshot"])
            session = record["snapshot"]["session"]
            for body in state["bodies"]:
                if "objects" in body:
                    detailed += 1
                    continue
                short += 1
                assert body["is"] in load.STATE_SHAPES
                assert leaves(body) <= 16 + 12 * len(body.get("features", [])) \
                    + 2 * len(body.get("outline", []))
            # An open sketch is always inside a detailed body.
            if session["open_sketch"]:
                assert any(state["open_sketch"] in {o["name"] for o in body.get("objects", [])}
                           for body in state["bodies"])
    # On average less than one body per step is in detail; the others are one entry each.
    steps = sum(len(records) for _, records, _ in sessions)
    assert 0 < detailed < steps and short > 4 * detailed


def test_the_short_entry_says_what_the_snapshot_says_not_what_the_plan_says(sessions):
    """A body with a wrong size is a complete shape: it gets the short entry, with the
    wrong number in it. The reader never compares with the plan."""
    seen = 0
    for header, records, _ in sessions:
        sizes = {}
        for item in header["plan"]:
            if item["kind"] == "part" and item["shape"] == "box":
                sizes[item["body"]] = item["sizes"]
        for record in records:
            if record["progress"]["on_plan"]:
                continue
            for body in view_state(record["snapshot"])["bodies"]:
                want = sizes.get(body["body"])
                if want and body.get("is") == "box" and (
                        abs(body["length"] - want["length"]) > 1e-6
                        or abs(body["height"] - want["height"]) > 1e-6):
                    seen += 1
    assert seen > 0


def test_no_history_reaches_the_view(sessions):
    _, records, _ = sessions[0]
    record = next(r for r in records if r["snapshot"]["session"]["open_sketch"]
                  and any(len(shape["fixed"]) > 1 for item in r["snapshot"]["items"]
                          if item["type"] == "sketch" for shape in item["shapes"]))
    other = copy.deepcopy(record["snapshot"])
    other["session"]["undo_depth"] += 17
    for item in other["items"]:
        for shape in item.get("shapes", []):
            shape["fixed"] = shape["fixed"][::-1]
    assert view_state(other) == view_state(record["snapshot"])
    assert "undo_depth" not in json.dumps(view_state(other))


# --- poisoned records ----------------------------------------------------------------------------

POISON = {"noise": "wrong_size", "on_plan": False, "progress": {"on_plan": False}, "target": [1],
          "executed": {"command": "undo"}, "reply": "ok", "sources": {"x": "size:length"},
          "name_of_part": "left leg", "kind_of_thing": "stool", "line": 7, "plan_id": "abc",
          "secret": "stool"}


def _poison_everywhere(value):
    """The same structure with POISON added to every dictionary at every depth."""
    if isinstance(value, dict):
        return {**{key: _poison_everywhere(inner) for key, inner in value.items()}, **POISON}
    if isinstance(value, list):
        return [_poison_everywhere(inner) for inner in value]
    return value


def test_poison_at_every_depth_of_a_record_does_not_reach_the_view(sessions):
    checked = 0
    for header, records, _ in sessions[:4]:
        clean_plan = [{**item, "features": item["features"]} for item in header["plan"]]
        for record in records[::7]:
            clean = view(clean_plan, record["snapshot"], record["valid"])
            poisoned_snapshot = _poison_everywhere(record["snapshot"])
            # A poisoned plan: every stored item gets the poison beside its own fields.
            poisoned_plan = [{**item, **POISON} for item in header["plan"]]
            seen = view(poisoned_plan, poisoned_snapshot, record["valid"])
            assert seen == clean
            assert "stool" not in json.dumps(seen) and "left leg" not in json.dumps(seen)
            checked += 1
    assert checked > 50


def test_poison_where_the_reader_cannot_drop_it_is_an_error(sessions):
    header, records, _ = sessions[0]
    record = records[len(records) // 2]
    plan = header["plan"]
    part = next(k for k, item in enumerate(plan) if item["kind"] == "part")

    def with_part(**changes) -> list[dict]:
        return [*plan[:part], {**plan[part], **changes}, *plan[part + 1:]]

    # A size slot, a drawn number or a feature field the reader has never heard of.
    with pytest.raises(ValueError, match="sizes"):
        view(with_part(sizes={**plan[part]["sizes"], "legs of a stool": 4}),
             record["snapshot"], record["valid"])
    with pytest.raises(ValueError, match="drawn"):
        view(with_part(drawn={"label": "seat"}), record["snapshot"], record["valid"])
    with pytest.raises(ValueError, match="feature"):
        view(with_part(features=[{"feature": "hole", "side": "top", "note": "for the leg"}]),
             record["snapshot"], record["valid"])
    with pytest.raises(ValueError, match="shape"):
        view(with_part(shape="stool seat"), record["snapshot"], record["valid"])
    with pytest.raises(ValueError, match="VOCABULARY"):
        view(with_part(features=[{"feature": "hole", "side": "the stool's top"}]),
             record["snapshot"], record["valid"])
    # A command, an object type or a selection word that is not in the catalogue.
    with pytest.raises(ValueError, match="catalogue"):
        view(plan, record["snapshot"], [*record["valid"], "make_stool"])
    detailed = next(r for r in records if r["snapshot"]["session"]["open_sketch"])
    strange = copy.deepcopy(detailed["snapshot"])
    strange["items"][-1]["type"] = "stool_leg"
    with pytest.raises(ValueError, match="no field list"):
        view(plan, strange, detailed["valid"])
    strange = copy.deepcopy(record["snapshot"])
    strange["session"]["selection"] = {"type": "the seat"}
    with pytest.raises(ValueError, match="VOCABULARY"):
        view(plan, strange, record["valid"])


def test_object_names_become_ids_whatever_they_are(sessions):
    _, records, _ = sessions[0]
    record = next(r for r in records if r["snapshot"]["session"]["open_sketch"])
    renamed = json.loads(json.dumps(record["snapshot"]).replace("Sketch", "seat_outline")
                         .replace("Body", "stool_leg"))
    assert view_state(renamed) == view_state(record["snapshot"])


# --- the plan the model reads --------------------------------------------------------------------

def _box(name: str, matrix: sp.Mat, line: int = 1, cuts=()) -> Body:
    return Body(name, name, line, "box", None, {"length": 40.0, "depth": 20.0, "height": 10.0},
                matrix, (5.0, 6.0, 7.0), cuts=list(cuts))


def test_a_mirrored_box_shows_the_turn_that_is_left_to_do():
    """A mirror image of a box is a box. Mirrored left to right nothing is left to do;
    mirrored front to back, half a turn is. The plan's matrix (which is not a turn at all)
    never reaches the model."""
    for matrix, turn in ((sp.mirror(0), [0.0, 0.0, 0.0]), (sp.mirror(1), [180.0, 0.0, 0.0])):
        bodies = [_box("a", matrix)]
        items = structure_items(bodies)
        seen = view_plan(json.loads(json.dumps(plan_json(bodies, items))))
        assert seen == [{"kind": "part", "body": 1, "shape": "box",
                         "sizes": {"length": 40.0, "depth": 20.0, "height": 10.0},
                         "at": [5.0, 6.0, 7.0], "turn": turn}]
        issued = [c.args for c in flatten(items[0].entries) if c.name == "turn_body"]
        assert [list(args.values()) for args in issued] == ([] if turn == [0.0] * 3 else [turn])


def test_every_number_of_a_command_is_in_the_plan_the_model_reads():
    """The executor copies, it never calculates: each number an item's commands take is in
    that item's entry of the plan (0 and the XY plane are the recipe's own constants)."""
    hole = Cut("hole_pair", "pair of holes", {"diameter": 4.0, "spacing": 12.0},
               origin=(0.0, -10.0, 1.0), normal=(0.0, -1.0, 0.0), first=(1.0, 0.0, 0.0),
               through=20.0, spots=[(-6.0, 0.0), (6.0, 0.0)], line=3)
    pocket = Cut("pocket", "pocket", {"length": 8.0, "width": 3.0, "depth": 2.5},
                 origin=(0.0, 0.0, 5.0), through=10.0, spots=[(1.5, -2.0)], line=4)
    fillet = Cut("top_fillet", "top fillet", {"radius": 1.5}, origin=(0.0, 0.0, 5.0), line=5)
    bodies = [_box("a", sp.IDENTITY, 1, [hole, fillet]), _box("b", sp.rotation(2, 90.0), 2, [pocket]),
              Body("c", "c", 6, "cone", None,
                   {"bottom diameter": 30.0, "top diameter": 0.0, "height": 25.0},
                   sp.rotation(1, 90.0), (50.0, 0.0, 15.0))]
    items = structure_items(bodies)
    stored = json.loads(json.dumps(plan_json(bodies, items)))
    seen = view_plan(stored)

    def numbers(value) -> set[float]:
        if isinstance(value, (bool, str)):
            return set()
        if isinstance(value, (int, float)):
            return {round(float(value), 9)}
        inner = value.values() if isinstance(value, dict) else value
        return set().union(*map(numbers, inner)) if inner else set()

    for item, entry in zip(items, seen):
        for command in flatten(item.entries):
            for name, value in command.args.items():
                if command.sources.get(name) == "const" or command.name == "activate_body":
                    continue
                assert numbers(value) <= numbers(entry) | {0.0}, (command.name, name, value)
    # The feature on the first body is written after the second body: the executor goes back.
    assert [entry.name for _, entry in script_of(items) if not hasattr(entry, "commands")
            ].count("activate_body") >= 1
    assert seen[2]["kind"] == "feature" and seen[2]["body"] == 1
    assert seen[2]["features"][0]["side"] == "front"


# --- packing -------------------------------------------------------------------------------------

def test_packing_is_lossless_and_small(sessions):
    for header, records, end in sessions:
        lines = pack(records, end)
        assert unpack(json.loads(json.dumps(lines))) == (records, end)
        whole = len(json.dumps([*records, end]))
        assert len(json.dumps(lines)) < whole / 3 or header["parts"] <= 2


# --- the oracle ----------------------------------------------------------------------------------

def test_the_oracle_shares_no_code_with_the_teacher():
    source = Path(oracle.__file__).read_text()
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert imports == ["from __future__ import annotations"]
    indented = [line.strip() for line in source.splitlines() if line.startswith("    import ")]
    assert indented == ["import math"]


def test_the_oracle_agrees_with_every_recorded_label(sessions):
    steps = off_plan = 0
    for _, records, end in sessions:
        for record in records:
            on_plan, commands = oracle.oracle(record["snapshot"], end["snapshot"])
            assert on_plan == record["progress"]["on_plan"], record["t"]
            assert oracle.agrees(commands, record["target"]), (record["t"], commands)
            steps += 1
            off_plan += not on_plan
    assert steps > 1000 and off_plan > 50


def test_the_oracle_notices_a_wrong_label(sessions):
    _, records, end = sessions[0]
    record = next(r for r in records if r["target"][0]["command"] == "move_body")
    _, commands = oracle.oracle(record["snapshot"], end["snapshot"])
    wrong = copy.deepcopy(record["target"])
    wrong[0]["args"]["x"] += 1.0
    assert oracle.agrees(commands, record["target"]) and not oracle.agrees(commands, wrong)


# --- what the splits are decided on --------------------------------------------------------------

PART = {"name": "seat", "owner": "seat", "line": 1, "shape": "box",
        "sizes": {"length": 300.0, "depth": 300.0, "height": 30.0},
        "low": [-150.0, -150.0, 400.0], "high": [150.0, 150.0, 430.0],
        "centre": [0.0, 0.0, 415.0], "turn": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]}


def test_the_geometry_key_ignores_names_and_order():
    """A stool and a small table with the same parts are the same thing to the executor."""
    leg = {**PART, "name": "leg", "owner": "leg", "line": 2, "shape": "cylinder",
           "sizes": {"diameter": 30.0, "height": 400.0}, "low": [-15.0, -15.0, 0.0],
           "high": [15.0, 15.0, 400.0], "centre": [0.0, 0.0, 200.0]}
    stool = [PART, leg]
    table = [{**leg, "name": "post", "owner": "post", "line": 1},
             {**PART, "name": "top", "owner": "top", "line": 2}]
    assert plan_facts.geometry_key(stool) == plan_facts.geometry_key(table)
    taller = [PART, {**leg, "sizes": {"diameter": 30.0, "height": 401.0}}]
    assert plan_facts.geometry_key(taller) != plan_facts.geometry_key(stool)


def test_cells_are_what_the_executor_sees():
    lying = {**PART, "shape": "cylinder", "sizes": {"diameter": 30.0, "height": 400.0},
             "turn": [[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]],
             "features": [{"kind": "hole", "name": "hole", "line": 2, "slots": {"diameter": 5.0},
                           "origin": [0.0, 0.0, 200.0], "normal": [0.0, 0.0, 1.0],
                           "first": [1.0, 0.0, 0.0], "spots": [[0.0, 0.0]]}]}
    # Its own top now faces right: the executor is told `select_side right`.
    assert plan_facts.cells_of([lying]) == {"st:cylinder|0,90,0", "fs:hole|right",
                                            "sf:cylinder|hole"}


def test_the_recording_order_is_a_weighted_shuffle():
    ids = [f"{n:016x}" for n in range(4000)]
    plain = sorted(ids, key=selection.order_key)
    assert plain != ids and sorted(plain) == ids
    heavy = set(ids[:400])                  # a tenth of the plans, eight times the weight
    order = sorted(ids, key=lambda i: selection.order_key(i, 8.0 if i in heavy else 1.0))
    early = sum(i in heavy for i in order[:400])
    assert 150 < early < 260                # 8 x 400 / (8 x 400 + 3600) = 47% of the first 400


def test_held_out_cells_keep_both_halves_elsewhere():
    """Each held-out cell is one combination; its shape (or feature) and its turn (or side)
    are not held out themselves."""
    for cell in selection.HELD_OUT_CELLS:
        family, pair = cell.split(":")
        assert family in ("st", "fs", "sf") and len(pair.split("|")) == 2
    firsts = [cell.split("|")[0] for cell in selection.HELD_OUT_CELLS]
    assert len(set(firsts)) == len(firsts)
