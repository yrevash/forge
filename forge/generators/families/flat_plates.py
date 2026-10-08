"""Flat parts cut from plate or sheet: slots, shaped outlines, windows, countersinks.

Each part is one outline extruded to a thickness, with holes or slots cut
through it. The outlines are new here (L, T, cross, clipped triangle, strip
with round ends, frame), and so are the cuts (slots, an open slot, a window
with rounded corners, countersunk holes).

Every range below is our own design choice, not a standard. Areas are exact:
rectangles, triangles, circles and slots (a rectangle plus two half circles).
"""

from __future__ import annotations

import math
import random

from forge.generators.base import Part, circle_area, grid, program
from forge.generators.families._common import (
    MIN_WALL,
    ceil_to,
    count_cylinders,
    no_standard_parts,
    span,
)
from forge.generators.families.turned import countersink_depth, countersunk_hole_volume


def slot_area(length: float, width: float) -> float:
    """Area of a slot with round ends: `length` is overall, end to end."""
    return (length - width) * width + circle_area(width)


def _hole_and_arm(rng: random.Random) -> tuple[float, float]:
    """A hole and the width of the flat arm it sits in (hole centred, wall on both sides)."""
    hole = grid(rng, 3.0, 10.0, 0.5)
    arm = math.ceil(hole + 2 * MIN_WALL) + grid(rng, 0.0, 15.0, 1.0)
    return hole, arm


class SlottedPlate:
    NAME = "slotted_plate"
    DESCRIPTION = "Rectangular plate with one to three parallel slots cut through it"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        slot_width = grid(rng, 4.0, 14.0, 0.5)
        # A slot is at least three times as long as it is wide; otherwise it reads as a hole.
        slot_length = slot_width + math.ceil(2 * slot_width) + grid(rng, 0.0, 60.0, 1.0)
        count = rng.randint(1, 3)
        # Neighbouring slots keep at least 4 mm of plate between them.
        pitch = math.ceil(slot_width + MIN_WALL) + grid(rng, 2.0, 20.0, 1.0)
        # Plate left beyond the slots at the ends and at the sides.
        end_margin = grid(rng, 4.0, 20.0, 1.0)
        side_margin = grid(rng, 4.0, 20.0, 1.0)
        length = slot_length + 2 * end_margin
        width = (count - 1) * pitch + slot_width + 2 * side_margin
        # A plate, not a block: at most a quarter of its width thick.
        thickness = span(rng, 2.0, min(12.0, width / 4), 0.5)

        params: dict[str, float | int] = {"length": length, "width": width,
                                          "thickness": thickness}
        if count > 1:  # one slot needs neither a count nor a pitch
            params.update(slot_count=count, slot_pitch=pitch)
        params.update(slot_length=slot_length, slot_width=slot_width)
        pattern = "    .rarray(1, slot_pitch, 1, slot_count)\n" if count > 1 else ""
        code = program(
            params,
            "result = (\n"
            '    cq.Workplane("XY")\n'
            "    .box(length, width, thickness, centered=(True, True, False))\n"
            '    .faces(">Z")\n'
            "    .workplane()\n"
            f"{pattern}"
            "    .slot2D(slot_length, slot_width)\n"
            "    .cutThruAll()\n"
            ")",
        )
        area = length * width - count * slot_area(slot_length, slot_width)
        # Each slot end is half a cylinder.
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {slot_width: 2 * count})


class PerforatedStrip:
    NAME = "perforated_strip"
    DESCRIPTION = "Perforated strip: a flat bar with round ends and a row of evenly spaced holes"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 10.0, 0.5)
        width = math.ceil(hole + 2 * MIN_WALL) + grid(rng, 0.0, 12.0, 1.0)
        count = rng.randint(3, 10)
        # Holes are pitched at least 4 mm of metal apart.
        pitch = math.ceil(hole + MIN_WALL) + grid(rng, 2.0, 20.0, 1.0)
        # Strip is thin: at most a third of its width.
        thickness = span(rng, 1.0, min(6.0, width / 3), 0.5)
        length = (count - 1) * pitch + width  # the end holes sit at the centres of the round ends
        params = {"width": width, "thickness": thickness, "hole_count": count,
                  "hole_pitch": pitch, "hole_diameter": hole}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .slot2D((hole_count - 1) * hole_pitch + width, width)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .rarray(hole_pitch, 1, hole_count, 1)
    .hole(hole_diameter)
)
""",
        )
        area = slot_area(length, width) - count * circle_area(hole)
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {hole: count, width: 2})


class GussetPlate:
    NAME = "gusset_plate"
    DESCRIPTION = "Gusset plate: a right-angled triangular plate with clipped tips and three holes"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 10.0, 0.5)
        inset = math.ceil(hole / 2 + MIN_WALL) + grid(rng, 0.0, 4.0, 1.0)
        # Each tip is clipped square, wide enough to hold a hole centred in it.
        tip = 2 * inset
        # The holes sit at least three diameters apart.
        length = ceil_to(tip + 3 * hole, 5) + grid(rng, 0.0, 80.0, 5.0)
        # The shorter leg is at least half the longer one: a gusset, not a sliver.
        width = span(rng, max(ceil_to(tip + 3 * hole, 5), ceil_to(0.5 * length, 5)), length, 5.0)
        # Gussets are cut from plate: at most an eighth of the shorter leg thick.
        thickness = span(rng, 2.0, min(10.0, width / 8), 0.5)
        params = {"length": length, "width": width, "thickness": thickness,
                  "tip_width": tip, "hole_diameter": hole, "hole_inset": inset}
        code = program(
            params,
            """
plate = (
    cq.Workplane("XY")
    .polyline([
        (0, 0),
        (length, 0),
        (length, tip_width),
        (tip_width, width),
        (0, width),
    ])
    .close()
    .extrude(thickness)
)
holes = (
    cq.Workplane("XY")
    .pushPoints([
        (hole_inset, hole_inset),
        (length - hole_inset, hole_inset),
        (hole_inset, width - hole_inset),
    ])
    .circle(hole_diameter / 2)
    .extrude(thickness)
)
result = plate.cut(holes)
""",
        )
        # The bounding rectangle minus the triangle cut off by the sloping edge.
        area = (length * width - (length - tip) * (width - tip) / 2
                - 3 * circle_area(hole))
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {hole: 3})


class CornerPlate:
    NAME = "corner_plate"
    DESCRIPTION = "Corner plate: a flat L-shaped plate with a hole at the corner and at each end"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole, arm = _hole_and_arm(rng)
        # Each arm is at least twice as long as it is wide, so the end hole clears the corner hole.
        length = ceil_to(2 * arm, 5) + grid(rng, 0.0, 80.0, 5.0)
        width = ceil_to(2 * arm, 5) + grid(rng, 0.0, 80.0, 5.0)
        # Flat mending plates are thin: at most a third of the arm width.
        thickness = span(rng, 1.5, min(8.0, arm / 3), 0.5)
        params = {"length": length, "width": width, "arm_width": arm, "thickness": thickness,
                  "hole_diameter": hole}
        code = program(
            params,
            """
plate = (
    cq.Workplane("XY")
    .polyline([
        (0, 0),
        (length, 0),
        (length, arm_width),
        (arm_width, arm_width),
        (arm_width, width),
        (0, width),
    ])
    .close()
    .extrude(thickness)
)
holes = (
    cq.Workplane("XY")
    .pushPoints([
        (arm_width / 2, arm_width / 2),
        (length - arm_width / 2, arm_width / 2),
        (arm_width / 2, width - arm_width / 2),
    ])
    .circle(hole_diameter / 2)
    .extrude(thickness)
)
result = plate.cut(holes)
""",
        )
        area = arm * (length + width - arm) - 3 * circle_area(hole)
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {hole: 3})


class TPlate:
    NAME = "t_plate"
    DESCRIPTION = "T-plate: a flat T-shaped plate with a hole at each end and at the junction"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole, arm = _hole_and_arm(rng)
        # The cross bar is at least three arm widths long and the stem at least one arm
        # width, so no two holes come closer than one arm width.
        length = ceil_to(3 * arm, 5) + grid(rng, 0.0, 60.0, 5.0)
        width = ceil_to(2 * arm, 5) + grid(rng, 0.0, 80.0, 5.0)
        thickness = span(rng, 1.5, min(8.0, arm / 3), 0.5)
        params = {"length": length, "width": width, "arm_width": arm, "thickness": thickness,
                  "hole_diameter": hole}
        code = program(
            params,
            """
plate = (
    cq.Workplane("XY")
    .polyline([
        (-arm_width / 2, 0),
        (arm_width / 2, 0),
        (arm_width / 2, width - arm_width),
        (length / 2, width - arm_width),
        (length / 2, width),
        (-length / 2, width),
        (-length / 2, width - arm_width),
        (-arm_width / 2, width - arm_width),
    ])
    .close()
    .extrude(thickness)
)
holes = (
    cq.Workplane("XY")
    .pushPoints([
        (0, arm_width / 2),
        (0, width - arm_width / 2),
        (-(length - arm_width) / 2, width - arm_width / 2),
        ((length - arm_width) / 2, width - arm_width / 2),
    ])
    .circle(hole_diameter / 2)
    .extrude(thickness)
)
result = plate.cut(holes)
""",
        )
        area = arm * (length + width - arm) - 4 * circle_area(hole)
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {hole: 4})


class CrossPlate:
    NAME = "cross_plate"
    DESCRIPTION = "Cross plate: a flat plus-shaped plate with a hole in the centre and at each end"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole, arm = _hole_and_arm(rng)
        # Each bar is at least three arm widths long, so the end holes clear the centre hole.
        length = ceil_to(3 * arm, 5) + grid(rng, 0.0, 80.0, 5.0)
        width = ceil_to(3 * arm, 5) + grid(rng, 0.0, 80.0, 5.0)
        thickness = span(rng, 1.5, min(8.0, arm / 3), 0.5)
        params = {"length": length, "width": width, "arm_width": arm, "thickness": thickness,
                  "hole_diameter": hole}
        code = program(
            params,
            """
plate = (
    cq.Workplane("XY")
    .rect(length, arm_width)
    .extrude(thickness)
    .union(cq.Workplane("XY").rect(arm_width, width).extrude(thickness))
)
holes = (
    cq.Workplane("XY")
    .pushPoints([
        (0, 0),
        (-(length - arm_width) / 2, 0),
        ((length - arm_width) / 2, 0),
        (0, -(width - arm_width) / 2),
        (0, (width - arm_width) / 2),
    ])
    .circle(hole_diameter / 2)
    .extrude(thickness)
)
result = plate.cut(holes)
""",
        )
        area = arm * (length + width - arm) - 5 * circle_area(hole)
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {hole: 5})


class RectangularFrame:
    NAME = "rectangular_frame"
    DESCRIPTION = "Rectangular frame: a plate with a round-cornered window and a hole in each corner"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 8.0, 0.5)
        # The border holds a hole with a wall on both sides.
        border = math.ceil(hole + 2 * MIN_WALL) + grid(rng, 0.0, 10.0, 1.0)
        window_length = grid(rng, 20.0, 140.0, 5.0)
        window_width = span(rng, 20.0, window_length, 5.0)
        length, width = window_length + 2 * border, window_width + 2 * border
        thickness = grid(rng, 2.0, 10.0, 0.5)
        while True:
            # Window corners are rounded by at most a quarter of the window's short side.
            radius = span(rng, 1.0, min(10.0, window_width / 4), 0.5)
            if 2 * radius != hole:  # keeps corner arcs and holes distinguishable when measured
                break
        params = {"length": length, "width": width, "thickness": thickness,
                  "border_width": border, "window_corner_radius": radius,
                  "hole_diameter": hole}
        code = program(
            params,
            """
window = (
    cq.Workplane("XY")
    .rect(length - 2 * border_width, width - 2 * border_width)
    .extrude(thickness)
    .edges("|Z")
    .fillet(window_corner_radius)
)
result = (
    cq.Workplane("XY")
    .box(length, width, thickness, centered=(True, True, False))
    .cut(window)
    .faces(">Z")
    .workplane()
    .rect(length - border_width, width - border_width, forConstruction=True)
    .vertices()
    .hole(hole_diameter)
)
""",
        )
        # A rectangle with rounded corners loses (4 - pi) * r^2 to the four corners.
        window = window_length * window_width - (4 - math.pi) * radius**2
        area = length * width - window - 4 * circle_area(hole)
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {hole: 4, 2 * radius: 4})


class CountersunkPlate:
    NAME = "countersunk_plate"
    DESCRIPTION = "Plate with a row of countersunk holes for flat-head screws"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 10.0, 0.5)
        angle = rng.choice([82, 90])  # the two common flat-head screw angles
        # A flat screw head is up to about twice its thread diameter.
        countersink = hole + span(rng, 2.0, hole, 0.5)
        depth = countersink_depth(hole, countersink, angle)
        # The cone stops at least 0.5 mm above the bottom face, so a plain hole remains.
        thickness = ceil_to(depth + 0.5, 0.5) + grid(rng, 0.0, 4.0, 0.5)
        count = rng.randint(2, 6)
        spacing = math.ceil(countersink + MIN_WALL) + grid(rng, 0.0, 30.0, 1.0)
        margin = math.ceil(countersink / 2 + MIN_WALL) + grid(rng, 0.0, 10.0, 1.0)
        length = (count - 1) * spacing + 2 * margin
        width = math.ceil(countersink + 2 * MIN_WALL) + grid(rng, 0.0, 30.0, 1.0)
        params = {"length": length, "width": width, "thickness": thickness,
                  "hole_count": count, "hole_spacing": spacing, "hole_diameter": hole,
                  "countersink_diameter": countersink, "countersink_angle": angle}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .box(length, width, thickness, centered=(True, True, False))
    .faces(">Z")
    .workplane()
    .rarray(hole_spacing, 1, hole_count, 1)
    .cskHole(hole_diameter, countersink_diameter, countersink_angle)
)
""",
        )
        volume = (length * width * thickness
                  - count * countersunk_hole_volume(hole, countersink, angle, thickness))
        return Part(self.NAME, params, code, [length, width, thickness], volume,
                    {hole: count})


class SlottedShim:
    NAME = "slotted_shim"
    DESCRIPTION = "Slotted shim: a thin rectangular plate with a round-ended slot open to one edge"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        slot_width = grid(rng, 6.0, 30.0, 1.0)
        # Each side of the slot keeps a leg at least half the slot wide.
        leg = span(rng, max(6.0, math.ceil(0.5 * slot_width)), 30.0, 1.0)
        width = slot_width + 2 * leg
        length = ceil_to(width, 5) + grid(rng, 0.0, 40.0, 5.0)
        # The slot reaches the middle of the shim or a little past it, as on machinery shims,
        # and is at least as long as it is wide.
        slot_depth = span(rng, max(math.ceil(0.5 * length), slot_width),
                          math.floor(0.75 * length), 1.0)
        thickness = grid(rng, 0.5, 3.0, 0.5)  # shims are thin
        params = {"length": length, "width": width, "thickness": thickness,
                  "slot_width": slot_width, "slot_depth": slot_depth}
        code = program(
            params,
            """
plate = cq.Workplane("XY").box(length, width, thickness, centered=(True, True, False))
slot = (
    cq.Workplane("XY")
    .center(length / 2, 0)
    .slot2D(2 * slot_depth, slot_width)
    .extrude(thickness)
)
result = plate.cut(slot)
""",
        )
        # Inside the plate the slot is a rectangle ending in half a circle.
        removed = slot_width * (slot_depth - slot_width / 2) + circle_area(slot_width) / 2
        area = length * width - removed
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {slot_width: 1})


class SquareFlange:
    NAME = "square_flange"
    DESCRIPTION = "Square flange: a square plate with rounded corners, a centre bore and four bolt holes"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        bore = grid(rng, 10.0, 60.0, 1.0)
        # Bolt holes are smaller than the bore: at most half of it.
        bolt_hole = span(rng, 4.0, min(12.0, bore / 2), 0.5)
        # The bolt holes sit on a square. A hole is spacing / sqrt(2) from the centre,
        # which has to clear the bore with a wall between them.
        spacing = (math.ceil((bore + bolt_hole + 2 * MIN_WALL) / math.sqrt(2))
                   + grid(rng, 0.0, 20.0, 1.0))
        inset = math.ceil(bolt_hole / 2 + MIN_WALL) + grid(rng, 0.0, 6.0, 1.0)
        side = spacing + 2 * inset
        while True:
            radius = span(rng, 1.0, inset, 0.5)
            if 2 * radius not in (bolt_hole, bore):  # keep the round features distinguishable
                break
        # A flange is a plate: 6% to 20% of its side, at most 20 mm.
        thickness = span(rng, max(3.0, math.ceil(0.06 * side)),
                         min(20.0, max(4.0, math.floor(0.2 * side))), 0.5)
        params = {"side": side, "thickness": thickness, "corner_radius": radius,
                  "bore_diameter": bore, "bolt_hole_spacing": spacing,
                  "bolt_hole_diameter": bolt_hole}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .box(side, side, thickness, centered=(True, True, False))
    .edges("|Z")
    .fillet(corner_radius)
    .faces(">Z")
    .workplane()
    .rect(bolt_hole_spacing, bolt_hole_spacing, forConstruction=True)
    .vertices()
    .hole(bolt_hole_diameter)
    .faces(">Z")
    .workplane()
    .hole(bore_diameter)
)
""",
        )
        area = (side * side - (4 - math.pi) * radius**2 - circle_area(bore)
                - 4 * circle_area(bolt_hole))
        return Part(self.NAME, params, code, [side, side, thickness], area * thickness,
                    count_cylinders((bore, 1), (bolt_hole, 4), (2 * radius, 4)))


FLAT_PLATE_FAMILIES = (
    SlottedPlate(), PerforatedStrip(), GussetPlate(), CornerPlate(), TPlate(), CrossPlate(),
    RectangularFrame(), CountersunkPlate(), SlottedShim(), SquareFlange(),
)
