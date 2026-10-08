"""General machined shapes, built the way a designer builds them: sketch, then extrude,
cut, revolve, fillet, chamfer, shell or loft.

Where the ideas come from. Published text-to-CAD work describes parts as short
sequences of sketches and extrusions (DeepCAD, Text2CAD), generates programs
procedurally at scale (CAD-Recode), and stresses coverage of operations beyond
extrude (Zero-to-CAD). These families take those ideas only. No program, prompt
or shape from the non-commercial datasets was looked at or copied; every range
below is our own choice.

Each family keeps its features apart from each other (no overlapping cuts or
bosses), so its volume and bounding box can be worked out exactly by arithmetic
and compared with what the kernel measures.
"""

from __future__ import annotations

import math
import random

from forge.generators.base import Part, circle_area, grid, program

MIN_WALL = 2.0  # mm of material kept between any feature and any edge or other feature


def _no_standard_parts() -> list[Part]:
    return []


def _section_length(rng: random.Random, across: float) -> float:
    """A bar of section is cut longer than it is wide: 1.5 to 6 times its largest side."""
    return grid(rng, math.ceil(1.5 * across / 5) * 5, math.ceil(6 * across / 5) * 5, 5.0)


class MountingPlate:
    NAME = "mounting_plate"
    DESCRIPTION = "Plate with rounded corners and a hole near each corner"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 10.0, 0.5)
        inset = math.ceil(hole / 2 + MIN_WALL) + grid(rng, 0.0, 8.0, 1.0)
        # Corner holes sit well apart: at least three hole diameters between centres.
        shortest = math.ceil((2 * inset + 3 * hole) / 5) * 5
        width = shortest + grid(rng, 0.0, 80.0, 5.0)
        length = width + grid(rng, 0.0, 100.0, 5.0)
        thickness = grid(rng, 2.0, 12.0, 0.5)
        while True:
            radius = grid(rng, 1.0, inset, 0.5)
            if 2 * radius != hole:  # keeps corner arcs and holes distinguishable when measured
                break
        params = {"length": length, "width": width, "thickness": thickness,
                  "corner_radius": radius, "hole_diameter": hole, "hole_inset": inset}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .box(length, width, thickness, centered=(True, True, False))
    .edges("|Z")
    .fillet(corner_radius)
    .faces(">Z")
    .workplane()
    .rect(length - 2 * hole_inset, width - 2 * hole_inset, forConstruction=True)
    .vertices()
    .hole(hole_diameter)
)
""",
        )
        area = length * width - (4 - math.pi) * radius**2 - 4 * circle_area(hole)
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {hole: 4, 2 * radius: 4})


class PolygonPrism:
    NAME = "polygon_prism"
    DESCRIPTION = "Regular polygon bar: a prism with 3, 5, 6 or 8 sides"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        sides = rng.choice([3, 5, 6, 8])
        diameter = grid(rng, 6.0, 80.0, 1.0)  # across corners
        height = grid(rng, 2.0, 120.0, 1.0)
        params = {"sides": sides, "corner_diameter": diameter, "height": height}
        code = program(
            params,
            """
result = cq.Workplane("XY").polygon(sides, corner_diameter).extrude(height)
""",
        )
        radius = diameter / 2
        # CadQuery puts the first corner on +X and spaces the rest evenly.
        xs = [radius * math.cos(2 * math.pi * k / sides) for k in range(sides)]
        ys = [radius * math.sin(2 * math.pi * k / sides) for k in range(sides)]
        area = sides / 2 * radius**2 * math.sin(2 * math.pi / sides)
        return Part(self.NAME, params, code,
                    [max(xs) - min(xs), max(ys) - min(ys), height], area * height)


class SlottedLink:
    NAME = "slotted_link"
    DESCRIPTION = "Flat link with rounded ends and a hole at each end"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 16.0, 0.5)
        width = math.ceil(hole + 2 * MIN_WALL) + grid(rng, 0.0, 16.0, 1.0)
        centre_distance = math.ceil(hole + MIN_WALL) + grid(rng, 5.0, 120.0, 1.0)
        thickness = grid(rng, 2.0, 12.0, 0.5)
        length = centre_distance + width
        params = {"centre_distance": centre_distance, "width": width, "thickness": thickness,
                  "hole_diameter": hole}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .slot2D(centre_distance + width, width)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .pushPoints([(-centre_distance / 2, 0), (centre_distance / 2, 0)])
    .hole(hole_diameter)
)
""",
        )
        area = centre_distance * width + circle_area(width) - 2 * circle_area(hole)
        return Part(self.NAME, params, code, [length, width, thickness], area * thickness,
                    {hole: 2, width: 2})


class UChannel:
    NAME = "u_channel"
    DESCRIPTION = "U-channel: a length of channel section with a flat web and two flanges"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        width = grid(rng, 15.0, 100.0, 1.0)
        depth = grid(rng, max(8.0, math.ceil(0.3 * width)), math.ceil(1.2 * width), 1.0)
        # Wall: thin relative to the section, as in rolled or folded channel.
        wall = grid(rng, 1.5, max(1.5, math.floor(min(width, depth) / 6 * 2) / 2), 0.5)
        length = _section_length(rng, max(width, depth))
        params = {"width": width, "depth": depth, "wall_thickness": wall, "length": length}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .polyline([
        (-width / 2, 0),
        (width / 2, 0),
        (width / 2, depth),
        (width / 2 - wall_thickness, depth),
        (width / 2 - wall_thickness, wall_thickness),
        (-width / 2 + wall_thickness, wall_thickness),
        (-width / 2 + wall_thickness, depth),
        (-width / 2, depth),
    ])
    .close()
    .extrude(length)
)
""",
        )
        area = width * depth - (width - 2 * wall) * (depth - wall)
        return Part(self.NAME, params, code, [width, depth, length], area * length)


class TSection:
    NAME = "t_section"
    DESCRIPTION = "T-section bar: a flange across the top of a centred web"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        height = grid(rng, 15.0, 100.0, 1.0)
        width = grid(rng, math.ceil(0.5 * height), math.ceil(1.2 * height), 1.0)
        thickest = max(1.5, math.floor(min(width, height) / 6 * 2) / 2)
        flange = grid(rng, 1.5, thickest, 0.5)
        web = grid(rng, 1.5, thickest, 0.5)
        length = _section_length(rng, max(width, height))
        params = {"width": width, "height": height, "flange_thickness": flange,
                  "web_thickness": web, "length": length}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .polyline([
        (-web_thickness / 2, 0),
        (web_thickness / 2, 0),
        (web_thickness / 2, height - flange_thickness),
        (width / 2, height - flange_thickness),
        (width / 2, height),
        (-width / 2, height),
        (-width / 2, height - flange_thickness),
        (-web_thickness / 2, height - flange_thickness),
    ])
    .close()
    .extrude(length)
)
""",
        )
        area = width * flange + web * (height - flange)
        return Part(self.NAME, params, code, [width, height, length], area * length)


class IBeam:
    NAME = "i_beam"
    DESCRIPTION = "I-beam: two flanges joined by a centred web"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        height = grid(rng, 20.0, 120.0, 1.0)
        width = grid(rng, math.ceil(0.4 * height), height, 1.0)
        thickest = max(1.5, math.floor(min(width, height) / 8 * 2) / 2)
        flange = grid(rng, 1.5, thickest, 0.5)
        web = grid(rng, 1.5, thickest, 0.5)
        length = _section_length(rng, max(width, height))
        params = {"width": width, "height": height, "flange_thickness": flange,
                  "web_thickness": web, "length": length}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .polyline([
        (-width / 2, 0),
        (width / 2, 0),
        (width / 2, flange_thickness),
        (web_thickness / 2, flange_thickness),
        (web_thickness / 2, height - flange_thickness),
        (width / 2, height - flange_thickness),
        (width / 2, height),
        (-width / 2, height),
        (-width / 2, height - flange_thickness),
        (-web_thickness / 2, height - flange_thickness),
        (-web_thickness / 2, flange_thickness),
        (-width / 2, flange_thickness),
    ])
    .close()
    .extrude(length)
)
""",
        )
        area = 2 * width * flange + web * (height - 2 * flange)
        return Part(self.NAME, params, code, [width, height, length], area * length)


class SteppedShaft:
    NAME = "stepped_shaft"
    DESCRIPTION = "Stepped shaft: two to four round sections of different diameter, made by revolving"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        steps = rng.randint(2, 4)
        diameters: list[float] = []
        while len(diameters) < steps:
            d = grid(rng, 6.0, 80.0, 1.0)
            # Neighbouring sections must differ, or they would merge into one.
            if not diameters or abs(d - diameters[-1]) >= 2.0:
                diameters.append(d)
        lengths = [grid(rng, 3.0, 60.0, 1.0) for _ in range(steps)]

        params: dict[str, float | int] = {}
        for i, (d, length) in enumerate(zip(diameters, lengths, strict=True), start=1):
            params[f"step_{i}_diameter"] = d
            params[f"step_{i}_length"] = length

        # Walk up the outline: out to each radius, then up by that section's length.
        outline = ["    .moveTo(0, 0)"]
        height_so_far = "0"
        for i in range(1, steps + 1):
            top = (f"step_{i}_length" if height_so_far == "0"
                   else f"{height_so_far} + step_{i}_length")
            outline.append(f"    .lineTo(step_{i}_diameter / 2, {height_so_far})")
            outline.append(f"    .lineTo(step_{i}_diameter / 2, {top})")
            height_so_far = top
        outline.append(f"    .lineTo(0, {height_so_far})")
        body = ("profile = (\n    cq.Workplane(\"XZ\")\n" + "\n".join(outline) + "\n    .close()\n)\n"
                "result = profile.revolve(360, (0, 0, 0), (0, 1, 0))")
        code = program(params, body)

        widest = max(diameters)
        volume = sum(circle_area(d) * length
                     for d, length in zip(diameters, lengths, strict=True))
        cylinders: dict[float, int | None] = {}
        for d in diameters:
            cylinders[d] = None  # a diameter can repeat on non-neighbouring sections
        return Part(self.NAME, params, code, [widest, widest, sum(lengths)], volume, cylinders)


class BlockWithFeatures:
    NAME = "block_with_features"
    DESCRIPTION = "Block with up to three features: a round boss, a through hole, a rectangular pocket"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        length = grid(rng, 40.0, 160.0, 5.0)
        width = grid(rng, 40.0, 120.0, 5.0)
        height = grid(rng, 8.0, 40.0, 1.0)
        kinds = rng.sample(["pocket", "hole", "boss"], k=rng.randint(1, 3))
        # Each feature gets its own quarter of the top face, so features never touch.
        quarters = rng.sample([(-1, -1), (1, -1), (-1, 1), (1, 1)], k=len(kinds))
        room = min(length, width) / 2 - 2 * MIN_WALL  # largest feature a quarter can hold

        params: dict[str, float | int] = {"length": length, "width": width, "height": height}
        # Each feature is drawn as its own solid at an absolute position and then
        # cut from or joined to the block. Chaining them on the block's top face
        # instead would make each feature's position depend on the one before it.
        steps = [("block = cq.Workplane(\"XY\")"
                  ".box(length, width, height, centered=(True, True, False))")]
        combine = "block"
        volume = length * width * height
        top = height
        cylinders: dict[float, int | None] = {}

        for kind in ("pocket", "hole", "boss"):
            if kind not in kinds:
                continue
            sx, sy = quarters[kinds.index(kind)]
            x, y = sx * length / 4, sy * width / 4
            params[f"{kind}_x"], params[f"{kind}_y"] = x, y
            if kind == "pocket":
                pl = grid(rng, 6.0, min(room, length / 2 - 2 * MIN_WALL), 1.0)
                pw = grid(rng, 6.0, min(room, width / 2 - 2 * MIN_WALL), 1.0)
                depth = grid(rng, 2.0, height - MIN_WALL, 1.0)
                params.update(pocket_length=pl, pocket_width=pw, pocket_depth=depth)
                steps.append('pocket = (\n    cq.Workplane("XY")\n'
                             "    .workplane(offset=height - pocket_depth)\n"
                             "    .center(pocket_x, pocket_y)\n"
                             "    .rect(pocket_length, pocket_width)\n"
                             "    .extrude(pocket_depth)\n)")
                combine += ".cut(pocket)"
                volume -= pl * pw * depth
            elif kind == "hole":
                d = grid(rng, 4.0, room, 0.5)
                params["hole_diameter"] = d
                steps.append('hole = (\n    cq.Workplane("XY")\n    .center(hole_x, hole_y)\n'
                             "    .circle(hole_diameter / 2)\n    .extrude(height)\n)")
                combine += ".cut(hole)"
                volume -= circle_area(d) * height
                cylinders[d] = 1
            else:
                while True:
                    d = grid(rng, 6.0, room, 1.0)
                    if d != params.get("hole_diameter"):
                        break
                h = grid(rng, 2.0, 30.0, 1.0)
                params.update(boss_diameter=d, boss_height=h)
                steps.append('boss = (\n    cq.Workplane("XY")\n    .workplane(offset=height)\n'
                             "    .center(boss_x, boss_y)\n    .circle(boss_diameter / 2)\n"
                             "    .extrude(boss_height)\n)")
                combine += ".union(boss)"
                top = height + h
                volume += circle_area(d) * h
                cylinders[d] = 1
        steps.append(f"result = {combine}")

        # Parameters read better grouped by feature than in the order they were drawn.
        ordered = {k: params[k] for k in ("length", "width", "height")}
        for kind in ("pocket", "hole", "boss"):
            ordered.update({k: v for k, v in params.items() if k.startswith(kind)})
        code = program(ordered, "\n".join(steps))
        return Part(self.NAME, ordered, code, [length, width, top], volume, cylinders)


class ChamferedBlock:
    NAME = "chamfered_block"
    DESCRIPTION = "Rectangular block with its top edges chamfered"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        length = grid(rng, 10.0, 150.0, 1.0)
        width = grid(rng, 10.0, 120.0, 1.0)
        height = grid(rng, 5.0, 60.0, 1.0)
        # A chamfer breaks an edge; it stays within a fifth of the smallest dimension.
        chamfer = grid(rng, 0.5, max(0.5, math.floor(min(length, width, height) / 5 * 2) / 2), 0.5)
        params = {"length": length, "width": width, "height": height, "chamfer": chamfer}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .box(length, width, height, centered=(True, True, False))
    .faces(">Z")
    .edges()
    .chamfer(chamfer)
)
""",
        )
        c = chamfer
        # Below the chamfer it is a plain box. The chamfered layer is a prismatoid:
        # volume = h/6 * (bottom area + top area + 4 * mid area).
        lower = length * width * (height - c)
        upper = c / 6 * (length * width + (length - 2 * c) * (width - 2 * c)
                         + 4 * (length - c) * (width - c))
        return Part(self.NAME, params, code, [length, width, height], lower + upper)


class OpenBox:
    NAME = "open_box"
    DESCRIPTION = "Open-top box: a rectangular tray with walls and a floor of equal thickness"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        wall = grid(rng, 1.0, 6.0, 0.5)
        # The inside is at least 20 mm across and 10 mm deep: a box, not a slit.
        length = math.ceil(2 * wall + 20) + grid(rng, 0.0, 160.0, 2.0)
        width = math.ceil(2 * wall + 20) + grid(rng, 0.0, 100.0, 2.0)
        height = math.ceil(wall + 10) + grid(rng, 0.0, 70.0, 1.0)
        params = {"length": length, "width": width, "height": height, "wall_thickness": wall}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .box(length, width, height, centered=(True, True, False))
    .faces(">Z")
    .shell(-wall_thickness)
)
""",
        )
        volume = (length * width * height
                  - (length - 2 * wall) * (width - 2 * wall) * (height - wall))
        return Part(self.NAME, params, code, [length, width, height], volume)


class TaperedBlock:
    NAME = "tapered_block"
    DESCRIPTION = "Tapered block: a rectangular base lofted to a smaller rectangular top"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        base_length = grid(rng, 20.0, 150.0, 1.0)
        base_width = grid(rng, 20.0, 120.0, 1.0)
        top_length = grid(rng, math.ceil(0.25 * base_length), base_length - 2.0, 1.0)
        top_width = grid(rng, math.ceil(0.25 * base_width), base_width - 2.0, 1.0)
        height = grid(rng, 5.0, 100.0, 1.0)
        params = {"base_length": base_length, "base_width": base_width,
                  "top_length": top_length, "top_width": top_width, "height": height}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .rect(base_length, base_width)
    .workplane(offset=height)
    .rect(top_length, top_width)
    .loft(ruled=True)
)
""",
        )
        mid = (base_length + top_length) / 2 * (base_width + top_width) / 2
        volume = height / 6 * (base_length * base_width + top_length * top_width + 4 * mid)
        return Part(self.NAME, params, code, [base_length, base_width, height], volume)


class CounterboredBlock:
    NAME = "counterbored_block"
    DESCRIPTION = "Block with a row of counterbored holes for socket head screws"
    TABLE = None
    standard_parts = staticmethod(_no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 12.0, 0.5)
        cbore = math.ceil(hole + 2.0) + grid(rng, 0.0, 6.0, 0.5)
        count = rng.randint(2, 6)
        spacing = math.ceil(cbore + MIN_WALL) + grid(rng, 0.0, 30.0, 1.0)
        margin = math.ceil(cbore / 2 + MIN_WALL) + grid(rng, 0.0, 10.0, 1.0)
        length = (count - 1) * spacing + 2 * margin
        width = math.ceil(cbore + 2 * MIN_WALL) + grid(rng, 0.0, 40.0, 1.0)
        height = grid(rng, 6.0, 40.0, 1.0)
        depth = grid(rng, 2.0, height - MIN_WALL, 0.5)
        params = {"length": length, "width": width, "height": height,
                  "hole_count": count, "hole_spacing": spacing, "hole_diameter": hole,
                  "counterbore_diameter": cbore, "counterbore_depth": depth}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .box(length, width, height, centered=(True, True, False))
    .faces(">Z")
    .workplane()
    .rarray(hole_spacing, 1, hole_count, 1)
    .cboreHole(hole_diameter, counterbore_diameter, counterbore_depth)
)
""",
        )
        removed = count * (circle_area(hole) * height
                           + (circle_area(cbore) - circle_area(hole)) * depth)
        return Part(self.NAME, params, code, [length, width, height],
                    length * width * height - removed, {hole: count, cbore: count})
