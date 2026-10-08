"""load.py: only the whitelisted fields can reach a model. No FreeCAD needed."""

import copy
import json

import pytest

from forge.freecad import load
from forge.freecad.load import ITEM_FIELDS, examples_of, label, view, view_snapshot

POISON = "LEAK"     # no legitimate field has this value

HEADER = {
    "session": "s1", "part_id": "p1", "family": "composed_block", "split": "train",
    "slice": "train_v2", "seed": 10, "noise_level": 0.3, "clean_length": 21,
    "start": {"kind": "stray_solid", "commands": [["new_document", {}]], "forget_undo": False},
    "plan": [{"kind": "block", "slots": {"length": 40.0, "width": 30.0, "height": 10.0}},
             {"kind": "top_chamfer", "slots": {"size": 2.0}}],
    "source": "gen", "license": "forge", "generator_version": "1", "code_hash": "abc",
    "mix": "v2", "runtime": {"exact_undo": True},
}
SNAPSHOT = {
    "items": [
        {"type": "body", "name": "Body", "tip": "Chamfer", "active": True, "valid": True},
        {"type": "sketch", "name": "Sketch", "body": "Body", "plane": "XY", "offset": 0.0,
         "shapes": [{"shape": "rectangle", "first": 0, "x": 0.0, "y": 0.0, "length": 40.0,
                     "width": 30.0, "fixed": ["y", "length", "x", "width"]}],
         "dof": 0, "closed": True, "used_by": "Pad", "valid": True, "n_geometry": 5,
         "n_constraints": 13},
        {"type": "pad", "name": "Pad", "body": "Body", "sketch": "Sketch", "length": 10.0,
         "valid": True},
        {"type": "chamfer", "name": "Chamfer", "body": "Body", "on": "Pad", "edges": 4,
         "rule": "top_face", "size": 2.0, "valid": True},
    ],
    "session": {"document": True, "active_body": "Body", "tip": "Chamfer", "open_sketch": None,
                "selection": {"type": "edges", "rule": "vertical", "of": "Chamfer", "count": 4},
                "undo_depth": 17, "finished": False},
    "solid": {"volume": 11000.0, "bbox": [-20.0, -15.0, 0.0, 20.0, 15.0, 10.0], "solids": 1,
              "valid": True},
}
RECORD = {
    "session": "s1", "t": 14, "snapshot": SNAPSHOT, "valid": ["new_document", "undo", "done"],
    "target": [{"command": "clear_selection", "args": {}, "item": None, "sources": {}}],
    "progress": {"on_plan": True, "built": 2, "active": None},
    "executed": {"command": "done", "args": {}, "noise": "early_done"},
    "reply": {"status": "ok"}, "before": "forget_undo",
}


def poisoned(value):
    """A copy with an extra field holding POISON in every dict, at every depth."""
    if isinstance(value, dict):
        return {**{key: poisoned(inner) for key, inner in value.items()}, "extra_field": POISON}
    if isinstance(value, list):
        return [poisoned(inner) for inner in value]
    return value


def test_the_view_has_exactly_the_three_parts_and_none_of_the_answers():
    example = next(examples_of(HEADER, [RECORD]))
    assert set(example.view) == {"plan", "snapshot", "valid"}
    text = json.dumps(example.view)
    # Nothing about how the session was made, what happened next, or where numbers come from.
    for forbidden in ("progress", "on_plan", "executed", "noise", "reply", "noise_level",
                      "stray_solid", "clean_length", "sources", "undo_depth", "forget_undo",
                      "early_done", "code_hash", "seed", "split"):
        assert forbidden not in text
    assert example.label == [{"command": "clear_selection", "args": {}}]     # no item, no sources
    assert (example.session, example.t) == ("s1", 14)


def test_a_field_that_is_not_listed_cannot_reach_the_model_side():
    clean = view(HEADER["plan"], SNAPSHOT, RECORD["valid"])
    header, record = poisoned(HEADER), poisoned(RECORD)
    dirty = next(examples_of(header, [record]))
    assert POISON not in json.dumps(dirty.view) and POISON not in json.dumps(dirty.label)
    assert dirty.view == clean
    assert dirty.label == label(RECORD["target"])
    # Also inside the two dictionaries whose keys depend on the step kind and the command.
    assert set(dirty.view["plan"][0]["slots"]) == {"length", "width", "height"}
    targets = poisoned([{"command": "pad", "args": {"length": 10.0}, "item": 0, "sources": {}}])
    assert label(targets) == [{"command": "pad", "args": {"length": 10.0}}]


def test_every_level_of_the_view_holds_only_listed_fields():
    seen = view_snapshot(SNAPSHOT)
    assert set(seen) == {"items", "session", "solid"}
    for item in seen["items"]:
        allowed = {*load.COMMON, *ITEM_FIELDS[item["type"]]} | ({"shapes"} if item["type"] == "sketch" else set())
        assert set(item) <= allowed
    for shape in seen["items"][1]["shapes"]:
        assert set(shape) <= {*load.SHAPE_FIELDS, "fixed"}
    assert set(seen["session"]) == {*load.SESSION_FIELDS, "selection", "can_undo"}
    assert set(seen["session"]["selection"]) <= set(load.SELECTION_FIELDS)
    assert set(seen["solid"]) == set(load.SOLID_FIELDS)


def test_history_traces_are_removed():
    seen = view_snapshot(SNAPSHOT)
    # The order the dimensions were given in is gone; the count of commands so far is a yes/no.
    assert seen["items"][1]["shapes"][0]["fixed"] == ["length", "width", "x", "y"]
    assert seen["session"]["can_undo"] is True and "undo_depth" not in seen["session"]
    other = copy.deepcopy(SNAPSHOT)
    other["items"][1]["shapes"][0]["fixed"] = ["x", "y", "width", "length"]
    other["session"]["undo_depth"] = 3
    assert view_snapshot(other) == seen
    other["session"]["undo_depth"] = 0
    assert view_snapshot(other)["session"]["can_undo"] is False


def test_an_unknown_object_type_is_an_error_not_a_leak():
    snapshot = copy.deepcopy(SNAPSHOT)
    snapshot["items"].append({"type": "loft", "name": "Loft", "valid": True, "secret": 1})
    with pytest.raises(ValueError, match="loft"):
        view_snapshot(snapshot)


def test_object_names_become_opaque_ids_and_no_name_reaches_the_model():
    """Whatever the objects are called, the model sees `#place` and the type."""
    renames = {"Body": "left_bracket", "Sketch": "mounting_face_outline", "Pad": "base_plate",
               "Chamfer": "deburr_top"}
    named = json.loads(json.dumps(SNAPSHOT))
    for item in named["items"]:
        for field, value in item.items():
            if isinstance(value, str) and value in renames:
                item[field] = renames[value]
    for field in ("active_body", "tip"):
        named["session"][field] = renames[named["session"][field]]
    named["session"]["selection"]["of"] = renames[named["session"]["selection"]["of"]]
    seen = view_snapshot(named)
    assert seen == view_snapshot(SNAPSHOT)              # the names made no difference
    text = json.dumps(seen)
    assert not any(name in text for name in (*renames, *renames.values()))
    assert [item["name"] for item in seen["items"]] == ["#0", "#1", "#2", "#3"]
    assert seen["items"][2] == {"type": "pad", "name": "#2", "body": "#0", "valid": True,
                                "sketch": "#1", "length": 10.0}
    assert seen["items"][1]["used_by"] == "#2" and seen["items"][3]["on"] == "#2"
    assert seen["session"]["tip"] == "#3" and seen["session"]["active_body"] == "#0"
    assert seen["session"]["selection"] == {"type": "edges", "rule": "vertical", "of": "#3",
                                            "count": 4}
    # A plane is a word of the catalogue, not an object: it keeps its name.
    named["session"]["selection"] = {"type": "plane", "name": "XZ"}
    assert view_snapshot(named)["session"]["selection"] == {"type": "plane", "name": "XZ"}
    named["session"]["selection"] = {"type": "sketch", "name": "mounting_face_outline"}
    named["session"]["open_sketch"] = "mounting_face_outline"
    again = view_snapshot(named)
    assert again["session"]["selection"]["name"] == "#1" and again["session"]["open_sketch"] == "#1"


def test_every_string_in_the_view_is_an_id_or_a_word_of_the_closed_vocabulary():
    seen = view(HEADER["plan"], SNAPSHOT, RECORD["valid"])
    load.check_strings(seen)
    for text in ("composed_block", "M8 hex nut", "bracket", "Pad", "gen:composed"):
        assert text not in load.VOCABULARY
    # A meaning smuggled in as a VALUE of an allowed field is an error, not a leak.
    for spoil in (lambda s: s["items"][3].update(rule="the_flange_rim"),
                  lambda s: s["items"][1].update(plane="front_of_bracket"),
                  lambda s: s["items"][1]["shapes"][0].update(shape="keyhole_for_m8")):
        snapshot = copy.deepcopy(SNAPSHOT)
        spoil(snapshot)
        with pytest.raises(ValueError, match="VOCABULARY"):
            view(HEADER["plan"], snapshot, RECORD["valid"])
    with pytest.raises(ValueError, match="catalogue"):
        label([{"command": "mirror", "args": {"plane": "bracket_mid_plane"}}])
    with pytest.raises(KeyError):       # an object pointing at a name that is not in the document
        broken = copy.deepcopy(SNAPSHOT)
        broken["items"][2]["sketch"] = "Sketch999"
        view_snapshot(broken)


def test_an_unknown_command_or_step_kind_is_an_error():
    with pytest.raises(ValueError, match="teleport"):
        view(HEADER["plan"], SNAPSHOT, ["undo", "teleport"])
    with pytest.raises(KeyError):
        view([{"kind": "wormhole", "slots": {"secret": 1.0}}], SNAPSHOT, ["undo"])
    with pytest.raises(KeyError):
        label([{"command": "teleport", "args": {"secret": 1}}])
    with pytest.raises(ValueError, match="catalogue"):
        label([{"command": "pad", "args": {"length": "tall"}}])


def test_the_view_does_not_share_memory_with_the_record():
    seen = view(HEADER["plan"], SNAPSHOT, RECORD["valid"])
    seen["plan"][0]["slots"]["length"] = -1.0
    seen["snapshot"]["items"][1]["shapes"][0]["fixed"].append("zzz")
    assert HEADER["plan"][0]["slots"]["length"] == 40.0
    assert "zzz" not in SNAPSHOT["items"][1]["shapes"][0]["fixed"]


def test_only_sessions_that_ended_done_on_the_right_solid_are_usable():
    assert load.usable({"end": "done", "problems": []})
    for end in ({"end": "budget", "problems": []}, {"end": "stuck", "problems": []},
                {"end": "error", "problems": ["x"]}, {"end": "done", "problems": ["volume"]}):
        assert not load.usable(end)


def test_the_label_is_sorted_and_keeps_numbers_only():
    targets = [{"command": "constrain_y", "args": {"value": 0.0}, "item": 0,
                "sources": {"value": "const"}},
               {"command": "constrain_length", "args": {"value": 40.0}, "item": 0,
                "sources": {"value": "slot:length"}}]
    assert label(targets) == [{"command": "constrain_length", "args": {"value": 40.0}},
                              {"command": "constrain_y", "args": {"value": 0.0}}]


def test_a_label_side_field_nested_under_a_whitelisted_one_is_refused():
    """A whitelisted field whose value is a dictionary must not smuggle label-side fields."""
    import pytest

    from forge.freecad import load

    with pytest.raises(ValueError, match="label-side"):
        load.check_keys({"plan": [{"kind": "block", "slots": {
            "length": {"value": 10.0, "noise_level": 0.3, "on_plan": False}}}]})
    load.check_keys({"plan": [{"kind": "block", "slots": {"length": 10.0}}], "valid": ["pad"]})
