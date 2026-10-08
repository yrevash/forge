"""Structures: the shared vocabulary, the formulas, the arithmetic on parts, the default rules."""

import inspect
import math

import pytest

from forge.generators.structures import defaults
from forge.generators.structures.formula import BadFormula, evaluate, names_in, rename
from forge.generators.structures.solids import Solid, bounding_box, contacts, problems, relation
from forge.generators.structures.vocabulary import PLACEMENTS, ROLES, place, slots_of
from forge.runs import PROJECT_ROOT

README = (PROJECT_ROOT / "forge" / "generators" / "structures" / "README.md").read_text()


# --- formulas -------------------------------------------------------------------------------

def test_a_formula_is_plain_arithmetic_on_named_numbers():
    values = {"seat_top": 450.0, "seat_thickness": 30.0, "count": 3}
    assert evaluate("seat_top - seat_thickness", values) == 420.0
    assert evaluate("(seat_top + 40) / (count + 1)", values) == 122.5
    assert evaluate("-seat_thickness / 2", values) == -15.0
    assert evaluate("0", values) == 0
    assert names_in("(a_b - c) / 2") == ["a_b", "c"]
    assert rename("top - height", lambda name: f"aprons_{name}") == "aprons_top - aprons_height"


@pytest.mark.parametrize("text", ["__import__('os')", "a.b", "a ** 2", "open('x')", "missing + 1",
                                  "[1, 2]", "'text'"])
def test_a_formula_can_do_nothing_else(text):
    with pytest.raises(BadFormula):
        evaluate(text, {"a": 1.0})


# --- how two parts meet, by arithmetic -------------------------------------------------------

def _box(name, size, at):
    return Solid(name, "box", size, at)


def _cyl(name, diameter, height, at):
    return Solid(name, "cylinder", (diameter, diameter, height), at)


def test_volume_and_bounding_box():
    box, post = _box("a", (100, 60, 20), (0, 0, 10)), _cyl("b", 40, 50, (10, 0, 30))
    assert box.volume == 120000
    assert post.volume == pytest.approx(math.pi * 20 * 20 * 50)
    assert bounding_box([box, post]) == ((-50, -30, 10), (50, 30, 80))


def test_relation_between_boxes():
    top = _box("top", (100, 100, 10), (0, 0, 50))
    assert relation(top, _box("leg", (10, 10, 50), (45, 45, 0))) == "face"      # leg under a corner
    assert relation(top, _box("leg", (10, 10, 55), (45, 45, 0))) == "overlap"   # leg pokes in
    assert relation(top, _box("leg", (10, 10, 49), (45, 45, 0))) == "apart"     # leg too short
    assert relation(top, _box("rail", (10, 10, 10), (55, 55, 50))) == "edge"    # corner to corner
    assert relation(top, _box("rail", (10, 100, 10), (55, 0, 50))) == "face"    # side by side


def test_relation_with_a_round_part():
    leg = _cyl("leg", 40, 400, (0, 0, 0))
    assert relation(leg, _box("seat", (300, 300, 20), (0, 0, 400))) == "face"   # seat on the leg
    assert relation(leg, _box("rail", (100, 20, 40), (70, 0, 100))) == "line"   # rail end touches
    assert relation(leg, _box("rail", (100, 20, 40), (65, 0, 100))) == "overlap"
    assert relation(leg, _box("rail", (100, 20, 40), (75, 0, 100))) == "apart"
    assert relation(leg, _cyl("other", 40, 400, (40, 0, 0))) == "line"
    assert relation(leg, _cyl("other", 40, 10, (0, 0, 400))) == "face"


def test_problems_finds_an_overlap_and_a_floating_part():
    top = _box("top", (100, 100, 10), (0, 0, 50))
    leg = _box("leg", (10, 10, 50), (45, 45, 0))
    assert problems([top, leg]) == []
    assert contacts([top, leg]) == {("top", "leg"): "face"}
    assert any("overlap" in p for p in problems([top, _box("leg", (10, 10, 60), (45, 45, 0))]))
    assert any("not one connected" in p
               for p in problems([top, leg, _box("stray", (10, 10, 10), (300, 0, 0))]))
    # Touching only along an edge does not count as joined.
    assert any("not one connected" in p
               for p in problems([top, _box("rail", (10, 10, 10), (55, 55, 50))]))


# --- placements -----------------------------------------------------------------------------

def test_every_role_uses_known_placements_and_every_placement_is_used():
    used = {placement for _, placements in ROLES.values() for placement in placements}
    assert used == set(PLACEMENTS)


def test_corner_legs_stand_flush_inside_the_spread():
    legs = place("legs", "corners", "box",
                 {"thickness": 40, "bottom": 0, "top": 420, "spread_x": 420, "spread_y": 400})
    assert [leg.name for leg in legs] == ["legs_front_left", "legs_front_right",
                                          "legs_back_left", "legs_back_right"]
    assert bounding_box(legs) == ((-210, -200, 0), (210, 200, 420))
    assert legs[0].at == (-190, -180, 0) and legs[0].size == (40, 40, 420)


def test_ring_closes_a_rectangle_without_overlapping():
    values = {"height": 60, "thickness": 20, "top": 420, "length_x": 400, "length_y": 360,
              "spread_x": 400, "spread_y": 400}
    ring = place("aprons", "ring", "box", values)
    assert len(ring) == 4 and problems(ring) == []
    assert bounding_box(ring) == ((-200, -200, 360), (200, 200, 420))


def test_rows_and_stacks_are_evenly_spaced():
    slats = place("slats", "upright_row", "box", {"count": 3, "width": 40, "thickness": 12,
                                                  "bottom": 600, "top": 800, "pitch": 95, "y": 190})
    assert [s.at[0] for s in slats] == [-95, 0, 95]
    shelves = place("shelves", "stacked", "box", {"count": 4, "width": 764, "depth": 300,
                                                  "thickness": 18, "bottom": 0, "pitch": 594,
                                                  "x": 0, "y": 0})
    assert [s.at[2] for s in shelves] == [0, 594, 1188, 1782]
    rings = place("slats", "stacked_ring", "box", {
        "count": 3, "height": 50, "thickness": 12, "bottom": 12, "pitch": 80, "length_x": 400,
        "length_y": 276, "spread_x": 400, "spread_y": 300})
    assert len(rings) == 12
    # Three separate rings with gaps between them: only the corner posts of a crate join them.
    found = problems(rings)
    assert len(found) == 1 and found[0].startswith("not one connected structure: 3 groups")


def test_three_legs_on_a_circle():
    legs = place("legs", "circle", "cylinder",
                 {"count": 3, "diameter": 30, "bottom": 0, "top": 430, "circle": 200})
    assert legs[0].at == (0, 100, 0)                       # the first leg is at the back
    for leg in legs:
        assert math.hypot(leg.at[0], leg.at[1]) == pytest.approx(100)


def test_a_step_with_a_missing_slot_is_refused():
    with pytest.raises(ValueError, match="missing slots"):
        place("seat", "level", "box", {"width": 400, "depth": 400})
    assert slots_of("level", "cylinder") == ("diameter", "thickness", "top")
    with pytest.raises(ValueError, match="no cylinder form"):
        slots_of("ring", "cylinder")


# --- default rules --------------------------------------------------------------------------

def _rules():
    return {name: fn for name, fn in inspect.getmembers(defaults, inspect.isfunction)
            if fn.__module__ == defaults.__name__ and not name.startswith("_")
            and name != "round_to"}


def test_every_default_rule_is_documented_and_listed_in_the_readme():
    rules = _rules()
    assert len(rules) >= 30
    for name, rule in rules.items():
        assert inspect.getdoc(rule), name
        assert f"| `{name}` |" in README, f"{name} is not in the README's default-rules table"


def test_the_readme_lists_every_role_and_placement():
    for name in (*ROLES, *PLACEMENTS):
        assert f"| `{name}` |" in README, name


def test_default_rules_give_the_demo_chairs_sizes():
    """forge/system1/demo_chair.py: seat 420 x 420 at 450, back to 900."""
    assert defaults.top_thickness(420) == 30                     # SEAT_THICKNESS
    assert defaults.leg_thickness(420, 420) == 40                # LEG
    assert defaults.rail_thickness(40) == 20                     # RAIL_THICKNESS
    assert defaults.apron_height(40) == 60                       # APRON_HEIGHT
    assert defaults.top_rail_height(40) == 80                    # TOP_RAIL_HEIGHT
    assert defaults.lower_rail_height(40) == 40                  # LOW_RAIL_HEIGHT
    assert defaults.lower_rail_top(450, 900, 40) == 450 + 110 + 40
    assert defaults.slat_width(40) == 40 and defaults.slat_thickness(20) == 12
    assert defaults.slat_count(340) == 3


def test_default_rules_at_their_thresholds():
    assert [defaults.leg_thickness(w, w) for w in (349, 350, 599, 600, 1099, 1100, 1499, 1500)] \
        == [30, 40, 40, 50, 50, 60, 60, 80]
    assert [defaults.top_thickness(s) for s in (399, 400, 1199, 1200)] == [20, 30, 30, 40]
    assert [defaults.bar_size(s) for s in (499, 500, 999, 1000, 1499, 1500)] \
        == [20, 30, 30, 40, 40, 50]
    assert [defaults.shelf_count(h) for h in (600, 900, 1800, 2200)] == [3, 4, 6, 7]
    assert defaults.round_to(125, 10) == 130 and defaults.round_to(124.9, 10) == 120
    assert defaults.pedestal_width(1000) == 300 and defaults.pedestal_width(1800) == 450
    assert defaults.leg_circle(300, 30) == 250
    assert defaults.crate_slat_height(288, 3) == 70
