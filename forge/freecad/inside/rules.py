"""Pick edges and faces of a solid by a geometric rule. Runs inside FreeCAD.

FreeCAD names the pieces of a solid "Edge1, Edge2, ... Face1, Face2, ...", and
those numbers change whenever the solid changes (the "topological naming
problem"). So we never store or ask for a number. A selection is a RULE such as
"the vertical edges", and the rule is turned into today's names at the moment
it is used.
"""

from __future__ import annotations

FLAT = 1e-6     # mm; two heights closer than this are the same height


def _is_level(box, z: float) -> bool:
    """True if a bounding box is flat and lies at height z."""
    return abs(box.ZMin - z) < FLAT and abs(box.ZMax - z) < FLAT


def _is_vertical(edge) -> bool:
    """A straight edge that runs parallel to Z."""
    if edge.Curve.__class__.__name__ != "Line":
        return False
    direction = edge.Curve.Direction
    return abs(direction.x) < FLAT and abs(direction.y) < FLAT


def edges(shape, rule: str) -> list[str]:
    """Names of the edges of `shape` that fit `rule` (see catalogue.EDGE_RULES)."""
    box = shape.BoundBox
    tests = {
        "vertical": _is_vertical,
        "top_face": lambda edge: _is_level(edge.BoundBox, box.ZMax),
        "bottom_face": lambda edge: _is_level(edge.BoundBox, box.ZMin),
        "all": lambda edge: True,
    }
    return [f"Edge{number}" for number, edge in enumerate(shape.Edges, start=1)
            if tests[rule](edge)]


def faces(shape, rule: str) -> list[str]:
    """Names of the flat faces at the very top or the very bottom of `shape`."""
    box = shape.BoundBox
    z = box.ZMax if rule == "top" else box.ZMin
    return [f"Face{number}" for number, face in enumerate(shape.Faces, start=1)
            if face.Surface.__class__.__name__ == "Plane" and _is_level(face.BoundBox, z)]
