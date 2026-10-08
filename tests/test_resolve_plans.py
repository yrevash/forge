"""The resolver on whole plans made of boxes and cylinders: positions, replies, undo.

No CAD kernel is used anywhere in this file: the judge given to the resolver fails the
test if it is asked anything. That is itself a check of "which checks are arithmetic".
"""

from pathlib import Path

import pytest

from forge.resolve import resolve_text
from forge.resolve import space as sp

ROOT = Path(__file__).resolve().parent.parent
# The four plans exactly as PLAN_LANGUAGE Draft 2 printed them. Draft 3 replaced the
# document's examples with plans that build (tests/test_plan_examples.py pins those);
# the Draft 2 texts are kept here because the findings below are about them.
PLAN_EXAMPLES = ROOT / "tests" / "plan_examples_draft2"
OUR_EXAMPLES = ROOT / "forge" / "resolve" / "examples"


class NoKernel:
    """A judge that must never be asked: these plans are decided by arithmetic alone."""

    calls = 0

    def look(self, bodies):
        raise AssertionError(f"the kernel was asked about {[b.name for b in bodies]}")

    def close(self):
        pass


def resolve(text):
    return resolve_text(text, NoKernel())


def frames(resolution):
    return {body.name: (body.frame.low, body.frame.high) for body in resolution.bodies}


def replies(resolution):
    return [reply.reply for reply in resolution.replies]


BASE = "base: box 400 by 300 by 20, on ground\n"


# --- the chair of section 11 ----------------------------------------------------------------

def test_chair_positions():
    done = resolve((PLAN_EXAMPLES / "chair.txt").read_text())
    assert done.complete and len(done.bodies) == 14
    at = frames(done)
    assert at["seat"] == ((-210, -210, 420), (210, 210, 450))
    assert at["front legs 1"] == ((-210, -210, 0), (-170, -170, 420))      # rest = 420
    assert at["front legs 2"] == ((170, -210, 0), (210, -170, 420))
    # `behind seat`: the posts stand behind the seat's back face, not under its corners.
    assert at["back posts 1"] == ((-210, 210, 0), (-170, 250, 900))
    assert at["back posts 2"] == ((170, 210, 0), (210, 250, 900))          # the mirror image
    assert at["side aprons 1"] == ((-200, -170, 360), (-180, 210, 420))    # rest depth = 380
    assert at["front apron"] == ((-170, -200, 360), (170, -180, 420))
    assert at["top rail"] == ((-170, 220, 800), (170, 240, 880))           # 20 below the tops
    assert at["lower rail"] == ((-170, 220, 560), (170, 240, 600))         # 110 above the seat
    # three slats of 40 in 340: four equal gaps of (340 - 120) / 4 = 55
    assert [at[f"slats {i}"][0][0] for i in (1, 2, 3)] == [-115, -20, 75]
    whole = sp.around([body.frame for body in done.bodies])
    assert whole.size == (420, 460, 900)        # 40 deeper than the demo's 420: the posts


def test_chair_against_the_hand_built_demo():
    """Where the plan and forge/system1/demo_chair.py agree, they agree exactly."""
    from forge.system1 import demo_chair

    demo = {name: part.val().BoundingBox() for _, _, parts, _ in demo_chair.steps()
            for name, part in parts}
    at = frames(resolve((PLAN_EXAMPLES / "chair.txt").read_text()))

    def same(ours, theirs):
        box = demo[theirs]
        assert at[ours][0] == pytest.approx((box.xmin, box.ymin, box.zmin), abs=1e-6)
        assert at[ours][1] == pytest.approx((box.xmax, box.ymax, box.zmax), abs=1e-6)

    same("seat", "seat")
    same("front legs 1", "front_leg_left")
    same("front legs 2", "front_leg_right")
    same("front apron", "apron_front")
    # The rails are at the demo's heights, but 40 further back (behind the seat).
    for ours, theirs in (("top rail", "top_rail"), ("lower rail", "lower_rail")):
        box = demo[theirs]
        assert (at[ours][0][2], at[ours][1][2]) == pytest.approx((box.zmin, box.zmax))
        assert at[ours][0][1] - box.ymin == pytest.approx(40)


# --- every reply of section 10 --------------------------------------------------------------

def test_overlap_is_rejected_and_changes_nothing():
    done = resolve(BASE + "a: box 50 by 50 by 50, on top of base\n"
                          "b: box 50 by 50 by 50, on top of base, offset 20 to the left\n"
                          "c: box 10 by 10 by 10, on top of a\ndone\n")
    assert replies(done) == ["built", "built", "rejected: overlaps a", "built", "built"]
    assert [body.name for body in done.bodies] == ["base", "a", "c"]
    assert not done.complete                    # a line was rejected


def test_touches_nothing():
    done = resolve(BASE + "a: box 50 by 50 by 50, on top of base, offset 5 to the top\ndone\n")
    assert replies(done)[1] == "rejected: touches nothing"


def test_gap_and_above_ground_may_float_until_done():
    done = resolve(BASE + "a: box 50 by 50 by 50, on top of base, gap 5\ndone\n")
    assert replies(done) == ["built", "built", "rejected: not joined to the ground"]
    done = resolve(BASE + "a: box 50 by 50 by 50, on top of base, gap 5\n"
                          "link: box 10 by 10 by rest, between base and a\ndone\n")
    assert done.complete
    assert frames(done)["link"] == ((-5, -5, 20), (5, 5, 25))


def test_two_separate_bodies_are_not_one_structure():
    done = resolve(BASE + "far: box 10 by 10 by 10, on ground, offset 500 to the right\ndone\n")
    assert replies(done)[-1] == "rejected: not joined to the ground"
    assert "2 separate bodies" in done.replies[-1].notes[0]


def test_does_not_fit():
    cases = [
        "a: box 50 by 50 by 50, on ground, top at height 80",           # on ground, top at 50
        "a: box 50 by 50 by 50, left of base",                          # centred: below ground
        "a: box 50 by 50 by 500, under base",                           # below the ground
        "a: box 50 by 50 by 50, on top of base, 9 evenly spaced along length",   # 450 in 400
        "a: box 300 by 50 by 50, on top of base, mirrored left-right",  # its image is itself
        "a: box 50 by 50 by 50, in front of base, at the front corners of base",
    ]
    for line in cases:
        done = resolve(BASE + line + "\ndone\n")
        assert replies(done)[1] == "rejected: does not fit", line
        assert done.replies[1].notes, line          # it says why


def test_unknown_part_after_a_rejected_line():
    done = resolve(BASE + "a: box 50 by 50 by 50, on top of base, offset 5 to the top\n"
                          "b: box 10 by 10 by 10, on top of a\ndone\n")
    assert replies(done)[1:3] == ["rejected: touches nothing", "rejected: unknown part a"]


def test_not_understood():
    done = resolve(BASE + "a: blob 50, on top of base\ndone\n")
    assert replies(done)[1] == "rejected: not understood"
    assert "unknown shape" in done.replies[1].echo


def test_echo_is_the_fixed_wording():
    done = resolve("Base : box 400 x 300 x 20mm, on ground\ndone\n")
    assert done.replies[0].echo == "base: box 400 by 300 by 20, on ground"


def test_undo_and_undo_to():
    done = resolve(BASE + "a: box 50 by 50 by 50, on top of base\n"
                          "b: box 10 by 10 by 10, on top of a\nundo\n"
                          "c: box 20 by 20 by 20, on top of a\n"
                          "d: box 5 by 5 by 5, on top of c\nundo to a\ndone\n")
    assert all(reply.built for reply in done.replies)
    assert [body.name for body in done.bodies] == ["base", "a"]
    assert done.touching == {frozenset({"base", "a"})}
    done = resolve(BASE + "undo to nowhere\ndone\n")
    assert not done.replies[1].built


# --- alignment ------------------------------------------------------------------------------

def test_alignments():
    done = resolve(
        BASE
        + "a: box 40 by 40 by 10, on top of base, flush with base's left, inset 15\n"
        + "b: box 40 by 40 by 10, on top of base, right 10 inside base's right\n"
        + "c: box 40 by 40 by 10, on top of base, back 5 beyond base's back\n"
        + "d: box 40 by 40 by 10, right of base, top 5 above base's top\n"
        + "e: box 40 by 40 by 10, on top of base, offset 100 to the front, "
        + "offset 30 to the left\n"
        + "f: box 20 by 20 by 10, on top of base, flush with base's left, "
        + "flush with base's right, flush with base's front\n"
        + "g: cylinder 20 by 10, on top of a, on the same axis as b\n")
    at = frames(done)
    assert at["a"][0] == (-185, -20, 20)        # 15 in from the left face
    assert at["b"][1][0] == 190                 # its right face 10 inside base's right
    assert at["c"][1][1] == 155                 # its back face 5 beyond base's back
    assert at["d"] == ((200, -20, 15), (240, 20, 25))
    assert at["e"][0] == (-50, -120, 20)
    assert at["f"][1][0] == 200                 # two alignments along length: the later wins
    # g takes its height from `on top of a` and its place from b's axis: it lands on b.
    assert at["g"] == ((160, -10, 30), (180, 10, 40)) and replies(done)[-1] == "built"


def test_lying_and_rest_between():
    done = resolve("left post: box 40 by 40 by 300, on ground, offset 200 to the left\n"
                   "right post: box 40 by 40 by 300, on ground, offset 200 to the right\n"
                   "rail: cylinder 20 by rest lying along length, "
                   "between left post and right post\n"
                   "done\n")
    assert done.complete
    assert frames(done)["rail"] == ((-180, -10, 140), (180, 10, 160))
    assert done.replies[2].notes == ["rest height = 360"]


# --- repetition and sets --------------------------------------------------------------------

def test_corners_spacing_grid():
    done = resolve(
        BASE
        + "legs: cylinder 30 by 200, on top of base, inset 20, at each corner of base\n"
        + "deck: box rest by rest by 20, on top of legs, across legs\n"
        + "caps: cylinder 10 by 5, on top of legs\n"
        + "stud: box 10 by 10 by 10, on top of deck, at the back-left corner of deck\n"
        + "rungs: box 100 by 10 by 5, under deck, 3 spread along depth\n"
        + "tiles: box 30 by 30 by 3, on top of base, grid 3 by 2\n")
    at = frames(done)
    assert at["legs 1"][0] == (-180, -130, 20)              # tucked in, then 20 inward
    assert at["deck"] == ((-180, -130, 220), (180, 130, 240))   # the frame round all four legs
    assert at["stud"] == ((-180, 120, 240), (-170, 130, 250))
    assert [at[f"rungs {i}"][0][1] for i in (1, 2, 3)] == [-130, -5, 120]   # ends flush
    # grid 3 by 2 on a 400 by 300 face: gaps (400 - 90) / 4 = 77.5 and (300 - 60) / 3 = 80
    assert at["tiles 1"][0] == (-122.5, -70, 20) and at["tiles 6"][0] == (92.5, 40, 20)
    # `caps` on a set: one per leg, but each is covered by the deck -> overlap
    assert replies(done)[3] == "rejected: overlaps deck"


def test_a_set_as_a_target():
    done = resolve(
        BASE
        + "pegs: cylinder 10 by 20, on top of base, at the front corners of base\n"
        + "caps: box 12 by 12 by 4, on top of pegs\n"
        + "rail: box rest by 4 by 4, between caps\n"
        + "bar: box 20 by 20 by 5, on top of base, top flush with caps's top\n")
    at = frames(done)
    assert at["caps 1"] == ((-201, -151, 40), (-189, -139, 44))     # each on its own peg
    assert at["caps 2"][0][0] == 189
    assert at["rail"] == ((-189, -147, 40), (189, -143, 44))        # between the two copies
    # A set in an alignment is the frame round all its copies: the top of the caps is 44.
    # The alignment is written after the placement, so it wins, and the bar would float.
    assert replies(done)[4] == "rejected: touches nothing"


def test_around_a_circle():
    done = resolve(BASE + "column: cylinder 60 by 100, on top of base\n"
                          "bolts: cylinder 8 by 10, on top of base, 4 around column on circle 150\n"
                          "lugs: box 20 by 10 by 10, on top of base, "
                          "2 around column on circle 250 facing outward\n")
    at = frames(done)
    middles = [tuple(round((at[f"bolts {i}"][0][k] + at[f"bolts {i}"][1][k]) / 2, 6)
                     for k in (0, 1)) for i in (1, 2, 3, 4)]
    assert middles == [(0, -75), (75, 0), (0, 75), (-75, 0)]        # the first at the front
    assert at["lugs 2"] == ((-10, 120, 20), (10, 130, 30))          # turned half way round


def test_mirrored_group_moves_to_the_ground():
    text = (OUR_EXAMPLES / "robot_dog_fixed.txt").read_text()
    done = resolve(text)
    assert done.complete and len(done.bodies) == 18
    at = frames(done)
    assert at["front legs 1 foot"] == ((-125, -170, 0), (-75, -100, 20))    # on the ground
    assert at["front legs 1 upper"] == ((-120, -160, 140), (-80, -110, 300))    # touches the body
    assert at["front legs 2 upper"] == ((80, -160, 140), (120, -110, 300))      # the mirror image
    whole = sp.around([body.frame for body in done.bodies])
    assert whole.size == (250, 480, 580)


def test_robot_dog_as_written_in_the_document_is_not_joined():
    """Document finding: the group's frame touches the body, the legs themselves do not.

    The foot (50 wide) is wider than the upper leg (40), so `left of body` puts the
    foot's side against the body's side plane, 240 lower down, and leaves the upper leg
    5 short of the body. forge/resolve/examples/robot_dog_fixed.txt adds
    `offset 5 to the right`.
    """
    done = resolve((PLAN_EXAMPLES / "robot_dog.txt").read_text())
    assert replies(done)[-1] == "rejected: not joined to the ground"
    assert all(reply.built for reply in done.replies[:-1])
    assert frames(done)["front legs 1 upper"][1][0] == -85      # the body's side is at -80


# --- a part sunk into another part of the same group (fault of 6 Oct 2026) -----------------------

GROUP_WITH_A_SUNK_PART = """lid: box 600 by 400 by 40, on ground
bu11: cylinder 50 by 10 lying along length, on top of lid
group kb33
  fm15: box 30 by 25 by 25.5
  outer tab: cylinder 12 by 15 lying along depth, right of fm15, sunk 15 into fm15
  upper rib: cylinder 20 by 12, in front of fm15
end
{placed}
done
"""


def _undeclared_overlaps(bodies):
    """Every pair that shares volume without one of the two saying it may (arithmetic)."""
    from forge.resolve import contact
    return [(a.name, b.name) for i, a in enumerate(bodies) for b in bodies[i + 1:]
            if contact.relation(a, b) == contact.OVERLAP
            and b.name not in a.may_overlap and a.name not in b.may_overlap]


def test_sunk_inside_a_group_keeps_its_permission_when_the_group_is_placed():
    """Plan 528d79d7072341f1: `outer tab` is sunk into `fm15` inside a group. Placed as
    `ch20`, the parts are "ch20 outer tab" and "ch20 fm15", and the permission to overlap
    must name the placed part, not the group's own "fm15"."""
    solved = resolve_text(GROUP_WITH_A_SUNK_PART.format(placed="ch20: kb33, left of bu11"))
    assert solved.complete
    by_name = {body.name: body for body in solved.bodies}
    assert by_name["ch20 outer tab"].may_overlap == frozenset({"ch20 fm15"})
    assert _undeclared_overlaps(solved.bodies) == []


def test_every_copy_of_a_group_is_sunk_into_its_own_part():
    solved = resolve_text(GROUP_WITH_A_SUNK_PART.format(
        placed="ch20: kb33, on top of lid, at the front corners of lid"))
    assert solved.complete, [reply.reply for reply in solved.replies]
    by_name = {body.name: body for body in solved.bodies}
    assert by_name["ch20 1 outer tab"].may_overlap == frozenset({"ch20 1 fm15"})
    assert by_name["ch20 2 outer tab"].may_overlap == frozenset({"ch20 2 fm15"})
    assert _undeclared_overlaps(solved.bodies) == []


def test_a_group_permission_does_not_reach_an_outside_part_of_the_same_name():
    """Before the fix a stale "fm15" would have let a group part run into an unrelated
    part that is also called fm15."""
    solved = resolve_text(GROUP_WITH_A_SUNK_PART.format(placed="ch20: kb33, left of bu11"))
    tab = next(body for body in solved.bodies if body.name == "ch20 outer tab")
    assert "fm15" not in tab.may_overlap
