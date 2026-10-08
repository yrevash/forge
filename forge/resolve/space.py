"""Points, directions, quarter turns and frames: the arithmetic everything else stands on.

Directions:
    length runs left to right   -> x (axis 0), left is low,  right is high
    depth  runs front to back   -> y (axis 1), front is low, back is high
    height runs bottom to top   -> z (axis 2), bottom is low, top is high

Everything is plain floats and tuples; there is no solver and no CAD kernel here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Vec = tuple[float, float, float]
Mat = tuple[Vec, Vec, Vec]          # three rows

TOL = 1e-6                          # mm; two numbers closer than this are "the same"

AXIS_OF_DIRECTION = {"length": 0, "depth": 1, "height": 2}
# face -> (axis, side): side -1 is the low end of that axis, +1 the high end.
FACE = {"left": (0, -1), "right": (0, 1), "front": (1, -1), "back": (1, 1),
        "bottom": (2, -1), "top": (2, 1)}
IDENTITY: Mat = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def unit(axis: int, sign: float = 1.0) -> Vec:
    return tuple(sign if k == axis else 0.0 for k in range(3))


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec, k: float) -> Vec:
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def normalised(a: Vec) -> Vec:
    return scale(a, 1.0 / length(a))


# --- 3 by 3 matrices: how a standing shape is turned into place -----------------------------

def apply(m: Mat, v: Vec) -> Vec:
    return (dot(m[0], v), dot(m[1], v), dot(m[2], v))


def multiply(a: Mat, b: Mat) -> Mat:
    """First b, then a."""
    columns = [apply(a, (b[0][k], b[1][k], b[2][k])) for k in range(3)]
    return tuple(tuple(columns[k][row] for k in range(3)) for row in range(3))


def transpose(m: Mat) -> Mat:
    return tuple(tuple(m[k][row] for k in range(3)) for row in range(3))


def from_columns(x: Vec, y: Vec, z: Vec) -> Mat:
    """The matrix that sends the x, y and z directions to the three given vectors."""
    return transpose((x, y, z))


def determinant(m: Mat) -> float:
    return dot(m[0], cross(m[1], m[2]))


def _clean(value: float) -> float:
    """Quarter turns must give exact 0, 1 and -1, not 6e-17."""
    nearest = round(value)
    return float(nearest) if abs(value - nearest) < 1e-12 else value


def rotation(axis: int, degrees: float) -> Mat:
    """A turn about the x, y or z axis (right-hand rule)."""
    c, s = _clean(math.cos(math.radians(degrees))), _clean(math.sin(math.radians(degrees)))
    if axis == 0:
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if axis == 1:
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def mirror(axis: int) -> Mat:
    """A mirror image across the plane square to that axis."""
    return tuple(unit(k, -1.0 if k == axis else 1.0) for k in range(3))


def is_square(m: Mat) -> bool:
    """True when the matrix only swaps and flips axes: the part is square to the axes."""
    return all(abs(abs(value) - round(abs(value))) < 1e-9 for row in m for value in row)


def axis_and_angle(m: Mat) -> tuple[Vec, float]:
    """A pure rotation as (axis, degrees), for writing `.rotate(...)` in a program."""
    trace = m[0][0] + m[1][1] + m[2][2]
    angle = math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))
    if angle < 1e-12:
        return (0.0, 0.0, 1.0), 0.0
    if abs(angle - math.pi) < 1e-9:
        # A half turn: the axis is the direction the matrix leaves alone.
        for k in range(3):
            column = [(m[row][k] + (1.0 if row == k else 0.0)) for row in range(3)]
            if length(tuple(column)) > 1e-6:
                return tuple(_clean(v) for v in normalised(tuple(column))), 180.0
    axis = (m[2][1] - m[1][2], m[0][2] - m[2][0], m[1][0] - m[0][1])
    return tuple(_clean(v) for v in normalised(axis)), _clean(math.degrees(angle))


# --- frames ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Box:
    """An upright box given by its low and high corner. A part's frame is one of these."""

    low: Vec
    high: Vec

    @property
    def size(self) -> Vec:
        return sub(self.high, self.low)

    @property
    def mid(self) -> Vec:
        return scale(add(self.low, self.high), 0.5)

    def face(self, name: str) -> float:
        """Where a face sits along its own axis: `face("top")` is the height of the top."""
        axis, side = FACE[name]
        return self.high[axis] if side > 0 else self.low[axis]

    def anchor(self, place: str) -> Vec:
        """The point a place names: "top" (the face's middle), "top-front edge" (the edge's
        middle), "top-front-left corner"."""
        words = place.replace(" edge", "").replace(" corner", "").split("-")
        point = list(self.mid)
        for word in words:
            axis, _ = FACE[word]
            point[axis] = self.face(word)
        return tuple(point)

    def moved(self, by: Vec) -> Box:
        return Box(add(self.low, by), add(self.high, by))

    def depth_into(self, other: Box) -> Vec:
        """Per axis, how far the two boxes run into each other (negative: a gap)."""
        return tuple(min(self.high[k], other.high[k]) - max(self.low[k], other.low[k])
                     for k in range(3))


def around(boxes: list[Box]) -> Box:
    """The smallest box that holds all of them."""
    return Box(tuple(min(b.low[k] for b in boxes) for k in range(3)),
               tuple(max(b.high[k] for b in boxes) for k in range(3)))


def box_of_points(points: list[Vec]) -> Box:
    return Box(tuple(min(p[k] for p in points) for k in range(3)),
               tuple(max(p[k] for p in points) for k in range(3)))
