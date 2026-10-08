"""Each rejection reason is triggered by small examples, and the tolerances are exact."""

import pytest

from forge.plan import parse_plan
from forge.plan.model import MISSING_MOVE_REASONS, REASONS

BASE = ("base: box 400 by 300 by 20, on ground\n"
        "wall: box 20 by 300 by 200, right of base\n"
        "posts: box 40 by 40 by 300, on top of base, at the left corners of base\n"
        "legs: box 40 by 40 by 100, on top of base, at each corner of base\n"
        "pipe: tube 60 by 50 by 200, on top of base\n")
BOX = "t: box 1 by 1 by 1"


def rejections(body: str, setup: str = BASE) -> list[tuple[str, str]]:
    """(reason, fragment) for the last non-`done` line of setup + body."""
    plan = parse_plan(setup + body + "\ndone\n")
    return [(r.reason, r.fragment) for r in plan.lines[-2].rejections]


# One small line per case, with the fragment the parser must point at.
EXAMPLES = [
    # --- words the language does not have ---
    ("unknown shape", "t: pyramid 10 by 10, on top of base", "pyramid"),
    ("unknown shape", "t: bar Z 10 by 10 by 100, on top of base", "bar z"),
    ("unknown placement", f"{BOX}, next to base", "next to base"),
    ("unknown placement", f"{BOX}, on the ground", "on the ground"),
    ("unknown placement", f"{BOX}, above ground", "above ground"),
    ("unknown placement", f"{BOX}, between legs", "between legs"),
    ("unknown placement", f"{BOX}, between base", "between base"),
    ("unknown placement", f"{BOX}, between base and base", "between base and base"),
    ("unknown placement", f"{BOX}, between posts and legs", "between posts and legs"),
    ("unknown placement", "t: box rest by 1 by 1, spans from base's top to wall's middle",
     "spans from base's top to wall's middle"),
    ("unknown placement", "t: box rest by 1 by 1, spans from base's top-bottom edge to wall's top",
     "spans from base's top-bottom edge to wall's top"),
    ("unknown placement", "t: box rest by 1 by 1, spans from posts's top to wall's top",
     "spans ... posts and wall"),
    ("unknown placement", f"{BOX}, on top of base, gap", "gap"),
    ("unknown alignment", f"{BOX}, on top of base, flush with base", "flush with base"),
    ("unknown alignment", f"{BOX}, on top of base, inset", "inset"),
    ("unknown alignment", f"{BOX}, on top of base, top flush with base's left", "top ... left"),
    ("unknown alignment", f"{BOX}, on top of base, left 5 above base's left",
     "above (left ... left)"),
    ("unknown alignment", f"{BOX}, on top of base, top 5 inside base's front", "top ... front"),
    ("unknown repetition", f"{BOX}, on top of base, at every corner of base",
     "at every corner of base"),
    ("unknown repetition", f"{BOX}, on top of base, at the top-left corner of base",
     "at the top-left corner of base"),
    ("unknown repetition", f"{BOX}, on top of base, mirrored up-down", "mirrored up-down"),
    ("unknown repetition", f"{BOX}, on top of base, 3 in a row", "3 in a row"),
    ("unknown repetition", f"{BOX}, on top of base, grid 0 by 2", "grid 0 by 2"),
    ("unknown size form", "t: box 10 by wide by 5, on top of base", "wide"),
    ("unknown size form", "t: box 10 by 2/3 of base's depth by 5, on top of base",
     "2/3 of base's depth"),
    ("unknown size form", "t: box 10 by same as base's width by 5, on top of base",
     "same as base's width"),
    ("unknown size form", "t: box 10 by double of base's depth by 5, on top of base",
     "double of base's depth"),
    ("unknown size form", "t: box 0 by 10 by 5, on top of base", "0"),
    ("unknown size form", "t: cone 0 by 10 by 5, on top of base", "0"),
    ("unknown size form", "t: prism 2 by 10 by 5, on top of base", "2"),
    ("unknown size form", "t: cylinder 40, 100, on top of base", "cylinder 40, 100"),
    ("unknown size form", "t: cone 40, 0, 100 pointing down, on top of base",
     "cone 40, 0, 100 pointing down"),
    ("unknown size form", f"{BOX}, on top of base, flush with base's left, inset a bit", "a bit"),
    ("unknown size form", f"{BOX}, on top of base, gap rest", "rest"),
    ("unknown size form", "on base: hole diameter eight", "diameter eight"),
    ("unknown size form", "on base: hole diameter 0", "diameter 0"),
    ("unknown size form", "on base: circle of holes count 2.5", "count 2.5"),
    ("unknown orientation", "t: box 1 by 1 by 9 lying along height, on top of base",
     "lying along height"),
    ("unknown orientation", "t: sphere 9 lying along length, on top of base", "lying along length"),
    ("unknown orientation", "t: cone 9 by 0 by 9 lying along length, on top of base",
     "lying along length"),
    ("unknown orientation", "t: box 1 by 1 by 9 pointing left, on top of base", "pointing left"),
    ("unknown orientation", "t: wedge 9 by 9 by 9 pointing sideways, on top of base",
     "pointing sideways"),
    ("unknown orientation", "t: wedge 9 by 9 by 9 pointing left flat right, on top of base",
     "flat right"),
    ("unknown orientation", "t: cone 9 by 0 by 9 pointing left flat top, on top of base",
     "flat top"),
    ("unknown orientation", f"{BOX}, on top of base, lying flat", "lying flat"),
    ("unknown feature", "on base: dovetail 5", "dovetail"),
    ("unknown feature", "on base: hole radius 5", "radius 5"),
    ("unknown feature", "on base: hole x 5", "x 5"),
    ("unknown feature", "on base: hole diameter 5, diameter 6", "diameter 6"),
    ("unknown part", f"{BOX}, on top of lid", "lid"),
    ("unknown part", f"{BOX}, on top of base, flush with lid's front", "lid"),
    ("unknown part", "t: box same as lid's length by 1 by 1, on top of base", "lid"),
    ("unknown part", f"{BOX}, on top of base, gap half of lid's depth", "lid"),
    ("unknown part", f"{BOX}, on top of base, 4 around lid on circle 50", "lid"),
    ("unknown part", "on lid: hole", "lid"),
    ("unknown part", f"{BOX}, on top of t", "t"),
    ("not in the language", "make it look nice", "make it look nice"),
    ("not in the language", f"{BOX}, on top of base, tilted 15 degrees", "tilted 15 degrees"),
    ("not in the language", "ground: box 1 by 1 by 1, on top of base", "ground"),
    ("not in the language", "my_part: box 1 by 1 by 1, on top of base", "my_part"),
    # --- known words, broken rule ---
    ("wrong number of sizes", "t: box 10 by 10, on top of base", "10 by 10"),
    ("wrong number of sizes", "t: box, on top of base", "box"),
    ("wrong number of sizes", "t: sphere 10 by 10, on top of base", "10 by 10"),
    ("wrong number of sizes", "t: bar L 600, on top of base", "600"),
    ("wrong number of sizes", "t: bar tube 20 by 20 by 2 by 600, on top of base",
     "20 by 20 by 2 by 600"),
    ("missing placement", BOX, BOX),
    ("missing placement", f"{BOX}, offset 5 to the left", f"{BOX}, offset 5 to the left"),
    ("more than one placement", f"{BOX}, on top of base, left of base", "left of base"),
    ("more than one repetition", f"{BOX}, on top of base, grid 2 by 2, mirrored left-right",
     "mirrored left-right"),
    ("duplicate name", "base: box 1 by 1 by 1, on top of base", "base"),
    ("rest not defined", "t: box 1 by 1 by rest, on top of base", "rest"),
    ("rest not defined", "t: box 1 by 1 by rest, left of base, down to ground", "rest"),
    ("rest not defined", "t: box rest by 1 by 1, under wall, down to ground", "rest (length)"),
    ("rest not defined", "t: cylinder rest by 10, between base and wall", "rest (diameter)"),
    ("rest not defined", "t: tube 60 by rest by 10, around pipe", "rest (inner diameter)"),
    ("rest not defined", "t: bar L rest by 9 by 1 by 90, between base and wall",
     "rest (profile width)"),
    ("rest not defined", "t: box rest by 1 by rest, between base and wall", "rest"),
    ("rest not defined", "t: box 1 by 1 by rest, inside pipe", "rest (height)"),
    ("rest not defined", "t: box 1 by 1 by rest, on top of posts, across posts", "rest (height)"),
    ("rest not defined", "t: box 1 by 1 by 1, spans from base's top to wall's top", "spans"),
    ("rest not defined", "t: sphere 5, spans from base's top to wall's top", "spans"),
    ("rest not defined", "t: cylinder 5 by rest lying along length, under wall, down to ground",
     "rest (height)"),
    ("first part not on ground", f"{BOX}, on top of base", "on top of"),
    ("out of place", f"{BOX}, offset 5 to the left, on top of base", "on top of base"),
    ("out of place", f"{BOX}, on top of base, grid 2 by 2, offset 5 to the left", "grid 2 by 2"),
    ("out of place", "t: box 1 by 1 by 9, lying along length, on top of base",
     "lying along length"),
    ("out of place", "end", "end"),
    ("out of place", f"{BOX}, on ground, gap 5", "gap"),
    ("out of place", f"{BOX}, between base and wall, sunk 5 into base", "sunk"),
    ("out of place", f"{BOX}, on top of base, sunk 5 into wall", "sunk ... wall"),
    ("out of place", f"{BOX}, on top of posts, across legs", "across ... legs"),
    ("out of place", f"{BOX}, on top of base, gap 5, sunk 5 into base", "sunk"),
    ("out of place", f"{BOX}, around pipe", "around pipe"),
    ("out of place", f"{BOX}, inside base", "inside base"),
    ("out of place", f"{BOX}, on top of base, left 5 inside base's inner left",
     "base's inner left"),
    ("out of place", "on base's inner left: hole", "base's inner left"),
    ("out of place", f"{BOX}, on top of base, inset 5", "inset"),
    ("out of place", f"{BOX}, on ground, mirrored left-right", "mirrored left-right"),
    ("out of place", f"{BOX}, on top of base, 3 evenly spaced along height", "along height"),
    ("out of place", f"{BOX}, left of base, 3 spread along length", "along length"),
    ("out of place", f"{BOX}, on top of base, 4 around pipe", "around"),
    ("out of place", f"{BOX}, left of pipe, 4 around pipe on circle 90", "on circle"),
    ("out of place", ("t: box rest by 1 by 1, spans from base's top to wall's top, "
                     "mirrored left-right"), "spans"),
    ("out of place", "on base's front: top chamfer size 2", "top chamfer"),
    ("out of place", "on base: circle of holes count 4, at (5, 5)", "circle of holes"),
    ("out of place", "on base: hole diameter 4, open at top", "open at top"),
    ("out of place", "on base: hole diameter 4, centred, 5 from base's left",
     "5 from base's left"),
    ("out of place", "on base: hole diameter 4, 5 from base's top", "from base's top"),
    ("out of place", "on base: hole diameter 4, 5 from base's left, 5 from base's right",
     "from base's right"),
    ("out of place", "on base: hole diameter 4, 5 from wall's left", "from wall"),
]


@pytest.mark.parametrize(("reason", "body", "fragment"), EXAMPLES)
def test_reason_and_fragment(reason, body, fragment):
    setup = "" if reason == "first part not on ground" else BASE
    assert rejections(body, setup) == [(reason, fragment)]


def test_every_reason_has_an_example():
    covered = {reason for reason, _, _ in EXAMPLES} | {"missing done"}
    assert covered == set(REASONS)
    assert MISSING_MOVE_REASONS <= set(REASONS)


def test_several_mistakes_on_one_line_are_all_reported():
    found = rejections("t: blob 3, beside base, upside down")
    assert found == [("unknown shape", "blob"), ("unknown placement", "beside base"),
                     ("not in the language", "upside down")]


# --- rules about the whole plan -------------------------------------------------------------

def test_missing_done_is_a_problem_of_the_plan():
    plan = parse_plan(BASE)
    assert all(line.accepted for line in plan.lines)
    assert not plan.accepted
    assert [p.reason for p in plan.problems] == ["missing done"]


def test_nothing_may_follow_done():
    plan = parse_plan(BASE + "done\nlid: box 1 by 1 by 1, on top of base\n")
    assert [(r.reason, r.detail) for r in plan.lines[-1].rejections] == [
        ("out of place", "`done` must be the last line")]


def test_the_first_part_may_be_on_ground_or_above_ground():
    for first in ("on ground", "above ground, top at height 50"):
        assert parse_plan(f"{BOX}, {first}\ndone\n").accepted


def test_undo_with_nothing_built_is_out_of_place():
    plan = parse_plan("undo\n")
    assert plan.lines[0].rejections[0].reason == "out of place"


def test_undo_to_an_unknown_name():
    assert rejections("undo to lid") == [("unknown part", "lid")]


GROUP = "group leg\n  upper: box 1 by 1 by 1\n  lower: box 1 by 1 by 1, under upper\nend\n"


def test_group_must_be_declared_before_it_is_used():
    plan = parse_plan(BASE + "feet: leg, on top of base\n" + GROUP
                      + "feet: leg, on top of base\ndone\n")
    first_try = plan.lines[len(BASE.splitlines())]
    assert [(r.reason, r.fragment) for r in first_try.rejections] == [("unknown shape", "leg")]
    assert plan.lines[-2].accepted           # the same line, now after the declaration


def test_the_first_part_of_a_group_has_a_shape_and_sizes_only():
    plan = parse_plan(BASE + "group leg\n  upper: box 1 by 1 by 1, on ground\nend\ndone\n")
    assert [(r.reason, r.fragment) for r in plan.lines[-3].rejections] == [
        ("out of place", "on ground")]
    later = parse_plan(BASE + "group leg\n  upper: box 1 by 1 by 1\n  lower: box 1 by 1 by 1\n"
                              "end\ndone\n")
    assert [r.reason for r in later.lines[-3].rejections] == ["missing placement"]


def test_group_rules():
    unclosed = parse_plan(BASE + "group leg\n  a: box 1 by 1 by 1\n")
    assert ("out of place", "group leg") in [(p.reason, p.fragment) for p in unclosed.problems]
    nested = parse_plan(BASE + "group leg\ngroup foot\n")
    assert nested.lines[-1].rejections[0].reason == "out of place"
    assert rejections("feet: leg 40 by 40, on top of base", BASE + GROUP) == [
        ("wrong number of sizes", "leg 40 by 40")]
    assert rejections("feet: leg lying along length, on top of base", BASE + GROUP) == [
        ("unknown orientation", "lying along length")]


def test_parts_of_a_group_are_only_visible_inside_it():
    assert rejections(f"{BOX}, on top of upper", BASE + GROUP) == [("unknown part", "upper")]
    inside = parse_plan(BASE + "group leg\n  a: box 1 by 1 by 1\n"
                               "  b: box 1 by 1 by 1, on top of base\nend\ndone\n")
    assert [(r.reason, r.fragment) for r in inside.lines[-3].rejections] == [
        ("unknown part", "base")]


def test_names_are_unique_across_the_plan_including_groups():
    assert rejections("upper: box 1 by 1 by 1, on top of base", BASE + GROUP) == [
        ("duplicate name", "upper")]
    assert rejections("leg: box 1 by 1 by 1, on top of base", BASE + GROUP)[0] == (
        "duplicate name", "leg")


def test_a_rejected_line_is_not_counted_again_by_the_lines_that_point_at_it():
    plan = parse_plan(BASE + "lid: blob 3, on top of base\nknob: sphere 10, on top of lid\ndone\n")
    assert not plan.lines[-3].accepted
    assert plan.lines[-2].accepted
    assert plan.lines[-2].notes == ['"lid" was defined by a rejected line']
    assert not plan.accepted


def test_a_rejected_name_can_be_written_again():
    plan = parse_plan(BASE + "lid: blob 3, on top of base\nlid: box 3 by 3 by 3, on top of base\n"
                             "done\n")
    assert [line.accepted for line in plan.lines[-3:]] == [False, True, True]


# --- `rest` ---------------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    "t: box 1 by 1 by rest, under wall, down to ground",
    "t: box 1 by 1 by rest, on ground, top at height 900",
    "t: box 1 by 1 by rest, on top of base, top 20 below wall's top",
    "t: box rest by 1 by 1, right of base, right 5 inside wall's right",
    "t: box 1 by rest by 1, behind base, flush with base's back",
    "t: box rest by 1 by 1, between base and wall",
    "t: box rest by 1 by 1, between posts",
    "t: cylinder 5 by rest, between base and wall",
    "t: bar tube 20 by 2 by rest, between base and wall",
    "t: box rest by rest by 1, inside pipe",
    "t: box rest by rest by 5, on top of legs, across legs",
    "t: box 5 by 5 by rest, left of posts, across posts, flush with posts's top",
    "t: box rest by 1 by 1, spans from base's top to wall's left",
    "t: prism 6 by 10 by rest, spans from base's top-front edge to wall's top-back-left corner",
    # A lying part: the written height runs sideways, so it is what a sideways gap fills.
    "t: cylinder 5 by rest lying along length, right of base, right 0 inside wall's left",
    "t: box rest by 1 by 1 lying along length, on ground, top at height 40",
    "t: box 1 by 1 by rest lying along depth, behind base, flush with base's back",
    "t: cone 9 by 0 by rest pointing left, left of base, flush with base's left",
    "t: wedge rest by 9 by 9 pointing left, right of base, right 0 inside wall's left",
])
def test_rest_is_accepted_where_the_line_fixes_both_ends(body):
    assert rejections(body) == []


@pytest.mark.parametrize("body", [
    "t: box 1 by 1 by rest, on top of base, offset 5 to the left",
    "t: box 1 by 1 by rest, left of base, flush with base's left",
    "t: cylinder 10 by rest, through pipe",
    "t: box 1 by 1 by rest lying along length, on ground, top at height 40",
    "t: cone 9 by 0 by rest pointing left, on ground, top at height 40",
    "t: box rest by 1 by rest, spans from base's top to wall's left",
    "t: box rest by rest by 1, on top of base",
])
def test_rest_is_rejected_where_nothing_defines_it(body):
    assert [reason for reason, _ in rejections(body)] == ["rest not defined"]


# --- notes: accepted, but worth a look ------------------------------------------------------

def test_notes_do_not_reject():
    plan = parse_plan("seat: box 420 by 420 by 30, on ground, top at height 450\n"
                      "leg: box 40 by 40 by 300, under seat, down to ground\ndone\n")
    assert plan.accepted
    assert "use `above ground`" in plan.lines[0].notes[0]
    assert "is written `rest`" in plan.lines[1].notes[0]


# --- the tolerances, and what is deliberately not tolerated ---------------------------------

@pytest.mark.parametrize("body", [
    "T: Box 10 BY 10 by 10, On Top Of Base",                 # capitals
    "t:   box  10 by 10   by 10 ,   on top of   base",        # extra spaces
    "t: box 10 x 10 x 10, on top of base",                    # x
    "t: box 10x10x10, on top of base",
    "t: box 10 × 10 × 10, on top of base",                    # the multiplication sign
    "t: box 10mm by 10 mm by 10, on top of base, gap 5mm",    # a trailing mm
    "t: box 10 by 10 by 10, on top of base, flush with base’s front",   # curly apostrophe
    "t: box 10 by 10 by 10, on top of bases",                 # plural of a part name
    "t: box 10 by 10 by 10, on top of base, mirrored left right",       # a space for a hyphen
    "t: box 10 by 10 by 10, on top of base, at the front left corner of base",
    "t: box rest by 1 by 1, spans from base's top front edge to wall's top",
    "t: box 10 by 10 by 10 standing, on top of base",         # the default, written out
])
def test_tolerated_surface_variation(body):
    assert rejections(body) == []


def test_plural_and_singular_names_and_possessives():
    for body in (f"{BOX}, on top of leg",
                 f"{BOX}, on top of base, flush with legs's front",
                 f"{BOX}, on top of base, flush with legs' front",
                 f"{BOX}, on top of base, flush with leg's front"):
        plan = parse_plan(BASE + body + "\ndone\n")
        assert plan.accepted, body
    noted = parse_plan(BASE + f"{BOX}, on top of leg\ndone\n").lines[-2]
    assert noted.placement.parts == ["legs"]
    assert noted.notes == ['read "leg" as the part "legs"']


def test_plural_is_not_guessed_when_an_exact_name_exists():
    setup = BASE + "leg: box 1 by 1 by 1, on top of base\n"
    line = parse_plan(setup + f"{BOX}, on top of leg\ndone\n").lines[-2]
    assert line.placement.parts == ["leg"]        # an exact name always wins


@pytest.mark.parametrize("body", [
    "t: box 10 by 10 by 10, on top of the base",       # articles are not tolerated
    "t: box 10 by 10 by 10, on base",                  # "on" is not "on top of"
    "t: box 10 by 10 by 10; on top of base",           # the separator is a comma
    "t: box 10 by 10 by 10 cm, on top of base",        # only mm
    "t: box ten by 10 by 10, on top of base",          # number words
    "t - box 10 by 10 by 10, on top of base",          # the name ends with a colon
    "1. t: box 10 by 10 by 10, on top of base",        # list numbering
    "t: box 10, 10, 10, on top of base",               # commas between sizes
    "t: box 10 by 10 by 10, on top of base, gap 5,",   # a trailing comma
    "done.",
])
def test_not_tolerated(body):
    assert rejections(body) != []
