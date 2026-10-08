"""Build a part through FreeCAD's interface from its steps, and check it.

    result = build_part(ui, "composed_block", params)
    result.problems        # [] when the interface built the reference solid

The checks are the command-level runtime's own (forge/freecad/build.py), so the
two layers are held to the same standard:
  1. after EVERY step, the solid has the volume and bounding box the reference
     engine works out for the part so far (a mismatch names the step kind);
  2. at the end, probe points confirm every feature is where it should be;
  3. the caller compares the final solid with the stored kernel measurement.
Tolerances: 0.001 mm on the bounding box, one part in a million on the volume.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from forge.freecad.build import against_stored, compare
from forge.freecad.probes import failed, probes_of
from forge.freecad_ui.client import UIClient
from forge.freecad_ui.recipes import Act, AnyOrder, context_of, recipe, resolve
from forge.system1 import engine
from forge.system1.steps import Step, steps_of

__all__ = ["BuildResult", "against_stored", "build_part", "build_steps", "run_recipe"]

OnAction = Callable[[Step, Act, dict], None]


@dataclass
class BuildResult:
    problems: list[str] = field(default_factory=list)
    solid: dict | None = None
    actions: int = 0
    semantic_actions: int = 0
    steps: list[tuple[str, bool]] = field(default_factory=list)   # (step kind, matched?)
    action_ms: list[tuple[str, float]] = field(default_factory=list)  # (element kind, in-app ms)

    @property
    def ok(self) -> bool:
        return not self.problems


def _showing(reply: dict) -> set[str]:
    return {element["id"] for element in reply["elements"]}


def run_recipe(ui: UIClient, step: Step, entries: list[Act | AnyOrder], reply: dict,
               result: BuildResult, rng: random.Random | None = None,
               on_action: OnAction | None = None) -> tuple[dict, str | None]:
    """Carry out a recipe's actions in order. Returns (last reply, what went wrong or None).

    `reply` is the newest interface reply (its elements say what can be acted on).
    Inside an any-order group the next action is drawn, at random when `rng` is
    given, from the members whose element is showing right now.
    """
    def do(action: Act) -> str | None:
        nonlocal reply
        try:
            action = resolve(action, reply)
        except LookupError as error:
            return f"{step.kind}: {error}"
        reply = ui.act(action.id, action.value)
        result.actions += 1
        result.semantic_actions += action.semantic
        result.action_ms.append((action.id.split(":", 1)[0], reply.get("ms", 0.0)))
        if on_action is not None:
            on_action(step, action, reply)
        if reply["status"] != "ok":
            return (f"{step.kind}: {action.id} = {action.value!r} -> {reply['status']}: "
                    f"{reply.get('reason')}")
        return None

    for entry in entries:
        if isinstance(entry, Act):
            problem = do(entry)
            if problem:
                return reply, problem
            continue
        waiting = list(entry.actions)
        while waiting:
            ready = [action for action in waiting if action.id in _showing(reply)]
            if not ready:
                return reply, f"{step.kind}: none of {[a.id for a in waiting]} is showing"
            action = rng.choice(ready) if rng is not None else ready[0]
            waiting.remove(action)
            problem = do(action)
            if problem:
                return reply, problem
    return reply, None


def build_steps(ui: UIClient, steps: list[Step], rng: random.Random | None = None,
                on_action: OnAction | None = None, probe: bool = True) -> BuildResult:
    """Build steps one at a time (the first must be a start), checking after each."""
    result = BuildResult()
    context = context_of(steps)
    state = engine.State([])
    ui.new_part()
    reply = ui.elements()
    for step in steps:
        if engine.apply(state, step) != "ok":
            result.problems.append(f"{step.kind}: the reference engine rejects this step")
            return result
        reply, problem = run_recipe(ui, step, recipe(step, context), reply, result, rng, on_action)
        if problem:
            result.problems.append(problem)
            result.steps.append((step.kind, False))
            return result
        document = ui.document()
        result.solid = document["solid"]
        invalid = [item["name"] for item in document["items"] if not item["valid"]]
        want = engine.expected(state)
        differs = compare(result.solid, want.expected_bbox, want.expected_volume)
        if invalid:
            differs.append(f"left {invalid} invalid")
        result.steps.append((step.kind, not differs))
        if differs:
            result.problems += [f"after {step.kind}: {text}" for text in differs]
            return result
    if probe:
        probes = probes_of(steps)
        if probes:
            result.problems += failed(probes, ui.probe([point for point, _, _ in probes]))
    return result


def build_part(ui: UIClient, family: str, params: dict, rng: random.Random | None = None,
               on_action: OnAction | None = None) -> BuildResult:
    """Build a composed part from its parameters."""
    return build_steps(ui, steps_of(family, params), rng, on_action)
