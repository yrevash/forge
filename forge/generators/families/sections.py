"""More bar sections: a cross-section drawn once and extruded to a length.

These join the U-channel, T-section and I-beam in shapes.py. New here: a hollow
section with rounded corners (two filleted solids, one cut from the other), an
angle, a Z and a top-hat (drawn as half an outline and mirrored).

Every range below is our own design choice, not a standard: no table of rolled
section sizes is vendored, so these are not catalogue sections.
"""

from __future__ import annotations

import math
import random

from forge.generators.base import Part, grid, program
from forge.generators.families._common import no_standard_parts, section_length, span


def _thin_wall(rng: random.Random, smallest_side: float, fraction: float = 6.0) -> float:
    """Wall of a rolled or folded section: 1.5 mm up to a sixth of its smallest side."""
    return span(rng, 1.5, smallest_side / fraction, 0.5)


class RectangularTube:
    NAME = "rectangular_tube"
    DESCRIPTION = "Rectangular tube: a hollow box section with rounded corners"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        width = grid(rng, 15.0, 120.0, 1.0)
        height = span(rng, math.ceil(0.4 * width), width, 1.0)
        # Thin wall: at most an eighth of the shorter side.
        wall = _thin_wall(rng, height, 8.0)
        # Formed hollow sections have an outside corner radius of 1.5 to 2.5 wall
        # thicknesses; the inside corner follows it, one wall thickness smaller.
        radius = wall * rng.choice([1.5, 2.0, 2.5])
        length = section_length(rng, width)
        params = {"width": width, "height": height, "wall_thickness": wall,
                  "corner_radius": radius, "length": length}
        code = program(
            params,
            """
outer = (
    cq.Workplane("XY")
    .rect(width, height)
    .extrude(length)
    .edges("|Z")
    .fillet(corner_radius)
)
inner = (
    cq.Workplane("XY")
    .rect(width - 2 * wall_thickness, height - 2 * wall_thickness)
    .extrude(length)
    .edges("|Z")
    .fillet(corner_radius - wall_thickness)
)
result = outer.cut(inner)
""",
        )
        inner_radius = radius - wall
        # A rectangle with rounded corners loses (4 - pi) * r^2 to the four corners.
        outer_area = width * height - (4 - math.pi) * radius**2
        inner_area = ((width - 2 * wall) * (height - 2 * wall)
                      - (4 - math.pi) * inner_radius**2)
        return Part(self.NAME, params, code, [width, height, length],
                    (outer_area - inner_area) * length,
                    {2 * radius: 4, 2 * inner_radius: 4})


class AngleSection:
    NAME = "angle_section"
    DESCRIPTION = "Angle section: an L-shaped bar with two legs at a right angle"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        leg_a = grid(rng, 15.0, 100.0, 1.0)
        # Equal or unequal legs; the shorter is at least half the longer.
        leg_b = span(rng, math.ceil(0.5 * leg_a), leg_a, 1.0)
        thickness = _thin_wall(rng, leg_b)
        length = section_length(rng, leg_a)
        params = {"leg_a": leg_a, "leg_b": leg_b, "thickness": thickness, "length": length}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .polyline([
        (0, 0),
        (leg_a, 0),
        (leg_a, thickness),
        (thickness, thickness),
        (thickness, leg_b),
        (0, leg_b),
    ])
    .close()
    .extrude(length)
)
""",
        )
        area = thickness * (leg_a + leg_b - thickness)
        return Part(self.NAME, params, code, [leg_a, leg_b, length], area * length)


class ZSection:
    NAME = "z_section"
    DESCRIPTION = "Z-section: a web with a flange at each end, pointing opposite ways"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        height = grid(rng, 20.0, 120.0, 1.0)
        # Each flange is 30% to 80% of the web height.
        flange = span(rng, math.ceil(0.3 * height), math.ceil(0.8 * height), 1.0)
        thickness = _thin_wall(rng, min(height, flange))
        length = section_length(rng, max(height, flange))
        params = {"height": height, "flange_width": flange, "thickness": thickness,
                  "length": length}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .polyline([
        (0, 0),
        (flange_width, 0),
        (flange_width, thickness),
        (thickness, thickness),
        (thickness, height),
        (thickness - flange_width, height),
        (thickness - flange_width, height - thickness),
        (0, height - thickness),
    ])
    .close()
    .extrude(length)
)
""",
        )
        # Two flanges plus the web between them.
        area = 2 * flange * thickness + (height - 2 * thickness) * thickness
        return Part(self.NAME, params, code, [2 * flange - thickness, height, length],
                    area * length)


class HatSection:
    NAME = "hat_section"
    DESCRIPTION = "Hat section: a top-hat profile with a crown, two walls and two outward brims"
    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def sample(self, rng: random.Random) -> Part:
        crown = grid(rng, 20.0, 100.0, 1.0)
        height = span(rng, math.ceil(0.4 * crown), crown, 1.0)
        # Each brim is 20% to 60% of the crown width.
        brim = span(rng, max(6.0, math.ceil(0.2 * crown)), math.ceil(0.6 * crown), 1.0)
        # Folded sheet: thin (6 mm at most), and never more than half a brim.
        thickness = span(rng, 1.5, min(6.0, height / 6, brim / 2), 0.5)
        length = section_length(rng, max(crown, height))
        params = {"crown_width": crown, "height": height, "brim_width": brim,
                  "thickness": thickness, "length": length}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .polyline([
        (0, height - thickness),
        (crown_width / 2 - thickness, height - thickness),
        (crown_width / 2 - thickness, 0),
        (crown_width / 2 + brim_width, 0),
        (crown_width / 2 + brim_width, thickness),
        (crown_width / 2, thickness),
        (crown_width / 2, height),
        (0, height),
    ])
    .mirrorY()
    .extrude(length)
)
""",
        )
        # Crown, two walls below it, and two brims beside them.
        area = (crown * thickness + 2 * thickness * (height - thickness)
                + 2 * brim * thickness)
        return Part(self.NAME, params, code, [crown + 2 * brim, height, length], area * length)


SECTION_FAMILIES = (RectangularTube(), AngleSection(), ZSection(), HatSection())
