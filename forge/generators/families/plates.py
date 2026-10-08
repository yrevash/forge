"""Free-parameter families: no standard fixes their sizes, so we choose the ranges.

Every range below is our own design choice, not a standard. Each `sample` only
returns combinations that leave real material around every hole.
"""

from __future__ import annotations

import math
import random

from forge.generators.base import Part, circle_area, grid, program

MIN_WALL = 2.0  # mm of material we insist on between a hole and any edge or other hole


class Spacer:
    NAME = "spacer"
    DESCRIPTION = "Round spacer: a tube with a centre bore"
    TABLE = None

    def standard_parts(self) -> list[Part]:
        return []

    def sample(self, rng: random.Random) -> Part:
        inner = grid(rng, 2.0, 30.0, 0.5)
        outer = inner + 2 * grid(rng, 1.0, 10.0, 0.5)
        length = grid(rng, 2.0, 100.0, 0.5)
        params = {"outer_diameter": outer, "inner_diameter": inner, "length": length}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .circle(outer_diameter / 2)
    .circle(inner_diameter / 2)
    .extrude(length)
)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[outer, outer, length],
            expected_volume=(circle_area(outer) - circle_area(inner)) * length,
            expected_cylinders={inner: 1, outer: 1},
        )


class PlateWithHoles:
    NAME = "plate_with_holes"
    DESCRIPTION = "Rectangular plate with a rectangular grid of through holes"
    TABLE = None

    def standard_parts(self) -> list[Part]:
        return []

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 12.0, 0.5)
        nx, ny = rng.randint(2, 6), rng.randint(2, 4)
        # Spacing and edge margin are built up from the hole size so holes never
        # touch each other or the edge.
        spacing_x = math.ceil(hole + MIN_WALL) + grid(rng, 0.0, 30.0, 1.0)
        spacing_y = math.ceil(hole + MIN_WALL) + grid(rng, 0.0, 30.0, 1.0)
        margin_x = math.ceil(hole / 2 + MIN_WALL) + grid(rng, 0.0, 15.0, 0.5)
        margin_y = math.ceil(hole / 2 + MIN_WALL) + grid(rng, 0.0, 15.0, 0.5)
        length = (nx - 1) * spacing_x + 2 * margin_x
        width = (ny - 1) * spacing_y + 2 * margin_y
        thickness = grid(rng, 1.0, 20.0, 0.5)
        params = {
            "length": length, "width": width, "thickness": thickness,
            "hole_diameter": hole, "holes_x": nx, "holes_y": ny,
            "spacing_x": spacing_x, "spacing_y": spacing_y,
        }
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .box(length, width, thickness, centered=(True, True, False))
    .faces(">Z")
    .workplane()
    .rarray(spacing_x, spacing_y, holes_x, holes_y)
    .hole(hole_diameter)
)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[length, width, thickness],
            expected_volume=(length * width - nx * ny * circle_area(hole)) * thickness,
            expected_cylinders={hole: nx * ny},
        )


class RoundFlange:
    NAME = "round_flange"
    DESCRIPTION = "Round flange: a disc with a centre bore and a circle of bolt holes"
    TABLE = None

    def standard_parts(self) -> list[Part]:
        return []

    def sample(self, rng: random.Random) -> Part:
        while True:
            bore = grid(rng, 6.0, 60.0, 1.0)
            bolt_hole = grid(rng, 3.0, 14.0, 0.5)
            count = rng.choice([3, 4, 5, 6, 8, 10, 12])
            # The ring of material between the bore and the rim has to hold the
            # bolt hole with a wall on both sides; real flanges put the bolt
            # circle near the middle of that ring, so we do too.
            ring = math.ceil(bolt_hole + 2 * MIN_WALL) + grid(rng, 0.0, 20.0, 1.0)
            outer = bore + 2 * ring
            # float(): a diameter is a length, and lengths are floats everywhere
            # (whole numbers are reserved for counts).
            bolt_circle = float(round((bore + outer) / 2 + grid(rng, -1.0, 1.0, 1.0)
                                      * min(1.0, (ring - bolt_hole - 2 * MIN_WALL) / 2)))
            inner_wall = (bolt_circle - bolt_hole - bore) / 2
            outer_wall = (outer - bolt_circle - bolt_hole) / 2
            # Neighbouring bolt holes must not run into each other.
            gap = bolt_circle * math.sin(math.pi / count) - bolt_hole
            if (gap >= MIN_WALL and inner_wall >= MIN_WALL and outer_wall >= MIN_WALL
                    and bolt_hole != bore):
                break
        # Flanges are plates: thickness stays between 6% and 25% of the diameter.
        thickness = grid(rng, max(3.0, math.ceil(0.06 * outer)), max(4.0, math.floor(0.25 * outer)),
                         0.5)
        params = {
            "outer_diameter": outer, "bore_diameter": bore, "thickness": thickness,
            "bolt_circle_diameter": bolt_circle, "bolt_hole_count": count,
            "bolt_hole_diameter": bolt_hole,
        }
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .circle(outer_diameter / 2)
    .circle(bore_diameter / 2)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .polarArray(bolt_circle_diameter / 2, 0, 360, bolt_hole_count)
    .hole(bolt_hole_diameter)
)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[outer, outer, thickness],
            expected_volume=(circle_area(outer) - circle_area(bore)
                             - count * circle_area(bolt_hole)) * thickness,
            expected_cylinders={outer: 1, bore: 1, bolt_hole: count},
        )


class LBracket:
    NAME = "l_bracket"
    DESCRIPTION = "L-bracket: a base leg and an upright leg, each with one mounting hole"
    TABLE = None

    def standard_parts(self) -> list[Part]:
        return []

    def sample(self, rng: random.Random) -> Part:
        hole = grid(rng, 3.0, 12.0, 0.5)
        # Each leg must be long enough for its hole plus a wall on both sides.
        clear = math.ceil(hole + 2 * MIN_WALL)
        reach = clear + grid(rng, 4.0, 60.0, 1.0)       # base leg beyond the upright
        rise = clear + grid(rng, 4.0, 60.0, 1.0)        # upright above the base
        width = clear + grid(rng, 0.0, 50.0, 1.0)
        # Brackets are bent or cut from plate: thickness is at most a quarter of the shorter leg.
        thickness = grid(rng, 2.0, max(2.0, min(10.0, math.floor(min(reach, rise) / 4))), 0.5)
        base_length = thickness + reach
        height = thickness + rise
        params = {"base_length": base_length, "height": height, "width": width,
                  "thickness": thickness, "hole_diameter": hole}
        code = program(
            params,
            """
base = cq.Workplane("XY").box(base_length, width, thickness, centered=(False, True, False))
upright = cq.Workplane("XY").box(thickness, width, height, centered=(False, True, False))
base_hole = (
    cq.Workplane("XY")
    .center((base_length + thickness) / 2, 0)
    .circle(hole_diameter / 2)
    .extrude(thickness)
)
upright_hole = (
    cq.Workplane("YZ")
    .center(0, (height + thickness) / 2)
    .circle(hole_diameter / 2)
    .extrude(thickness)
)
result = base.union(upright).cut(base_hole).cut(upright_hole)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[base_length, width, height],
            expected_volume=(base_length * width * thickness
                             + thickness * width * (height - thickness)
                             - 2 * circle_area(hole) * thickness),
            expected_cylinders={hole: 2},
        )
