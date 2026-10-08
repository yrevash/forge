"""Build a part in FreeCAD from its steps, and check it against our reference engine.

    result = build_part(client, "composed_block", params)
    result.problems        # [] when FreeCAD's solid is the reference solid

Three checks, strongest last:
  1. after EVERY step, FreeCAD's solid has the volume and bounding box that the
     reference engine (forge/system1/engine.py) works out by arithmetic for the
     part built so far, so a mismatch names the step kind that caused it;
  2. at the end, probe points confirm every feature is in the right place;
  3. the caller compares the final solid with the stored kernel measurement
     (`against_stored`).
Tolerances are the generators' own: 0.001 mm on the bounding box, one part in a
million on the volume.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from forge.freecad.client import FreeCADClient
from forge.freecad.probes import failed, probes_of
from forge.freecad.recipes import Cmd, context_of, flatten, recipe
from forge.generators.base import BBOX_TOLERANCE_MM, VOLUME_RELATIVE_TOLERANCE
from forge.system1 import engine
from forge.system1.steps import Step, steps_of

OnCommand = Callable[[Step, Cmd, dict], None]


@dataclass
class BuildResult:
    problems: list[str] = field(default_factory=list)
    snapshot: dict | None = None
    commands: int = 0
    steps: list[tuple[str, bool]] = field(default_factory=list)   # (step kind, matched?)

    @property
    def ok(self) -> bool:
        return not self.problems


def compare(solid: dict | None, size: list[float], volume: float) -> list[str]:
    """How a measured solid differs from the size and volume it should have."""
    if solid is None:
        return ["there is no solid"]
    problems = []
    if solid["solids"] != 1 or not solid["valid"]:
        problems.append(f"not one valid solid (solids={solid['solids']}, valid={solid['valid']})")
    for axis, got, want in zip("XYZ", solid["size"], size, strict=True):
        if abs(got - want) > BBOX_TOLERANCE_MM:
            problems.append(f"bbox {axis}: FreeCAD {got:.6f}, reference {want:.6f}")
    if abs(solid["bbox"][2]) > BBOX_TOLERANCE_MM:
        problems.append(f"the part does not sit on the XY plane (z min {solid['bbox'][2]:.6f})")
    if abs(solid["volume"] - volume) > VOLUME_RELATIVE_TOLERANCE * volume:
        problems.append(f"volume: FreeCAD {solid['volume']:.6f}, reference {volume:.6f} "
                        f"(relative {(solid['volume'] - volume) / volume:.2e})")
    return problems


def against_stored(solid: dict | None, measured: dict) -> list[str]:
    """Compare with a stored row's `measured` (what the CadQuery kernel built)."""
    return compare(solid, measured["bbox"], measured["volume"])


def run_commands(client: FreeCADClient, step: Step, commands: list[Cmd],
                 on_command: OnCommand | None = None) -> tuple[dict | None, str | None]:
    """Issue commands in order. Returns (last reply, what went wrong or None)."""
    reply = None
    for command in commands:
        reply = client.command(command.name, **command.args)
        if on_command is not None:
            on_command(step, command, reply)
        if reply["status"] != "ok":
            return reply, (f"{step.kind}: {command.name} {command.args} -> {reply['status']}: "
                           f"{reply.get('reason')}")
        invalid = [item["name"] for item in reply["snapshot"]["items"] if not item["valid"]]
        if invalid:
            return reply, f"{step.kind}: {command.name} left {invalid} invalid"
    return reply, None


def build_steps(client: FreeCADClient, steps: list[Step], rng: random.Random | None = None,
                on_command: OnCommand | None = None, probe: bool = True) -> BuildResult:
    """Build steps one at a time (the first must be a start), checking after each.

    With `rng`, the commands inside every any-order group are shuffled, so over
    many parts every order of a sketch's dimensions gets exercised.
    """
    result = BuildResult()
    context = context_of(steps)
    state = engine.State([])
    for step in steps:
        if engine.apply(state, step) != "ok":
            result.problems.append(f"{step.kind}: the reference engine rejects this step")
            return result
        commands = flatten(recipe(step, context), rng)
        reply, problem = run_commands(client, step, commands, on_command)
        result.commands += len(commands)
        if problem:
            result.problems.append(problem)
            result.steps.append((step.kind, False))
            return result
        result.snapshot = reply["snapshot"]
        want = engine.expected(state)
        differs = compare(result.snapshot["solid"], want.expected_bbox, want.expected_volume)
        result.steps.append((step.kind, not differs))
        if differs:
            result.problems += [f"after {step.kind}: {text}" for text in differs]
            return result
    if probe:
        probes = probes_of(steps)
        if probes:
            result.problems += failed(probes, client.probe([point for point, _, _ in probes]))
    return result


def build_part(client: FreeCADClient, family: str, params: dict,
               rng: random.Random | None = None, on_command: OnCommand | None = None,
               ) -> BuildResult:
    """Build a composed part from its parameters."""
    return build_steps(client, steps_of(family, params), rng, on_command)
