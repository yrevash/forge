"""Structures added on 6 Oct 2026: lying cylinders, the new placements, the two added rules
(minimum board thickness, tall narrow units are rare) and what is particular to each new kind."""

import math
import random

import pytest

from forge.generators.structures import KINDS, defaults
from forge.generators.structures.check import check
from forge.generators.structures.draft import MIN_BOARD_THICKNESS, Invalid
from forge.generators.structures.generate import (
    _save,
    already_verified,
    generator_version,
    structures_for,
)
from forge.generators.structures.prompts import prompts_for
from forge.generators.structures.sampling import TALL_NARROW_SHARE, is_tall_narrow, sample
from forge.generators.structures.solids import Solid, bounding_box, contacts, problems, relation
from forge.generators.structures.vocabulary import SHAPES, place, slots_of
from forge.sandbox import Sandbox

NEW = ["bed", "cabinet", "cart", "chest", "fence", "ladder", "pallet", "standoffs", "toolbox",
       "workbench"]


def _many(name, count=300, seed=21):
    rng = random.Random(f"{seed}:{name}")
    return [sample(KINDS[name], rng) for _ in range(count)]


# --- a cylinder lying left to right -----------------------------------------------------------

def _lying(name, diameter, length, at):
    return Solid(name, "cylinder_x", (length, diameter, diameter), at)


def _box(name, size, at):
    return Solid(name, "box", size, at)


def test_a_lying_cylinder_has_the_volume_and_box_of_a_cylinder_on_its_side():
    bar = _lying("bar", 30, 400, (0, 0, 100))
    assert bar.volume == pytest.approx(math.pi * 15 * 15 * 400)
    assert bounding_box([bar]) == ((-200, -15, 100), (200, 15, 130))
    assert bar.axis == 0 and _box("b", (1, 1, 1), (0, 0, 0)).axis is None
    assert SHAPES == ("box", "cylinder", "cylinder_x")


def test_relation_of_a_lying_cylinder_with_flat_parts():
    bar = _lying("bar", 30, 400, (0, 0, 100))                    # x -200..200, z 100..130
    assert relation(bar, _box("post", (40, 40, 300), (220, 0, 0))) == "face"      # end on a post
    assert relation(bar, _box("post", (40, 40, 300), (215, 0, 0))) == "overlap"   # pokes into it
    assert relation(bar, _box("post", (40, 40, 300), (225, 0, 0))) == "apart"
    assert relation(bar, _box("deck", (300, 100, 20), (0, 0, 130))) == "line"     # lies under a deck
    assert relation(bar, _box("deck", (300, 100, 20), (0, 0, 125))) == "overlap"
    # An end that only reaches the corner of a block is not a joint.
    assert relation(bar, _box("block", (40, 40, 40), (220, 35, 115))) == "edge"


def test_relation_of_two_lying_cylinders_on_one_axis():
    wheel = _lying("wheel", 100, 30, (0, 0, 0))                  # x -15..15
    assert relation(wheel, _lying("axle", 20, 200, (115, 0, 40))) == "face"       # end to end
    assert relation(wheel, _lying("axle", 20, 200, (110, 0, 40))) == "overlap"
    assert relation(wheel, _lying("other", 100, 30, (0, 100, 0))) == "line"       # side by side
    assert relation(wheel, _lying("other", 100, 30, (0, 101, 0))) == "apart"


def test_crossed_round_parts_are_refused_not_guessed():
    upright = Solid("leg", "cylinder", (40, 40, 400), (0, 0, 0))
    with pytest.raises(NotImplementedError):
        relation(upright, _lying("bar", 30, 400, (0, 0, 100)))
    assert relation(upright, _lying("bar", 30, 400, (0, 300, 100))) == "apart"    # boxes apart


# --- the new placements -----------------------------------------------------------------------

def test_wheels_are_lying_discs_in_the_corners():
    wheels = place("wheels", "corners", "cylinder_x",
                   {"diameter": 100, "width": 30, "bottom": 0, "spread_x": 560, "spread_y": 800})
    assert [w.name for w in wheels] == ["wheels_front_left", "wheels_front_right",
                                        "wheels_back_left", "wheels_back_right"]
    assert bounding_box(wheels) == ((-280, -400, 0), (280, 400, 100))
    assert wheels[0].size == (30, 100, 100) and wheels[0].at == (-265, -350, 0)


def test_members_one_above_another_and_side_by_side():
    rungs = place("rungs", "stacked_across", "cylinder_x",
                  {"count": 3, "width": 350, "diameter": 30, "bottom": 235, "pitch": 250, "y": 0})
    assert [r.at[2] for r in rungs] == [235, 485, 735] and rungs[0].size == (350, 30, 30)
    fronts = place("fronts", "stacked_across", "box",
                   {"count": 2, "width": 500, "height": 200, "thickness": 18, "bottom": 80,
                    "pitch": 203, "y": -190})
    assert [f.at for f in fronts] == [(0, -190, 80), (0, -190, 283)]
    assert fronts[0].size == (500, 18, 200)
    slats = place("slats", "long_row", "box",
                  {"count": 3, "length_x": 900, "width": 70, "height": 20, "top": 300,
                   "pitch": 200, "y": -15})
    assert [s.at[1] for s in slats] == [-215, -15, 185] and slats[0].size == (900, 70, 20)
    bar = place("handle", "across", "cylinder_x", {"width": 440, "diameter": 25, "y": 430,
                                                   "top": 880})
    assert bar[0].size == (440, 25, 25) and bar[0].at == (0, 430, 855)
    assert slots_of("left_right", "cylinder") == ("diameter", "bottom", "top", "spread_x")
    with pytest.raises(ValueError, match="no lying cylinder form"):
        slots_of("ring", "cylinder_x")


# --- added rule 1: a board that carries weight is never thinner than 15 -----------------------

def test_a_weight_carrying_board_thinner_than_15_is_refused():
    table = KINDS["table"]
    plain = table.choices(random.Random(0), plain=True)
    sizes = {"top_width": 1200.0, "top_depth": 800.0, "top_top": 750.0}
    assert table.build(plain, {**sizes, "top_thickness": 15.0})
    with pytest.raises(Invalid, match="at least 15"):
        table.build(plain, {**sizes, "top_thickness": 14.0})
    shelf = KINDS["shelf"]
    unit = {"sides_spread_x": 800.0, "sides_height": 900.0, "sides_length_y": 300.0}
    with pytest.raises(Invalid, match="at least 15"):
        shelf.build(shelf.choices(random.Random(0), plain=True), {**unit, "shelves_thickness": 13.0})


@pytest.mark.parametrize("name", sorted(KINDS))
def test_no_generated_top_seat_or_shelf_is_thinner_than_15(name):
    light = {("crate", "lid"), ("standoffs", "top"), ("standoffs", "middle")}   # not such boards
    thinnest = math.inf
    for structure in _many(name, 400):
        for step in structure.steps:
            if step.role in ("top", "shelves") and (name, step.name) not in light:
                thinnest = min(thinnest, step.slots["thickness"].value)
    assert thinnest >= MIN_BOARD_THICKNESS


def test_a_lid_and_a_metal_plate_may_be_thinner():
    crate = KINDS["crate"].build({"sides": "solid", "lid": "yes", "base": "flat", "handles": "none"},
                                 {"bottom_width": 400.0, "bottom_depth": 300.0, "lid_top": 250.0})
    assert crate.params["lid_thickness"] == 12
    assert min(s.params["top_thickness"] for s in _many("standoffs", 100)) < MIN_BOARD_THICKNESS


# --- added rule 2: tall narrow units are rare, not excluded -------------------------------------

@pytest.mark.parametrize("name", ["shelf", "cabinet", "frame"])
def test_tall_narrow_units_are_a_few_percent(name):
    found = _many(name, 1500, seed=23)
    share = sum(is_tall_narrow(s) for s in found) / len(found)
    assert 0 < share <= 0.05, share
    assert TALL_NARROW_SHARE == 0.03


@pytest.mark.parametrize("name", sorted(set(KINDS) - {"ladder", "fence"}))
def test_no_kind_is_often_tall_and_narrow(name):
    found = _many(name, 400, seed=25)
    assert sum(is_tall_narrow(s) for s in found) / len(found) <= 0.06


def test_a_ladder_and_a_fence_are_tall_by_nature():
    assert KINDS["ladder"].TALL_BY_NATURE and KINDS["fence"].TALL_BY_NATURE
    assert all(is_tall_narrow(s) for s in _many("ladder", 50))


# --- the wording fix: a count that says the option is not repeated in words ---------------------

@pytest.mark.parametrize("name", ["chair", "bench", "crate", "bed", "shelf", "cabinet", "fence"])
def test_an_option_a_count_expresses_is_not_also_said_in_words(name):
    kind = KINDS[name]
    seen_by_count = 0
    for structure in _many(name, 200):
        for prompt in prompts_for(kind, structure)[0]:
            by_count = {o["option"] for o in prompt["options"] if o["by"] == "count"}
            by_phrase = {o["option"] for o in prompt["options"] if o["by"] == "phrase"}
            assert not by_count & by_phrase, prompt["text"]
            seen_by_count += len(by_count)
    assert seen_by_count > 20


# --- the generator version belongs to one kind --------------------------------------------------

def test_each_kind_has_its_own_generator_version():
    versions = {name: generator_version(name) for name in KINDS}
    assert all(len(v) == 12 and int(v, 16) >= 0 for v in versions.values())
    assert len(set(versions.values())) == len(KINDS)
    assert generator_version("cart") == versions["cart"]


def test_a_resumed_run_keeps_only_rows_of_the_same_generator_version(tmp_path):
    path = tmp_path / "cart.jsonl"
    assert already_verified(path, "abc") == {}
    _save(path, [{"id": "1", "generator_version": "abc"}, {"id": "2", "generator_version": "old"}])
    assert list(already_verified(path, "abc")) == ["1"]
    assert not path.with_suffix(".jsonl.partial").exists()


def test_a_short_run_is_the_start_of_a_longer_run():
    """So 1,500 structures can be topped up to 3,000 without verifying the first half again."""
    short, longer = structures_for("toolbox", 20, seed=0), structures_for("toolbox", 40, seed=0)
    assert [s.id for s in short] == [s.id for s in longer[:20]]


# --- what is particular to each new kind --------------------------------------------------------

def test_cart_wheels_sit_on_the_ends_of_the_axle_beams():
    cart = KINDS["cart"].build({"handle": "back", "sides": "no"},
                               {"top_width": 500.0, "top_depth": 900.0, "posts_top": 900.0})
    v = cart.params
    assert v["wheels_diameter"] == 125 and v["axles_length_x"] == 520
    assert cart.expected_bbox == [520 + 2 * v["wheels_width"], 900.0, 900.0]
    shapes = {step.name: step.shape for step in cart.steps}
    assert shapes["wheels"] == shapes["handle"] == "cylinder_x" and shapes["axles"] == "box"
    met = contacts(cart.solids)
    for corner, axle in (("front_left", 1), ("front_right", 1), ("back_left", 2), ("back_right", 2)):
        assert met[(f"wheels_{corner}", f"axles_{axle}")] == "face"
    assert ("wheels_front_left", "top") not in met          # 10 clear of the deck's edge
    assert met[("posts_back_left", "handle")] == "face"
    assert "def lying_cylinder(" in cart.code
    assert "def lying_cylinder(" not in KINDS["chest"].build(
        {"base": "plinth", "back": "yes"},
        {"top_width": 800.0, "top_depth": 450.0, "top_top": 900.0}).code


def test_chest_fronts_fill_the_opening_with_3_between_them():
    chest = KINDS["chest"].build({"base": "none", "back": "no"},
                                 {"top_width": 800.0, "top_depth": 450.0, "top_top": 900.0,
                                  "fronts_count": 4})
    v = chest.params
    opening = v["sides_top"] - v["bottom_top"]
    assert 4 * v["fronts_height"] + 3 * 3 == pytest.approx(opening)
    fronts = [s for s in chest.solids if s.name.startswith("fronts_")]
    assert fronts[0].low[2] == v["bottom_top"]
    assert fronts[-1].high[2] == pytest.approx(v["sides_top"])


def test_cabinet_doors_meet_in_the_middle_and_shelves_stop_behind_them():
    cabinet = KINDS["cabinet"].build({"doors": "yes", "base": "plinth", "back": "yes"},
                                     {"top_width": 900.0, "top_depth": 400.0, "top_top": 1200.0})
    v = cabinet.params
    assert v["doors_count"] == 2 and 2 * v["doors_width"] == v["bottom_width"]
    assert v["shelves_depth"] == 400 - v["back_panel_thickness"] - v["doors_thickness"]
    assert contacts(cabinet.solids)[("doors_1", "doors_2")] == "face"
    assert KINDS["cabinet"].build({"doors": "yes", "base": "legs", "back": "no"},
                                  {"top_width": 450.0, "top_depth": 400.0, "top_top": 800.0}
                                  ).params["doors_count"] == 1


def test_ladder_rungs_are_one_step_apart_from_floor_to_top():
    ladder = KINDS["ladder"].build({"rungs": "round", "stabiliser": "no"},
                                   {"sides_spread_x": 400.0, "sides_top": 2800.0})
    v = ladder.params
    assert v["rungs_count"] == 9 and v["rungs_pitch"] == 280
    centres = [s.at[2] + s.size[2] / 2 for s in ladder.solids if s.name.startswith("rungs_")]
    assert centres == pytest.approx([280 * (i + 1) for i in range(9)])
    assert ladder.expected_bbox == [400.0, 90.0, 2800.0]


def test_pallet_height_is_the_stack_of_board_stringer_and_board():
    pallet = KINDS["pallet"].build({"deck": "slats", "bottom_boards": "yes"},
                                   {"stringers_length_x": 1200.0, "deck_length_y": 800.0})
    v = pallet.params
    assert pallet.expected_bbox[2] == v["bottom_boards_height"] + v["stringers_height"] \
        + v["deck_height"] == 22 + 100 + 22
    assert v["stringers_count"] == 3 and v["deck_count"] == 8 and v["bottom_boards_count"] == 5


def test_standoffs_of_a_three_plate_stack_are_equally_long():
    stack = KINDS["standoffs"].build({"plates": "rectangular", "standoffs": "round",
                                      "tiers": "three"},
                                     {"base_width": 120.0, "base_depth": 80.0, "top_top": 85.0})
    v = stack.params
    lower = v["standoffs_top"] - v["standoffs_bottom"]
    upper = v["upper_standoffs_top"] - v["upper_standoffs_bottom"]
    assert lower == upper == 35 and v["base_thickness"] == 5
    assert len(stack.solids) == 3 + 8


def test_bed_slats_lie_between_the_headboard_and_the_footboard():
    bed = KINDS["bed"].build({"headboard": "panel", "footboard": "yes", "base": "slats",
                              "centre_rail": "yes"},
                             {"legs_spread_x": 1400.0, "legs_spread_y": 2000.0, "slats_top": 350.0,
                              "headboard_top": 1000.0})
    slats = [s for s in bed.solids if s.name.startswith("slats_")]
    head = next(s for s in bed.solids if s.name == "headboard")
    foot = next(s for s in bed.solids if s.name == "footboard")
    assert min(s.low[1] for s in slats) == pytest.approx(foot.high[1])
    assert max(s.high[1] for s in slats) == pytest.approx(head.low[1])
    assert problems(bed.solids) == [] and bed.expected_bbox == [1400.0, 2000.0, 1000.0]


def test_fence_pickets_stand_in_front_of_the_rails_inside_the_posts_depth():
    fence = KINDS["fence"].build({"infill": "pickets", "cap": "no"},
                                 {"posts_spread_x": 1800.0, "posts_top": 1200.0})
    v = fence.params
    assert fence.expected_bbox == [1800.0, v["posts_thickness"], 1200.0]
    met = contacts(fence.solids)
    assert met[("rails_1", "pickets_1")] == "face" and met[("posts_left", "rails_1")] == "face"


def test_new_default_rules_at_their_thresholds():
    assert [defaults.door_count(w) for w in (499, 500)] == [1, 2]
    assert [defaults.drawer_count(h) for h in (300, 700, 1500)] == [2, 4, 6]
    assert [defaults.wheel_diameter(n) for n in (799, 800, 999, 1000)] == [100, 125, 125, 160]
    assert defaults.wheel_width(125) == 35 and defaults.axle_size(100) == 30
    assert [defaults.rung_count(h) for h in (1500, 2800, 4000)] == [4, 9, 13]
    assert defaults.footboard_top(350, 600) == 590 and defaults.footboard_top(350, 0) == 450
    assert defaults.standoff_spread(120, 8) == 104
    assert [defaults.fence_rail_count(h) for h in (1299, 1300)] == [2, 3]
    assert defaults.picket_thickness(80) == 20 and defaults.mid_rail_top(1450) == 730


# --- the kernel agrees on the parts that are new ------------------------------------------------

@pytest.fixture(scope="module")
def sandbox():
    with Sandbox(timeout=60) as box:
        yield box


def test_kernel_catches_a_wheel_moved_into_the_deck(sandbox):
    cart = KINDS["cart"].build({"handle": "back", "sides": "no"},
                               {"top_width": 500.0, "top_depth": 900.0, "posts_top": 900.0})
    assert check(cart, sandbox.run(cart.code, structure=True)) == []
    line = "axles_length_x = top_width + 20"
    assert line in cart.code
    # Axle beams no longer than the deck is wide: the wheels now rub against the deck's edge,
    # and the cart is narrower than promised.
    found = check(cart, sandbox.run(cart.code.replace(line, "axles_length_x = top_width"),
                                    structure=True))
    assert any(p.startswith("overall X") for p in found), found


def test_kernel_catches_a_rung_that_is_too_short(sandbox):
    ladder = KINDS["ladder"].build({"rungs": "round", "stabiliser": "no"},
                                   {"sides_spread_x": 400.0, "sides_top": 2800.0})
    assert check(ladder, sandbox.run(ladder.code, structure=True)) == []
    line = "rungs_width = sides_spread_x - 2 * sides_thickness"
    assert line in ladder.code
    found = check(ladder, sandbox.run(ladder.code.replace(line, line + " - 30"), structure=True))
    assert any("touches nothing" in p for p in found), found
