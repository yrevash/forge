"""Structures: every kind. Arithmetic, sources, variety, and the kernel's agreement."""

import inspect
import json
import random

import pytest

from forge.generators.structures import KINDS, defaults
from forge.generators.structures.check import check
from forge.generators.structures.draft import Invalid
from forge.generators.structures.formula import evaluate
from forge.generators.structures.generate import row, shape_of, structures_for, verify
from forge.generators.structures.prompts import prompts_for
from forge.generators.structures.sampling import OVERRIDE_RANGE, sample
from forge.generators.structures.solids import problems
from forge.generators.structures.vocabulary import ROLES, place, slots_of
from forge.sandbox import Sandbox

NAMES = sorted(KINDS)
DEMO = {"seat_width": 420.0, "seat_depth": 420.0, "seat_top": 450.0, "posts_top": 900.0}
PLAIN_CHAIR = {"back": "slats", "arms": "no", "stretchers": "none", "legs": "square"}


def _many(name, count=250, seed=4):
    rng = random.Random(f"{seed}:{name}")
    return [sample(KINDS[name], rng) for _ in range(count)]


def test_the_kinds_are_registered():
    assert NAMES == ["bed", "bench", "cabinet", "cart", "chair", "chest", "crate", "desk", "fence",
                     "frame", "ladder", "pallet", "shelf", "standoffs", "stool", "table",
                     "toolbox", "workbench"]


# --- the demo chair -------------------------------------------------------------------------

def test_default_chair_is_the_hand_written_demo_chair():
    """Same request, same 16 parts, same overall size and volume as forge/system1/demo_chair.py."""
    from forge.system1 import demo_chair

    chair = KINDS["chair"].build(PLAIN_CHAIR, DEMO)
    demo_parts = [part for _, _, parts, _ in demo_chair.steps() for part in parts]
    assert len(chair.solids) == len(demo_parts) == 16
    assert chair.expected_bbox == [420.0, 420.0, 900.0]
    assert chair.expected_volume == pytest.approx(
        sum(part[1].val().Volume() for part in demo_parts), rel=1e-9)
    assert [step.name for step in chair.steps] == ["seat", "legs", "aprons", "posts", "top_rail",
                                                   "lower_rail", "slats"]
    assert chair.stated() == dict.fromkeys(DEMO, "headline")


def test_build_is_repeatable_and_the_id_names_the_structure():
    a, b = KINDS["chair"].build(PLAIN_CHAIR, DEMO), KINDS["chair"].build(PLAIN_CHAIR, DEMO)
    assert a.id == b.id and a.code == b.code
    wider = KINDS["chair"].build(PLAIN_CHAIR, {**DEMO, "seat_width": 430.0})
    assert wider.id != a.id


def test_a_stated_number_nobody_uses_is_an_error():
    with pytest.raises(ValueError, match="nobody used"):
        KINDS["chair"].build(PLAIN_CHAIR, {**DEMO, "seat_colour": 3.0})


def test_sizes_that_do_not_fit_are_refused():
    with pytest.raises(Invalid, match="slats too close"):
        KINDS["chair"].build(PLAIN_CHAIR, {**DEMO, "slats_count": 9})
    with pytest.raises(Invalid, match="aprons thicker than the legs"):
        KINDS["chair"].build(PLAIN_CHAIR, {**DEMO, "aprons_thickness": 90.0})
    # Legs spread wider than the round seat above them: the stool is no longer 300 across.
    round_stool = {"seat": "round", "legs": "square", "leg_count": "four", "foot_ring": "no"}
    with pytest.raises(Invalid, match="overall X"):
        KINDS["stool"].build(round_stool, {"seat_diameter": 300.0, "seat_top": 450.0,
                                           "legs_spread_x": 400.0})


# --- every kind: structure, sources, arithmetic ----------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_structures_are_sound_by_arithmetic(name):
    for structure in _many(name):
        solids = structure.solids
        assert problems(solids) == []
        assert structure.expected_volume == pytest.approx(sum(s.volume for s in solids))
        low = [min(s.low[axis] for s in solids) for axis in range(3)]
        high = [max(s.high[axis] for s in solids) for axis in range(3)]
        # Stands on the floor, centred on the Z axis, at the promised overall size.
        assert low[2] == pytest.approx(0, abs=1e-9)
        for axis in range(3):
            assert high[axis] - low[axis] == pytest.approx(structure.expected_bbox[axis])
        assert low[0] == pytest.approx(-high[0]) and low[1] == pytest.approx(-high[1])


@pytest.mark.parametrize("name", NAMES)
def test_every_slot_has_a_source_that_reproduces_its_value(name):
    rules = dict(inspect.getmembers(defaults, inspect.isfunction))
    for structure in _many(name):
        seen = set()
        for step in structure.steps:
            assert step.placement in ROLES[step.role][1]
            assert tuple(step.slots) == slots_of(step.placement, step.shape)
            for slot_name, slot in step.slots.items():
                assert slot.param == f"{step.name}_{slot_name}"
                assert structure.params[slot.param] == slot.value
                seen.add(slot.param)
                if slot.source == "stated":
                    assert structure.given[slot.param] == slot.value
                elif slot.source == "default":
                    assert rules[slot.rule](**slot.args) == slot.value
                elif slot.source == "arithmetic":
                    assert evaluate(slot.formula, structure.params) == slot.value
                else:
                    raise AssertionError(slot.source)
                if slot.default_value is not None:      # a default that was stated instead
                    assert slot.source == "stated"
                    assert rules[slot.rule](**slot.args) == slot.default_value
            # A step is self-contained: its slot values alone give back its parts.
            values = {slot_name: slot.value for slot_name, slot in step.slots.items()}
            assert place(step.name, step.placement, step.shape, values) == step.parts
        assert seen == set(structure.params) and set(structure.given) <= seen


@pytest.mark.parametrize("name", NAMES)
def test_the_program_names_every_parameter_and_every_part(name):
    for structure in _many(name, 40):
        code = structure.code
        assert code.startswith("import cadquery as cq\n")
        for param in structure.params:
            assert f"\n{param} = " in code, param
        for solid in structure.solids:
            assert f'result.add({solid.name}, name="{solid.name}")' in code
        assert f'result = cq.Assembly(name="{name}")' in code
        # Numbers first, then formulas, then parts: a formula only reads what is above it.
        for param, value in structure.params.items():
            _, _, slot = structure.slot(param)
            line = f"{param} = {slot.formula}" if slot.source == "arithmetic" else f"{param} = "
            assert code.index(line) < code.index("def box(")
            if slot.source != "arithmetic" and not isinstance(value, int):
                assert f"{param} = {value!r}\n" in code


@pytest.mark.parametrize("name", NAMES)
def test_the_three_levels_state_what_they_should(name):
    kind = KINDS[name]
    levels = {}
    for structure in _many(name, 400):
        levels.setdefault(structure.level, []).append(structure)
        sorts = set(structure.stated().values())
        if structure.level == "a":
            assert structure.choices == kind.choices(random.Random(0), plain=True)
            assert sorts == {"headline"}
        elif structure.level == "b":
            assert "override" not in sorts
        else:
            assert "override" in sorts
            for param, sort in structure.stated().items():
                if sort == "override":
                    slot = structure.slot(param)[2]
                    assert slot.value != slot.default_value
                    assert OVERRIDE_RANGE[0] * slot.default_value - 1e-9 <= slot.value \
                        <= OVERRIDE_RANGE[1] * slot.default_value + 1e-9
    assert set(levels) == {"a", "b", "c"}
    assert all(len(found) > 40 for found in levels.values())


@pytest.mark.parametrize("name", NAMES)
def test_every_option_choice_and_every_override_turns_up(name):
    kind = KINDS[name]
    wanted = {(option, choice) for option, found in kind.OPTIONS.items() for choice in found}
    overrides = set(kind.OVERRIDES)
    shapes = set()
    for structure in _many(name, 1500, seed=6):
        wanted -= set(structure.choices.items())
        overrides -= {p for p, sort in structure.stated().items() if sort == "override"}
        shapes.add(shape_of(structure))
        assert set(structure.choices) <= set(kind.OPTIONS)
    assert wanted == set() and overrides == set()
    assert len(shapes) >= 8          # real variation in make-up, not one fixed design


def test_round_parts_and_part_counts_vary():
    shapes, counts = set(), set()
    for name in NAMES:
        for structure in _many(name, 150):
            shapes |= {step.shape for step in structure.steps}
            counts.add(len(structure.solids))
    assert shapes == {"box", "cylinder", "cylinder_x"}
    assert min(counts) <= 4 and max(counts) >= 25


# --- the kernel agrees ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def sandbox():
    with Sandbox(timeout=60) as box:
        yield box


def _one_of_each_shape(name, limit):
    """Structures with different make-ups, so the kernel sees every branch of a kind."""
    chosen = {}
    for structure in _many(name, 600, seed=9):
        chosen.setdefault(shape_of(structure), structure)
    return list(chosen.values())[:limit]


@pytest.mark.parametrize("name", NAMES)
def test_the_kernel_measures_what_arithmetic_says(name, sandbox):
    for structure in _one_of_each_shape(name, 14):
        reply = sandbox.run(structure.code, structure=True)
        assert check(structure, reply) == [], (structure.choices, structure.given)
        measured = reply["measure"]["structure"]
        assert measured["n_parts"] == len(structure.solids)
        assert measured["overlaps"] == [] and measured["untouched"] == []
        assert measured["n_groups"] == 1


def _rail_back_chair():
    return KINDS["chair"].build({**PLAIN_CHAIR, "back": "rails"}, DEMO)


def test_mutation_overlapping_part_is_caught(sandbox):
    chair = _rail_back_chair()
    line = "legs_top = seat_top - seat_thickness"
    assert line in chair.code
    found = check(chair, sandbox.run(chair.code.replace(line, line + " + 10"), structure=True))
    assert any("overlap by" in p for p in found), found      # the legs now poke into the seat


def test_mutation_floating_part_is_caught(sandbox):
    chair = _rail_back_chair()
    line = "lower_rail_width = seat_width - 2 * posts_thickness"
    assert line in chair.code
    found = check(chair, sandbox.run(chair.code.replace(line, line + " - 20"), structure=True))
    assert "lower_rail touches nothing" in found
    assert any("separate groups" in p for p in found)
    assert any("joints the kernel did not find" in p for p in found)


def test_mutation_missing_part_is_caught(sandbox):
    chair = _rail_back_chair()
    line = 'result.add(top_rail, name="top_rail")\n'
    assert line in chair.code
    found = check(chair, sandbox.run(chair.code.replace(line, ""), structure=True))
    assert "part count: measured 12, expected 13" in found
    assert any("missing ['top_rail']" in p for p in found)
    assert any("total volume" in p for p in found)


def test_mutation_wrong_overall_size_is_caught(sandbox):
    chair = _rail_back_chair()
    assert "seat_width = 420.0\n" in chair.code
    found = check(chair, sandbox.run(chair.code.replace("seat_width = 420.0\n",
                                                         "seat_width = 430.0\n"), structure=True))
    assert any(p.startswith("overall X: measured 430") for p in found), found
    taller = chair.code.replace("posts_top = 900.0\n", "posts_top = 905.0\n")
    assert any(p.startswith("overall Z: measured 905")
               for p in check(chair, sandbox.run(taller, structure=True)))


def test_mutation_part_moved_off_the_floor_is_caught(sandbox):
    chair = _rail_back_chair()
    lifted = chair.code.replace("legs_bottom = 0\n", "legs_bottom = 5\n")
    assert lifted != chair.code
    assert check(chair, sandbox.run(lifted, structure=True)) != []


def test_a_program_that_fails_is_reported_not_raised(sandbox):
    chair = _rail_back_chair()
    assert check(chair, sandbox.run("result = 1 / 0", structure=True))[0].startswith("error")
    assert check(chair, sandbox.run(chair.code)) == [
        "the sandbox was not asked for structure measurements"]


# --- the sandbox and `measure` are unchanged for single parts --------------------------------

def test_structure_flag_adds_one_entry_and_changes_nothing_else(sandbox):
    code = "import cadquery as cq\nresult = cq.Workplane('XY').box(40, 30, 10)\n"
    plain, flagged = sandbox.run(code)["measure"], sandbox.run(code, structure=True)["measure"]
    assert "structure" not in plain
    extra = flagged.pop("structure")
    assert flagged == plain and plain["one_valid_solid"]
    assert extra["n_parts"] == 1 and extra["n_groups"] == 1 and extra["overlaps"] == []


# --- rows -----------------------------------------------------------------------------------

def test_rows_carry_provenance_steps_sources_and_prompts():
    structures = structures_for("stool", 6, seed=0)
    assert [s.id for s in structures] == [s.id for s in structures_for("stool", 6, seed=0)]
    assert len({s.id for s in structures}) == 6
    for structure in structures[:3]:
        structure, measure, found = verify(structure)
        assert found == []
        prompts, _ = prompts_for(KINDS["stool"], structure)
        made = json.loads(json.dumps(row(structure, measure, prompts, "test")))
        assert made["source"] == "gen:structure:stool" and made["license"] == "forge"
        assert made["generator_version"] == "test" and made["kind"] == "stool"
        assert made["measured"]["n_parts"] == made["expected"]["n_parts"]
        assert made["measured"]["n_overlaps"] == 0 and made["measured"]["n_groups"] == 1
        assert {"id", "level", "variant", "given", "stated", "steps", "params", "program",
                "geom_fingerprint", "prompts"} <= set(made)
        for step in made["steps"]:
            assert {"name", "role", "placement", "shape", "slots", "parts"} <= set(step)
            for slot in step["slots"].values():
                assert slot["source"] in ("stated", "default", "arithmetic")
                assert ("formula" in slot) == (slot["source"] == "arithmetic")
                assert ("rule" in slot) or slot["source"] != "default"
        assert KINDS["stool"].build(made["variant"], made["given"]).id == made["id"]
