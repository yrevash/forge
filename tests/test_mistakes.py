"""Repair data: mutations, the really-wrong check, feedback, and pairing with prompts.

The programs below are test fixtures only; nothing here is training data.
"""

import difflib
import inspect
import random
import re

import pytest

from forge.data import mistakes
from forge.data.mistakes import (
    GROUPS,
    OPERATORS,
    REPAIR_PAIR_FIELDS,
    feedback,
    mutations,
    parse_program,
    problems,
    program_text_problems,
    repair_pairs,
    repairs_for_part,
    same_solid,
    stated_params,
)
from forge.sandbox import Sandbox

BLOCK = """import cadquery as cq

length = 60.0
width = 40.0
height = 12.0
hole_count = 3
hole_spacing = 15.0
hole_diameter = 4.5
boss_x = 10.0
boss_y = 8.0
boss_diameter = 14.0
boss_height = 8.0

block = cq.Workplane("XY").box(length, width, height, centered=(True, True, False))
holes = (
    cq.Workplane("XY")
    .center(0, -12)
    .rarray(hole_spacing, 1, hole_count, 1)
    .circle(hole_diameter / 2)
    .extrude(height)
)
boss = (
    cq.Workplane("XY")
    .workplane(offset=height)
    .center(boss_x, boss_y)
    .circle(boss_diameter / 2)
    .extrude(boss_height)
)
result = block.cut(holes).union(boss)
"""
BLOCK_PARAMS = {"length": 60.0, "width": 40.0, "height": 12.0, "hole_count": 3,
                "hole_spacing": 15.0, "hole_diameter": 4.5, "boss_x": 10.0, "boss_y": 8.0,
                "boss_diameter": 14.0, "boss_height": 8.0}

PLATE = """import cadquery as cq

length = 80.0
width = 50.0
thickness = 4.0
hole_diameter = 5.0
corner_radius = 3.0

plate = (
    cq.Workplane("XY")
    .box(length, width, thickness, centered=(True, True, False))
    .edges("|Z")
    .fillet(corner_radius)
)
holes = (
    cq.Workplane("XY")
    .pushPoints([
        (-30, -15),
        (30, -15),
        (0, 15),
    ])
    .circle(hole_diameter / 2)
    .extrude(thickness)
)
socket = cq.Workplane("XY").polygon(6, 8.0).extrude(thickness)
result = plate.cut(holes).cut(socket)
"""

PLAIN = "import cadquery as cq\n\nside = 7.0\n\nresult = cq.Workplane('XY').box(side, side, side)\n"


def changed(original: str, wrong: str) -> tuple[list[str], list[str]]:
    """Lines only in the original, and lines only in the wrong program."""
    diff = list(difflib.ndiff(original.split("\n"), wrong.split("\n")))
    return ([x[2:].strip() for x in diff if x.startswith("- ")],
            [x[2:].strip() for x in diff if x.startswith("+ ")])


def apply(name: str, code: str, seed: int = 0) -> str | None:
    return OPERATORS[name].apply(parse_program(code), random.Random(seed))


def one_param(line: str) -> str:
    return f"import cadquery as cq\n\n{line}\n\nresult = cq.Workplane('XY').box(1, 1, 1)\n"


# --- the program as text ----------------------------------------------------

@pytest.mark.parametrize("code", [BLOCK, PLATE, PLAIN])
def test_parse_writes_back_the_same_text(code):
    assert parse_program(code).text() == code


def test_parse_finds_the_parameter_block():
    program = parse_program(BLOCK)
    assert program.names[:4] == ("length", "width", "height", "hole_count")
    assert program.values[3] == "3"
    assert program.head == ("import cadquery as cq", "")
    assert program.body[0] == ""


# --- operators, one by one --------------------------------------------------

@pytest.mark.parametrize(("name", "line", "allowed"), [
    ("swap_digits", "length = 52.0", {"length = 25.0"}),
    ("off_by_step", "length = 6.5", {"length = 6.0", "length = 7.0"}),
    ("off_by_step", "pocket_x = 16.25", {"pocket_x = 16.0", "pocket_x = 16.5"}),
    ("times_ten", "length = 52.0", {"length = 520.0", "length = 5.2"}),
    ("diameter_radius_value", "hole_diameter = 5.0", {"hole_diameter = 2.5"}),
    ("diameter_radius_value", "corner_radius = 3.0", {"corner_radius = 6.0"}),
    ("flip_parameter_sign", "boss_y = -16.25", {"boss_y = 16.25"}),
    ("wrong_count", "hole_count = 4", {f"hole_count = {n}" for n in (2, 3, 5, 6)}),
])
def test_number_operators_on_one_parameter(name, line, allowed):
    for seed in range(8):
        removed, added = changed(one_param(line), apply(name, one_param(line), seed))
        assert removed == [line]
        assert len(added) == 1 and added[0] in allowed


def test_other_parameter_value_copies_a_value_that_exists():
    for seed in range(8):
        removed, added = changed(BLOCK, apply("other_parameter_value", BLOCK, seed))
        name, value = added[0].split(" = ")
        assert removed[0].startswith(f"{name} = ")
        assert float(value) in {v for v in BLOCK_PARAMS.values() if isinstance(v, float)}


@pytest.mark.parametrize(("name", "code", "removed", "added"), [
    ("drop_feature", PLATE, ['.edges("|Z")', ".fillet(corner_radius)"], []),
    ("drop_centered", PLATE, [".box(length, width, thickness, centered=(True, True, False))"],
     [".box(length, width, thickness)"]),
    ("drop_offset", BLOCK, [".workplane(offset=height)"], [".workplane()"]),
    ("no_result", BLOCK, ["result = block.cut(holes).union(boss)"], None),
    ("wrong_count", PLATE, ["socket = cq.Workplane(\"XY\").polygon(6, 8.0).extrude(thickness)"],
     None),
])
def test_body_operators_change_the_expected_line(name, code, removed, added):
    got_removed, got_added = changed(code, apply(name, code))
    assert got_removed == removed
    if added is not None:
        assert got_added == added


def test_diameter_used_as_radius_drops_one_half():
    removed, added = changed(BLOCK, apply("diameter_used_as_radius", BLOCK))
    assert removed[0].endswith("_diameter / 2)") and added == [removed[0].replace(" / 2", "")]


def test_drop_boolean_removes_one_cut_or_union():
    for seed in range(6):
        _, added = changed(BLOCK, apply("drop_boolean", BLOCK, seed))
        assert added[0] in {"result = block.union(boss)", "result = block.cut(holes)"}


def test_drop_list_item_removes_one_point():
    wrong = apply("drop_list_item", PLATE)
    removed, added = changed(PLATE, wrong)
    assert len(removed) == 1 and removed[0] in {"(-30, -15),", "(30, -15),", "(0, 15),"}
    assert added == []


def test_swap_boolean_turns_cut_into_union_or_back():
    for seed in range(6):
        _, added = changed(BLOCK, apply("swap_boolean", BLOCK, seed))
        assert added[0] in {"result = block.union(holes).union(boss)",
                            "result = block.cut(holes).cut(boss)"}


def test_extrude_wrong_parameter_uses_another_parameter():
    for seed in range(6):
        removed, added = changed(BLOCK, apply("extrude_wrong_parameter", BLOCK, seed))
        old = re.fullmatch(r"\.extrude\((\w+)\)", removed[0]).group(1)
        new = re.fullmatch(r"\.extrude\((\w+)\)", added[0]).group(1)
        assert new in BLOCK_PARAMS and BLOCK_PARAMS[new] != BLOCK_PARAMS[old]


def test_flip_offset_sign_negates_one_argument():
    allowed = {".workplane(offset=-height)", ".center(-boss_x, boss_y)",
               ".center(boss_x, -boss_y)", ".center(0, 12)"}
    seen = {changed(BLOCK, apply("flip_offset_sign", BLOCK, seed))[1][0] for seed in range(30)}
    assert seen <= allowed and len(seen) >= 3


def test_wrong_plane_changes_one_plane():
    removed, added = changed(PLAIN.replace("'XY'", '"XY"'),
                             apply("wrong_plane", PLAIN.replace("'XY'", '"XY"')))
    assert '"XY"' in removed[0] and ('"XZ"' in added[0] or '"YZ"' in added[0])


def test_misspell_method_changes_only_a_method_name():
    for seed in range(10):
        removed, added = changed(BLOCK, apply("misspell_method", BLOCK, seed))
        assert len(removed) == len(added) == 1
        # Same line once the method names are taken out.
        strip = re.compile(r"\.[A-Za-z_]\w*\(")
        assert strip.sub(".(", removed[0]) == strip.sub(".(", added[0])


def test_misspellings_are_made_by_rule():
    spellings = mistakes._misspellings("cboreHole")
    assert "cborehole" in spellings and "cbore_hole" in spellings and "cboreHole" not in spellings
    assert "extrud" in mistakes._misspellings("extrude")


def test_undefined_name_removes_a_definition_or_misspells_a_use():
    for seed in range(10):
        wrong = apply("undefined_name", BLOCK, seed)
        removed, added = changed(BLOCK, wrong)
        if not added:
            assert re.fullmatch(r"\w+ = [\d.]+", removed[0])
        else:
            assert len(removed) == len(added) == 1


def test_missing_bracket_removes_exactly_one_bracket():
    wrong = apply("missing_bracket", BLOCK)
    assert len(wrong) == len(BLOCK) - 1
    assert wrong.count(")") == BLOCK.count(")") - 1


@pytest.mark.parametrize("name", ["wrong_count", "drop_feature", "drop_boolean", "drop_list_item",
                                  "swap_boolean", "extrude_wrong_parameter", "flip_offset_sign",
                                  "flip_parameter_sign", "drop_offset", "drop_centered",
                                  "diameter_radius_value", "diameter_used_as_radius",
                                  "other_parameter_value"])
def test_operator_that_does_not_apply_returns_nothing(name):
    assert apply(name, PLAIN) is None


def test_setting_a_value_to_itself_is_not_a_mutation():
    program = parse_program(BLOCK)
    assert program.with_value(0, 60.0) is None
    assert program.with_value(3, 3) is None
    assert program.with_value(0, 61.0) is not None


# --- all mutations of a part ------------------------------------------------

def test_every_group_has_an_operator():
    assert set(GROUPS) == {"wrong_number", "missing_feature", "wrong_count", "wrong_operation",
                           "position", "does_not_run"}


@pytest.mark.parametrize("code", [BLOCK, PLATE])
def test_mutations_differ_from_the_original_and_from_each_other(code):
    found = mutations("part-1", code, seed=0)
    wrongs = [wrong for _, wrong in found]
    assert len(found) >= 12
    assert code not in wrongs and len(set(wrongs)) == len(wrongs)
    assert {name for name, _ in found} <= set(OPERATORS)


def test_mutations_are_deterministic_and_depend_on_part_and_seed():
    assert mutations("part-1", BLOCK, seed=0) == mutations("part-1", BLOCK, seed=0)
    assert mutations("part-1", BLOCK, seed=0) != mutations("part-1", BLOCK, seed=1)
    assert mutations("part-1", BLOCK, seed=0) != mutations("part-2", BLOCK, seed=0)


def test_first_mutations_cover_different_kinds():
    first = [OPERATORS[name].group for name, _ in mutations("part-1", BLOCK)[:len(GROUPS)]]
    assert len(set(first)) == len(GROUPS)


# --- feedback, from made-up outcomes (no kernel needed) -----------------------

def built(bbox, cylinders, lowest=0.0, n_solids=1):
    return {"status": "ok", "measure": {
        "one_valid_solid": n_solids == 1, "n_solids": n_solids, "bbox": bbox,
        "bbox_min": [-bbox[0] / 2, -bbox[1] / 2, lowest], "cylinders": cylinders}}


GOOD_BLOCK = built([60.0, 40.0, 20.0], {"4.500": 3, "14.000": 1})


def test_correct_part_gets_no_complaint():
    # height 12 is not an extent, but 20 - 12 is the stated boss height: stacked.
    assert problems(BLOCK_PARAMS, GOOD_BLOCK) == []
    assert feedback(BLOCK_PARAMS, GOOD_BLOCK) == "No problem found."


def test_wrong_diameter_is_named():
    text = feedback(BLOCK_PARAMS, built([60.0, 40.0, 20.0], {"45.000": 3, "14.000": 1}))
    assert "hole diameter 4.5 is stated, but no round face of diameter 4.5 was measured." in text
    assert "boss diameter" not in text and "length" not in text
    assert text.endswith("Measured: bounding box 60 x 40 x 20 mm; "
                         "round face diameters (count): 14 (1), 45 (3).")


def test_wrong_length_is_named():
    text = feedback(BLOCK_PARAMS, built([6.0, 40.0, 20.0], {"4.500": 3, "14.000": 1}))
    assert text.startswith("length 60 is stated, but the bounding box measures 6 x 40 x 20.")


def test_wrong_count_is_named():
    text = feedback(BLOCK_PARAMS, built([60.0, 40.0, 20.0], {"4.500": 5, "14.000": 1}))
    assert "hole count 3 is stated, but 5 round faces of diameter 4.5 were measured." in text


def test_off_the_plane_and_split_solids_are_named():
    text = feedback(BLOCK_PARAMS, built([60.0, 40.0, 20.0], {"4.500": 3, "14.000": 1},
                                        lowest=-6.0, n_solids=2))
    assert "The result is 2 separate solids, not one." in text
    assert "its lowest point is at z = -6." in text


def test_unmeasurable_diameters_are_not_checked():
    stated = {"outer_diameter": 20.0, "countersink_diameter": 9.0, "bolt_circle_diameter": 15.0}
    assert problems(stated, built([20.0, 20.0, 3.0], {"20.000": 1})) == []


def test_failed_runs():
    error = {"status": "error", "error_type": "NameError", "line": 14,
             "error": "name 'heigh' is not defined"}
    assert feedback(BLOCK_PARAMS, error) == (
        "The program failed on line 14: NameError: name 'heigh' is not defined")
    syntax = {"status": "error", "error_type": "SyntaxError", "line": None,
              "error": "'(' was never closed (<program>, line 9)"}
    assert feedback({}, syntax) == "The program failed on line 9: SyntaxError: '(' was never closed"
    assert feedback({}, {"status": "no_result", "error": "program did not set `result`"}) == (
        "The program ran but did not set `result`.")
    assert "not a solid" in feedback({}, {"status": "no_result",
                                          "error": "result contains no solid"})
    assert "time limit" in feedback({}, {"status": "timeout", "error": "exceeded 30s"})


def test_feedback_only_speaks_about_what_the_prompt_states():
    """The candidate's width and hole diameter are both wrong; the prompt gave only two numbers."""
    stated = {"length": 60.0, "boss_diameter": 14.0}
    candidate = built([60.0, 44.0, 20.0], {"5.400": 3, "41.000": 1})  # right: 40 and 4.5
    text = feedback(stated, candidate)
    assert "boss diameter 14 is stated" in text
    assert "width" not in text and "hole" not in text
    # Every number is a stated value or a measurement of the candidate; the
    # correct values the prompt never gave (40, 4.5) are nowhere.
    numbers = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)}
    assert numbers <= {60.0, 14.0, 44.0, 20.0, 5.4, 41.0, 3.0, 1.0}
    assert not numbers & {40.0, 4.5}
    # With nothing stated there is nothing to compare, so nothing is said.
    assert feedback({}, candidate) == "No problem found."


def test_feedback_takes_no_correct_program():
    assert list(inspect.signature(feedback).parameters) == ["stated", "outcome", "code"]
    assert list(inspect.signature(problems).parameters) == ["stated", "outcome", "code"]
    assert list(inspect.signature(program_text_problems).parameters) == ["stated", "code"]


# --- the program-text check ---------------------------------------------------

def test_program_text_names_a_value_that_differs_from_the_prompt():
    wrong = BLOCK.replace("boss_x = 10.0", "boss_x = 1.0")  # invisible in any measurement
    assert problems(BLOCK_PARAMS, GOOD_BLOCK) == []
    text = feedback(BLOCK_PARAMS, GOOD_BLOCK, wrong)
    assert text.split("\n")[0] == "Program text: `boss_x = 1.0`, but the prompt states boss x 10."
    assert text.split("\n")[1].startswith("Measured: bounding box 60 x 40 x 20 mm")
    assert len(text.split("\n")) == 2


def test_program_text_is_silent_on_the_correct_program_and_on_unstated_names():
    assert program_text_problems(BLOCK_PARAMS, BLOCK) == []
    wrong = BLOCK.replace("boss_x = 10.0", "boss_x = 1.0")
    assert program_text_problems({"length": 60.0}, wrong) == []      # boss_x not stated
    assert program_text_problems({"radius": 3.0}, BLOCK) == []       # name not in the program
    assert program_text_problems({"hole_count": 3}, BLOCK.replace("hole_count = 3",
                                                                    "hole_count = 5")) == [
        "Program text: `hole_count = 5`, but the prompt states hole count 3."]


def test_program_text_is_added_to_a_failed_run():
    error = {"status": "error", "error_type": "Standard_Failure", "line": None,
             "error": "BRep_API: command not done"}
    wrong = BLOCK.replace("height = 12.0", "height = 120.0")
    assert feedback(BLOCK_PARAMS, error, wrong).split("\n") == [
        "The program failed: Standard_Failure: BRep_API: command not done",
        "Program text: `height = 120.0`, but the prompt states height 12."]


def test_ring_of_round_section_is_checked_by_arithmetic():
    """An O-ring has no cylinder: X = Y = inner + 2 x section, Z = section."""
    stated = {"inner_diameter": 25.0, "section_diameter": 2.5}
    assert problems(stated, built([30.0, 30.0, 2.5], {})) == []
    text = feedback(stated, built([35.0, 35.0, 2.5], {}))            # inner built as 30
    # The two diameters are tied by one sum, so both are named when it fails.
    assert "inner diameter 25 is stated" in text
    assert "section diameter 2.5 is stated" in feedback(stated, built([31.0, 31.0, 3.0], {}))


def test_count_goes_with_the_diameter_that_shares_its_prefix():
    stated = {"base_outer_diameter": 70.0, "polar_1_count": 8, "polar_1_hole_diameter": 6.5}
    assert problems(stated, built([70.0, 70.0, 22.0], {"6.500": 8, "70.000": 1})) == []
    text = feedback(stated, built([70.0, 70.0, 22.0], {"6.500": 6, "70.000": 1}))
    assert "polar 1 count 8 is stated, but 6 round faces of diameter 6.5 were measured." in text
    # Two features with the same diameter: the faces cannot be told apart, so no count check.
    shared = {**stated, "hole_2_diameter": 6.5}
    assert problems(shared, built([70.0, 70.0, 22.0], {"6.500": 9, "70.000": 1})) == []


def test_drop_boolean_removes_a_call_that_has_its_own_line():
    code = BLOCK.replace("result = block.cut(holes).union(boss)",
                         "result = (\n    block\n    .cut(holes)\n    .union(boss)\n)")
    for seed in range(6):
        removed, added = changed(code, apply("drop_boolean", code, seed))
        assert removed[0] in {".cut(holes)", ".union(boss)"} and added == []


def test_same_solid_uses_the_generator_tolerances():
    a = {"volume": 1000.0, "area": 600.0, "bbox": [10.0, 10.0, 10.0], "cylinders": {},
         "n_faces": 6, "n_edges": 12}
    assert same_solid({**a, "volume": 1000.0 + 1e-7, "bbox": [10.0, 10.0, 10.0 + 1e-6]}, a)
    assert not same_solid({**a, "volume": 1000.1}, a)
    assert not same_solid({**a, "bbox": [10.0, 10.0, 10.01]}, a)
    assert not same_solid({**a, "cylinders": {"3.000": 1}}, a)


# --- pairing with prompts ---------------------------------------------------

def repair_row(outcome):
    return {"id": "r1", "part_id": "p1", "family": "demo", "mistake": "swap_digits",
            "mistake_group": "wrong_number", "wrong_code": "WRONG", "correct_code": BLOCK,
            "wrong_outcome": outcome, "correct_measure": GOOD_BLOCK["measure"],
            "params": BLOCK_PARAMS, "designation": None,
            "source": "gen:demo", "license": "forge", "generator_version": "abc",
            "mistake_version": "def", "feedback_model": "template",
            "geom_fingerprint": "fp"}


def test_stated_params_reads_the_numbers_in_the_prompt():
    assert stated_params("block 60 long with a 14 mm boss", BLOCK_PARAMS) == {
        "length": 60.0, "boss_diameter": 14.0}


def test_repair_pairs_write_feedback_per_prompt_and_inherit_the_split():
    wrong = built([60.0, 44.0, 20.0], {"5.400": 3, "14.000": 1})  # width and hole diameter wrong
    prompts = [
        {"text": "block 60 x 40 x 12, hole dia 4.5, boss 8 high", "style": "compact", "variant": "compact-0",
         "model": "template"},
        {"text": "block 60 long with three holes", "style": "request", "variant": "request-0",
         "model": "template"},
        {"text": "block 60 long, 12 high", "style": "request", "variant": "request-1",
         "model": "template"},
    ]
    rows = repair_pairs(repair_row(wrong), prompts, split="val")
    # request-0 states nothing the candidate visibly gets wrong: no row.
    # request-1 gives the height without the boss on top, so the check would
    # complain about the correct part too: no row.
    assert [r["caption_variant"] for r in rows] == ["compact-0"]
    row = rows[0]
    assert set(row) == set(REPAIR_PAIR_FIELDS)
    assert row["split"] == "val" and row["code"] == BLOCK and row["wrong_code"] == "WRONG"
    assert "width 40 is stated" in row["feedback"] and "hole diameter 4.5" in row["feedback"]
    assert all(isinstance(v, str) and v for v in row.values())


def test_a_failed_run_pairs_with_every_prompt():
    error = {"status": "error", "error_type": "NameError", "line": 3, "error": "name 'x'"}
    prompts = [{"text": "a block", "style": "request", "variant": f"request-{i}", "model": "m"}
               for i in range(3)]
    rows = repair_pairs(repair_row(error), prompts, split="train")
    assert len(rows) == 3 and len({r["id"] for r in rows}) == 3


# --- the whole step, through the sandbox --------------------------------------

@pytest.fixture(scope="module")
def sandbox():
    with Sandbox(timeout=20) as sb:
        yield sb


@pytest.fixture(scope="module")
def part(sandbox):
    measure = sandbox.run(BLOCK)["measure"]
    return {"id": "part-1", "family": "demo", "code": BLOCK, "params": BLOCK_PARAMS,
            "designation": None, "measured": measure, "geom_fingerprint": measure["fingerprint"],
            "source": "gen:demo", "license": "forge", "generator_version": "abc"}


def test_repairs_for_a_part(part, sandbox):
    rows, rejects = repairs_for_part(part, sandbox, per_part=40, seed=0, version="v")
    assert len(rows) >= 10
    for row in rows:
        assert row["correct_code"] == BLOCK and row["wrong_code"] != BLOCK
        assert row["source"] == "gen:demo" and row["license"] == "forge"
        assert row["generator_version"] == "abc" and row["mistake_version"] == "v"
        assert row["detected"] == (row["feedback"] != "No problem found.")
        outcome = row["wrong_outcome"]
        expect = OPERATORS[row["mistake"]].expect
        assert expect == "any" or (outcome["status"] == "ok") == (expect == "builds")
        if outcome["status"] == "ok":  # built, so it must measure differently
            assert not same_solid(outcome["measure"], part["measured"]) or abs(
                outcome["measure"]["bbox_min"][2]) > 1e-3
        assert BLOCK not in row["feedback"]
    assert len({row["id"] for row in rows}) == len(rows)
    assert all(r["reason"] for r in rejects)
    by_mistake = {row["mistake"]: row for row in rows}
    assert "NameError" in by_mistake["undefined_name"]["feedback"]
    assert by_mistake["no_result"]["feedback"] == "The program ran but did not set `result`."
    assert "length 60 is stated" in by_mistake["times_ten"]["feedback"] or by_mistake[
        "times_ten"]["feedback"]


def test_per_part_limits_the_repairs_the_feedback_can_see(part, sandbox):
    rows, _ = repairs_for_part(part, sandbox, per_part=2, seed=0, version="v")
    assert sum(row["detected"] for row in rows) == 2
    again, _ = repairs_for_part(part, sandbox, per_part=2, seed=0, version="v")
    assert again == rows


def test_a_mutation_that_builds_the_same_solid_is_rejected(part, sandbox, monkeypatch):
    # Text differs (a different but equal spelling of 60), geometry does not.
    same = BLOCK.replace("length = 60.0", "length = 60.00")
    monkeypatch.setattr(mistakes, "mutations", lambda *a, **k: [("swap_digits", same)])
    rows, rejects = repairs_for_part(part, sandbox, per_part=2, seed=0, version="v")
    assert rows == []
    assert [(r["mistake"], r["reason"]) for r in rejects] == [("swap_digits", "same_solid")]


def test_a_part_that_no_longer_reproduces_is_skipped(part, sandbox):
    stale = {**part, "measured": {**part["measured"], "volume": part["measured"]["volume"] * 2}}
    rows, rejects = repairs_for_part(stale, sandbox, per_part=2, seed=0, version="v")
    assert rows == [] and rejects[0]["reason"] == "original_not_reproduced"


class RecordingSandbox:
    """Stands in for the sandbox and remembers every program it was asked to run."""

    def __init__(self, measure):
        self.measure, self.programs = measure, []

    def run(self, code, **kwargs):
        self.programs.append(code)
        if len(self.programs) == 1:
            return {"status": "ok", "measure": self.measure}
        return {"status": "error", "error_type": "NameError", "error": "x", "line": 1}


def test_programs_run_only_through_the_sandbox(part):
    fake = RecordingSandbox(part["measured"])
    rows, _ = repairs_for_part(part, fake, per_part=3, seed=0, version="v")
    # The original first, then one run per mutation tried; nothing ran elsewhere.
    assert fake.programs[0] == BLOCK
    assert [row["wrong_code"] for row in rows] == [
        p for p in fake.programs[1:] if p in {row["wrong_code"] for row in rows}]
    assert len(fake.programs) >= 1 + len(rows)
    source = inspect.getsource(mistakes)
    assert "exec(" not in source and "eval(" not in source and "import cadquery" not in source
