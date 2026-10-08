"""Blocks, brackets and housings: milled or cast shapes built from several solids.

Each part is a few simple solids drawn at absolute positions and then joined
or cut (the way BlockWithFeatures in shapes.py does it). Solids that are joined
only touch face to face and cuts never overlap each other, so every volume is a
sum and difference of boxes, prisms, cylinders and half cylinders.

Every range below is our own design choice, not a standard.
"""

from __future__ import annotations

import math
import random

from forge.generators.base import Part, circle_area, grid, program
from forge.generators.families._common import (
    MIN_WALL,
    ceil_to,
    no_standard_parts,
    span,
)


class SlottedBlock:
    NAME = "slotted_block"
    DESCRIPTION = "Block with a rectangular slot milled across its top face"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        length = grid(rng, 30.0, 150.0, 5.0)
        width = grid(rng, 20.0, 100.0, 5.0)
        # It lies flat: no taller than its shorter side. And it is a block, not a plate:
        # at least 15% of the length tall where the shorter side allows.
        tallest = min(60.0, length, width)
        height = span(rng, min(tallest, max(10.0, math.ceil(0.15 * length))), tallest, 1.0)
        # The slot takes 15% to 50% of the length, so a solid shoulder stays on each side.
        slot_width = span(rng, max(5.0, math.ceil(0.15 * length)), math.floor(0.5 * length), 1.0)
        # It goes 20% to 60% of the way down: a real slot, not a scratch.
        slot_depth = span(rng, max(2.0, math.ceil(0.2 * height)), math.floor(0.6 * height), 1.0)
        params = {"length": length, "width": width, "height": height,
                  "slot_width": slot_width, "slot_depth": slot_depth}
        code = program(
            params,
            """
block = cq.Workplane("XY").box(length, width, height, centered=(True, True, False))
slot = (
    cq.Workplane("XY")
    .workplane(offset=height - slot_depth)
    .rect(slot_width, width)
    .extrude(slot_depth)
)
result = block.cut(slot)
""",
        )
        volume = length * width * height - slot_width * width * slot_depth
        return Part(self.NAME, params, code, [length, width, height], volume)


class SteppedBlock:
    NAME = "stepped_block"
    DESCRIPTION = "Stepped block: a block with one end raised, giving an L-shaped side view"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        length = grid(rng, 20.0, 150.0, 5.0)
        # A block, not a slab on edge: width is 30% to 100% of the length, and the
        # height is no more than the length.
        width = span(rng, max(10.0, ceil_to(0.3 * length, 5)), length, 5.0)
        height = span(rng, 10.0, min(80.0, length), 1.0)
        # The raised end and the low end each keep 30% to 70% of the length and height.
        step_length = span(rng, math.ceil(0.3 * length), math.floor(0.7 * length), 1.0)
        base_height = span(rng, math.ceil(0.3 * height), math.floor(0.7 * height), 1.0)
        params = {"length": length, "width": width, "height": height,
                  "step_length": step_length, "base_height": base_height}
        code = program(
            params,
            """
base = cq.Workplane("XY").box(length, width, base_height, centered=(False, True, False))
step = cq.Workplane("XY").box(step_length, width, height, centered=(False, True, False))
result = base.union(step)
""",
        )
        volume = width * (length * base_height + step_length * (height - base_height))
        return Part(self.NAME, params, code, [length, width, height], volume)


class Wedge:
    NAME = "wedge"
    DESCRIPTION = "Wedge: a triangular prism that tapers from full height to an edge"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        length = grid(rng, 20.0, 150.0, 5.0)
        # A wedge is longer than it is tall: height is 10% to 60% of the length.
        height = span(rng, max(3.0, math.ceil(0.1 * length)), math.floor(0.6 * length), 1.0)
        # Width is 30% to 150% of the length: neither a blade nor a long ramp strip.
        width = span(rng, max(10.0, ceil_to(0.3 * length, 5)), ceil_to(1.5 * length, 5), 5.0)
        params = {"length": length, "width": width, "height": height}
        code = program(
            params,
            """
result = (
    cq.Workplane("XZ")
    .polyline([(0, 0), (length, 0), (0, height)])
    .close()
    .extrude(width / 2, both=True)
)
""",
        )
        return Part(self.NAME, params, code, [length, width, height],
                    length * height / 2 * width)


class VBlock:
    NAME = "v_block"
    DESCRIPTION = "V-block: a block with a 90 degree V groove along its top for holding round work"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        width = grid(rng, 30.0, 120.0, 5.0)
        height = span(rng, ceil_to(0.5 * width, 5), width, 5.0)
        # The groove is 30% of the width or more, leaves 5 mm of flat each side, and its
        # point (half the groove width deep, for 90 degrees) stays 8 mm above the base.
        groove = span(rng, math.ceil(0.3 * width), min(width - 10.0, 2 * (height - 8.0)), 1.0)
        length = span(rng, ceil_to(0.8 * width, 5), ceil_to(2.5 * width, 5), 5.0)
        params = {"width": width, "length": length, "height": height, "groove_width": groove}
        code = program(
            params,
            """
result = (
    cq.Workplane("XZ")
    .polyline([
        (-width / 2, 0),
        (width / 2, 0),
        (width / 2, height),
        (groove_width / 2, height),
        (0, height - groove_width / 2),
        (-groove_width / 2, height),
        (-width / 2, height),
    ])
    .close()
    .extrude(length / 2, both=True)
)
""",
        )
        area = width * height - groove * groove / 4  # the groove is a right-angled triangle
        return Part(self.NAME, params, code, [width, length, height], area * length)


class DovetailSlide:
    NAME = "dovetail_slide"
    DESCRIPTION = "Dovetail slide: a base with a dovetail rail along its top, wider at the top than at the neck"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        base_width = grid(rng, 20.0, 80.0, 1.0)
        base_height = span(rng, 5.0, math.floor(0.5 * base_width), 1.0)
        dovetail_height = span(rng, 3.0, math.floor(0.3 * base_width), 1.0)
        # The rail's top is 60% of the base or more and always narrower than the base.
        top = span(rng, math.ceil(0.6 * base_width), base_width - 2.0, 1.0)
        # Each side leans in by about 30 degrees from upright (a 60 degree dovetail),
        # rounded to half a millimetre.
        lean = max(0.5, round(dovetail_height * math.tan(math.radians(30)) * 2) / 2)
        neck = top - 2 * lean
        length = span(rng, ceil_to(base_width, 5), ceil_to(4 * base_width, 5), 5.0)
        params = {"base_width": base_width, "base_height": base_height,
                  "dovetail_top_width": top, "dovetail_neck_width": neck,
                  "dovetail_height": dovetail_height, "length": length}
        code = program(
            params,
            """
result = (
    cq.Workplane("XZ")
    .polyline([
        (-base_width / 2, 0),
        (base_width / 2, 0),
        (base_width / 2, base_height),
        (dovetail_neck_width / 2, base_height),
        (dovetail_top_width / 2, base_height + dovetail_height),
        (-dovetail_top_width / 2, base_height + dovetail_height),
        (-dovetail_neck_width / 2, base_height),
        (-base_width / 2, base_height),
    ])
    .close()
    .extrude(length / 2, both=True)
)
""",
        )
        # A rectangle for the base and a trapezium for the rail.
        area = base_width * base_height + (neck + top) / 2 * dovetail_height
        return Part(self.NAME, params, code,
                    [base_width, length, base_height + dovetail_height], area * length)


class TSlotNut:
    NAME = "t_slot_nut"
    DESCRIPTION = "T-slot nut: a T-shaped block with a hole through its centre"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 4.0, 16.0, 1.0)
        neck_width = math.ceil(hole + 2 * MIN_WALL) + grid(rng, 0.0, 6.0, 1.0)
        # The base overhangs the neck by 3 to 8 mm each side; that overhang grips the slot.
        base_width = neck_width + 2 * grid(rng, 3.0, 8.0, 1.0)
        base_height = grid(rng, 3.0, 10.0, 1.0)
        neck_height = grid(rng, 3.0, 10.0, 1.0)
        # About as long as it is wide, up to twice.
        length = span(rng, math.ceil(0.8 * base_width), math.ceil(2 * base_width), 1.0)
        params = {"base_width": base_width, "base_height": base_height,
                  "neck_width": neck_width, "neck_height": neck_height,
                  "length": length, "hole_diameter": hole}
        code = program(
            params,
            """
base = cq.Workplane("XY").box(base_width, length, base_height, centered=(True, True, False))
neck = (
    cq.Workplane("XY")
    .workplane(offset=base_height)
    .rect(neck_width, length)
    .extrude(neck_height)
)
hole = cq.Workplane("XY").circle(hole_diameter / 2).extrude(base_height + neck_height)
result = base.union(neck).cut(hole)
""",
        )
        height = base_height + neck_height
        volume = ((base_width * base_height + neck_width * neck_height) * length
                  - circle_area(hole) * height)
        return Part(self.NAME, params, code, [base_width, length, height], volume, {hole: 1})


class PillowBlock:
    NAME = "pillow_block"
    DESCRIPTION = ("Pillow block: a base with two mounting holes carrying a round-topped housing "
                   "with a horizontal bore")
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        bore = grid(rng, 8.0, 40.0, 1.0)
        # Housing wall around the bore: 4 mm up to 40% of the bore.
        housing_width = bore + 2 * span(rng, 4.0, math.ceil(0.4 * bore), 1.0)
        base_thickness = span(rng, 4.0, min(12.0, math.ceil(0.3 * bore)), 1.0)
        # The bore clears the base by 3 mm up to 40% of the bore.
        centre_height = base_thickness + bore / 2 + span(rng, 3.0, math.ceil(0.4 * bore), 1.0)
        # Mounting holes are at most half the bore, so they cannot be mistaken for it.
        mount_hole = span(rng, 4.0, min(12.0, math.floor(0.5 * bore)), 0.5)
        # Each foot beside the housing holds a mounting hole with a wall on both sides.
        foot = math.ceil(mount_hole + 2 * MIN_WALL) + grid(rng, 0.0, 8.0, 1.0)
        length = housing_width + 2 * foot
        spacing = housing_width + foot  # holes at the middle of each foot
        low = max(10.0, math.ceil(mount_hole + 2 * MIN_WALL), math.ceil(0.5 * bore))
        width = span(rng, low, max(low, math.ceil(1.2 * bore)), 1.0)
        params = {"length": length, "width": width, "base_thickness": base_thickness,
                  "housing_width": housing_width, "centre_height": centre_height,
                  "bore_diameter": bore, "mount_hole_diameter": mount_hole,
                  "mount_hole_spacing": spacing}
        code = program(
            params,
            """
base = cq.Workplane("XY").box(length, width, base_thickness, centered=(True, True, False))
housing = (
    cq.Workplane("XZ")
    .moveTo(-housing_width / 2, base_thickness)
    .lineTo(housing_width / 2, base_thickness)
    .lineTo(housing_width / 2, centre_height)
    .threePointArc((0, centre_height + housing_width / 2), (-housing_width / 2, centre_height))
    .close()
    .extrude(width / 2, both=True)
)
bore = (
    cq.Workplane("XZ")
    .center(0, centre_height)
    .circle(bore_diameter / 2)
    .extrude(width / 2, both=True)
)
mount_holes = (
    cq.Workplane("XY")
    .pushPoints([(-mount_hole_spacing / 2, 0), (mount_hole_spacing / 2, 0)])
    .circle(mount_hole_diameter / 2)
    .extrude(base_thickness)
)
result = base.union(housing).cut(bore).cut(mount_holes)
""",
        )
        # Housing side view: a rectangle from the base up to the bore centre, topped
        # by half a circle, minus the bore.
        housing_area = (housing_width * (centre_height - base_thickness)
                        + circle_area(housing_width) / 2 - circle_area(bore))
        volume = (length * width * base_thickness + housing_area * width
                  - 2 * circle_area(mount_hole) * base_thickness)
        return Part(self.NAME, params, code,
                    [length, width, centre_height + housing_width / 2], volume,
                    {housing_width: 1, bore: 1, mount_hole: 2})


class ClevisBracket:
    NAME = "clevis_bracket"
    DESCRIPTION = ("Clevis bracket: a base with two round-topped ears, a pin hole through both "
                   "ears and a mounting hole in the base")
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        while True:
            pin = grid(rng, 4.0, 20.0, 1.0)
            # The ears are as deep as the pin hole plus at least 3 mm of metal each side.
            depth = math.ceil(pin + 6.0) + grid(rng, 0.0, 10.0, 1.0)
            ear = span(rng, 3.0, math.ceil(0.6 * pin), 0.5)
            # The gap takes the mating lug: from one pin diameter up to three.
            gap = span(rng, max(8.0, math.ceil(pin)), math.ceil(3 * pin), 1.0)
            mount_hole = span(rng, 3.0, min(12.0, gap - 2 * MIN_WALL, depth - 2 * MIN_WALL), 0.5)
            if mount_hole != pin:  # keep the two holes distinguishable when measured
                break
        base_thickness = span(rng, 3.0, max(3.0, math.ceil(0.5 * pin)), 0.5)
        # The pin hole clears the base by at least 3 mm.
        pin_height = base_thickness + pin / 2 + grid(rng, 3.0, 20.0, 1.0)
        width = gap + 2 * ear
        params = {"width": width, "depth": depth, "base_thickness": base_thickness,
                  "gap_width": gap, "pin_height": pin_height, "pin_hole_diameter": pin,
                  "mount_hole_diameter": mount_hole}
        code = program(
            params,
            """
body = (
    cq.Workplane("YZ")
    .moveTo(-depth / 2, 0)
    .lineTo(depth / 2, 0)
    .lineTo(depth / 2, pin_height)
    .threePointArc((0, pin_height + depth / 2), (-depth / 2, pin_height))
    .close()
    .extrude(width / 2, both=True)
)
gap = (
    cq.Workplane("XY")
    .workplane(offset=base_thickness)
    .rect(gap_width, depth)
    .extrude(pin_height + depth / 2 - base_thickness)
)
pin_hole = (
    cq.Workplane("YZ")
    .center(0, pin_height)
    .circle(pin_hole_diameter / 2)
    .extrude(width / 2, both=True)
)
mount_hole = cq.Workplane("XY").circle(mount_hole_diameter / 2).extrude(base_thickness)
result = body.cut(gap).cut(pin_hole).cut(mount_hole)
""",
        )
        # Side view of an ear: a rectangle up to the pin centre, topped by half a circle.
        side_area = depth * pin_height + circle_area(depth) / 2
        above_base = side_area - depth * base_thickness
        volume = (side_area * width - above_base * gap
                  - 2 * circle_area(pin) * ear          # the pin hole only meets the two ears
                  - circle_area(mount_hole) * base_thickness)
        return Part(self.NAME, params, code, [width, depth, pin_height + depth / 2], volume,
                    {depth: 2, pin: 2, mount_hole: 1})


class GussetedBracket:
    NAME = "gusseted_bracket"
    DESCRIPTION = ("Gusseted bracket: an L-bracket stiffened by a triangular rib, with two holes "
                   "in each leg")
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 4.0, 10.0, 0.5)
        thickness = grid(rng, 3.0, 8.0, 0.5)
        # The rib is no thicker than the legs it stiffens.
        rib_thickness = span(rng, 2.0, thickness, 0.5)
        clear = math.ceil(hole + 2 * MIN_WALL)
        reach = clear + grid(rng, 8.0, 50.0, 1.0)   # base leg beyond the upright
        rise = clear + grid(rng, 8.0, 50.0, 1.0)    # upright above the base
        # The rib spans 40% to 90% of the shorter leg.
        shorter = min(reach, rise)
        rib_size = span(rng, math.ceil(0.4 * shorter), math.floor(0.9 * shorter), 1.0)
        # The holes sit either side of the rib, clear of it by a wall.
        spacing = math.ceil(rib_thickness + 2 * MIN_WALL + hole) + grid(rng, 0.0, 30.0, 1.0)
        width = spacing + 2 * (math.ceil(hole / 2 + MIN_WALL) + grid(rng, 0.0, 8.0, 1.0))
        base_length = thickness + reach
        height = thickness + rise
        params = {"base_length": base_length, "height": height, "width": width,
                  "thickness": thickness, "rib_size": rib_size, "rib_thickness": rib_thickness,
                  "hole_diameter": hole, "hole_spacing": spacing}
        code = program(
            params,
            """
base = cq.Workplane("XY").box(base_length, width, thickness, centered=(False, True, False))
upright = cq.Workplane("XY").box(thickness, width, height, centered=(False, True, False))
rib = (
    cq.Workplane("XZ")
    .polyline([
        (thickness, thickness),
        (thickness + rib_size, thickness),
        (thickness, thickness + rib_size),
    ])
    .close()
    .extrude(rib_thickness / 2, both=True)
)
base_holes = (
    cq.Workplane("XY")
    .pushPoints([
        ((base_length + thickness) / 2, -hole_spacing / 2),
        ((base_length + thickness) / 2, hole_spacing / 2),
    ])
    .circle(hole_diameter / 2)
    .extrude(thickness)
)
upright_holes = (
    cq.Workplane("YZ")
    .pushPoints([
        (-hole_spacing / 2, (height + thickness) / 2),
        (hole_spacing / 2, (height + thickness) / 2),
    ])
    .circle(hole_diameter / 2)
    .extrude(thickness)
)
result = base.union(upright).union(rib).cut(base_holes).cut(upright_holes)
""",
        )
        volume = (base_length * width * thickness
                  + thickness * width * (height - thickness)
                  + rib_size * rib_size / 2 * rib_thickness
                  - 4 * circle_area(hole) * thickness)
        return Part(self.NAME, params, code, [base_length, width, height], volume, {hole: 4})


class EnclosureBase:
    NAME = "enclosure_base"
    DESCRIPTION = ("Enclosure base: an open box with rounded corners and four screw posts "
                   "standing on its floor")
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        while True:
            wall = grid(rng, 1.5, 4.0, 0.5)
            # The outside corner radius is larger than the wall, so the inside corner
            # (one wall thickness smaller) is still rounded.
            radius = wall + grid(rng, 1.0, 8.0, 0.5)
            post_hole = grid(rng, 2.0, 4.0, 0.5)
            post = post_hole + 2 * grid(rng, 1.5, 3.0, 0.5)
            # All four kinds of round face must be told apart when measured.
            if len({2 * radius, 2 * (radius - wall), post, post_hole}) == 4:
                break
        # Posts stand at least 1 mm clear of the walls, and no nearer the corner than the
        # centre of the corner arc, which keeps them clear of the rounded inside corner.
        inset = ceil_to(max(radius, wall + post / 2 + 1.0), 0.5) + grid(rng, 0.0, 4.0, 0.5)
        shortest = ceil_to(2 * inset + 3 * post + 10.0, 5)
        length = shortest + grid(rng, 0.0, 120.0, 5.0)
        width = shortest + grid(rng, 0.0, 80.0, 5.0)
        height = math.ceil(wall + 8.0) + grid(rng, 0.0, 50.0, 1.0)
        # Posts end at or below the rim.
        post_height = span(rng, 4.0, height - wall, 1.0)
        params = {"length": length, "width": width, "height": height,
                  "wall_thickness": wall, "corner_radius": radius,
                  "post_diameter": post, "post_height": post_height,
                  "post_hole_diameter": post_hole, "post_inset": inset}
        code = program(
            params,
            """
shell = (
    cq.Workplane("XY")
    .box(length, width, height, centered=(True, True, False))
    .edges("|Z")
    .fillet(corner_radius)
    .faces(">Z")
    .shell(-wall_thickness)
)
posts = (
    cq.Workplane("XY")
    .workplane(offset=wall_thickness)
    .rect(length - 2 * post_inset, width - 2 * post_inset, forConstruction=True)
    .vertices()
    .circle(post_diameter / 2)
    .extrude(post_height)
)
post_holes = (
    cq.Workplane("XY")
    .workplane(offset=wall_thickness)
    .rect(length - 2 * post_inset, width - 2 * post_inset, forConstruction=True)
    .vertices()
    .circle(post_hole_diameter / 2)
    .extrude(post_height)
)
result = shell.union(posts).cut(post_holes)
""",
        )
        inner_radius = radius - wall
        # A rectangle with rounded corners loses (4 - pi) * r^2 to the four corners.
        outer_area = length * width - (4 - math.pi) * radius**2
        inner_area = ((length - 2 * wall) * (width - 2 * wall)
                      - (4 - math.pi) * inner_radius**2)
        volume = (outer_area * height - inner_area * (height - wall)
                  + 4 * (circle_area(post) - circle_area(post_hole)) * post_height)
        return Part(self.NAME, params, code, [length, width, height], volume,
                    {2 * radius: 4, 2 * inner_radius: 4, post: 4, post_hole: 4})


BLOCK_FAMILIES = (
    SlottedBlock(), SteppedBlock(), Wedge(), VBlock(), DovetailSlide(), TSlotNut(),
    PillowBlock(), ClevisBracket(), GussetedBracket(), EnclosureBase(),
)
