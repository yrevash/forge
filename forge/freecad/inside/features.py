"""PartDesign features: sketch placement, pad, pocket, hole, dress-ups, patterns. Runs inside FreeCAD.

Each function does what one toolbar button does: it adds one object to the
body and sets the properties that button's dialog would set. FreeCAD works the
new solid out later, when the document is recomputed (the session does that
once per command).

Three FreeCAD habits worth knowing
  - A body has a TIP: the feature whose solid is "the body" right now. A new
    feature starts from the tip's solid and must then be made the tip itself;
    for patterns FreeCAD does not do that for us, so `_as_tip` always does.
  - A body owns an ORIGIN: three base planes and three axes that never move. We
    hang every sketch, pattern and mirror on those, never on a face of the
    solid, so nothing breaks when the solid changes.
  - A sketch is placed by ATTACHMENT: "lie flat on this plane", plus an offset
    along the plane's normal. A hole in the top of a 20 mm block is sketched on
    the XY plane with offset 20, not on "Face6".
"""

from __future__ import annotations

import FreeCAD


def origin_feature(body, role: str):
    """One of the body's base planes or axes, by FreeCAD's role name ('XY_Plane', 'Z_Axis')."""
    for feature in body.Origin.OriginFeatures:
        if feature.Role == role:
            return feature
    raise LookupError(role)


def _as_tip(body, feature):
    body.Tip = feature
    return feature


def new_sketch(body, plane: str, offset: float):
    sketch = body.newObject("Sketcher::SketchObject", "Sketch")
    sketch.AttachmentSupport = [(origin_feature(body, f"{plane}_Plane"), "")]
    sketch.MapMode = "FlatFace"
    sketch.AttachmentOffset = FreeCAD.Placement(FreeCAD.Vector(0, 0, offset), FreeCAD.Rotation())
    return sketch


# --- features made from a sketch ---------------------------------------------------------------

def pad(body, sketch, length: float):
    """Extrude the sketch along its normal (for an XY sketch: upwards)."""
    feature = body.newObject("PartDesign::Pad", "Pad")
    feature.Profile = (sketch, [""])
    feature.Length = length
    return _as_tip(body, feature)


def pocket(body, sketch, depth: float | None):
    """Cut the sketch into the solid, against its normal (for an XY sketch: downwards)."""
    feature = body.newObject("PartDesign::Pocket", "Pocket")
    feature.Profile = (sketch, [""])
    if depth is None:
        feature.Type = "ThroughAll"
    else:
        feature.Type = "Length"
        feature.Length = depth
    return _as_tip(body, feature)


def revolution(body, sketch, angle: float, axis: str):
    feature = body.newObject("PartDesign::Revolution", "Revolution")
    feature.Profile = (sketch, [""])
    feature.ReferenceAxis = (sketch, [f"{axis}_Axis"])      # the sketch's own V or H axis
    feature.Angle = angle
    return _as_tip(body, feature)


def hole(body, sketch, diameter: float, depth: float | None = None,
         counterbore: tuple[float, float] | None = None):
    """FreeCAD's Hole feature: one hole at the centre of every circle in the sketch.

    The sketch circle only marks WHERE; the size comes from the feature. We turn
    the thread tables off ("None") so the diameter is exactly the one given.
    """
    feature = body.newObject("PartDesign::Hole", "Hole")
    feature.Profile = (sketch, [""])
    feature.Threaded = False
    feature.ThreadType = "None"
    feature.Diameter = diameter
    feature.DrillPoint = "Flat"             # a flat bottom, not a drill's cone
    if depth is None:
        feature.DepthType = "ThroughAll"
    else:
        feature.DepthType = "Dimension"
        feature.Depth = depth
    if counterbore is not None:
        feature.HoleCutType = "Counterbore"
        feature.HoleCutCustomValues = True  # our own numbers, not a screw-head table
        feature.HoleCutDiameter, feature.HoleCutDepth = counterbore
    return _as_tip(body, feature)


# --- edge and face treatments --------------------------------------------------------------------

def dressup(body, kind: str, on, names: list[str], value: float):
    """Fillet or chamfer the named edges of `on`, or hollow it through the named faces."""
    type_name = {"fillet": "Fillet", "chamfer": "Chamfer", "thickness": "Thickness"}[kind]
    feature = body.newObject(f"PartDesign::{type_name}", type_name)
    feature.Base = (on, names)
    if kind == "fillet":
        feature.Radius = value
    elif kind == "chamfer":
        feature.Size = value
    else:
        feature.Value = value
        feature.Reversed = True             # walls grow inwards; the outside keeps its size
        feature.Mode = "Skin"
        feature.Join = "Intersection"       # sharp inside corners
    return _as_tip(body, feature)


# --- copies ------------------------------------------------------------------------------------

def polar_pattern(body, original, count: int, axis: str):
    feature = body.newObject("PartDesign::PolarPattern", "PolarPattern")
    feature.Originals = [original]
    feature.Axis = (origin_feature(body, f"{axis}_Axis"), [""])
    feature.Angle = 360                     # a full turn, shared evenly between the copies
    feature.Occurrences = count
    return _as_tip(body, feature)


def linear_pattern(body, original, count: int, spacing: float, direction: str):
    feature = body.newObject("PartDesign::LinearPattern", "LinearPattern")
    feature.Originals = [original]
    feature.Direction = (origin_feature(body, f"{direction}_Axis"), [""])
    feature.Mode = "Spacing"                # give the gap between copies, not the overall length
    feature.Offset = spacing
    feature.Occurrences = count
    return _as_tip(body, feature)


def mirror(body, original, plane: str):
    feature = body.newObject("PartDesign::Mirrored", "Mirrored")
    feature.Originals = [original]
    feature.MirrorPlane = (origin_feature(body, f"{plane}_Plane"), [""])
    return _as_tip(body, feature)
