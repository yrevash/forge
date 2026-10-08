"""Shaded pictures of structures, so a person can look at them.

A picture is drawn from the structure's parts as numbers (solids.py), in this
process: nothing here runs program text. That the generated PROGRAM builds
these same parts is what check.py proves, part by part, in the sandbox.
"""

from __future__ import annotations

import math
from pathlib import Path

import cadquery as cq
from cadquery import vis

from forge.generators.structures.draft import Structure
from forge.generators.structures.solids import Solid, bounding_box

# Two wood tones: boards and panels light, uprights and thin members darker.
LIGHT, DARK = cq.Color(0.82, 0.64, 0.42), cq.Color(0.56, 0.39, 0.23)
DARK_ROLES = {"legs", "posts", "slats", "stretchers", "arms", "rungs", "wheels", "axles",
              "handle", "fronts", "rail"}
VIEW = (0.55, -0.75, 0.40)      # the camera looks from the front, a little right and above


def to_shape(solid: Solid) -> cq.Workplane:
    x, y, z = solid.size
    if solid.shape == "cylinder":
        made = cq.Workplane("XY").circle(x / 2).extrude(z)
    elif solid.shape == "cylinder_x":      # lying left to right; `at` is its lowest line
        made = (cq.Workplane("YZ").circle(z / 2).extrude(x / 2, both=True)
                .translate((0, 0, z / 2)))
    else:
        made = cq.Workplane("XY").box(x, y, z, centered=(True, True, False))
    return made.translate(solid.at)


def picture(structure: Structure, path: Path, size: int = 480) -> None:
    """Write a PNG of the whole structure, framed to fit whatever its size."""
    assembly = cq.Assembly(name=structure.kind)
    for step in structure.steps:
        colour = DARK if step.role in DARK_ROLES else LIGHT
        for part in step.parts:
            assembly.add(to_shape(part), name=part.name, color=colour)
    low, high = bounding_box(structure.solids)
    centre = [(a + b) / 2 for a, b in zip(low, high, strict=True)]
    radius = math.dist(low, high) / 2
    length = math.sqrt(sum(v * v for v in VIEW))
    # Far enough back that a sphere round the structure fits the camera's 30 degree view.
    eye = [c + 4.0 * radius * v / length for c, v in zip(centre, VIEW, strict=True)]
    vis.show(assembly, screenshot=str(path), interact=False, edges=False, trihedron=False,
             width=size, height=size, gradient=False, roll=0, elevation=0, azimuth=0,
             position=tuple(eye), focus=tuple(centre), viewup=(0, 0, 1))
