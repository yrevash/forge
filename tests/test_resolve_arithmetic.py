"""The resolver's arithmetic on its own: turns, shapes, pins, contact, defaults. No kernel.

Every number asserted here is worked out by hand in the comment next to it.
"""

import math

import pytest

from forge.resolve import contact, shapes
from forge.resolve import space as sp
from forge.resolve.bodies import Body, turn_matrix
from forge.resolve.comma_fix import fix_commas
from forge.resolve.defaults import fill_defaults, fill_feature_slots
from forge.resolve.pins import Pin, solve
from forge.resolve.shapes import DoesNotFit, describe
from forge.resolve.spans import span_matrix


def body(shape, sizes, at=(0.0, 0.0, 0.0), orientation="standing", name="b", **more):
    matrix = turn_matrix(shape, orientation, more.pop("pointing", "up"))
    return Body(name, name, 1, shape, None, {k: float(v) for k, v in sizes.items()}, matrix,
                tuple(float(v) for v in at), **more)


def box(x, y, z, at=(0, 0, 0), name="b"):
    return body("box", {"length": x, "depth": y, "height": z}, at, name=name)


def cylinder(d, h, at=(0, 0, 0), orientation="standing", name="c"):
    return body("cylinder", {"diameter": d, "height": h}, at, orientation, name=name)


# --- space ----------------------------------------------------------------------------------

def test_quarter_turns_are_exact():
    assert sp.rotation(1, 90) == ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))
    assert sp.apply(sp.rotation(1, 90), (0.0, 0.0, 1.0)) == (1.0, 0.0, 0.0)     # up -> right


@pytest.mark.parametrize("axis,degrees", [(0, 90), (1, -90), (2, 37), (2, 180), (0, 180)])
def test_axis_and_angle_gives_the_turn_back(axis, degrees):
    turn = sp.rotation(axis, degrees)
    found_axis, found_degrees = sp.axis_and_angle(turn)
    index = max(range(3), key=lambda k: abs(found_axis[k]))
    again = sp.rotation(index, found_degrees * found_axis[index])
    assert all(again[r][c] == pytest.approx(turn[r][c], abs=1e-9)
               for r in range(3) for c in range(3))


def test_anchor_names():
    frame = sp.Box((0.0, 0.0, 0.0), (10.0, 20.0, 30.0))
    assert frame.anchor("top") == (5.0, 10.0, 30.0)
    assert frame.anchor("top-front edge") == (5.0, 0.0, 30.0)
    assert frame.anchor("top-front-left corner") == (0.0, 0.0, 30.0)
    assert frame.anchor("bottom-back-right corner") == (10.0, 20.0, 0.0)


# --- shapes: frames and volumes by the textbook formulas ----------------------------------------

def test_volumes():
    assert describe("box", None, {"length": 2, "depth": 3, "height": 4}).volume == 24
    assert describe("cylinder", None, {"diameter": 10, "height": 2}).volume == \
        pytest.approx(math.pi * 25 * 2)
    assert describe("tube", None, {"outer diameter": 10, "inner diameter": 6, "height": 1}
                    ).volume == pytest.approx(math.pi * (25 - 9))
    # a cone to a point: a third of the cylinder
    assert describe("cone", None, {"bottom diameter": 10, "top diameter": 0, "height": 3}
                    ).volume == pytest.approx(math.pi * 25)
    assert describe("sphere", None, {"diameter": 6}).volume == pytest.approx(36 * math.pi)
    # a dome as high as its radius is half a ball
    assert describe("dome", None, {"diameter": 6, "height": 3}).volume == \
        pytest.approx(18 * math.pi)
    assert describe("wedge", None, {"length": 2, "depth": 3, "height": 4}).volume == 12
    # a tapered box to a point is a pyramid: base times height over three
    pyramid = {"bottom length": 6, "bottom depth": 4, "top length": 0, "top depth": 0, "height": 5}
    assert describe("tapered box", None, pyramid).volume == pytest.approx(6 * 4 * 5 / 3)


def test_prism_across_and_frame():
    hexagon = describe("prism", None, {"sides": 6, "across": 40, "height": 10})
    # flat to flat 40 (depth); corner to corner 40 / cos 30 = 46.188 (length)
    assert hexagon.dims == pytest.approx((40 / math.cos(math.radians(30)), 40, 10))
    assert hexagon.volume == pytest.approx(6 * 20 * 20 * math.tan(math.radians(30)) * 10)
    assert hexagon.axis_at == pytest.approx((0, 0, 0))
    triangle = describe("prism", None, {"sides": 3, "across": 30, "height": 10})
    # odd: across is flat side to opposite corner = apothem + corner radius = 10 + 20
    assert triangle.dims[1] == pytest.approx(30)
    assert triangle.axis_at == pytest.approx((0, -5, 0))     # the axis is not the frame's middle
    assert min(y for _, y in triangle.outline) == pytest.approx(-15)    # a flat side at the front


def test_impossible_sizes_do_not_fit():
    with pytest.raises(DoesNotFit):
        describe("dome", None, {"diameter": 60, "height": 40})       # higher than half a ball
    with pytest.raises(DoesNotFit):
        describe("tube", None, {"outer diameter": 50, "inner diameter": 60, "height": 10})
    with pytest.raises(DoesNotFit):
        describe("bar", "u", {"profile width": 10, "profile depth": 20, "thickness": 5,
                              "length": 100})                        # two legs of 5 fill 10


def test_bar_areas():
    sizes = {"profile width": 30, "profile depth": 20, "thickness": 3, "length": 10}
    assert describe("bar", "l", sizes).volume == pytest.approx(3 * (30 + 20 - 3) * 10)
    assert describe("bar", "t", sizes).volume == pytest.approx((30 * 3 + 3 * 17) * 10)
    assert describe("bar", "u", sizes).volume == pytest.approx((30 * 3 + 2 * 3 * 17) * 10)
    assert describe("bar", "i", sizes).volume == pytest.approx((2 * 30 * 3 + 3 * 14) * 10)


def test_lying_swaps_the_frame():
    lying = body("box", {"length": 10, "depth": 20, "height": 30}, orientation="lying along length")
    assert lying.frame.size == pytest.approx((30, 20, 10))      # written length now runs up
    along_depth = cylinder(10, 80, orientation="lying along depth")
    assert along_depth.frame.size == pytest.approx((10, 80, 10))
    assert along_depth.axis[1] == pytest.approx((0, 1, 0))


def test_frame_of_a_turned_box_is_exact():
    turned = Body("b", "b", 1, "box", None, {"length": 10.0, "depth": 10.0, "height": 5.0},
                  sp.rotation(2, 45), (0.0, 0.0, 0.0))
    assert turned.frame.size == pytest.approx((10 * math.sqrt(2), 10 * math.sqrt(2), 5))
    assert turned.kind == "other"           # not square to the axes: the kernel judges it


def test_wedge_table():
    """Section 4: `pointing left` keeps the right and bottom faces; sharp edge bottom-left."""
    left = describe("wedge", None, {"length": 80, "depth": 60, "height": 30}, "left")
    assert left.flat_faces == {(1.0, 0.0, 0.0), (0.0, 0.0, -1.0)}
    assert (-40.0, -30.0, -15.0) in left.corners and (-40.0, -30.0, 15.0) not in left.corners
    up = describe("wedge", None, {"length": 80, "depth": 60, "height": 30}, "up")
    assert up.flat_faces == {(0.0, 0.0, -1.0), (0.0, 1.0, 0.0)}      # bottom and back


def test_span_matrix_along_the_axes_is_the_plain_part():
    assert span_matrix(0, (1.0, 0.0, 0.0)) == sp.IDENTITY           # a box's length, left to right
    assert span_matrix(2, (0.0, 0.0, 1.0)) == sp.IDENTITY           # a height, straight up
    assert span_matrix(1, (0.0, 1.0, 0.0)) == sp.IDENTITY
    tilted = span_matrix(2, sp.normalised((1.0, 0.0, 1.0)))
    assert sp.determinant(tilted) == pytest.approx(1.0)
    assert sp.apply(tilted, (1.0, 0.0, 0.0))[2] == pytest.approx(0.0)   # first cross size level


# --- pins -----------------------------------------------------------------------------------

def test_solve_known_size():
    assert solve([Pin(-1, 5.0)], 10.0, 0) == (5.0, 15.0)
    assert solve([Pin(1, 5.0)], 10.0, 0) == (-5.0, 5.0)
    assert solve([Pin(0, 5.0, strong=False)], 10.0, 0) == (0.0, 10.0)
    # a stated pin beats "centred"; of two stated pins the later one wins
    assert solve([Pin(0, 5.0, strong=False), Pin(-1, 0.0)], 10.0, 0) == (0.0, 10.0)
    assert solve([Pin(-1, 0.0), Pin(1, 50.0)], 10.0, 0) == (40.0, 50.0)


def test_solve_rest_needs_both_ends():
    assert solve([Pin(1, 420.0), Pin(-1, 0.0, hard=True)], None, 2) == (0.0, 420.0)
    with pytest.raises(DoesNotFit):
        solve([Pin(1, 420.0)], None, 2)
    with pytest.raises(DoesNotFit):
        solve([Pin(1, 0.0), Pin(-1, 10.0)], None, 2)                 # nothing left


def test_the_ground_cannot_be_overridden():
    with pytest.raises(DoesNotFit):
        solve([Pin(-1, 0.0, hard=True), Pin(1, 900.0)], 100.0, 2)    # on ground, top at 900
    assert solve([Pin(-1, 0.0, hard=True), Pin(1, 100.0)], 100.0, 2) == (0.0, 100.0)


# --- contact --------------------------------------------------------------------------------

def test_boxes():
    a = box(10, 10, 10)
    assert contact.relation(a, box(10, 10, 10, (10, 0, 0))) == contact.TOUCH        # a face
    assert contact.relation(a, box(10, 10, 10, (10, 10, 0))) == contact.TOUCH       # an edge
    assert contact.relation(a, box(10, 10, 10, (10, 10, 10))) == contact.TOUCH      # a corner
    assert contact.relation(a, box(10, 10, 10, (9, 0, 0))) == contact.OVERLAP
    assert contact.relation(a, box(10, 10, 10, (10.5, 0, 0))) == contact.APART


def test_cylinder_and_box():
    wall = box(10, 100, 100)                        # x from -5 to 5
    assert contact.relation(cylinder(20, 50, (15, 0, 0)), wall) == contact.TOUCH    # along a line
    assert contact.relation(cylinder(20, 50, (14, 0, 0)), wall) == contact.OVERLAP
    assert contact.relation(cylinder(20, 50, (16, 0, 0)), wall) == contact.APART
    # Near a corner of the box the frames meet but the round side does not reach.
    corner = box(10, 10, 10)                        # corner at (5, 5)
    near = cylinder(20, 10, (14, 14, 0))            # frame from 4 to 24: frames overlap
    assert math.hypot(9, 9) > 10                    # the corner is 12.7 from the axis
    assert contact.relation(near, corner) == contact.APART
    assert contact.relation(cylinder(20, 10, (0, 0, 10)), corner) == contact.TOUCH  # standing on it


def test_parallel_cylinders():
    a = cylinder(20, 50)
    assert contact.relation(a, cylinder(10, 50, (15, 0, 0), name="d")) == contact.TOUCH
    assert contact.relation(a, cylinder(10, 50, (14, 0, 0), name="d")) == contact.OVERLAP
    assert contact.relation(a, cylinder(10, 50, (12, 12, 0), name="d")) == contact.APART
    assert contact.relation(a, cylinder(20, 10, (0, 0, 30), name="d")) == contact.TOUCH


def test_crossing_cylinders():
    post = cylinder(20, 100)                                        # along height
    rail = cylinder(10, 100, (0, 15, 0), "lying along length", "r")  # along length, 15 behind
    assert contact.relation(post, rail) == contact.TOUCH           # 10 + 5 = 15
    assert contact.relation(post, cylinder(10, 100, (0, 14, 0), "lying along length", "r")) \
        == contact.OVERLAP
    assert contact.relation(post, cylinder(10, 100, (0, 16, 0), "lying along length", "r")) \
        == contact.APART
    # The rail lies on top of the post: its round side touches the flat end at a line.
    assert contact.relation(post, cylinder(10, 100, (0, 0, 55), "lying along length", "r")) \
        == contact.TOUCH


def test_other_shapes_go_to_the_kernel():
    ball = body("sphere", {"diameter": 10}, (0, 0, 10), name="s")
    assert contact.relation(box(10, 10, 10), ball) is None
    far = body("sphere", {"diameter": 10}, (0, 0, 40), name="s")
    assert contact.relation(box(10, 10, 10), far) == contact.APART  # frames apart: arithmetic


def test_joined_groups():
    groups = contact.joined_groups(["a", "b", "c", "d"], {frozenset("ab"), frozenset("bc")})
    assert sorted(sorted(g) for g in groups) == [["a", "b", "c"], ["d"]]


# --- defaults -------------------------------------------------------------------------------

def test_default_rule_table():
    sizes = {"length": 400.0, "height": 20.0}
    said = fill_defaults("box", sizes, ["depth"], 100.0)
    assert sizes["depth"] == 400.0 and "same as its length" in said[0]
    sizes = {"diameter": 30.0}
    fill_defaults("cylinder", sizes, ["height"], 100.0)
    assert sizes["height"] == 30.0
    sizes = {}
    said = fill_defaults("sphere", sizes, ["diameter"], 60.0)        # nothing to go on
    assert sizes["diameter"] == 60.0 and "nothing on the line" in said[0]
    sizes = {"profile width": 30.0, "profile depth": 20.0}
    fill_defaults("bar", sizes, ["thickness", "length"], 100.0)
    assert sizes == {"profile width": 30.0, "profile depth": 20.0, "thickness": 2.0,
                     "length": 300.0}


def test_feature_slot_defaults():
    slots = {"hole diameter": 6.0}
    said = fill_feature_slots("counterbored hole", ("hole diameter", "diameter", "depth"), slots,
                              100.0, 50.0, 20.0)
    assert slots == {"hole diameter": 6.0, "diameter": 12.0, "depth": 5.0} and len(said) == 2


# --- the comma repair -------------------------------------------------------------------------

def test_fix_commas():
    assert fix_commas("seat: cylinder 320, 30, on ground, top at height 650") == \
        "seat: cylinder 320 by 30, on ground, top at height 650"
    assert fix_commas("rotor: cylinder 1200, 30 lying along depth, in front of fin") == \
        "rotor: cylinder 1200 by 30 lying along depth, in front of fin"
    untouched = "on deck: blind hole diameter 8, depth 5"
    assert fix_commas(untouched) == untouched
    assert shapes.rest_axis("cylinder", "height") == 2
