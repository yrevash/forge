"""Build a WIDE plan in FreeCAD through the recipes and compare it with its reference.

    result = build_plan(client, plan, step_volumes, measured)
    result.problems        # [] when FreeCAD's solid is the reference solid

The same idea as build.py, without the arithmetic engine (a wide plan has no closed-form
volume; see wide_reference.py). Two checks:
  1. after EVERY plan item, FreeCAD's solid has the volume the CadQuery reference had
     after that item (one part in a million), so a mismatch names the item that caused it;
  2. at the end, the stored measurement: bounding box within 0.001 mm, volume, one valid
     solid sitting on the XY plane (`build.against_stored`).
"""

from __future__ import annotations

import random

from forge.freecad.build import BuildResult, against_stored, run_commands
from forge.freecad.client import FreeCADClient
from forge.freecad.recipes import context_of, flatten, recipe
from forge.generators.base import VOLUME_RELATIVE_TOLERANCE
from forge.system1.steps import Step


def build_plan(client: FreeCADClient, plan: list[Step], step_volumes: list[float],
               measured: dict, rng: random.Random | None = None) -> BuildResult:
    """Build the plan item by item from an empty FreeCAD. With `rng` the dimensions of every
    sketch shape are issued in a shuffled order."""
    result = BuildResult()
    context = context_of(plan)
    client.reset()
    for step, volume in zip(plan, step_volumes, strict=True):
        commands = flatten(recipe(step, context), rng)
        reply, problem = run_commands(client, step, commands)
        result.commands += len(commands)
        if problem:
            result.problems.append(problem)
            result.steps.append((step.kind, False))
            return result
        result.snapshot = reply["snapshot"]
        solid = result.snapshot["solid"]
        same = solid is not None and solid["solids"] == 1 and solid["valid"] \
            and abs(solid["volume"] - volume) <= VOLUME_RELATIVE_TOLERANCE * volume
        result.steps.append((step.kind, same))
        if not same:
            got = None if solid is None else (solid["volume"], solid["solids"], solid["valid"])
            result.problems.append(f"after {step.kind}: FreeCAD (volume, solids, valid) {got}, "
                                   f"reference volume {volume:.6f}")
            return result
    result.problems += against_stored(result.snapshot["solid"], measured)
    return result
