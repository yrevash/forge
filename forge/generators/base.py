"""Shared pieces for part-family generators.

A family turns a set of parameters into one `Part`: the CadQuery program text
plus what that program MUST measure as (bounding box, volume, round features).
The expected values come from the family's own arithmetic, never from running
the program, so comparing them with the kernel's measurement is an independent
check that the program builds the intended part.

Conventions every family follows (so parts can be placed in assemblies later):
- units are millimetres;
- the part sits on the XY plane (z starts at 0) and grows towards +Z;
- the program names every dimension and ends with `result`.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from typing import Protocol

# How closely the kernel's measurement must match the family's arithmetic.
BBOX_TOLERANCE_MM = 1e-3
VOLUME_RELATIVE_TOLERANCE = 1e-6

HEX_CORNER_FACTOR = 2.0 / math.sqrt(3.0)  # across corners = across flats * this
HEX_AREA_FACTOR = math.sqrt(3.0) / 2.0     # hexagon area = across_flats**2 * this


def circle_area(diameter: float) -> float:
    return math.pi * diameter * diameter / 4.0


def num(value: float) -> str:
    """Write a number the way a careful person would in code: 13.0, 6.8, 0.35."""
    return repr(round(float(value), 4))


def program(parameters: dict[str, float | int], body: str) -> str:
    """Assemble a canonical program: import, named parameters, body ending in `result`."""
    lines = ["import cadquery as cq", ""]
    for name, value in parameters.items():
        lines.append(f"{name} = {value if isinstance(value, int) else num(value)}")
    lines += ["", body.strip(), ""]
    return "\n".join(lines)


def grid(rng: random.Random, low: float, high: float, step: float) -> float:
    """A random value on a regular grid, e.g. 2.0 to 20.0 in steps of 0.5."""
    return round(low + step * rng.randint(0, round((high - low) / step)), 4)


@dataclass
class Part:
    family: str
    params: dict[str, float | int]
    code: str
    expected_bbox: list[float]            # extents along X, Y, Z
    expected_volume: float
    # Diameters of round features that must exist; value is the exact number of
    # cylindrical faces expected at that diameter, or None for "at least one".
    expected_cylinders: dict[float, int | None] = field(default_factory=dict)
    designation: dict[str, str] | None = None  # e.g. {"standard": "iso4032", "size": "M8"}

    @property
    def id(self) -> str:
        key = json.dumps([self.family, self.designation, self.params], sort_keys=True)
        return hashlib.sha1(key.encode()).hexdigest()[:16]


class Family(Protocol):
    NAME: str
    DESCRIPTION: str
    TABLE: str | None  # vendored standards table this family reads, if any

    def standard_parts(self) -> list[Part]:
        """Every part the standards table defines. Empty for free-parameter families."""

    def sample(self, rng: random.Random) -> Part:
        """One random valid part."""


def check(part: Part, measure: dict) -> list[str]:
    """Compare the kernel's measurement with the family's arithmetic.

    Returns the list of problems; an empty list means the part is verified.
    """
    problems = []
    if not measure["one_valid_solid"]:
        problems.append(f"not one valid solid (solids={measure['n_solids']})")
    for axis, got, want in zip("XYZ", measure["bbox"], part.expected_bbox, strict=True):
        if abs(got - want) > BBOX_TOLERANCE_MM:
            problems.append(f"bbox {axis}: measured {got:.5f}, expected {want:.5f}")
    if min(measure["bbox_min"][2], 0.0) < -BBOX_TOLERANCE_MM or measure["bbox_min"][2] > BBOX_TOLERANCE_MM:
        problems.append(f"part does not sit on the XY plane (z min {measure['bbox_min'][2]:.5f})")
    want_v = part.expected_volume
    if abs(measure["volume"] - want_v) > VOLUME_RELATIVE_TOLERANCE * want_v:
        problems.append(f"volume: measured {measure['volume']:.6f}, expected {want_v:.6f}")
    for diameter, count in part.expected_cylinders.items():
        got = measure["cylinders"].get(f"{diameter:.3f}", 0)
        if got == 0 or (count is not None and got != count):
            want = "at least 1" if count is None else count
            problems.append(f"round feature Ø{diameter}: found {got} faces, expected {want}")
    return problems
