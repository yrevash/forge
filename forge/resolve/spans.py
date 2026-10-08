"""`spans from X's PLACE to Y's PLACE`: the one tilted part of the language (section 5).

The size written `rest` runs from the first point to the second; the part's middle is
half way. PLAN_LANGUAGE does not say how the cross-section is turned about that line,
nor how the ends are cut. OUR CHOICES (README, "Document gaps"):

  * no roll: the first of the two cross-section sizes stays level (horizontal, square
    to the span); a span that runs exactly along length, depth or height is the same
    part as the one written lying or standing that way;
  * the ends are cut square, so a tilted end pokes a little into the part it meets.
    That overlap with its two end parts is allowed, like `sunk`.

A spanning part is always checked by the CAD kernel, never by arithmetic.
"""

from __future__ import annotations

from forge.plan.model import Line
from forge.resolve import shapes
from forge.resolve import space as sp
from forge.resolve.bodies import Body
from forge.resolve.scene import Scene
from forge.resolve.shapes import DoesNotFit
from forge.resolve.space import TOL, Mat, Vec

_UP: Vec = (0.0, 0.0, 1.0)


def span_matrix(run_axis: int, along: Vec) -> Mat:
    """The turn that lays the standing shape's `run_axis` along the unit vector `along`."""
    level = sp.cross(_UP, along)                    # horizontal and square to the span
    upright = sp.length(level) < 1e-9
    if run_axis == 2:                               # a height: x stays level
        level = (1.0, 0.0, 0.0) if upright else sp.normalised(level)
        return sp.from_columns(level, sp.cross(along, level), along)
    if run_axis == 0:                               # a box's length: its depth stays level
        level = (0.0, 1.0, 0.0) if upright else sp.normalised(level)
        return sp.from_columns(along, level, sp.cross(along, level))
    level = (-1.0, 0.0, 0.0) if upright else sp.normalised(level)   # a box's depth
    return sp.from_columns(sp.scale(level, -1.0), along, sp.cross(along, level))


def build(line: Line, scene: Scene, sizes: dict[str, float | None]) -> Body:
    first, second = line.placement.parts
    start = scene.frame(first).anchor(line.placement.anchors[0])
    end = scene.frame(second).anchor(line.placement.anchors[1])
    run = sp.sub(end, start)
    distance = sp.length(run)
    if distance <= TOL:
        raise DoesNotFit("the two ends of the span are the same point")
    slot = next(slot for slot, value in sizes.items() if value is None)
    sizes[slot] = distance
    matrix = span_matrix(shapes.rest_axis(line.shape, slot), sp.scale(run, 1.0 / distance))
    ends = [body.name for name in (first, second) for body in scene.bodies(name)]
    return Body(line.name, line.name, line.number, line.shape, line.profile, sizes, matrix,
                sp.scale(sp.add(start, end), 0.5), may_overlap=frozenset(ends))
