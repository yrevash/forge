"""Sides of a placed body: "its front, as it sits". Runs inside FreeCAD.

A plan says `on deck's front: hole ...`. The deck may be lying down or turned round, so
which of ITS OWN faces is "the front" depends on how it was placed. Here a side is found
from the body's placement at the moment it is used; no FreeCAD face name is ever stored.

Directions, as in forge/resolve/space.py: length is X (left low, right high), depth is Y
(front low, back high), height is Z.

A sketch on a side
    Its origin is where the body's own origin falls on that side's plane (for a part
    drawn centred that is the middle of the face). Its x and y run along the face's
    FIRST and SECOND direction of the plan language (PLAN_LANGUAGE section 9):
        top, bottom    first = length, second = depth
        front, back    first = length, second = height
        left, right    first = depth,  second = height
    so a feature's written position (u, v) is its sketch position, unchanged.
    The sketch still hangs on a BASE plane of the body (with an attachment offset that
    turns and shifts it), never on a face, so it survives any later change of the solid.

    For three of the six sides (bottom, left, back) a sketch with those x and y looks INTO
    the body. FreeCAD pads along the sketch's normal and pockets against it, so features on
    such a sketch are made with "Reversed" set: a pad still grows out of the face and a
    pocket still cuts into it. `flipped` says which sketches those are.
"""

from __future__ import annotations

import FreeCAD

V = FreeCAD.Vector
# side -> (axis, which end): the outward normal is that axis, pointing to that end.
SIDE = {"left": (0, -1), "right": (0, 1), "front": (1, -1), "back": (1, 1),
        "bottom": (2, -1), "top": (2, 1)}
# The first and second direction of a face, by the axis it is square to.
FACE_DIRECTIONS = {2: (0, 1), 1: (0, 2), 0: (1, 2)}
SQUARE = 1e-9       # how far a turned axis may be from a whole axis and still count as square
FLAT = 1e-6         # mm; as in forge/freecad/inside/rules.py


class NotSquare(ValueError):
    """The body is turned by an odd angle: its faces are not the structure's sides."""


def _unit(axis: int, sign: float = 1.0):
    return V(*(sign if k == axis else 0.0 for k in range(3)))


def _snapped(vector):
    """A turned axis as exact whole numbers, or NotSquare if it is not along an axis."""
    values = [round(value) for value in (vector.x, vector.y, vector.z)]
    if any(abs(value - whole) > SQUARE for value, whole in
           zip((vector.x, vector.y, vector.z), values)) or sum(abs(v) for v in values) != 1:
        raise NotSquare("the body is turned by an odd angle; it has no such side")
    return V(*map(float, values))


def frame(body, side: str) -> tuple:
    """(normal, first, second, flipped) of a side, in the body's OWN coordinates."""
    axis, end = SIDE[side]
    first, second = FACE_DIRECTIONS[axis]
    back_turn = body.Placement.Rotation.inverted()      # structure direction -> body direction
    normal, x, y = (_snapped(back_turn.multVec(v))
                    for v in (_unit(axis, float(end)), _unit(first), _unit(second)))
    return normal, x, y, x.cross(y).dot(normal) < 0


def new_sketch(body, origin_plane, side: str, offset: float):
    """A sketch on a side of the body, `offset` mm out from the body's origin."""
    normal, x, y, flipped = frame(body, side)
    z = x.cross(y)                                      # the sketch's own normal
    turn = FreeCAD.Rotation(FreeCAD.Matrix(x.x, y.x, z.x, 0, x.y, y.y, z.y, 0,
                                           x.z, y.z, z.z, 0, 0, 0, 0, 1))
    sketch = body.newObject("Sketcher::SketchObject", "Sketch")
    # The XY base plane sits at the body's origin unturned, so the offset from it IS the
    # sketch's place in the body.
    sketch.AttachmentSupport = [(origin_plane, "")]
    sketch.MapMode = "FlatFace"
    sketch.AttachmentOffset = FreeCAD.Placement(normal * offset, turn)
    return sketch, flipped


def _along(box, normal) -> tuple[float, float]:
    """(low, high) of a bounding box measured along `normal` (which is a whole axis)."""
    if abs(normal.x) > 0.5:
        low, high = box.XMin, box.XMax
    elif abs(normal.y) > 0.5:
        low, high = box.YMin, box.YMax
    else:
        low, high = box.ZMin, box.ZMax
    return (low, high) if normal.x + normal.y + normal.z > 0 else (-high, -low)


def face_names(body, shape, side: str) -> list[str]:
    """Names of the flat faces of `shape` that face that side and lie furthest out."""
    normal = frame(body, side)[0]
    found = []
    for number, face in enumerate(shape.Faces, start=1):
        if face.Surface.__class__.__name__ != "Plane":
            continue
        u0, u1, v0, v1 = face.ParameterRange
        if face.normalAt((u0 + u1) / 2, (v0 + v1) / 2).dot(normal) > 1 - FLAT:
            found.append((_along(face.BoundBox, normal)[1], f"Face{number}"))
    if not found:
        return []
    outermost = max(height for height, _ in found)
    return [name for height, name in found if abs(height - outermost) < FLAT]


def edge_names(body, shape, side: str, rule: str) -> list[str]:
    """Names of the edges round that side ("around") or straight and square to it ("along")."""
    normal = frame(body, side)[0]
    outermost = _along(shape.BoundBox, normal)[1]
    names = []
    for number, edge in enumerate(shape.Edges, start=1):
        if rule == "around":
            low, high = _along(edge.BoundBox, normal)
            fits = abs(low - outermost) < FLAT and abs(high - outermost) < FLAT
        else:
            fits = (edge.Curve.__class__.__name__ == "Line"
                    and abs(abs(edge.Curve.Direction.dot(normal)) - 1) < FLAT)
        if fits:
            names.append(f"Edge{number}")
    return names
