"""Shapes the base commands cannot draw: a fixed outline, a symmetric pad, four primitives.

Runs inside FreeCAD. Every shape made here has the MIDDLE OF ITS FRAME on the body's
origin, the way forge/resolve/shapes.py describes a standing shape, so that the body is
then placed with the resolved plan's own centre and turn (see ../multi_catalogue.py).

Which FreeCAD tool makes which plan shape, and why (all measured exact, see the proof):

    box, cylinder, tube, prism, bars, wedge   a sketch, padded half to each side of its plane
    cone         PartDesign's Additive Cone: an exact cone surface. A loft between two
                 circles would give a spline surface whose volume is only approximate.
    sphere       Additive Sphere
    dome         Additive Sphere, cut off below a latitude: the top slice of a ball
    tapered box  Additive Wedge (FreeCAD's name for a box with a smaller far end), laid
                 so that its taper runs upwards. It also makes a ridge or a pyramid
                 (a top of zero length, zero depth, or both), which a loft cannot.
"""

from __future__ import annotations

import math

import FreeCAD
import Part
import Sketcher

V = FreeCAD.Vector
PRIMITIVE_TYPES = {"PartDesign::AdditiveCone": "cone", "PartDesign::AdditiveSphere": "sphere",
                   "PartDesign::AdditiveWedge": "tapered_box",
                   "PartDesign::SubtractiveSphere": "thickness"}
DIGITS = 6


def _r(value: float) -> float:
    return round(float(value), DIGITS) + 0.0


# --- a closed outline through given points --------------------------------------------------

def outline(sketch, points: list[list[float]]) -> dict:
    """Draw the closed outline and fix every side where it is. Returns the shape's record.

    Each side gets FreeCAD's "Block" constraint (the padlock in the Sketcher toolbar): the
    line may not move at all. So the sketch has no freedom left, and no solver can bend it.
    """
    first = sketch.GeometryCount
    corners = [V(float(x), float(y), 0) for x, y in points]
    for index, corner in enumerate(corners):
        sketch.addGeometry(Part.LineSegment(corner, corners[(index + 1) % len(corners)]), False)
    sketch.addConstraint([Sketcher.Constraint("Block", first + index)
                          for index in range(len(corners))])
    if sketch.solve() != 0:
        raise ValueError("the sketch solver refused the outline")
    return {"shape": "outline", "first": first,
            "points": [[_r(x), _r(y)] for x, y in points], "fixed": []}


# --- features --------------------------------------------------------------------------------

def pad_symmetric(body, sketch, length: float):
    feature = body.newObject("PartDesign::Pad", "Pad")
    feature.Profile = (sketch, [""])
    feature.Length = length
    feature.SideType = "Symmetric"      # half of `length` to each side of the sketch plane
    body.Tip = feature
    return feature


def _lowered(feature, by: float) -> None:
    """FreeCAD's primitives stand ON their origin; move this one down so its middle is there."""
    feature.Placement = FreeCAD.Placement(V(0, 0, -by), FreeCAD.Rotation())


def cone(body, bottom_diameter: float, top_diameter: float, height: float):
    feature = body.newObject("PartDesign::AdditiveCone", "Cone")
    feature.Radius1, feature.Radius2, feature.Height = bottom_diameter / 2, top_diameter / 2, height
    _lowered(feature, height / 2)
    body.Tip = feature
    return feature


def sphere(body, diameter: float):
    feature = body.newObject("PartDesign::AdditiveSphere", "Sphere")
    feature.Radius = diameter / 2
    body.Tip = feature
    return feature


def dome(body, diameter: float, height: float):
    """The top `height` of a ball. FreeCAD wants the ball's radius and the latitude of the cut."""
    # A slice `height` high and `diameter` wide at its base belongs to a ball of this radius
    # (the same formula as forge/resolve/shapes.py).
    ball = (diameter * diameter / 4 + height * height) / (2 * height)
    if height > ball + 1e-9:
        raise ValueError("a dome is at most half its diameter high")
    feature = body.newObject("PartDesign::AdditiveSphere", "Dome")
    feature.Radius = ball
    feature.Angle1 = math.degrees(math.asin(max(-1.0, min(1.0, (ball - height) / ball))))
    feature.Angle2 = 90
    # The slice runs from (ball - height) to ball above the ball's middle; centre it.
    _lowered(feature, ball - height / 2)
    body.Tip = feature
    return feature


def tapered_box(body, bottom_length: float, bottom_depth: float, top_length: float,
                top_depth: float, height: float):
    feature = body.newObject("PartDesign::AdditiveWedge", "TaperedBox")
    # FreeCAD's wedge tapers along ITS Y: the near end (Ymin) is the rectangle X by Z,
    # the far end (Ymax) the rectangle X2 by Z2.
    feature.Xmin, feature.Xmax = -bottom_length / 2, bottom_length / 2
    feature.Zmin, feature.Zmax = -bottom_depth / 2, bottom_depth / 2
    feature.X2min, feature.X2max = -top_length / 2, top_length / 2
    feature.Z2min, feature.Z2max = -top_depth / 2, top_depth / 2
    feature.Ymin, feature.Ymax = -height / 2, height / 2
    # A quarter turn about X stands that Y upright (and sends its Z to -Y, which changes
    # nothing because the shape is symmetric front to back).
    feature.Placement = FreeCAD.Placement(V(0, 0, 0), FreeCAD.Rotation(V(1, 0, 0), 90))
    body.Tip = feature
    return feature


def is_dome(feature) -> bool:
    return feature.TypeId == "PartDesign::AdditiveSphere" and feature.Angle1.Value > -90 + 1e-9


def hollow_dome(body, dome_feature, wall: float):
    """Hollow a dome through its flat base, leaving a wall of `wall`.

    FreeCAD's Thickness tool cannot do this: its kernel call fails on a slice of a ball that
    has been moved from where it was made (measured: "BRep_API: command not done"; the same
    slice unmoved works). The hollow of a dome is simply a smaller slice of a ball with the
    same middle, so it is cut out with a Subtractive Sphere, which gives the same solid.
    """
    outer = dome_feature.Radius.Value
    inner = outer - wall
    base = outer * math.sin(math.radians(dome_feature.Angle1.Value))    # height of the flat base
    if inner <= base + 1e-9:
        raise ValueError("the wall is thicker than the dome is high")
    feature = body.newObject("PartDesign::SubtractiveSphere", "Hollow")
    feature.Radius = inner
    feature.Angle1 = math.degrees(math.asin(max(-1.0, base / inner)))
    feature.Angle2 = 90
    feature.Placement = dome_feature.Placement
    body.Tip = feature
    return feature


# --- the snapshot's entry for a primitive --------------------------------------------------------

def primitive_item(obj, body: str | None, valid: bool) -> dict:
    """What the document says the primitive is, in the words of the command that made it."""
    kind = PRIMITIVE_TYPES[obj.TypeId]
    item = {"type": kind, "name": obj.Name, "body": body}
    if kind == "thickness":
        # A dome's hollow (see hollow_dome), described as the hollowing it stands in for.
        dome_feature = obj.BaseFeature
        item.update(on=dome_feature.Name if dome_feature else None, faces=1,
                    value=_r(dome_feature.Radius.Value - obj.Radius.Value) if dome_feature
                    else None, rule=None, valid=valid)
        return item
    if kind == "cone":
        item.update(bottom_diameter=_r(2 * obj.Radius1.Value), top_diameter=_r(2 * obj.Radius2.Value),
                    height=_r(obj.Height.Value))
    elif kind == "sphere":
        radius, cut = obj.Radius.Value, math.radians(obj.Angle1.Value)
        if obj.Angle1.Value > -90 + 1e-9:       # cut off below a latitude: a dome
            item["type"] = "dome"
            item.update(diameter=_r(2 * radius * math.cos(cut)),
                        height=_r(radius * (1 - math.sin(cut))))
        else:
            item["diameter"] = _r(2 * radius)
    else:
        item.update(bottom_length=_r(obj.Xmax.Value - obj.Xmin.Value),
                    bottom_depth=_r(obj.Zmax.Value - obj.Zmin.Value),
                    top_length=_r(obj.X2max.Value - obj.X2min.Value),
                    top_depth=_r(obj.Z2max.Value - obj.Z2min.Value),
                    height=_r(obj.Ymax.Value - obj.Ymin.Value))
    item["valid"] = valid
    return item
