"""Building one structure: steps, slots with their sources, parts, and the program text.

A kind (kinds/chair.py ...) describes a structure by opening steps on a `Draft`
and filling each step's slots in one of three ways. The way IS the slot's source:

    step.stated("width")                        the person states it; it will be in the prompt
    step.default("thickness", rule, span=...)   a default rule from defaults.py chooses it
    step.derive("top", "seat_top - seat_thickness")   arithmetic on other numbers

`default` becomes `stated` when the caller hands in a value for that slot: that
is how "legs 50 square" is made, a normally-default size said out loud.

Every slot is also a named PARAMETER of the program: `<step name>_<slot>`, so
`legs_thickness`, `seat_top`. A formula may use any parameter declared before it.

`Draft.finish` checks the result by arithmetic (nothing overlaps, everything is
joined, the overall size is what the kind promised) and writes the program.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from forge.generators.base import program
from forge.generators.structures import solids as arithmetic
from forge.generators.structures.formula import Number, evaluate, rename
from forge.generators.structures.solids import EPS, Solid
from forge.generators.structures.vocabulary import ROLES, part_specs, place, slots_of


class Invalid(ValueError):
    """These choices and sizes do not make a sound structure (parts collide, float or misfit)."""


# A wooden board that carries weight (a seat, a table or desk top, a deck, a shelf)
# is never thinner than this, whoever chose its thickness: a rule or a stated number.
# Added to the default rules on 6 Oct 2026. Our choice, not a standard.
CARRIES_WEIGHT = ("top", "shelves")
MIN_BOARD_THICKNESS = 15.0


@dataclass
class Slot:
    value: Number
    source: str                      # "stated", "default" or "arithmetic"
    param: str                       # the program's name for this number
    rule: str | None = None          # default (and overridden default): the rule's name ...
    args: dict | None = None         # ... and the numbers it was given
    formula: str | None = None       # arithmetic: the formula
    default_value: Number | None = None   # stated in place of a default: what the rule gives

    def as_dict(self) -> dict:
        return {key: value for key, value in vars(self).items() if value is not None}


@dataclass
class Step:
    name: str
    role: str
    placement: str
    shape: str                       # "box", "cylinder" or "cylinder_x" (lying left to right)
    slots: dict[str, Slot]
    parts: list[Solid]


@dataclass
class Structure:
    kind: str
    choices: dict[str, str]          # the variant: one choice per option that applies
    given: dict[str, Number]         # every stated number, by parameter name
    steps: list[Step]
    params: dict[str, Number]        # every parameter, in the program's order
    code: str
    expected_bbox: list[float]       # overall extents along X, Y, Z
    expected_volume: float
    level: str = "a"                 # which kind of prompt describes it (prompts.py)
    names: list[str] = field(default_factory=list)   # what people may call this one

    @property
    def solids(self) -> list[Solid]:
        return [part for step in self.steps for part in step.parts]

    @property
    def id(self) -> str:
        key = json.dumps([self.kind, self.choices, self.given], sort_keys=True)
        return hashlib.sha1(key.encode()).hexdigest()[:16]     # an id, not a secret

    def slot(self, param: str) -> tuple[int, str, Slot]:
        """Find a parameter: (step number, slot name, the slot)."""
        for index, step in enumerate(self.steps):
            for name, slot in step.slots.items():
                if slot.param == param:
                    return index, name, slot
        raise KeyError(param)

    def stated(self) -> dict[str, str]:
        """Every stated parameter and what kind of statement it is.

        headline  a size a person would normally give
        count     a number of things ("5 shelves")
        override  a size the default rules would have chosen, stated with another value
        """
        found = {}
        for step in self.steps:
            for name, slot in step.slots.items():
                if slot.source == "stated":
                    found[slot.param] = ("count" if name == "count" else
                                         "override" if slot.rule else "headline")
        return found


class StepDraft:
    """One step being filled in. Call `done()` when every slot has a value."""

    def __init__(self, draft: Draft, name: str, role: str, placement: str, shape: str,
                 light: bool = False) -> None:
        self.draft, self.name, self.role = draft, name, role
        self.placement, self.shape, self.light = placement, shape, light
        self.slots: dict[str, Slot] = {}

    def _add(self, slot: str, made: Slot) -> Number:
        if slot not in slots_of(self.placement, self.shape):
            raise ValueError(f"{self.placement!r} has no slot {slot!r}")
        self.slots[slot] = made
        self.draft.declare(made)
        return made.value

    def stated(self, slot: str) -> Number:
        """A number the person states. It must have been handed to the draft."""
        param = f"{self.name}_{slot}"
        return self._add(slot, Slot(self.draft.take(param), "stated", param))

    def default(self, slot: str, rule: Callable[..., Number], **args: Number) -> Number:
        """A number a default rule chooses, unless the person stated one."""
        param = f"{self.name}_{slot}"
        value = rule(**args)
        if param in self.draft.given:
            return self._add(slot, Slot(self.draft.take(param), "stated", param,
                                        rule=rule.__name__, args=args, default_value=value))
        return self._add(slot, Slot(value, "default", param, rule=rule.__name__, args=args))

    def derive(self, slot: str, formula: str) -> Number:
        """A number worked out from parameters declared earlier."""
        param = f"{self.name}_{slot}"
        value = evaluate(formula, self.draft.values)
        return self._add(slot, Slot(value, "arithmetic", param, formula=formula))

    def done(self) -> None:
        order = slots_of(self.placement, self.shape)
        missing = [slot for slot in order if slot not in self.slots]
        if missing:
            raise ValueError(f"step {self.name!r} has no value for {missing}")
        values = {slot: self.slots[slot].value for slot in order}
        if any(v < 0 for slot, v in values.items() if slot not in ("x", "y")) or any(
                values.get(size, 1) <= 0 for size in ("width", "depth", "thickness", "height",
                                                      "diameter", "length_x", "length_y")):
            raise Invalid(f"step {self.name!r} has a size that is zero or negative: {values}")
        if "top" in values and "bottom" in values and values["top"] <= values["bottom"]:
            raise Invalid(f"step {self.name!r} has no height")
        if (self.role in CARRIES_WEIGHT and not self.light
                and values["thickness"] < MIN_BOARD_THICKNESS):
            raise Invalid(f"step {self.name!r}: a board that carries weight is at least "
                          f"{MIN_BOARD_THICKNESS:g} thick")
        self.draft.steps.append(Step(
            self.name, self.role, self.placement, self.shape,
            {slot: self.slots[slot] for slot in order},      # in the vocabulary's order
            place(self.name, self.placement, self.shape, values)))


class Draft:
    def __init__(self, kind: str, choices: dict[str, str], given: dict[str, Number]) -> None:
        self.kind, self.choices, self.given = kind, dict(choices), dict(given)
        self.values: dict[str, Number] = {}      # every parameter so far, by name
        self.declared: list[Slot] = []           # the same, in order
        self.steps: list[Step] = []
        self._taken: set[str] = set()

    def step(self, name: str, role: str, placement: str, shape: str = "box",
             light: bool = False) -> StepDraft:
        """Open a step. `light` marks a top or shelf that is not a weight-carrying wooden
        board (a crate's lid, a metal plate), so the minimum board thickness does not apply."""
        if placement not in ROLES[role][1]:
            raise ValueError(f"role {role!r} is never placed {placement!r}")
        if any(name == step.name for step in self.steps):
            raise ValueError(f"two steps are called {name!r}")
        return StepDraft(self, name, role, placement, shape, light)

    def take(self, param: str) -> Number:
        if param not in self.given:
            raise KeyError(f"{self.kind}: the stated number {param!r} was not given")
        self._taken.add(param)
        return self.given[param]

    def declare(self, slot: Slot) -> None:
        if slot.param in self.values:
            raise ValueError(f"parameter {slot.param!r} declared twice")
        self.values[slot.param] = slot.value
        self.declared.append(slot)

    def require(self, ok: bool, why: str) -> None:
        """A rule of good sense the arithmetic check cannot see (a gap wide enough to be a gap)."""
        if not ok:
            raise Invalid(f"{self.kind}: {why}")

    def finish(self, overall: tuple[str, str, str]) -> Structure:
        """Check the structure by arithmetic and write its program.

        `overall` gives the width, depth and height the structure must come out
        at, as formulas over its parameters (usually three stated numbers).
        """
        unused = set(self.given) - self._taken
        if unused:
            raise ValueError(f"{self.kind}: stated numbers nobody used: {sorted(unused)}")
        parts = [part for step in self.steps for part in step.parts]
        wrong = arithmetic.problems(parts)
        low, high = arithmetic.bounding_box(parts)
        want = [float(evaluate(formula, self.values)) for formula in overall]
        for axis, size in enumerate(want):
            centred = axis < 2
            want_low = -size / 2 if centred else 0.0
            if abs(low[axis] - want_low) > EPS or abs(high[axis] - want_low - size) > EPS:
                wrong.append(f"overall {'XYZ'[axis]} runs {low[axis]:g} to {high[axis]:g}, "
                             f"expected {want_low:g} to {want_low + size:g}")
        if wrong:
            raise Invalid(f"{self.kind} {self.choices}: " + "; ".join(wrong))
        return Structure(
            kind=self.kind, choices=self.choices, given=self.given, steps=self.steps,
            params=dict(self.values), code=write_program(self.kind, self.declared, self.steps),
            expected_bbox=want, expected_volume=sum(part.volume for part in parts))


# --- the program text -----------------------------------------------------------------------

_LYING = '''

def lying_cylinder(diameter, length, at):
    """A cylinder lying left to right (along X); `at` is the middle of its lowest line."""
    round_bar = cq.Workplane("YZ").circle(diameter / 2).extrude(length / 2, both=True)
    return round_bar.translate((at[0], at[1], at[2] + diameter / 2))
'''

_HELPERS = '''
def box(x, y, z, at):
    """A block x by y by z; `at` is the centre of its bottom face."""
    return cq.Workplane("XY").box(x, y, z, centered=(True, True, False)).translate(at)


def cylinder(diameter, height, at):
    """An upright cylinder; `at` is the centre of its bottom face."""
    return cq.Workplane("XY").circle(diameter / 2).extrude(height).translate(at)
'''


def write_program(kind: str, declared: list[Slot], steps: list[Step]) -> str:
    """One CadQuery program: numbers, formulas, one named solid per part, then the assembly.

    Stated and default numbers are plain assignments at the top. Arithmetic
    numbers follow as the formulas themselves, so the program shows its working.
    """
    numbers = {slot.param: slot.value for slot in declared if slot.source != "arithmetic"}
    lines = [f"{slot.param} = {slot.formula}" for slot in declared if slot.source == "arithmetic"]
    # The helper for lying cylinders is written only into programs that have one.
    lines.append(_HELPERS + (_LYING if any(s.shape == "cylinder_x" for s in steps) else ""))
    names = []
    for step in steps:
        lines.append(f"\n# {step.name}: {step.role}, placed {step.placement}")
        values = {name: slot.value for name, slot in step.slots.items()}
        # A placement's formulas use its own slot names; the program uses the full names.
        full = partial(rename, new_name=partial("{0}_{1}".format, step.name))
        for ending, size, at in part_specs(step.placement, step.shape, values):
            name = f"{step.name}_{ending}" if ending else step.name
            spot = "(" + ", ".join(full(f) for f in at) + ")"
            if step.shape == "cylinder":
                lines.append(f"{name} = cylinder({full(size[0])}, {full(size[2])}, {spot})")
            elif step.shape == "cylinder_x":
                lines.append(f"{name} = lying_cylinder({full(size[1])}, {full(size[0])}, {spot})")
            else:
                lines.append(f"{name} = box({', '.join(full(f) for f in size)}, {spot})")
            names.append(name)
    lines.append(f'\nresult = cq.Assembly(name="{kind}")')
    lines += [f'result.add({name}, name="{name}")' for name in names]
    return program(numbers, "\n".join(lines))
