"""The resolver against the CAD kernel: the reference program, features, spans, pictures.

Everything that touches the kernel goes through forge.sandbox (one warm sandbox for the
whole file). `verify` builds the reference program and compares the kernel's
measurements with the resolver's arithmetic, so a passing verdict here means frames,
volumes, overlaps and touching pairs all agree.
"""

import math
from pathlib import Path

import pytest

from forge.plan import parse_plan
from forge.resolve import build, property_test, resolve_plan
from forge.resolve import space as sp
from forge.resolve.judge import KernelJudge
from forge.resolve.program import write_program
from forge.resolve.verify import verify
from forge.sandbox import Sandbox

ROOT = Path(__file__).resolve().parent.parent
# The four plans exactly as PLAN_LANGUAGE Draft 2 printed them. Draft 3 replaced the
# document's examples with plans that build (tests/test_plan_examples.py pins those);
# the Draft 2 texts are kept here because the findings below are about them.
PLAN_EXAMPLES = ROOT / "tests" / "plan_examples_draft2"
OUR_EXAMPLES = ROOT / "forge" / "resolve" / "examples"
BASE = "base: box 400 by 300 by 20, on ground\n"


@pytest.fixture(scope="module")
def sandbox():
    with Sandbox() as box:
        yield box


def resolve(text, sandbox):
    return resolve_plan(parse_plan(text), KernelJudge(sandbox))


def checked(text, sandbox):
    """Resolve, require a complete plan, build it and require every check to pass."""
    done = resolve(text, sandbox)
    rejected = [(r.text, r.reply, r.notes) for r in done.replies if not r.built]
    assert done.complete, rejected
    verdict = verify(done.bodies, done.touching, sandbox)
    assert verdict.passed, verdict.problems
    return done, verdict


def frames(resolution):
    """name -> the six numbers of its frame: low x, y, z then high x, y, z."""
    return {body.name: pytest.approx(body.frame.low + body.frame.high, abs=1e-6)
            for body in resolution.bodies}


# --- whole plans ----------------------------------------------------------------------------

@pytest.mark.parametrize("path,parts", [
    (PLAN_EXAMPLES / "chair.txt", 14),
    (PLAN_EXAMPLES / "rocket.txt", 10),
    (OUR_EXAMPLES / "robot_dog_fixed.txt", 18),
    (OUR_EXAMPLES / "every_form_buildable.txt", 81),
])
def test_example_plans_build_and_pass_every_check(path, parts, sandbox):
    done, _ = checked(path.read_text(), sandbox)
    assert len(done.bodies) == parts


def test_every_form_sampler_of_the_document_is_not_a_buildable_object(sandbox):
    """Document finding: section 12's sampler collides with itself (it says nobody built it).

    The first collision: `tray` is `above ground` at 300 over the middle of the base, and
    `column` rises from the deck through it. Pinned so a change in the document or in
    the resolver shows up here.
    """
    done = resolve((PLAN_EXAMPLES / "every_form.txt").read_text(), sandbox)
    by_name = {reply.text.split(":")[0]: reply.reply for reply in done.replies}
    assert by_name["column"] == "rejected: overlaps tray"
    assert by_name["plinth"] == "rejected: does not fit"        # centred on a 20 high face
    assert not done.complete


def test_rocket_numbers(sandbox):
    done, verdict = checked((PLAN_EXAMPLES / "rocket.txt").read_text(), sandbox)
    whole = sp.around([body.frame for body in done.bodies])
    assert whole.size == pytest.approx((870, 870, 7100))        # fins: 185 + 250 each side
    fin = next(body for body in done.bodies if body.name == "fins 2")
    assert fin.frame.low == pytest.approx((-10, -435, 0))       # turned to the front
    assert verdict.reply["measure"]["structure"]["n_groups"] == 1


# --- the kernel as the judge ----------------------------------------------------------------

def test_round_contact_is_a_touch_and_a_miss_is_not(sandbox):
    done = resolve(BASE + "ball: sphere 40, on top of base\n"
                          "peg: cylinder 10 by 30, on top of ball\n"
                          "roof: cone 100 by 0 by 50, on top of base, offset 150 to the right\n"
                          "hat: box 20 by 20 by 5, on top of roof\n"          # on the point
                          "side: box 20 by 20 by 20, right of roof, flush with roof's top\n"
                          "done\n", sandbox)
    replies = [reply.reply for reply in done.replies]
    assert replies[:5] == ["built"] * 5
    # The cone's frame is 100 wide all the way up, the cone is not: nothing to touch.
    assert replies[5] == "rejected: touches nothing"
    assert done.replies[1].by_kernel and not done.replies[0].by_kernel


def test_sunk_is_the_only_allowed_overlap(sandbox):
    text = BASE + "post: cylinder 40 by 100, on top of base\n"
    plain = resolve(text + "knob: sphere 60, on top of post, offset 10 to the bottom\ndone\n",
                    sandbox)
    assert plain.replies[2].reply == "rejected: overlaps post"
    checked(text + "knob: sphere 60, on top of post, sunk 10 into post\ndone\n", sandbox)


def test_around_and_through(sandbox):
    done, _ = checked(
        BASE + "column: tube 60 by 40 by 150, on top of base\n"
               "collar: tube 80 by 60 by 10, around column, bottom 20 above column's bottom\n"
               "on column's left: hole diameter 10\n"
               "pin: cylinder 10 by 60 lying along length, through column\n"
               "done\n", sandbox)
    at = frames(done)
    assert at["collar"] == (-40, -40, 40, 40, 40, 50)
    assert at["pin"] == (-30, -5, 90, 30, 5, 100)     # along the cross hole
    # A tube's own hole: the rod runs the way the tube does, centred along it unless told.
    done, _ = checked(
        BASE + "column: tube 60 by 40 by 150, on top of base\n"
               "rod: cylinder 40 by 200, through column, bottom flush with column's bottom\n"
               "done\n", sandbox)
    assert frames(done)["rod"] == (-20, -20, 20, 20, 20, 220)
    loose = resolve(BASE + "column: tube 60 by 40 by 150, on top of base\n"
                           "wire: cylinder 10 by 100 lying along length, through column\n"
                           "done\n", sandbox)
    assert loose.replies[2].reply == "rejected: does not fit"   # no hole runs that way


def test_spanning_part(sandbox):
    done, _ = checked(
        BASE + "post: box 20 by 20 by 300, on top of base, flush with base's left\n"
               "stay: box rest by 10 by 10, spans from base's top to post's right\n"
               "done\n", sandbox)
    stay = next(body for body in done.bodies if body.name == "stay")
    # from (0, 0, 20) to (-180, 0, 170): sqrt(180^2 + 150^2)
    assert stay.sizes["length"] == pytest.approx(math.hypot(180, 150))
    assert stay.may_overlap == {"base", "post"} and stay.kind == "other"


# --- features -------------------------------------------------------------------------------

def test_hole_volume_and_position(sandbox):
    done, verdict = checked(BASE + "on base: hole diameter 10, 30 from base's left\n"
                                   "on base's front: blind hole diameter 6, depth 15\n"
                                   "done\n", sandbox)
    part = verdict.reply["measure"]["structure"]["parts"][0]
    assert part["volume"] == pytest.approx(400 * 300 * 20 - math.pi * 25 * 20
                                           - math.pi * 9 * 15)
    cut = done.bodies[0].cuts[0]
    # kept in the standing shape's own coordinates: the middle of the top face, and the spot
    assert cut.origin == (0.0, 0.0, 10.0) and cut.spots == [(-170.0, 0.0)]
    assert done.bodies[0].volume is None        # the resolver states no volume once cut


def test_hollowed_out_and_inside(sandbox):
    done, verdict = checked(
        "tray: box 200 by 150 by 40, on ground\n"
        "on tray: hollowed out wall 5, open at top\n"
        "divider: box 5 by rest by 20, inside tray, left 40 inside tray's inner left\n"
        "on tray's inner left: hole diameter 4\n"
        "done\n", sandbox)
    assert frames(done)["divider"] == (-55, -70, 5, -50, 70, 25)
    tray = verdict.reply["measure"]["structure"]["parts"][0]
    assert tray["volume"] == pytest.approx(200 * 150 * 40 - 190 * 140 * 35 - math.pi * 4 * 5)


def test_a_bowl_is_hollowed_by_the_kernel_and_its_sloping_walls_are_not_used(sandbox):
    text = ("bowl: cone 100 by 200 by 80, on ground\n"
            "on bowl: hollowed out wall 5, open at top\n")
    checked(text + "ball: sphere 30, inside bowl\ndone\n", sandbox)     # the floor is exact
    refused = resolve(text + "bar: box rest by 10 by 10, inside bowl\ndone\n", sandbox)
    assert refused.replies[2].reply == "rejected: does not fit"
    assert "not resolved exactly" in refused.replies[2].notes[0]


def test_a_boss_makes_the_frame_bigger_and_can_collide(sandbox):
    done = resolve(BASE + "lid: box 100 by 100 by 10, above ground, bottom at height 23\n"
                          "on base: boss diameter 20, height 3\n"
                          "on base: boss diameter 10, height 9, 40 from base's left\n"
                          "on base: pad length 30, width 30, height 9, at (20, 0)\n", sandbox)
    assert [reply.reply for reply in done.replies[2:]] == ["built", "built",
                                                           "rejected: overlaps lid"]
    assert done.bodies[0].frame.high[2] == 29       # the tallest boss
    assert frozenset({"base", "lid"}) in done.touching      # the first boss holds the lid


def test_feature_that_does_not_fit(sandbox):
    done = resolve(BASE + "on base: hole diameter 500\n"
                          "on base: blind hole diameter 10, depth 30\n"
                          "on base: pair of holes diameter 6\n"
                          "ball: sphere 50, on top of base\n"
                          "on ball: boss diameter 5, height 5\n", sandbox)
    assert [reply.reply for reply in done.replies[1:4]] == ["rejected: does not fit"] * 3
    assert done.replies[5].reply == "rejected: does not fit"    # a ball has no flat top


# --- the program, the command, the property test --------------------------------------------

def test_program_text_is_readable_and_named(sandbox):
    done = resolve((PLAN_EXAMPLES / "chair.txt").read_text(), sandbox)
    program = write_program(done.bodies, {"seat": "seat: box 420 by 420 by 30, above ground"})
    assert "seat_length = 420.0" in program and "front_legs_height = 420.0" in program
    assert "# seat: box 420 by 420 by 30, above ground" in program
    assert "result.add(front_legs_1, name='front legs 1')" in program
    assert program.count("result.add(") == 14


def test_build_command_writes_step_picture_and_animation(tmp_path, capsys):
    row = build.build_one(PLAN_EXAMPLES / "chair.txt", tmp_path)
    assert row["reader_accepts"] and row["resolver_completes"] and row["builds_and_passes"]
    for ending in ("step", "py", "png", "gif"):
        assert (tmp_path / f"chair.{ending}").stat().st_size > 1000, ending
    assert any("rest height = 420" in line for line in row["print"])


def test_kernel_agrees_with_the_resolver_on_random_plans(sandbox):
    """A small slice of `python -m forge.resolve.property_test` (the full run is 3000 plans)."""
    rows = [property_test.check_plan(seed, sandbox) for seed in range(40)]
    assert [d for row in rows for d in row["disagreements"]] == []
    counts = {}
    for row in rows:
        for key, value in row["counts"].items():
            counts[key] = counts.get(key, 0) + value
    # The slice must actually contain what it claims to test.
    assert counts["built, decided by arithmetic"] > 50
    assert counts["overlaps, decided by arithmetic"] > 5
