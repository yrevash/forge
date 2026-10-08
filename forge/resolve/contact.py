"""Do two bodies overlap, touch, or stand apart? By arithmetic where that is exact.

Arithmetic decides a pair when
  * the two frames are further apart than TOL: they are apart, whatever the shapes; or
  * both bodies are plain boxes or plain cylinders, square to the axes, with no features.

Every other pair (a cone, a dome, a wedge, a sphere, a tube, a prism, a bar, a tapered
box, a part with features, a part turned by an odd angle, a spanning part) returns
None here, and the resolver asks the CAD kernel (judge.py).

"Touch" means the two solids share at least one point but no volume: a face, a line
(a cylinder standing beside a wall) or a single point all count, as they do for the
kernel's distance test in forge/assembly_geometry.py.
"""

from __future__ import annotations

import math

from forge.resolve import space as sp
from forge.resolve.bodies import Body
from forge.resolve.space import TOL, Box

APART, TOUCH, OVERLAP = "apart", "touch", "overlap"


def _axis_of(body: Body) -> int:
    """Which world axis a square cylinder's own axis runs along."""
    direction = sp.apply(body.matrix, (0.0, 0.0, 1.0))
    return max(range(3), key=lambda k: abs(direction[k]))


def _verdict(clear: float) -> str:
    """`clear` > 0: the solids stop short of each other by that much; < 0: they run in."""
    if clear > TOL:
        return APART
    return TOUCH if clear >= -TOL else OVERLAP


def _box_box(a: Box, b: Box) -> str:
    # Boxes overlap only if they run into each other along all three axes.
    return _verdict(-min(a.depth_into(b)))


def _cylinder_box(cylinder: Body, box: Box) -> str:
    axis = _axis_of(cylinder)
    frame = cylinder.frame
    radius = cylinder.sizes["diameter"] / 2
    along = min(frame.high[axis], box.high[axis]) - max(frame.low[axis], box.low[axis])
    # Seen along the cylinder's axis it is a disc and the box a rectangle: how far is
    # the disc's middle from the rectangle?
    away = 0.0
    for k in range(3):
        if k != axis:
            middle = frame.mid[k]
            away += max(box.low[k] - middle, 0.0, middle - box.high[k]) ** 2
    return _verdict(max(-along, math.sqrt(away) - radius))


def _cylinder_cylinder(a: Body, b: Body) -> str:
    axis_a, axis_b = _axis_of(a), _axis_of(b)
    fa, fb = a.frame, b.frame
    ra, rb = a.sizes["diameter"] / 2, b.sizes["diameter"] / 2
    if axis_a == axis_b:
        along = min(fa.high[axis_a], fb.high[axis_a]) - max(fa.low[axis_a], fb.low[axis_a])
        apart = math.sqrt(sum((fa.mid[k] - fb.mid[k]) ** 2 for k in range(3) if k != axis_a))
        return _verdict(max(-along, apart - ra - rb))
    # Crossing at a right angle. `third` is the axis square to both.
    third = 3 - axis_a - axis_b
    # Nearest that b's length comes to a's axis line (measured along b's axis), and the same
    # for a. Then each cylinder has only a chord left to reach the other along `third`.
    miss_a = max(fb.low[axis_b] - fa.mid[axis_b], 0.0, fa.mid[axis_b] - fb.high[axis_b])
    miss_b = max(fa.low[axis_a] - fb.mid[axis_a], 0.0, fb.mid[axis_a] - fa.high[axis_a])
    if miss_a > ra + TOL or miss_b > rb + TOL:
        return APART
    chord_a = math.sqrt(max(0.0, ra * ra - miss_a * miss_a))
    chord_b = math.sqrt(max(0.0, rb * rb - miss_b * miss_b))
    clear = abs(fa.mid[third] - fb.mid[third]) - chord_a - chord_b
    if miss_a > ra - TOL or miss_b > rb - TOL:
        # One only grazes the other's end: at most a touch, never shared volume.
        return TOUCH if clear <= TOL else APART
    return _verdict(clear)


def relation(a: Body, b: Body) -> str | None:
    """APART, TOUCH or OVERLAP; None when only the kernel can say."""
    if min(a.frame.depth_into(b.frame)) < -TOL:
        return APART                    # the frames do not even meet
    kinds = (a.kind, b.kind)
    if "other" in kinds:
        return None
    if kinds == ("box", "box"):
        return _box_box(a.frame, b.frame)
    if kinds == ("cylinder", "cylinder"):
        return _cylinder_cylinder(a, b)
    cylinder, box = (a, b) if a.kind == "cylinder" else (b, a)
    return _cylinder_box(cylinder, box.frame)


def on_ground(body: Body) -> bool:
    """A body whose frame reaches height 0 touches the ground (a ball does so at a point)."""
    return abs(body.frame.low[2]) <= TOL


def joined_groups(names: list[str], touching: set[frozenset[str]]) -> list[set[str]]:
    """The separate bodies the structure falls into, given which pairs touch."""
    neighbours: dict[str, set[str]] = {name: set() for name in names}
    for pair in touching:
        a, b = tuple(pair)
        if a in neighbours and b in neighbours:
            neighbours[a].add(b)
            neighbours[b].add(a)
    groups, seen = [], set()
    for name in names:
        if name in seen:
            continue
        group, waiting = set(), [name]
        while waiting:
            current = waiting.pop()
            if current not in group:
                group.add(current)
                waiting += neighbours[current] - group
        seen |= group
        groups.append(group)
    return groups
