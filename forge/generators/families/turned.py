"""Turned parts: round parts a lathe would make.

Bushings, collars, pulleys, hubs, pins, knobs and cups. They add operations
the first families use little or not at all on round stock: revolve with a
groove, chamfers on circular edges, countersinks, shell on a cylinder, and a
flat milled on a shaft.

Every range below is our own design choice, not a standard. Each volume is
exact arithmetic: cylinders, cone frustums (see `frustum_volume`) and, for the
D-shaft, a circle segment.
"""

from __future__ import annotations

import math
import random

from forge.generators.base import Part, circle_area, grid, program
from forge.generators.families._common import (
    MIN_WALL,
    ceil_to,
    circle_segment_area,
    count_cylinders,
    floor_to,
    frustum_volume,
    no_standard_parts,
    span,
)


def countersink_depth(hole: float, countersink: float, angle: int) -> float:
    """How deep a countersink cone goes before it narrows to the hole diameter."""
    return (countersink - hole) / 2.0 / math.tan(math.radians(angle / 2.0))


def countersunk_hole_volume(hole: float, countersink: float, angle: int, thickness: float) -> float:
    """Material removed by one countersunk through hole.

    The hole is a cylinder through the full thickness. The countersink is a cone
    frustum from the countersink diameter down to the hole diameter; the part of
    it that is not already inside the hole is the frustum minus that cylinder.
    """
    depth = countersink_depth(hole, countersink, angle)
    return (circle_area(hole) * thickness
            + frustum_volume(countersink, hole, depth) - circle_area(hole) * depth)


class FlangedBushing:
    NAME = "flanged_bushing"
    DESCRIPTION = "Flanged bushing: a sleeve with a wider flange at one end and a through bore"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        bore = grid(rng, 4.0, 40.0, 1.0)
        # Sleeve wall: thin relative to the bore, as in a plain bearing bush.
        wall = span(rng, 1.5, min(6.0, bore / 3), 0.5)
        outer = bore + 2 * wall
        # The flange stands out from the sleeve by 2 mm up to a third of the sleeve diameter.
        flange = outer + 2 * span(rng, 2.0, min(10.0, outer / 3), 0.5)
        # The flange is a thin collar, about as thick as the wall.
        flange_thickness = span(rng, 1.5, min(6.0, 1.5 * wall), 0.5)
        # Bushes are 0.5 to 2.5 diameters long.
        length = flange_thickness + span(rng, math.ceil(0.5 * outer), math.ceil(2.5 * outer), 1.0)
        params = {"flange_diameter": flange, "flange_thickness": flange_thickness,
                  "outer_diameter": outer, "bore_diameter": bore, "length": length}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .circle(flange_diameter / 2)
    .extrude(flange_thickness)
    .faces(">Z")
    .workplane()
    .circle(outer_diameter / 2)
    .extrude(length - flange_thickness)
    .faces(">Z")
    .workplane()
    .hole(bore_diameter)
)
""",
        )
        volume = (circle_area(flange) * flange_thickness
                  + circle_area(outer) * (length - flange_thickness)
                  - circle_area(bore) * length)
        return Part(self.NAME, params, code, [flange, flange, length], volume,
                    {flange: 1, outer: 1, bore: 1})


class ShaftCollar:
    NAME = "shaft_collar"
    DESCRIPTION = "Shaft collar: a thick ring with a bore and both outer edges chamfered"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        bore = grid(rng, 4.0, 50.0, 1.0)
        # A collar is a stout ring: its wall is 20% to 50% of the bore, at least 3 mm.
        wall = span(rng, max(3.0, math.ceil(0.2 * bore)), min(15.0, max(4.0, math.ceil(0.5 * bore))),
                    0.5)
        outer = bore + 2 * wall
        # Width along the shaft: 20% to 50% of the outer diameter.
        width = span(rng, max(5.0, math.ceil(0.2 * outer)), max(6.0, math.floor(0.5 * outer)), 1.0)
        # The chamfer only breaks the edge: at most a third of the wall and a fifth of the width.
        chamfer = span(rng, 0.5, min(2.0, wall / 3, width / 5), 0.5)
        params = {"outer_diameter": outer, "bore_diameter": bore, "width": width,
                  "chamfer": chamfer}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .circle(outer_diameter / 2)
    .extrude(width)
    .edges("%CIRCLE")
    .chamfer(chamfer)
    .faces(">Z")
    .workplane()
    .hole(bore_diameter)
)
""",
        )
        # A plain cylinder in the middle and a cone frustum at each end, minus the bore.
        volume = (circle_area(outer) * (width - 2 * chamfer)
                  + 2 * frustum_volume(outer, outer - 2 * chamfer, chamfer)
                  - circle_area(bore) * width)
        return Part(self.NAME, params, code, [outer, outer, width], volume,
                    {outer: 1, bore: 1})


class VPulley:
    NAME = "v_pulley"
    DESCRIPTION = "V-belt pulley: a disc with a bore and a V groove around its rim, made by revolving"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        bore = grid(rng, 6.0, 30.0, 1.0)
        width = grid(rng, 10.0, 30.0, 1.0)
        # The groove takes 45% of the width or more, leaving a land of at least 2 mm each side.
        top = span(rng, max(5.0, math.ceil(0.45 * width)), width - 2 * MIN_WALL, 1.0)
        # The flat at the bottom of the groove is 30% to 60% of its opening.
        root = span(rng, max(1.0, ceil_to(0.3 * top, 0.5)), floor_to(0.6 * top, 0.5), 0.5)
        # Depth of 1 to 1.5 times the narrowing gives flanks 37 to 53 degrees apart,
        # around the 40 degrees of a V belt.
        depth = span(rng, ceil_to(top - root, 0.5), floor_to(1.5 * (top - root), 0.5), 0.5)
        # Material between the bore and the bottom of the groove: 4 mm up to 1.5 bore
        # diameters, so a large pulley does not end up on a pin-sized bore.
        root_diameter = bore + 2 * span(rng, 4.0, max(6.0, 1.5 * bore), 1.0)
        outer = root_diameter + 2 * depth
        params = {"outer_diameter": outer, "bore_diameter": bore, "width": width,
                  "groove_depth": depth, "groove_top_width": top, "groove_root_width": root}
        code = program(
            params,
            """
profile = (
    cq.Workplane("XZ")
    .moveTo(bore_diameter / 2, 0)
    .lineTo(outer_diameter / 2, 0)
    .lineTo(outer_diameter / 2, (width - groove_top_width) / 2)
    .lineTo(outer_diameter / 2 - groove_depth, (width - groove_root_width) / 2)
    .lineTo(outer_diameter / 2 - groove_depth, (width + groove_root_width) / 2)
    .lineTo(outer_diameter / 2, (width + groove_top_width) / 2)
    .lineTo(outer_diameter / 2, width)
    .lineTo(bore_diameter / 2, width)
    .close()
)
result = profile.revolve(360, (0, 0, 0), (0, 1, 0))
""",
        )
        flank = (top - root) / 2  # height of each sloping side of the groove
        # The groove removes a ring over its flat bottom, and over each flank the
        # difference between a full cylinder and a cone frustum.
        groove = ((circle_area(outer) - circle_area(root_diameter)) * root
                  + 2 * (circle_area(outer) * flank
                         - frustum_volume(outer, root_diameter, flank)))
        volume = (circle_area(outer) - circle_area(bore)) * width - groove
        return Part(self.NAME, params, code, [outer, outer, width], volume,
                    {outer: 2, root_diameter: 1, bore: 1})


class FlangedHub:
    NAME = "flanged_hub"
    DESCRIPTION = "Flanged hub: a bolt flange with a raised hub, a through bore and a circle of holes"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        while True:
            bore = grid(rng, 6.0, 40.0, 1.0)
            # Hub wall around the bore: a fifth of the bore or more, 3 to 10 mm.
            hub = bore + 2 * span(rng, max(3.0, math.ceil(0.2 * bore)), 10.0, 1.0)
            # Bolts are smaller than the shaft: at most half the bore plus 1 mm.
            bolt_hole = span(rng, 3.0, min(10.0, 0.5 * bore + 1.0), 0.5)
            count = rng.choice([3, 4, 5, 6, 8])
            # The flange ring outside the hub holds the bolt hole with a wall on both
            # sides; the bolt circle runs down the middle of that ring.
            ring = math.ceil(bolt_hole + 2 * MIN_WALL) + grid(rng, 0.0, 12.0, 1.0)
            outer = hub + 2 * ring
            bolt_circle = hub + ring
            # Neighbouring bolt holes must not run into each other.
            gap = bolt_circle * math.sin(math.pi / count) - bolt_hole
            if gap >= MIN_WALL and bolt_hole != bore:
                break
        # The flange is a plate: 6% to 20% of its diameter, at most 15 mm.
        thickness = span(rng, max(3.0, math.ceil(0.06 * outer)),
                         min(15.0, max(4.0, math.floor(0.2 * outer))), 0.5)
        # The hub stands above the flange by 30% to 100% of its own diameter.
        height = thickness + span(rng, max(4.0, math.ceil(0.3 * hub)), math.ceil(hub), 1.0)
        params = {"flange_diameter": outer, "flange_thickness": thickness,
                  "hub_diameter": hub, "height": height, "bore_diameter": bore,
                  "bolt_circle_diameter": bolt_circle, "bolt_hole_count": count,
                  "bolt_hole_diameter": bolt_hole}
        code = program(
            params,
            """
flange = cq.Workplane("XY").circle(flange_diameter / 2).extrude(flange_thickness)
hub = cq.Workplane("XY").circle(hub_diameter / 2).extrude(height)
bore = cq.Workplane("XY").circle(bore_diameter / 2).extrude(height)
bolt_holes = (
    cq.Workplane("XY")
    .polarArray(bolt_circle_diameter / 2, 0, 360, bolt_hole_count)
    .circle(bolt_hole_diameter / 2)
    .extrude(flange_thickness)
)
result = flange.union(hub).cut(bore).cut(bolt_holes)
""",
        )
        volume = (circle_area(outer) * thickness
                  + circle_area(hub) * (height - thickness)
                  - circle_area(bore) * height
                  - count * circle_area(bolt_hole) * thickness)
        return Part(self.NAME, params, code, [outer, outer, height], volume,
                    {outer: 1, hub: 1, bore: 1, bolt_hole: count})


class CountersunkWasher:
    NAME = "countersunk_washer"
    DESCRIPTION = "Countersunk washer: a disc with a countersunk hole for a flat-head screw"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 12.0, 0.5)
        angle = rng.choice([82, 90])  # the two common flat-head screw angles
        # A flat screw head is up to about twice its thread diameter.
        countersink = hole + span(rng, 2.0, hole, 0.5)
        depth = countersink_depth(hole, countersink, angle)
        # The cone stops at least 0.5 mm above the bottom face, so a plain hole remains.
        thickness = ceil_to(depth + 0.5, 0.5) + grid(rng, 0.0, 2.0, 0.5)
        outer = math.ceil(countersink + 2 * MIN_WALL) + grid(rng, 0.0, 10.0, 1.0)
        params = {"outer_diameter": outer, "thickness": thickness, "hole_diameter": hole,
                  "countersink_diameter": countersink, "countersink_angle": angle}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .circle(outer_diameter / 2)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .cskHole(hole_diameter, countersink_diameter, countersink_angle)
)
""",
        )
        volume = (circle_area(outer) * thickness
                  - countersunk_hole_volume(hole, countersink, angle, thickness))
        return Part(self.NAME, params, code, [outer, outer, thickness], volume,
                    {outer: 1, hole: 1})


class ConeFrustum:
    NAME = "cone_frustum"
    DESCRIPTION = "Cone frustum: a round taper from a base diameter to a smaller top, made by revolving"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        base = grid(rng, 10.0, 120.0, 1.0)
        # The top stays between a fifth of the base and 2 mm less than it: a taper, not a point.
        top = span(rng, math.ceil(0.2 * base), base - 2.0, 1.0)
        # Height: 30% to 150% of the base diameter.
        height = span(rng, max(3.0, math.ceil(0.3 * base)), math.ceil(1.5 * base), 1.0)
        params = {"base_diameter": base, "top_diameter": top, "height": height}
        code = program(
            params,
            """
profile = (
    cq.Workplane("XZ")
    .moveTo(0, 0)
    .lineTo(base_diameter / 2, 0)
    .lineTo(top_diameter / 2, height)
    .lineTo(0, height)
    .close()
)
result = profile.revolve(360, (0, 0, 0), (0, 1, 0))
""",
        )
        return Part(self.NAME, params, code, [base, base, height],
                    frustum_volume(base, top, height))


class Knob:
    NAME = "knob"
    DESCRIPTION = "Knob: a round grip with a chamfered top on a narrower neck, with a blind bore"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        grip = grid(rng, 16.0, 60.0, 1.0)
        # The neck is 40% to 70% of the grip, so the grip clearly overhangs it.
        neck = span(rng, math.ceil(0.4 * grip), math.floor(0.7 * grip), 1.0)
        neck_height = span(rng, 3.0, math.floor(0.4 * grip), 1.0)
        # The grip is a thick disc: 25% to 60% of its diameter.
        grip_height = span(rng, max(5.0, math.ceil(0.25 * grip)), math.floor(0.6 * grip), 1.0)
        # The chamfer softens the top edge: at most a quarter of the grip height.
        chamfer = span(rng, 0.5, min(3.0, grip_height / 4), 0.5)
        bore = span(rng, 3.0, neck - 2 * MIN_WALL, 0.5)
        # The bore for the shaft stops short of the top face.
        bore_depth = span(rng, 4.0, neck_height + grip_height - MIN_WALL, 1.0)
        params = {"grip_diameter": grip, "grip_height": grip_height, "chamfer": chamfer,
                  "neck_diameter": neck, "neck_height": neck_height,
                  "bore_diameter": bore, "bore_depth": bore_depth}
        code = program(
            params,
            """
neck = cq.Workplane("XY").circle(neck_diameter / 2).extrude(neck_height)
grip = (
    cq.Workplane("XY")
    .workplane(offset=neck_height)
    .circle(grip_diameter / 2)
    .extrude(grip_height)
    .faces(">Z")
    .edges()
    .chamfer(chamfer)
)
bore = cq.Workplane("XY").circle(bore_diameter / 2).extrude(bore_depth)
result = neck.union(grip).cut(bore)
""",
        )
        volume = (circle_area(neck) * neck_height
                  + circle_area(grip) * (grip_height - chamfer)
                  + frustum_volume(grip, grip - 2 * chamfer, chamfer)
                  - circle_area(bore) * bore_depth)
        return Part(self.NAME, params, code, [grip, grip, neck_height + grip_height], volume,
                    {grip: 1, neck: 1, bore: 1})


class SteppedSleeve:
    NAME = "stepped_sleeve"
    DESCRIPTION = "Stepped sleeve: a hollow shaft with two or three outer diameters and one through bore"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        steps = rng.randint(2, 3)
        bore = grid(rng, 4.0, 30.0, 1.0)
        diameters: list[float] = []
        while len(diameters) < steps:
            # Every section keeps a wall of 2 to 15 mm around the bore.
            d = bore + 2 * grid(rng, 2.0, 15.0, 0.5)
            # Neighbouring sections must differ, or they would merge into one.
            if not diameters or abs(d - diameters[-1]) >= 2.0:
                diameters.append(d)
        lengths = [grid(rng, 4.0, 50.0, 1.0) for _ in range(steps)]

        params: dict[str, float | int] = {"bore_diameter": bore}
        for i, (d, length) in enumerate(zip(diameters, lengths, strict=True), start=1):
            params[f"step_{i}_diameter"] = d
            params[f"step_{i}_length"] = length

        # Walk up the outside from the bore: out to each radius, up by that section's
        # length, and finally back in to the bore, which closes the outline.
        outline = ["    .moveTo(bore_diameter / 2, 0)"]
        height_so_far = "0"
        for i in range(1, steps + 1):
            top = (f"step_{i}_length" if height_so_far == "0"
                   else f"{height_so_far} + step_{i}_length")
            outline.append(f"    .lineTo(step_{i}_diameter / 2, {height_so_far})")
            outline.append(f"    .lineTo(step_{i}_diameter / 2, {top})")
            height_so_far = top
        outline.append(f"    .lineTo(bore_diameter / 2, {height_so_far})")
        body = ("profile = (\n    cq.Workplane(\"XZ\")\n" + "\n".join(outline) + "\n    .close()\n)\n"
                "result = profile.revolve(360, (0, 0, 0), (0, 1, 0))")
        code = program(params, body)

        widest = max(diameters)
        total = sum(lengths)
        volume = (sum(circle_area(d) * length
                      for d, length in zip(diameters, lengths, strict=True))
                  - circle_area(bore) * total)
        cylinders: dict[float, int | None] = {bore: 1}
        for d in diameters:
            cylinders[d] = None  # a diameter can repeat on non-neighbouring sections
        return Part(self.NAME, params, code, [widest, widest, total], volume, cylinders)


class DowelPin:
    NAME = "dowel_pin"
    DESCRIPTION = "Dowel pin: a plain round pin with a small chamfer at each end"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        diameter = grid(rng, 2.0, 20.0, 0.5)
        # Pins are 2 to 6 diameters long.
        length = span(rng, math.ceil(2 * diameter), math.ceil(6 * diameter), 1.0)
        # A lead-in chamfer: 0.2 mm up to 15% of the diameter.
        chamfer = span(rng, 0.2, 0.15 * diameter, 0.1)
        params = {"diameter": diameter, "length": length, "chamfer": chamfer}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .circle(diameter / 2)
    .extrude(length)
    .edges("%CIRCLE")
    .chamfer(chamfer)
)
""",
        )
        volume = (circle_area(diameter) * (length - 2 * chamfer)
                  + 2 * frustum_volume(diameter, diameter - 2 * chamfer, chamfer))
        return Part(self.NAME, params, code, [diameter, diameter, length], volume,
                    {diameter: 1})


class Cup:
    NAME = "cup"
    DESCRIPTION = "Cup: a hollow cylinder closed at the bottom, wall and floor of equal thickness"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        outer = grid(rng, 20.0, 120.0, 1.0)
        # Thin wall: at most a tenth of the diameter.
        wall = span(rng, 1.0, min(5.0, outer / 10), 0.5)
        # Height: 40% to 150% of the diameter.
        height = span(rng, math.ceil(0.4 * outer), math.ceil(1.5 * outer), 1.0)
        params = {"outer_diameter": outer, "height": height, "wall_thickness": wall}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .circle(outer_diameter / 2)
    .extrude(height)
    .faces(">Z")
    .shell(-wall_thickness)
)
""",
        )
        inner = outer - 2 * wall
        volume = circle_area(outer) * height - circle_area(inner) * (height - wall)
        return Part(self.NAME, params, code, [outer, outer, height], volume,
                    {outer: 1, inner: 1})


class DShaft:
    NAME = "d_shaft"
    DESCRIPTION = "D-shaft: a round shaft with a flat milled along one end"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        diameter = grid(rng, 6.0, 50.0, 1.0)
        # Shafts are 2 to 6 diameters long.
        length = span(rng, ceil_to(2 * diameter, 5), ceil_to(6 * diameter, 5), 5.0)
        # The flat is 8% to 25% of the diameter deep: enough for a set screw to seat on.
        flat_depth = span(rng, max(0.5, ceil_to(0.08 * diameter, 0.5)), diameter / 4, 0.5)
        # The flat covers 15% to 60% of the length, measured from the top end.
        flat_length = span(rng, max(5.0, math.ceil(0.15 * length)), math.floor(0.6 * length), 1.0)
        params = {"diameter": diameter, "length": length,
                  "flat_depth": flat_depth, "flat_length": flat_length}
        code = program(
            params,
            """
shaft = cq.Workplane("XY").circle(diameter / 2).extrude(length)
flat_cut = (
    cq.Workplane("XY")
    .box(diameter, diameter, flat_length, centered=(False, True, False))
    .translate((diameter / 2 - flat_depth, 0, length - flat_length))
)
result = shaft.cut(flat_cut)
""",
        )
        volume = (circle_area(diameter) * length
                  - circle_segment_area(diameter, flat_depth) * flat_length)
        # The flat covers only part of the length, so the full diameter remains below it.
        return Part(self.NAME, params, code, [diameter, diameter, length], volume,
                    {diameter: None})


class EccentricCam:
    NAME = "eccentric_cam"
    DESCRIPTION = "Eccentric cam: a round disc with its bore set off centre"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        outer = grid(rng, 20.0, 100.0, 1.0)
        # The bore is 12% of the disc or more (a shaft that can drive it) and at most a
        # third, which leaves room to offset it.
        bore = span(rng, max(5.0, math.ceil(0.12 * outer)), math.floor(outer / 3), 1.0)
        # A cam is a thick disc: 10% to 30% of its diameter.
        thickness = span(rng, max(4.0, math.ceil(0.1 * outer)), math.floor(0.3 * outer), 1.0)
        # The offset keeps at least 3 mm of material between the bore and the rim.
        offset = span(rng, 1.0, outer / 2 - bore / 2 - 3.0, 0.5)
        params = {"outer_diameter": outer, "thickness": thickness,
                  "bore_diameter": bore, "bore_offset": offset}
        code = program(
            params,
            """
disc = cq.Workplane("XY").circle(outer_diameter / 2).extrude(thickness)
bore = cq.Workplane("XY").center(bore_offset, 0).circle(bore_diameter / 2).extrude(thickness)
result = disc.cut(bore)
""",
        )
        volume = (circle_area(outer) - circle_area(bore)) * thickness
        return Part(self.NAME, params, code, [outer, outer, thickness], volume,
                    count_cylinders((outer, 1), (bore, 1)))


TURNED_FAMILIES = (
    FlangedBushing(), ShaftCollar(), VPulley(), FlangedHub(), CountersunkWasher(),
    ConeFrustum(), Knob(), SteppedSleeve(), DowelPin(), Cup(), DShaft(), EccentricCam(),
)
