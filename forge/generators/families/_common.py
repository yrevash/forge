"""Small helpers shared by the family files added after the first 22 families.

Nothing here builds geometry. These are the sampling and arithmetic pieces that
several families need, kept in one place so each family stays short.
"""

from __future__ import annotations

import math
import random

from forge.generators.base import Part, grid

MIN_WALL = 2.0  # mm of material kept between any feature and any edge or other feature


def no_standard_parts() -> list[Part]:
    """For families whose sizes are our own ranges, not a standards table."""
    return []


def span(rng: random.Random, low: float, high: float, step: float) -> float:
    """A grid value from `low` up to at most `high`.

    `grid` rounds its top end to the nearest step, which can land above `high`
    when `high` is not on the grid. Here the top end is rounded DOWN, so a limit
    such as "at most a quarter of the width" is never exceeded. If `high` is
    below `low` the answer is `low`.
    """
    steps = max(0, math.floor((high - low) / step + 1e-9))
    return grid(rng, low, low + step * steps, step)


def ceil_to(value: float, step: float) -> float:
    """Round up to a multiple of `step`: ceil_to(23, 5) -> 25.0."""
    return float(math.ceil(value / step - 1e-9) * step)


def floor_to(value: float, step: float) -> float:
    """Round down to a multiple of `step`: floor_to(2.7, 0.5) -> 2.5."""
    return float(math.floor(value / step + 1e-9) * step)


def section_length(rng: random.Random, across: float) -> float:
    """A bar of section is cut longer than it is wide: 1.5 to 6 times its largest side."""
    return grid(rng, ceil_to(1.5 * across, 5), ceil_to(6 * across, 5), 5.0)


def frustum_volume(diameter_a: float, diameter_b: float, height: float) -> float:
    """Volume of a cone frustum between two end diameters.

    V = pi * h / 12 * (a^2 + a*b + b^2). With a == b it is a plain cylinder.
    """
    return math.pi * height / 12.0 * (diameter_a**2 + diameter_a * diameter_b + diameter_b**2)


def circle_strip_area(diameter: float, strip_width: float) -> float:
    """Area of a circle that lies inside a centred strip of the given width.

    This is the integral of the chord length across the strip:
    2 * [ (w/2) * sqrt(r^2 - (w/2)^2) + r^2 * asin(w / 2r) ].
    Used for a screwdriver slot across a round head and for a keyway in a bore.
    """
    r, half = diameter / 2.0, strip_width / 2.0
    return 2.0 * (half * math.sqrt(r * r - half * half) + r * r * math.asin(half / r))


def circle_segment_area(diameter: float, depth: float) -> float:
    """Area cut off a circle by a flat that is `depth` deep (a D-shaped shaft's flat).

    A = r^2 * acos((r - d) / r) - (r - d) * sqrt(2*r*d - d^2).
    """
    r = diameter / 2.0
    return r * r * math.acos((r - depth) / r) - (r - depth) * math.sqrt(2 * r * depth - depth**2)


def count_cylinders(*features: tuple[float, int | None]) -> dict[float, int | None]:
    """Build `expected_cylinders` from (diameter, face count) pairs.

    Families keep their round features at different diameters. If two features
    still land on one diameter, their counts are added, so the check stays exact.
    """
    counts: dict[float, int | None] = {}
    for diameter, count in features:
        if diameter in counts and counts[diameter] is not None and count is not None:
            counts[diameter] += count
        elif diameter in counts:
            counts[diameter] = None
        else:
            counts[diameter] = count
    return counts
