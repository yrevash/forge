"""The step vocabulary: what one step is, and how a composed part becomes a list of steps.

This file is the step table, written as code. A step has a KIND
("hole") and named SLOTS ("diameter", "x", "y"); every slot holds one number.
A composed part's parameters map onto steps one to one:

    base_length, base_width, base_height      -> the start step  block(length, width, height)
    base_top_chamfer                          -> the edge treatment  top_chamfer(size)
    hole_2_diameter, hole_2_x, hole_2_y       -> feature number 2:  hole(diameter, x, y)

`steps_of` goes from parameters to steps and `params_of` goes back; the two are
exact inverses (tests/test_system1_steps.py checks thousands of parts).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.generators.prompts import composed_features

Number = float | int

# --- the step table ---------------------------------------------------------------------------

STARTS: dict[str, tuple[str, ...]] = {
    "block": ("length", "width", "height"),
    "cylinder": ("diameter", "height"),
    "hex": ("across_flats", "height"),
    "ring": ("outer_diameter", "inner_diameter", "height"),
}
TREATMENTS: dict[str, tuple[str, ...]] = {
    "corner_radius": ("radius",),
    "top_chamfer": ("size",),
    "top_fillet": ("radius",),
    "shell": ("wall_thickness",),
}
FEATURES: dict[str, tuple[str, ...]] = {
    "hole": ("diameter", "x", "y"),
    "blind_hole": ("diameter", "depth", "x", "y"),
    "counterbore": ("hole_diameter", "diameter", "depth", "x", "y"),
    "boss": ("diameter", "height", "x", "y"),
    "pad": ("length", "width", "height", "x", "y"),
    "pocket": ("length", "width", "depth", "x", "y"),
    "slot": ("length", "width", "depth", "angle", "x", "y"),
    "polar": ("count", "hole_diameter", "circle_diameter"),
    "row": ("count", "hole_diameter", "spacing", "y"),
    # A pair has the slots of the single feature; the twin is mirrored across the YZ plane.
    "hole_pair": ("diameter", "x", "y"),
    "boss_pair": ("diameter", "height", "x", "y"),
    "pocket_pair": ("length", "width", "depth", "x", "y"),
}
CONTROL: dict[str, tuple[str, ...]] = {"undo": (), "done": ()}

SLOTS: dict[str, tuple[str, ...]] = {**STARTS, **TREATMENTS, **FEATURES, **CONTROL}
KINDS: tuple[str, ...] = tuple(SLOTS)

# What a slot's number is. Everything not listed here is a length in millimetres.
COUNT_SLOTS = frozenset({"count"})      # a whole number
ANGLE_SLOTS = frozenset({"angle"})      # degrees
POSITION_SLOTS = frozenset({"x", "y"})  # millimetres from the centre; may be zero or negative

# The composed generator's parameter name for each edge treatment's one slot.
TREATMENT_PARAM = {"corner_radius": "base_corner_radius", "top_chamfer": "base_top_chamfer",
                   "top_fillet": "base_top_fillet_radius", "shell": "base_wall_thickness"}


@dataclass(frozen=True)
class Step:
    """One step: a kind, the value of each slot, and where each value came from.

    `sources` maps a slot to the index of the mention (the i-th number in the
    sentence) that fills it. It is None for a step that did not come from a
    sentence, for example the steps `steps_of` reads off a part's parameters.
    """

    kind: str
    slots: dict[str, Number] = field(default_factory=dict)
    sources: dict[str, int] | None = None


UNDO = Step("undo")
DONE = Step("done")


def group_of(kind: str) -> str:
    """'start', 'treatment', 'feature' or 'control'."""
    if kind in STARTS:
        return "start"
    if kind in TREATMENTS:
        return "treatment"
    return "feature" if kind in FEATURES else "control"


# --- parameters <-> steps --------------------------------------------------------------------

def slot_params(family: str, params: dict[str, Number]) -> list[tuple[str, dict[str, str]]]:
    """For each step of a composed part, in build order: its kind and {slot: parameter name}.

    This is the one place that knows how parameter names spell steps; `steps_of`
    and the mention alignment (mentions.py) are both read off it.
    """
    start = family.removeprefix("composed_")
    if start not in STARTS:
        raise ValueError(f"{family} is not a composed family")
    walk = [(start, {slot: f"base_{slot}" for slot in STARTS[start]})]
    for kind, name in TREATMENT_PARAM.items():
        if name in params:
            walk.append((kind, {TREATMENTS[kind][0]: name}))
    _, features = composed_features(params)
    for number, (kind, fields) in enumerate(features, start=1):
        if kind not in FEATURES or set(fields) != set(FEATURES[kind]):
            raise ValueError(f"feature {number} is not a known step: {kind} {sorted(fields)}")
        walk.append((kind, {slot: f"{kind}_{number}_{slot}" for slot in FEATURES[kind]}))
    named = [name for _, slots in walk for name in slots.values()]
    if sorted(named) != sorted(params):
        raise ValueError(f"parameters that fit no step: {sorted(set(params) ^ set(named))}")
    return walk


def steps_of(family: str, params: dict[str, Number]) -> list[Step]:
    """A composed part as its ordered steps: start, edge treatment if any, features as built."""
    return [Step(kind, {slot: params[name] for slot, name in slots.items()})
            for kind, slots in slot_params(family, params)]


def params_of(steps: list[Step]) -> tuple[str, dict[str, Number]]:
    """The inverse of `steps_of`: the family and the parameters, in the generator's own order."""
    if not steps or steps[0].kind not in STARTS:
        raise ValueError("the first step must be a start")
    start = steps[0]
    params = {f"base_{slot}": start.slots[slot] for slot in STARTS[start.kind]}
    number = 0
    for position, step in enumerate(steps[1:], start=1):
        if step.kind in TREATMENTS:
            if position != 1:
                raise ValueError("an edge treatment must come straight after the start")
            params[TREATMENT_PARAM[step.kind]] = step.slots[TREATMENTS[step.kind][0]]
        elif step.kind in FEATURES:
            number += 1     # features are numbered in build order; the treatment is not one
            for slot in FEATURES[step.kind]:
                params[f"{step.kind}_{number}_{slot}"] = step.slots[slot]
        else:
            raise ValueError(f"{step.kind} cannot follow a start")
    return f"composed_{start.kind}", params
