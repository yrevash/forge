"""Describe the FreeCAD session as plain data: the snapshot. Runs inside FreeCAD.

The snapshot is what the model "sees" after every command. It is a dict of
three parts, all JSON:

    items    one entry per object in the document, in the order they were made.
             Every entry has "type", "name" and "valid"; the rest depends on the type.
    session  what is active, open and selected; how many commands can be undone.
    solid    the measured solid of the active body (None until there is one).

Nothing here is a picture, and nothing depends on FreeCAD's face or edge numbers.
"""

from __future__ import annotations

import math

DIGITS = 6      # sketch coordinates are rounded to a millionth of a millimetre
ROLE_TO_PLANE = {"XY_Plane": "XY", "XZ_Plane": "XZ", "YZ_Plane": "YZ"}
ROLE_TO_AXIS = {"X_Axis": "X", "Y_Axis": "Y", "Z_Axis": "Z"}
# FreeCAD's type name -> the type name used in the snapshot.
FEATURE_TYPES = {
    "PartDesign::Pad": "pad", "PartDesign::Pocket": "pocket", "PartDesign::Hole": "hole",
    "PartDesign::Revolution": "revolution", "PartDesign::Fillet": "fillet",
    "PartDesign::Chamfer": "chamfer", "PartDesign::Thickness": "thickness",
    "PartDesign::PolarPattern": "polar_pattern", "PartDesign::LinearPattern": "linear_pattern",
    "PartDesign::Mirrored": "mirror",
}
# Constraint types that carry a number.
DIMENSIONAL = ("Distance", "DistanceX", "DistanceY", "Radius", "Diameter", "Angle")
UNSET = -2000   # FreeCAD's "no geometry here" in a constraint


def _r(value: float) -> float:
    return round(value, DIGITS) + 0.0       # "+ 0.0" turns -0.0 into 0.0


def _xy(point) -> list[float]:
    return [_r(point.x), _r(point.y)]


def is_valid(obj) -> bool:
    """False if FreeCAD marked the object as failed when it last recomputed it."""
    return obj.isValid() and "Invalid" not in obj.State


def body_of(obj) -> str | None:
    """Name of the PartDesign body an object belongs to."""
    for parent in obj.InList:
        if parent.TypeId == "PartDesign::Body" and obj in parent.Group:
            return parent.Name
    return None


def profile_user(sketch) -> str | None:
    """Name of the feature that pads, pockets, revolves or drills this sketch."""
    for user in sketch.InList:
        if hasattr(user, "Profile") and user.Profile and user.Profile[0] == sketch:
            return user.Name
    return None


# --- sketches ----------------------------------------------------------------------------------

def _geometry_item(geometry, construction: bool) -> dict:
    kind = geometry.TypeId.removeprefix("Part::Geom")
    if kind == "LineSegment":
        item = {"kind": "line", "from": _xy(geometry.StartPoint), "to": _xy(geometry.EndPoint)}
    elif kind == "Circle":
        item = {"kind": "circle", "centre": _xy(geometry.Center), "radius": _r(geometry.Radius)}
    elif kind == "ArcOfCircle":
        item = {"kind": "arc", "centre": _xy(geometry.Center), "radius": _r(geometry.Radius),
                "from": _xy(geometry.StartPoint), "to": _xy(geometry.EndPoint)}
    elif kind == "Point":
        item = {"kind": "point", "at": [_r(geometry.X), _r(geometry.Y)]}
    else:
        item = {"kind": kind}
    item["construction"] = construction
    return item


def _constraint_item(constraint) -> dict:
    item = {"kind": constraint.Type,
            "on": [index for index in (constraint.First, constraint.Second, constraint.Third)
                   if index != UNSET]}
    if constraint.Name:
        item["name"] = constraint.Name
    if constraint.Type in DIMENSIONAL:
        value = math.degrees(constraint.Value) if constraint.Type == "Angle" else constraint.Value
        item["value"] = _r(value)
    return item


def _is_closed(sketch) -> bool:
    """True if the sketch has an outline and every outline is a closed loop."""
    wires = [] if sketch.Shape.isNull() else sketch.Shape.Wires
    return bool(wires) and all(wire.isClosed() for wire in wires)


def sketch_item(sketch, shapes: list[dict]) -> dict:
    support = sketch.AttachmentSupport
    geometry = sketch.Geometry
    return {
        "type": "sketch", "name": sketch.Name, "body": body_of(sketch),
        "plane": ROLE_TO_PLANE.get(support[0][0].Role) if support else None,
        "offset": _r(sketch.AttachmentOffset.Base.z),
        "shapes": shapes,
        "geometry": [_geometry_item(g, sketch.getConstruction(i))
                     for i, g in enumerate(geometry)],
        "constraints": [_constraint_item(c) for c in sketch.Constraints],
        "dof": sketch.DoF,                  # degrees of freedom left; 0 = fully constrained
        "closed": _is_closed(sketch),
        "used_by": profile_user(sketch),
        "valid": is_valid(sketch),
    }


# --- features ----------------------------------------------------------------------------------

def _mm(quantity) -> float:
    return float(quantity.Value)


def feature_item(obj) -> dict:
    kind = FEATURE_TYPES[obj.TypeId]
    item = {"type": kind, "name": obj.Name, "body": body_of(obj)}
    if kind in ("pad", "pocket", "hole", "revolution"):
        item["sketch"] = obj.Profile[0].Name if obj.Profile else None
    if kind == "pad":
        item["length"] = _mm(obj.Length)
    elif kind == "pocket":
        through = obj.Type == "ThroughAll"
        item.update(through_all=through, depth=None if through else _mm(obj.Length))
    elif kind == "hole":
        through = obj.DepthType == "ThroughAll"
        counterbore = obj.HoleCutType == "Counterbore"
        item.update(diameter=_mm(obj.Diameter), through_all=through,
                    depth=None if through else _mm(obj.Depth),
                    counterbore_diameter=_mm(obj.HoleCutDiameter) if counterbore else None,
                    counterbore_depth=_mm(obj.HoleCutDepth) if counterbore else None)
    elif kind == "revolution":
        axis = obj.ReferenceAxis
        item.update(angle=_mm(obj.Angle), axis=axis[1][0][0] if axis and axis[1] else None)
    elif kind in ("fillet", "chamfer", "thickness"):
        base = obj.Base
        item["on"] = base[0].Name if base else None
        if kind == "thickness":
            item.update(faces=len(base[1]) if base else 0, value=_mm(obj.Value))
        else:
            item["edges"] = len(base[1]) if base else 0
            item["radius" if kind == "fillet" else "size"] = _mm(
                obj.Radius if kind == "fillet" else obj.Size)
    else:
        item["of"] = obj.Originals[0].Name if obj.Originals else None
        if kind == "polar_pattern":
            item.update(count=int(obj.Occurrences), angle=_mm(obj.Angle),
                        axis=ROLE_TO_AXIS.get(obj.Axis[0].Role) if obj.Axis else None)
        elif kind == "linear_pattern":
            item.update(count=int(obj.Occurrences), spacing=_mm(obj.Offset),
                        direction=ROLE_TO_AXIS.get(obj.Direction[0].Role)
                        if obj.Direction else None)
        else:
            item["plane"] = ROLE_TO_PLANE.get(obj.MirrorPlane[0].Role) if obj.MirrorPlane else None
    item["valid"] = is_valid(obj)
    return item


# --- the measured solid ------------------------------------------------------------------------

def measure(shape) -> dict | None:
    """Volume, bounding box, number of solids and validity of a shape; None if it is empty."""
    if shape.isNull() or not shape.Solids:
        return None
    # The plain BoundBox may be a little too large on curved faces. This one is exact:
    # it is worked out from the surfaces themselves, not from a mesh, with no tolerance added.
    box = shape.optimalBoundingBox(False, False)
    low = [round(v, 9) + 0.0 for v in (box.XMin, box.YMin, box.ZMin)]
    high = [round(v, 9) + 0.0 for v in (box.XMax, box.YMax, box.ZMax)]
    return {"volume": shape.Volume, "bbox": low + high,
            "size": [round(b - a, 9) for a, b in zip(low, high)],
            "solids": len(shape.Solids), "valid": bool(shape.isValid())}
