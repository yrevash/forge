"""Sketch shapes: rectangle, circle, regular polygon, slot. Runs inside FreeCAD.

How a FreeCAD sketch works, in three lines
  - GEOMETRY is a numbered list of lines, circles, arcs and points.
  - CONSTRAINTS are rules between them: "these two ends coincide", "this line is
    horizontal", "this distance is 40 mm".
  - The SOLVER moves the geometry until every rule holds.

A shape here is drawn in two stages, as a person does it:
  1. `draw` adds the geometry at a rough default size, with the rules that make
     it that SHAPE (a rectangle stays a rectangle) but no sizes;
  2. each `constrain` call adds one DIMENSION: a size, an angle or a position.
When every dimension is given the sketch has no freedom left ("0 degrees of
freedom" in the snapshot).

Why `constrain` redraws the shape first. FreeCAD's solver searches from where
the geometry is now, and an equation system often has more than one answer: a
slot stretched from 20 mm to 140 mm in one jump can come back with an arc
flipped inside out. (Measured: with no redraw, about a third of random slots
came back wrong, and the solver still reported success.) So we move the
geometry to the answer ourselves, add the rule, and let the solver confirm it.
A person does the same thing by dragging a shape roughly to size before
typing the dimension.

A shape is remembered as a plain dict (it is part of the snapshot):
    {"shape": "slot", "first": 0, "x": 0.0, "y": 0.0, "length": 20.0, "width": 6.0,
     "angle": 0.0, "fixed": ["width"]}
`first` is the index of the shape's first geometry; `fixed` lists the dimensions given so far.
"""

from __future__ import annotations

import math

import FreeCAD
import Part
import Sketcher

ORIGIN = (-1, 1)            # FreeCAD's name for the sketch origin: geometry -1, point 1
START, END, CENTRE = 1, 2, 3  # point numbers on a line or arc; a circle has only CENTRE
UP = FreeCAD.Vector(0, 0, 1)
SOLVER_TOLERANCE = 1e-7     # mm; how far the solver may leave geometry from where we drew it

DEFAULTS = {
    "rectangle": {"x": 0.0, "y": 0.0, "length": 20.0, "width": 10.0},
    "circle": {"x": 0.0, "y": 0.0, "diameter": 10.0},
    "polygon": {"x": 0.0, "y": 0.0, "across_flats": 20.0, "angle": 90.0},
    "slot": {"x": 0.0, "y": 0.0, "length": 20.0, "width": 6.0, "angle": 0.0},
}


class ShapeError(ValueError):
    """The shape cannot take this dimension (for example a slot wider than it is long)."""


def _v(x: float, y: float) -> FreeCAD.Vector:
    return FreeCAD.Vector(x, y, 0)


# --- exact geometry of each shape, from its numbers ------------------------------------------
# Each function returns [(geometry, is_construction), ...]. Construction geometry is a
# helper: it carries constraints but is not part of the outline that gets padded.

def _rectangle(s: dict) -> list:
    x0, x1 = s["x"] - s["length"] / 2, s["x"] + s["length"] / 2
    y0, y1 = s["y"] - s["width"] / 2, s["y"] + s["width"] / 2
    corners = [_v(x0, y0), _v(x1, y0), _v(x1, y1), _v(x0, y1)]      # anticlockwise
    sides = [(Part.LineSegment(corners[i], corners[(i + 1) % 4]), False) for i in range(4)]
    return [*sides, (Part.Point(_v(s["x"], s["y"])), True)]         # 4: the centre point


def _circle(s: dict) -> list:
    return [(Part.Circle(_v(s["x"], s["y"]), UP, s["diameter"] / 2), False)]


def corner_radius(s: dict) -> float:
    """Radius of the circle through a polygon's corners."""
    return s["across_flats"] / 2 / math.cos(math.pi / s["sides"])


def _polygon(s: dict) -> list:
    n, radius = s["sides"], corner_radius(s)
    # Side 0 runs in direction `angle`; going anticlockwise, the middle of that side lies
    # at (angle - 90) degrees from the centre and its first corner half a step before that.
    first = math.radians(s["angle"] - 90) - math.pi / n
    corners = [_v(s["x"] + radius * math.cos(first + 2 * math.pi * k / n),
                  s["y"] + radius * math.sin(first + 2 * math.pi * k / n)) for k in range(n)]
    sides = [(Part.LineSegment(corners[k], corners[(k + 1) % n]), False) for k in range(n)]
    return [*sides, (Part.Circle(_v(s["x"], s["y"]), UP, radius), True)]   # n: corner circle


def _slot(s: dict) -> list:
    theta = math.radians(s["angle"])
    cos, sin = math.cos(theta), math.sin(theta)
    radius = s["width"] / 2
    half = s["length"] / 2 - radius         # from the middle to the centre of a round end

    def at(along: float, across: float) -> FreeCAD.Vector:
        return _v(s["x"] + along * cos - across * sin, s["y"] + along * sin + across * cos)

    quarter = math.pi / 2
    return [
        (Part.ArcOfCircle(Part.Circle(at(half, 0), UP, radius), theta - quarter,
                          theta + quarter), False),                     # 0: the far round end
        (Part.ArcOfCircle(Part.Circle(at(-half, 0), UP, radius), theta + quarter,
                          theta + 3 * quarter), False),                 # 1: the near round end
        (Part.LineSegment(at(half, radius), at(-half, radius)), False),   # 2: one straight side
        (Part.LineSegment(at(-half, -radius), at(half, -radius)), False),  # 3: the other
        (Part.LineSegment(at(-half - radius, 0), at(half + radius, 0)), True),  # 4: tip to tip
        (Part.Point(at(0, 0)), True),                                   # 5: the middle
    ]


GEOMETRY = {"rectangle": _rectangle, "circle": _circle, "polygon": _polygon, "slot": _slot}


# --- the rules that make it that shape (no sizes) --------------------------------------------

def _shape_rules(s: dict) -> list:
    f, c = s["first"], Sketcher.Constraint
    if s["shape"] == "rectangle":
        rules = [c("Coincident", f + i, END, f + (i + 1) % 4, START) for i in range(4)]
        rules += [c("Horizontal", f), c("Horizontal", f + 2), c("Vertical", f + 1),
                  c("Vertical", f + 3)]
        # The centre point sits midway between two opposite corners.
        return [*rules, c("Symmetric", f, START, f + 2, START, f + 4, START)]
    if s["shape"] == "polygon":
        n = s["sides"]
        rules = [c("Coincident", f + k, END, f + (k + 1) % n, START) for k in range(n)]
        rules += [c("Equal", f, f + k) for k in range(1, n)]
        return rules + [c("PointOnObject", f + k, START, f + n) for k in range(n)]
    if s["shape"] == "slot":
        far, near, side_a, side_b, tips, middle = range(f, f + 6)
        return [
            c("Tangent", far, END, side_a, START), c("Tangent", side_a, END, near, START),
            c("Tangent", near, END, side_b, START), c("Tangent", side_b, END, far, START),
            c("Equal", far, near),
            # The tip-to-tip helper line: its ends lie on the round ends and it passes
            # through both of their centres, so its length is the slot's overall length.
            c("PointOnObject", tips, START, near), c("PointOnObject", tips, END, far),
            c("PointOnObject", far, CENTRE, tips), c("PointOnObject", near, CENTRE, tips),
            c("Symmetric", tips, START, tips, END, middle, START),
        ]
    return []       # a circle needs no rules to stay a circle


# --- one dimension ---------------------------------------------------------------------------

def _direction(line: int, degrees: float) -> Sketcher.Constraint:
    """Fix the direction of a line. Along an axis FreeCAD has a rule of its own."""
    turn = degrees % 180
    if turn == 0:
        return Sketcher.Constraint("Horizontal", line)
    if turn == 90:
        return Sketcher.Constraint("Vertical", line)
    return Sketcher.Constraint("Angle", line, math.radians(degrees))


def _centre_point(s: dict) -> tuple[int, int]:
    """(geometry index, point number) of the point that marks the shape's centre."""
    f = s["first"]
    return {"rectangle": (f + 4, START), "circle": (f, CENTRE),
            "polygon": (f + s.get("sides", 0), CENTRE), "slot": (f + 5, START)}[s["shape"]]


def _dimension_rule(s: dict, dimension: str, value: float) -> Sketcher.Constraint:
    f, kind, c = s["first"], s["shape"], Sketcher.Constraint
    if dimension in ("x", "y"):
        return c("DistanceX" if dimension == "x" else "DistanceY", *ORIGIN, *_centre_point(s),
                 value)
    if kind == "rectangle":
        # DistanceX and DistanceY are signed (end minus start), so the rectangle cannot flip.
        return (c("DistanceX", f, START, f, END, value) if dimension == "length"
                else c("DistanceY", f + 1, START, f + 1, END, value))
    if kind == "circle":
        return c("Diameter", f, value)
    if kind == "polygon":
        n = s["sides"]
        if dimension == "across_flats":     # from a corner of side 0 to the opposite side
            return c("Distance", f, START, f + n // 2, value)
        if dimension == "diameter":
            return c("Diameter", f + n, value)
        return _direction(f, value)
    if dimension == "length":
        return c("Distance", f + 4, value)              # length of the tip-to-tip line
    if dimension == "width":
        return c("Distance", f + 2, START, f + 3, value)    # between the two straight sides
    return _direction(f + 4, value)


def _stretch(s: dict, dimension: str, value: float) -> None:
    """Put the new number into the shape, moving any still-free number out of its way."""
    if s["shape"] == "polygon" and dimension == "diameter":
        dimension, value = "across_flats", value * math.cos(math.pi / s["sides"])
    s[dimension] = float(value)
    if s["shape"] != "slot":
        return
    # A slot must stay longer than it is wide, or it is no longer a slot.
    if dimension == "width" and "length" not in s["fixed"]:
        s["length"] = max(s["length"], 2 * s["width"])
    if dimension == "length" and "width" not in s["fixed"]:
        s["width"] = min(s["width"], s["length"] / 2)
    if s["length"] <= s["width"]:
        raise ShapeError("a slot must be longer than it is wide")


# --- what the session calls --------------------------------------------------------------------

def _place(sketch, s: dict) -> None:
    """Move the shape's geometry to exactly where its numbers say."""
    wanted = GEOMETRY[s["shape"]](s)
    everything = sketch.Geometry
    everything[s["first"]:s["first"] + len(wanted)] = [geometry for geometry, _ in wanted]
    sketch.Geometry = everything
    for offset, (_, helper) in enumerate(wanted):
        sketch.setConstruction(s["first"] + offset, helper)


def _solve(sketch, s: dict) -> None:
    """Run the solver and make sure it agreed with where we put the shape."""
    status = sketch.solve()             # 0 means solved; negative means it could not
    if status != 0:
        raise ShapeError(f"the sketch solver failed (code {status})")
    solved = sketch.Geometry
    for index, (wanted, _) in enumerate(GEOMETRY[s["shape"]](s), start=s["first"]):
        got = solved[index]
        moved = [getattr(wanted, name).distanceToPoint(getattr(got, name))
                 for name in ("StartPoint", "EndPoint", "Center") if hasattr(wanted, name)]
        if hasattr(wanted, "Radius"):
            moved.append(abs(wanted.Radius - got.Radius))
        if any(distance > SOLVER_TOLERANCE for distance in moved):
            raise ShapeError(f"the solver moved the {s['shape']} away from its dimensions")


def draw(sketch, kind: str, sides: int | None = None) -> dict:
    """Add a shape at its default size and return its record."""
    s = {"shape": kind, "first": sketch.GeometryCount, **DEFAULTS[kind], "fixed": []}
    if kind == "polygon":
        s["sides"] = sides
    for geometry, helper in GEOMETRY[kind](s):
        sketch.addGeometry(geometry, helper)
    sketch.addConstraint(_shape_rules(s))
    _solve(sketch, s)
    return s


def constrain(sketch, s: dict, dimension: str, value: float, name: str) -> None:
    """Give the shape one dimension: redraw it at that number, add the rule, solve."""
    _stretch(s, dimension, value)
    _place(sketch, s)
    index = sketch.addConstraint(_dimension_rule(s, dimension, float(value)))
    # A named constraint shows up by name in FreeCAD's constraint list, so a person who
    # opens the saved document sees "length = 80 mm" and can edit it.
    sketch.renameConstraint(index, name)
    s["fixed"].append(dimension)
    _solve(sketch, s)
