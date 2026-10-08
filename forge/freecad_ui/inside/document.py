"""The document as plain data, for checks and for the teacher. Runs inside FreeCAD.

The interface elements say what can be ACTED ON. This says what has been BUILT:
one entry per object, in the order they were made, and the measured solid. It
reuses the command-level runtime's reading of features and its measurement
(forge/freecad/inside/snapshot.py), and reads sketches by their drawn shapes.
"""

from __future__ import annotations

import math

import rules  # forge/freecad/inside/rules.py
import session
import snapshot  # forge/freecad/inside/snapshot.py (on the path, see dispatch.py)

DIGITS = 6
PLANE_ROLES = {"XY_Plane": "XY", "XZ_Plane": "XZ", "YZ_Plane": "YZ"}


def _r(value: float) -> float:
    return round(value, DIGITS) + 0.0


def _kind(geometry) -> str:
    return geometry.TypeId.removeprefix("Part::Geom")


def shapes_of(sketch) -> list[dict]:
    """The shapes drawn in a sketch, read back from its geometry, in drawing order.

    The sketcher's tools leave fixed groups: a circle; four lines and a helper
    point (centred rectangle); six lines and a helper circle (hexagon); two arcs
    and two lines (slot). Anything else is reported as "other".
    """
    geometry = sketch.Geometry
    helper = [sketch.getConstruction(i) for i in range(len(geometry))]
    kinds = [_kind(g) for g in geometry]
    out, i = [], 0
    while i < len(geometry):
        if kinds[i] == "Circle" and not helper[i]:
            c = geometry[i]
            out.append({"shape": "circle", "x": _r(c.Center.x), "y": _r(c.Center.y),
                        "diameter": _r(2 * c.Radius)})
            i += 1
        elif kinds[i:i + 5] == ["LineSegment"] * 4 + ["Point"]:
            xs = [p.x for line in geometry[i:i + 4] for p in (line.StartPoint, line.EndPoint)]
            ys = [p.y for line in geometry[i:i + 4] for p in (line.StartPoint, line.EndPoint)]
            out.append({"shape": "rectangle", "x": _r(geometry[i + 4].X), "y": _r(geometry[i + 4].Y),
                        "length": _r(max(xs) - min(xs)), "width": _r(max(ys) - min(ys))})
            i += 5
        elif kinds[i:i + 7] == ["LineSegment"] * 6 + ["Circle"] and helper[i + 6]:
            circle, corner = geometry[i + 6], geometry[i].StartPoint
            angle = math.degrees(math.atan2(corner.y - circle.Center.y, corner.x - circle.Center.x))
            out.append({"shape": "hexagon", "x": _r(circle.Center.x), "y": _r(circle.Center.y),
                        "corner_radius": _r(circle.Radius), "angle": _r(angle % 60)})
            i += 7
        elif kinds[i:i + 4] == ["ArcOfCircle", "ArcOfCircle", "LineSegment", "LineSegment"]:
            a, b = geometry[i].Center, geometry[i + 1].Center
            angle = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
            out.append({"shape": "slot", "x": _r(a.x), "y": _r(a.y),
                        "centre_distance": _r(a.distanceToPoint(b)), "angle": _r(angle),
                        "end_radius": _r(geometry[i].Radius)})
            i += 4
        else:
            out.append({"shape": "other", "geometry": kinds[i]})
            i += 1
    return out


def _support(obj):
    """The object a sketch or datum plane is attached to (None if it floats free).

    FreeCAD writes a base plane as "the XY_Plane inside the Origin": the link names
    the Origin and the plane is in the sub-name ('XY_Plane.'). Both ways of writing
    it are resolved to the plane itself.
    """
    support = obj.AttachmentSupport
    if not support:
        return None
    target, subs = support[0]
    inner = subs[0].split(".")[0] if subs and subs[0] else ""
    found = obj.Document.getObject(inner) if inner else None
    return found if found is not None else target


def _support_name(obj) -> str | None:
    support = _support(obj)
    return support.Name if support is not None else None


def _plane_of(obj) -> str | None:
    return PLANE_ROLES.get(getattr(_support(obj), "Role", None))


def datum_item(obj) -> dict:
    return {"type": "datum_plane", "name": obj.Name, "on": _support_name(obj),
            "plane": _plane_of(obj), "offset": _r(obj.AttachmentOffset.Base.z),
            "valid": snapshot.is_valid(obj)}


def sketch_item(sketch) -> dict:
    return {"type": "sketch", "name": sketch.Name, "on": _support_name(sketch),
            "plane": _plane_of(sketch),
            "height": _r(sketch.getGlobalPlacement().Base.z),
            "shapes": shapes_of(sketch), "dof": _dof(sketch),
            "closed": snapshot._is_closed(sketch), "used_by": snapshot.profile_user(sketch),
            "valid": snapshot.is_valid(sketch)}


def rule_of(base, names: list[str], kind: str) -> str | None:
    """Which geometric rule gives exactly these edges (or faces) of `base`? None if none does.

    FreeCAD stores a fillet's edges as numbers ("Edge3"). A number says nothing, so
    it is turned back into the rule a person had in mind ("the vertical edges").
    """
    if base is None or base.Shape.isNull():
        return None
    wanted = set(names)
    if kind == "faces":
        options = [(rule, rules.faces(base.Shape, rule)) for rule in ("top", "bottom")]
    else:
        options = [(rule, rules.edges(base.Shape, rule))
                   for rule in ("vertical", "top_face", "bottom_face", "all")]
    for rule, found in options:
        if found and set(found) == wanted:
            return rule
    return None


def feature_item(obj) -> dict:
    """The command-level runtime's reading of a feature, plus what only a dialog can set."""
    item = snapshot.feature_item(obj)
    if item["type"] in ("fillet", "chamfer", "thickness"):
        base = obj.Base
        kind = "faces" if item["type"] == "thickness" else "edges"
        item["rule"] = rule_of(base[0], list(base[1]), kind) if base else None
    if item["type"] == "thickness":
        item.update(join=str(obj.Join), inwards=bool(obj.Reversed), mode=str(obj.Mode))
    elif item["type"] == "linear_pattern":
        item["mode"] = str(obj.Mode)
    elif item["type"] == "pad":
        item["type_setting"] = str(obj.Type)
    return item


def _dof(sketch) -> int:
    """Degrees of freedom left in the sketch (0 = every number is fixed)."""
    try:
        return int(sketch.DoF)
    except AttributeError:      # FreeCAD without the property: fully constrained or not
        return 0 if sketch.FullyConstrained else 1


def items() -> list[dict]:
    doc = session.document()
    out = []
    if doc is None:
        return out
    for obj in doc.Objects:
        kind = obj.TypeId
        if kind == "PartDesign::Body":
            out.append({"type": "body", "name": obj.Name,
                        "tip": obj.Tip.Name if obj.Tip is not None else None, "valid": True})
        elif kind in ("Part::DatumPlane", "PartDesign::Plane"):
            out.append(datum_item(obj))
        elif kind == "Sketcher::SketchObject":
            out.append(sketch_item(obj))
        elif kind in snapshot.FEATURE_TYPES:
            try:
                out.append(feature_item(obj))
            except Exception as error:  # noqa: BLE001 - a feature whose dialog is still open
                # may have links that are not set yet (a pattern with no axis, say).
                out.append({"type": snapshot.FEATURE_TYPES[kind], "name": obj.Name,
                            "valid": False, "unreadable": f"{type(error).__name__}: {error}"})
        elif not kind.startswith("App::"):
            out.append({"type": "other", "name": obj.Name, "freecad_type": kind,
                        "valid": snapshot.is_valid(obj)})
    return out


def solid() -> dict | None:
    body = session.active_body()
    if body is None or body.Shape.isNull():
        return None
    return snapshot.measure(body.Shape)


def inside(points: list[list[float]]) -> list[bool]:
    """For each point: is it inside the body's solid?"""
    import FreeCAD
    body = session.active_body()
    shape = body.Shape
    return [bool(shape.isInside(FreeCAD.Vector(*point), 1e-7, False)) for point in points]
