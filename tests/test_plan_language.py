"""Every form in the tables of the plan language (Draft 2) parses, and echoes back unchanged."""

import pytest

from forge.plan import echo_line, parse_plan
from forge.plan import grammar as g

# A plan every test line can stand in: a ground part, a set of two, a tube, a hollow box, a group.
SETUP = """\
base: box 400 by 300 by 20, on ground
posts: box 40 by 40 by 500, on top of base, at the left corners of base
wall: box 20 by 300 by 200, right of base
pipe: tube 60 by 50 by 200, on top of base
tray: box 200 by 150 by 40, above ground, bottom at height 300
on tray: hollowed out wall 5, open at top
group pair
  one: box 10 by 10 by 10
  two: box 10 by 10 by 10, on top of one
end
"""


def read(line: str, setup: str = SETUP):
    """Parse `line` after the setup plan and return its parsed Line."""
    plan = parse_plan(setup + line + "\ndone\n")
    setup_lines = len(setup.splitlines())
    for earlier in plan.lines[:setup_lines]:
        assert earlier.accepted, ("the setup itself must parse", earlier.text, earlier.rejections)
    return plan.lines[setup_lines]


# --- section 4: shapes ----------------------------------------------------------------------

SHAPE_LINES = [
    ("box 100 by 50 by 20", "box", 3),
    ("cylinder 40 by 100", "cylinder", 2),
    ("tube 40 by 30 by 100", "tube", 3),
    ("cone 40 by 20 by 100", "cone", 3),
    ("cone 40 by 0 by 100", "cone", 3),
    ("sphere 40", "sphere", 1),
    ("dome 60 by 20", "dome", 2),
    ("prism 6 by 30 by 10", "prism", 3),
    ("wedge 100 by 50 by 20", "wedge", 3),
    ("tapered box 100 by 80 by 60 by 40 by 50", "tapered box", 5),
    ("tapered box 100 by 80 by 0 by 0 by 50", "tapered box", 5),
    ("bar L 40 by 40 by 4 by 600", "bar", 4),
    ("bar T 40 by 40 by 4 by 600", "bar", 4),
    ("bar U 40 by 40 by 4 by 600", "bar", 4),
    ("bar I 40 by 40 by 4 by 600", "bar", 4),
    ("bar tube 40 by 3 by 600", "bar", 3),
]


@pytest.mark.parametrize(("text", "shape", "count"), SHAPE_LINES)
def test_every_shape_parses(text, shape, count):
    line = read(f"thing: {text}, on top of base")
    assert line.accepted, line.rejections
    assert line.shape == shape
    assert len(line.sizes) == count
    assert all(size.source == "number" for size in line.sizes)


def test_sizes_are_named_in_the_order_of_the_table():
    for shape, slots in g.SHAPES.items():
        numbers = " by ".join(str(10 * (i + 1)) for i in range(len(slots)))
        line = read(f"thing: {shape} {numbers}, on top of base")
        assert line.accepted, (shape, line.rejections)
        assert tuple(size.slot for size in line.sizes) == slots
    bar = read("thing: bar tube 20 by 2 by 300, on top of base")
    assert tuple(size.slot for size in bar.sizes) == ("outer diameter", "wall", "length")


@pytest.mark.parametrize("orientation", g.ORIENTATIONS)
@pytest.mark.parametrize("shape", ["box 40 by 40 by 300", "cylinder 40 by 300",
                                   "tube 40 by 30 by 300", "prism 6 by 40 by 300",
                                   "bar U 40 by 20 by 3 by 300"])
def test_every_lying_shape_in_every_orientation(shape, orientation):
    line = read(f"rail: {shape} {orientation}, on top of base")
    assert line.accepted, line.rejections
    assert line.orientation == orientation


@pytest.mark.parametrize("pointing", g.POINTINGS)
@pytest.mark.parametrize("shape", ["cone 40 by 0 by 60", "dome 40 by 15", "wedge 40 by 30 by 20",
                                   "tapered box 40 by 30 by 20 by 10 by 50"])
def test_every_pointing_shape_in_every_direction(shape, pointing):
    line = read(f"tip: {shape} pointing {pointing}, on top of base")
    assert line.accepted, line.rejections
    assert line.pointing == pointing


def test_defaults_are_standing_and_pointing_up():
    line = read("rail: box 40 by 40 by 300, on top of base")
    assert (line.orientation, line.pointing, line.flat) == ("standing", "up", None)


@pytest.mark.parametrize(("pointing", "flat"), [
    ("left", "bottom"), ("left", "top"), ("left", "front"), ("left", "back"),
    ("up", "left"), ("down", "front"), ("front", "right"), ("back", "top"),
])
def test_wedge_flat_face(pointing, flat):
    line = read(f"blade: wedge 60 by 10 by 80 pointing {pointing} flat {flat}, on top of base")
    assert line.accepted, line.rejections
    assert (line.pointing, line.flat) == (pointing, flat)


def test_wedge_default_flat_faces_are_beside_the_pointing_direction():
    for pointing, flat in g.WEDGE_DEFAULT_FLAT.items():
        tip = g.DIRECTION_OF_FACE[g.FACE_POINTED_AT[pointing]]
        assert g.DIRECTION_OF_FACE[flat] != tip


# --- section 4: where a size comes from -----------------------------------------------------

def test_size_number():
    size = read("t: box 12.5 by 50 by 20, on top of base").sizes[0]
    assert (size.source, size.value) == ("number", 12.5)


@pytest.mark.parametrize("dimension", g.DIMENSIONS)
def test_size_same_as(dimension):
    line = read(f"t: box same as pipe's {dimension} by 50 by 20, on top of base")
    assert line.accepted, line.rejections
    size = line.sizes[0]
    assert (size.source, size.part, size.dimension) == ("same as", "pipe", dimension)


@pytest.mark.parametrize(("text", "share"), [
    ("half of base's length", "half"), ("third of base's length", "third"),
    ("quarter of base's length", "quarter"), ("double base's length", "double"),
])
def test_size_share(text, share):
    line = read(f"t: box {text} by 50 by 20, on top of base")
    assert line.accepted, line.rejections
    size = line.sizes[0]
    assert (size.source, size.share, size.part, size.dimension) == ("share", share, "base", "length")


def test_size_default():
    line = read("t: box default by 50 by default, on top of base")
    assert line.accepted, line.rejections
    assert [s.source for s in line.sizes] == ["default", "number", "default"]


# --- section 5: placement and its options ---------------------------------------------------

PLACEMENTS = [
    ("on ground", "on ground", []),
    ("above ground, top at height 500", "above ground", []),
    ("above ground, bottom at height 500", "above ground", []),
    ("on top of base", "on top of", ["base"]),
    ("under tray", "under", ["tray"]),
    ("in front of base", "in front of", ["base"]),
    ("behind base", "behind", ["base"]),
    ("left of base", "left of", ["base"]),
    ("right of base", "right of", ["base"]),
    ("inside pipe", "inside", ["pipe"]),
    ("inside tray", "inside", ["tray"]),
    ("through pipe", "through", ["pipe"]),
    ("between base and tray", "between", ["base", "tray"]),
    ("between posts", "between", ["posts"]),
]


@pytest.mark.parametrize(("text", "kind", "parts"), PLACEMENTS)
def test_every_placement_parses(text, kind, parts):
    line = read(f"t: box 10 by 10 by 10, {text}")
    assert line.accepted, line.rejections
    assert (line.placement.kind, line.placement.parts) == (kind, parts)


def test_around_places_a_tube_round_a_part():
    line = read("hoop: tube 80 by 60 by 10, around pipe, bottom 20 above pipe's bottom")
    assert line.accepted, line.rejections
    assert (line.placement.kind, line.placement.parts) == ("around", ["pipe"])


@pytest.mark.parametrize(("start", "end"), [
    ("top", "bottom"), ("top-front edge", "bottom-back edge"),
    ("top-front-left corner", "bottom"), ("back-right edge", "top-back-right corner"),
])
def test_spans_between_two_places(start, end):
    line = read(f"stay: box rest by 10 by 10, spans from base's {start} to tray's {end}")
    assert line.accepted, line.rejections
    assert line.placement.kind == "spans"
    assert line.placement.parts == ["base", "tray"]
    assert line.placement.anchors == [start, end]


def test_place_names_are_put_in_the_fixed_order():
    line = read("stay: cylinder 8 by rest, spans from base's left-top-front corner to "
                "tray's back bottom edge")
    assert line.accepted, line.rejections
    assert line.placement.anchors == ["top-front-left corner", "bottom-back edge"]


def test_between_copes_with_a_name_that_contains_and():
    setup = SETUP + "nut and bolt: box 10 by 10 by 10, on top of base\n"
    line = read("t: box 10 by 10 by rest, between nut and bolt and wall", setup)
    assert line.accepted, line.rejections
    assert line.placement.parts == ["nut and bolt", "wall"]


OPTIONS = [
    ("right of base, gap 10", "gap", 10, None),
    ("on top of base, sunk 5 into base", "sunk", 5, "base"),
    ("on top of posts, across posts", "across", None, "posts"),
]


@pytest.mark.parametrize(("text", "kind", "amount", "part"), OPTIONS)
def test_every_option_parses(text, kind, amount, part):
    line = read(f"t: box 10 by 10 by 10, {text}")
    assert line.accepted, line.rejections
    [option] = line.options
    assert (option.kind, option.part) == (kind, part)
    assert (option.amount.value if option.amount else None) == amount


# --- section 6: alignment -------------------------------------------------------------------

ALIGNMENTS = [
    ("flush with base's front", {"kind": "flush", "part": "base", "face": "front",
                                 "other_face": "front"}),
    ("top flush with tray's bottom", {"kind": "flush", "face": "top", "part": "tray",
                                      "other_face": "bottom"}),
    ("top 20 above base's top", {"kind": "face offset", "face": "top", "relation": "above",
                                 "part": "base", "other_face": "top"}),
    ("top 20 below posts's top", {"kind": "face offset", "relation": "below", "part": "posts"}),
    ("left 5 inside base's left", {"kind": "face offset", "relation": "inside", "face": "left"}),
    ("back 5 beyond base's back", {"kind": "face offset", "relation": "beyond",
                                   "other_face": "back"}),
    ("left 5 inside tray's inner left", {"kind": "face offset", "other_face": "inner left"}),
    ("flush with tray's inner back", {"kind": "flush", "face": "back",
                                      "other_face": "inner back"}),
    ("flush with pipe's inner bottom", {"kind": "flush", "face": "bottom",
                                        "other_face": "inner bottom"}),
    ("offset 40 to the front", {"kind": "offset", "face": "front"}),
    ("top at height 450", {"kind": "top at height"}),
    ("bottom at height 100", {"kind": "bottom at height"}),
    ("down to ground", {"kind": "down to ground"}),
    ("on the same axis as pipe", {"kind": "same axis", "part": "pipe"}),
]


@pytest.mark.parametrize(("text", "expected"), ALIGNMENTS)
def test_every_alignment_parses(text, expected):
    line = read(f"t: box 10 by 10 by 10, right of base, {text}")
    assert line.accepted, line.rejections
    [alignment] = line.alignments
    for attribute, value in expected.items():
        assert getattr(alignment, attribute) == value


def test_inset_from_a_flush_face_and_from_a_corner():
    flush = read("t: box 10 by 10 by 10, on top of base, flush with base's left, inset 30")
    assert flush.accepted, flush.rejections
    assert flush.alignments[1].amount.value == 30
    corner = read("t: box 10 by 10 by 10, on top of base, inset 30, at each corner of base")
    assert corner.accepted, corner.rejections


@pytest.mark.parametrize("face", g.FACES)
def test_flush_with_every_face(face):
    line = read(f"t: box 10 by 10 by 10, on top of base, flush with base's {face}")
    assert line.accepted, line.rejections
    assert line.alignments[0].face == face


def test_an_amount_may_be_a_size_of_an_earlier_part():
    line = read("t: box 10 by 10 by 10, right of base, gap half of base's depth, "
                "top same as pipe's height above base's top")
    assert line.accepted, line.rejections
    gap, above = line.options[0].amount, line.alignments[0].amount
    assert (gap.source, gap.share, gap.part, gap.dimension) == ("share", "half", "base", "depth")
    assert (above.source, above.part, above.dimension) == ("same as", "pipe", "height")


def test_several_alignments_on_one_line():
    line = read("t: box 10 by 10 by 10, on top of base, flush with base's front, inset 5, "
                "offset 3 to the left")
    assert line.accepted, line.rejections
    assert [a.kind for a in line.alignments] == ["flush", "inset", "offset"]


# --- section 7: repetition and sets ---------------------------------------------------------

REPETITIONS = [
    ("on top of base, at each corner of base", {"kind": "each corner", "part": "base"}, 4),
    ("on top of base, at the front corners of base", {"kind": "corners", "side": "front"}, 2),
    ("on top of base, at the back corners of base", {"kind": "corners", "side": "back"}, 2),
    ("on top of base, at the left corners of base", {"kind": "corners", "side": "left"}, 2),
    ("on top of base, at the right corners of base", {"kind": "corners", "side": "right"}, 2),
    ("on top of base, at the front-left corner of base", {"kind": "corner",
                                                          "side": "front-left"}, 1),
    ("on top of base, at the back-right corner of base", {"kind": "corner",
                                                          "side": "back-right"}, 1),
    ("on top of base, 3 evenly spaced along length", {"kind": "evenly spaced", "count": 3,
                                                      "direction": "length"}, 3),
    ("on top of base, 3 evenly spaced along depth", {"kind": "evenly spaced"}, 3),
    ("in front of base, 3 evenly spaced along height", {"direction": "height"}, 3),
    ("on top of base, 3 spread along length", {"kind": "spread", "count": 3}, 3),
    ("on top of base, grid 3 by 4", {"kind": "grid", "count": 3, "count2": 4}, 12),
    ("on top of base, 6 around pipe on circle 120", {"kind": "around", "count": 6,
                                                     "part": "pipe", "outward": False}, 6),
    ("on top of base, 6 around pipe on circle 120 facing outward", {"outward": True}, 6),
    ("left of pipe, 4 around pipe", {"kind": "around", "diameter": None, "outward": False}, 4),
    ("left of pipe, 4 around pipe facing outward", {"kind": "around", "outward": True}, 4),
    ("on top of base, mirrored left-right", {"kind": "mirrored", "direction": "left-right"}, 2),
    ("on top of base, mirrored front-back", {"kind": "mirrored", "direction": "front-back"}, 2),
]


@pytest.mark.parametrize(("text", "expected", "copies"), REPETITIONS)
def test_every_repetition_parses(text, expected, copies):
    line = read(f"t: box 10 by 10 by 10, {text}")
    assert line.accepted, line.rejections
    for attribute, value in expected.items():
        assert getattr(line.repetition, attribute) == value
    assert line.copies == copies


def test_a_placement_on_a_set_makes_one_part_per_copy():
    caps = read("caps: sphere 12, on top of posts")
    assert caps.accepted, caps.rejections
    assert caps.copies == 2
    doubled = read("pins: sphere 4, on top of posts, mirrored front-back")
    assert doubled.copies == 4


def test_across_makes_one_part_over_the_whole_set():
    line = read("beam: box 20 by rest by 20, on top of posts, across posts")
    assert line.accepted, line.rejections
    assert line.copies == 1


def test_between_two_sets_of_the_same_size_makes_one_part_per_pair():
    setup = SETUP + "stubs: box 40 by 40 by 100, on top of base, at the right corners of base\n"
    line = read("rails: box rest by 10 by 10, between posts and stubs", setup)
    assert line.accepted, line.rejections
    assert line.copies == 2
    one_to_many = read("ties: box rest by 10 by 10, between posts and wall", setup)
    assert one_to_many.accepted, one_to_many.rejections
    assert one_to_many.copies == 2


# --- section 8: groups ----------------------------------------------------------------------

def test_group_is_declared_then_placed_like_a_part():
    plan = parse_plan(SETUP + "pairs: pair, on top of base, mirrored left-right\ndone\n")
    assert plan.accepted
    kinds = [line.kind for line in plan.lines]
    start = kinds.index("group")
    assert kinds[start:start + 4] == ["group", "part", "part", "end"]
    assert plan.lines[start + 1].in_group == "pair"
    assert plan.lines[start + 1].placement is None       # the first part: shape and sizes only
    placed = plan.lines[-2]
    assert (placed.group, placed.shape, placed.sizes) == ("pair", None, [])


def test_a_group_is_moved_down_to_ground():
    line = read("pairs: pair, left of base, down to ground")
    assert line.accepted, line.rejections


# --- section 9: features --------------------------------------------------------------------

@pytest.mark.parametrize("feature", list(g.FEATURES))
def test_every_feature_parses_bare_and_with_all_its_slots(feature):
    step_kind, slots, _ = g.FEATURES[feature]
    bare = read(f"on base: {feature}")
    assert bare.accepted, bare.rejections
    assert (bare.target, bare.face, bare.feature.step_kind) == ("base", "top", step_kind)
    assert (bare.feature.slots, bare.feature.position) == ({}, "centred")
    written = ", ".join(f"{slot} {i + 2}" for i, slot in enumerate(slots))
    full = read(f"on base: {feature} {written}")
    assert full.accepted, full.rejections
    assert full.feature.slots == {slot: i + 2 for i, slot in enumerate(slots)}


def test_features_match_the_step_table():
    """Every STEPS feature and edge treatment has a name here, with the same slots.

    x and y are not slots in the plan language: they are the position on the face.
    """
    from forge.system1.steps import FEATURES, TREATMENTS

    ours = {step_kind: (slots, positioned) for step_kind, slots, positioned in g.FEATURES.values()}
    assert set(ours) == set(FEATURES) | set(TREATMENTS)
    for step_kind, steps_slots in {**FEATURES, **TREATMENTS}.items():
        slots, positioned = ours[step_kind]
        named = tuple(g.STEPS_SLOT_OF.get(s, s.replace(" ", "_")) for s in slots)
        assert named == tuple(s for s in steps_slots if s not in ("x", "y"))
        assert positioned == bool({"x", "y"} & set(steps_slots))


@pytest.mark.parametrize("face", [*g.FACES, "inner left", "inner bottom"])
def test_feature_on_any_face(face):
    line = read(f"on tray's {face}: hole diameter 4")
    assert line.accepted, line.rejections
    assert (line.target, line.face) == ("tray", face)


def test_feature_position_forms():
    centred = read("on base: boss diameter 20, height 5, centred")
    assert centred.accepted and centred.feature.position == "centred"
    edges = read("on base: blind hole diameter 8, depth 5, 30 from base's left, "
                 "half of base's depth from base's front")
    assert edges.accepted, edges.rejections
    assert [(e.amount.source, e.part, e.face) for e in edges.feature.edges] == [
        ("number", "base", "left"), ("share", "base", "front")]
    side = read("on base's front: slot length 30, width 6, 10 from base's bottom")
    assert side.accepted, side.rejections
    at = read("on base: hole diameter 6, at (20, -15.5)")
    assert at.accepted, at.rejections
    assert (at.feature.position, at.feature.at) == ("at", (20, -15.5))


def test_hollowed_out_makes_a_part_hollow():
    setup = SETUP + "tub: box 200 by 150 by 40, on top of base\n"
    assert not read("t: box 10 by 10 by 10, inside tub", setup).accepted
    hollowed = setup + "on tub: hollowed out wall 5, open at top\n"
    line = read("t: box 10 by 10 by 10, inside tub", hollowed)
    assert line.accepted, line.rejections
    wall = parse_plan(hollowed + "done\n").lines[-2]
    assert (wall.feature.slots, wall.feature.open_at) == ({"wall": 5}, "top")


# --- section 10: control --------------------------------------------------------------------

def test_undo_removes_the_last_accepted_line():
    plan = parse_plan(SETUP + "t: box 1 by 1 by 1, on top of base\nundo\n"
                              "u: box 1 by 1 by 1, on top of t\ndone\n")
    assert [line.kind for line in plan.lines[-4:]] == ["part", "undo", "part", "done"]
    assert plan.lines[-3].accepted
    assert plan.lines[-2].rejections[0].reason == "unknown part"


def test_undo_also_takes_back_a_feature():
    plan = parse_plan(SETUP + "tub: box 50 by 50 by 20, on top of base\n"
                              "on tub: hollowed out wall 2\nundo\n"
                              "t: box 1 by 1 by 1, inside tub\ndone\n")
    assert [r.reason for r in plan.lines[-2].rejections] == ["out of place"]


def test_undo_to_keeps_the_named_part_and_drops_what_came_after():
    plan = parse_plan(SETUP + "undo to posts\nlid: box 1 by 1 by 1, on top of posts\n"
                              "x: box 1 by 1 by 1, on top of pipe\ndone\n")
    undo, lid, x, _ = plan.lines[-4:]
    assert (undo.kind, undo.name, undo.accepted) == ("undo to", "posts", True)
    assert lid.accepted
    assert [r.fragment for r in x.rejections] == ["pipe"]


def test_done_alone_on_the_last_line():
    plan = parse_plan("base: box 10 by 10 by 10, on ground\ndone\n")
    assert plan.accepted
    assert plan.lines[-1].kind == "done"


# --- the echo -------------------------------------------------------------------------------

ALL_FORMS = (
    [f"t: {text}, on top of base" for text, _, _ in SHAPE_LINES]
    + [f"t: box 10 by 10 by 10, {text}" for text, _, _ in PLACEMENTS]
    + [f"t: box 10 by 10 by 10, {text}" for text, _, _, _ in OPTIONS]
    + [f"t: box 10 by 10 by 10, right of base, {text}" for text, _ in ALIGNMENTS]
    + [f"t: box 10 by 10 by 10, {text}" for text, _, _ in REPETITIONS]
    + [("t: box same as pipe's diameter by half of base's depth by double base's height, "
        "on top of base"),
       "t: box rest by default by 5 lying along depth, between base and tray",
       "t: wedge 60 by 10 by 80 pointing right flat back, on top of base",
       "t: cone 30 by 0 by 60 pointing down, under tray",
       "t: tube 80 by 60 by 10, around pipe, flush with pipe's top",
       "t: bar tube 20 by 2 by rest, spans from base's top-back-left corner to tray's bottom",
       ("t: box 10 by 10 by 10, right of base, gap same as pipe's diameter, inset 0, "
       "flush with base's front"),
       "t: pair, on top of base",
       "on base: counterbored hole hole diameter 5, diameter 9, depth 3, at (-10, 0)",
       "on base's front: pocket length 30, width 8, 20 from base's left, 5 from base's top",
       "on tray's inner left: hole diameter 4",
       "on pipe: hollowed out wall 2, open at bottom"]
)


@pytest.mark.parametrize("text", ALL_FORMS)
def test_echo_is_canonical(text):
    """A line written in the canonical wording echoes as itself."""
    line = read(text)
    assert line.accepted, line.rejections
    assert echo_line(line) == text


def test_echo_evens_out_surface_variation():
    line = read("T :  BOX 10mm x 10 mm × 10,  On Top Of base ,  flush with BASE’s front")
    assert line.accepted, line.rejections
    assert echo_line(line) == "t: box 10 by 10 by 10, on top of base, flush with base's front"


def test_echo_uses_the_short_flush_form_when_both_faces_have_the_same_name():
    line = read("t: box 10 by 10 by 10, on top of base, top flush with pipe's top, "
                "bottom flush with pipe's inner bottom")
    assert line.accepted, line.rejections
    assert echo_line(line) == ("t: box 10 by 10 by 10, on top of base, flush with pipe's top, "
                               "flush with pipe's inner bottom")


def test_echo_of_a_rejected_line_gives_the_reasons():
    line = read("t: blob 10, on top of base")
    assert echo_line(line) == 'not understood (unknown shape "blob")'
