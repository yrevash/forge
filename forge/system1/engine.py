"""The engine: the state of a build, and the code that changes it one step at a time.

Everything here is plain Python: no CAD kernel is
started, so applying a step costs microseconds and a million of them are cheap.

    state = State(mention_values)      # empty: nothing built
    execute(state, step)               # -> "ok", "rejected" or "undone"
    build(state)                       # -> a CadQuery program for what is built

`build` only writes program text. To get a solid, run that text through
`forge.sandbox` like any other program; never `exec` it here.

Why the rules can be trusted: they are not written again in this file. Whether a
feature fits is decided by the same functions the composed generator uses to
place features (`clear`, `polar_range`, `row_range`, `treatment_range` in
forge/generators/families/composed.py), and the program text comes from the
generator's own `assemble`. So a part built step by step IS the generator's
part; `python -m forge.system1.prove` shows it on the kernel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from forge.generators.base import Part
from forge.generators.families import composed
from forge.generators.families._common import MIN_WALL
from forge.system1.steps import (
    CONTROL,
    COUNT_SLOTS,
    POSITION_SLOTS,
    SLOTS,
    STARTS,
    TREATMENT_PARAM,
    TREATMENTS,
    Number,
    Step,
)

OUTCOMES = ("none", "ok", "rejected", "undone")
# Slots that may be zero or negative. Every other slot is a size and must be positive.
_FREE_SLOTS = POSITION_SLOTS | {"angle"}


def layout_of(kind: str) -> str:
    """How a feature kind is placed: 'single', 'pair', 'polar' or 'row'."""
    if kind in ("polar", "row"):
        return kind
    return "pair" if kind.endswith("_pair") else "single"


@dataclass
class Built:
    """One item that exists in the build: a step that was accepted."""

    kind: str
    slots: dict[str, Number]
    sources: dict[str, int] | None = None   # which mention filled each slot


@dataclass
class State:
    """What the model is shown at each step, besides the sentence.

    It is a structured list, not a picture: no measured geometry is kept here.
    """

    mentions: list[float]                           # the value of every mention, in text order
    built: list[Built] = field(default_factory=list)
    last: str = "none"                              # outcome of the last step, one of OUTCOMES
    steps: int = 0                                  # how many steps have been executed
    # Footprint bookkeeping for the rule check; rebuilt from `built` whenever it is missing.
    _geometry: tuple | None = field(default=None, repr=False, compare=False)

    @property
    def used(self) -> list[bool]:
        """For every mention: has a built item used it?"""
        flags = [False] * len(self.mentions)
        for item in self.built:
            for index in (item.sources or {}).values():
                flags[index] = True
        return flags

    def copy(self) -> State:
        return State(self.mentions, [Built(b.kind, dict(b.slots), dict(b.sources or {}) or None)
                                     for b in self.built], self.last, self.steps)

    def to_json(self) -> dict:
        return {"built": [{"kind": b.kind, "slots": b.slots,
                           "sources": {k: f"mention:{i}" for k, i in (b.sources or {}).items()}}
                          for b in self.built],
                "used": [int(flag) for flag in self.used], "last": self.last, "steps": self.steps}

    @classmethod
    def from_json(cls, data: dict, mentions: list[float]) -> State:
        built = [Built(b["kind"], b["slots"],
                       {k: int(s.removeprefix("mention:")) for k, s in b["sources"].items()} or None)
                 for b in data["built"]]
        return cls(mentions, built, data["last"], data["steps"])

    # -- geometry of what is built -----------------------------------------------------------

    def geometry(self) -> tuple[composed.Base, list[composed.Placed], list[composed.Footprint]]:
        """The base (with its edge treatment), every placed feature, and all their footprints."""
        if self._geometry is None:
            start = self.built[0]
            base = composed.make_base(start.kind, {f"base_{k}": v for k, v in start.slots.items()})
            placed: list[composed.Placed] = []
            taken: list[composed.Footprint] = []
            for item in self.built[1:]:
                if item.kind in TREATMENTS:
                    composed.apply_treatment(base, _treatment_name(item.kind),
                                             item.slots[TREATMENTS[item.kind][0]])
                else:
                    feature = _place(base, item.kind, item.slots)
                    placed.append(feature)
                    taken += composed.footprints(feature[0], feature[2])
            self._geometry = (base, placed, taken)
        return self._geometry


def _treatment_name(kind: str) -> str:
    """The composed generator's name for an edge treatment ('top_fillet' -> 'top_fillet_radius')."""
    return TREATMENT_PARAM[kind].removeprefix("base_")


def _place(base: composed.Base, kind: str, slots: dict[str, Number]) -> composed.Placed:
    """A feature step as the generator's (draft, placement numbers, instance positions)."""
    where = composed.PLACED_FIELDS[layout_of(kind)]
    placed = {name: slots[name] for name in where}
    sizes = {name: slots[name] for name in SLOTS[kind] if name not in where}
    draft = composed.draft_of(kind, base, sizes)
    return draft, placed, composed.spots_of(draft.layout, draft.sizes, placed)


def resolve(kind: str, sources: dict[str, int], mentions: list[float]) -> Step:
    """Turn 'kind plus one mention per slot' into a step with values.

    This is the only place a number enters a step: it is copied from the
    sentence, never written by a model. A count becomes a whole number when the
    mention is one ("six" -> 6); otherwise it is left as it is and the rule
    check turns the step down.
    """
    slots: dict[str, Number] = {}
    for name in SLOTS[kind]:
        value = mentions[sources[name]]
        slots[name] = int(value) if name in COUNT_SLOTS and float(value).is_integer() else value
    return Step(kind, slots, dict(sources))


# --- the rule check --------------------------------------------------------------------------

def check(state: State, step: Step) -> str | None:
    """Why this step cannot be applied to this state, or None if it can.

    The rules are the ones the generator itself keeps:
    a start comes first and only once; an edge treatment comes straight after
    the start; sizes are positive and fit; a feature lies on the base, MIN_WALL
    from every edge and from every other feature.
    """
    kind, slots = step.kind, step.slots
    if kind not in SLOTS or kind in CONTROL:
        return f"{kind} is not something to build"
    if set(slots) != set(SLOTS[kind]):
        return f"{kind} needs exactly the slots {', '.join(SLOTS[kind])}"
    for name, value in slots.items():
        if not math.isfinite(value):
            return f"{name} is not a number"
        if name in COUNT_SLOTS:
            if not isinstance(value, int) or value < 2:
                return f"{name} must be a whole number, 2 or more"
        elif name not in _FREE_SLOTS and value <= 0:
            return f"{name} must be positive"

    if kind in STARTS:
        if state.built:
            return "a base already exists"
        if kind == "ring" and slots["inner_diameter"] >= slots["outer_diameter"]:
            return "the inner diameter must be smaller than the outer diameter"
        return None
    if not state.built:
        return "nothing to put it on: start with a base"

    base, _, taken = state.geometry()
    if kind in TREATMENTS:
        if len(state.built) != 1:
            return "an edge treatment must come straight after the start"
        name = _treatment_name(kind)
        if name not in composed.TREATMENTS[base.kind]:
            return f"a {base.kind} does not take a {kind}"
        if slots[TREATMENTS[kind][0]] > composed.treatment_range(base, name)[1]:
            return f"the {kind} is too large for this base"
        return None
    return _check_feature(base, taken, kind, slots)


def _check_feature(base: composed.Base, taken: list[composed.Footprint], kind: str,
                   slots: dict[str, Number]) -> str | None:
    if kind not in composed.KIND_WEIGHTS[base.kind]:
        return f"a {base.kind} does not take a {kind}"     # e.g. a row belongs on a block
    if base.shelled and kind in composed.CUTS:
        return "a shelled base takes no cuts, only bosses"
    if "depth" in slots and slots["depth"] > base.height - MIN_WALL:
        return f"too deep: a floor of {MIN_WALL:g} mm must stay"
    if kind == "counterbore" and slots["diameter"] <= slots["hole_diameter"]:
        return "the counterbore must be wider than its hole"
    if kind == "slot":
        if slots["angle"] not in (0, 90):
            return "a slot runs along X (0 degrees) or along Y (90 degrees)"
        if slots["length"] <= slots["width"]:
            return "a slot must be longer than it is wide"
    if layout_of(kind) == "pair" and slots["x"] <= 0:
        return "a mirrored pair needs x greater than zero"
    if kind == "polar":
        low, high = composed.polar_range(base, slots["count"], slots["hole_diameter"])
        if not low <= slots["circle_diameter"] <= high:
            return "the pitch circle does not fit: holes would touch each other or an edge"
    if kind == "row":
        low, high = composed.row_range(base, slots["count"], slots["hole_diameter"])
        if not low <= slots["spacing"] <= high:
            return "the spacing does not fit: holes would touch each other or leave the block"

    draft, _, spots = _place(base, kind, slots)
    prints = composed.footprints(draft, spots)
    # The instances of one feature must also keep clear of each other. The ranges above
    # already see to that for sensible sizes; this catches the rest (a row of tiny holes).
    if not all(composed._apart(a, b) for i, a in enumerate(prints) for b in prints[i + 1:]):
        return "its own instances are too close to each other"
    if not composed.clear(base, draft, prints, taken):
        return "it is off the base, or closer than 2 mm to an edge or to another feature"
    return None


# --- changing the state ----------------------------------------------------------------------

def apply(state: State, step: Step) -> str:
    """Add a build step to the state, or reject it. Returns the outcome."""
    reason = check(state, step)
    if reason is None:
        state.built.append(Built(step.kind, dict(step.slots),
                                 dict(step.sources) if step.sources is not None else None))
        state._geometry = None
    state.last = "rejected" if reason else "ok"
    state.steps += 1
    return state.last


def undo(state: State) -> str:
    """Remove the last built item. With nothing built there is nothing to undo: rejected."""
    if state.built:
        state.built.pop()
        state._geometry = None
        state.last = "undone"
    else:
        state.last = "rejected"
    state.steps += 1
    return state.last


def execute(state: State, step: Step) -> str:
    """Carry out any step, control steps included. `done` changes nothing."""
    if step.kind == "undo":
        return undo(state)
    if step.kind == "done":
        state.last = "ok"
        state.steps += 1
        return state.last
    return apply(state, step)


# --- the solid ---------------------------------------------------------------------------------

def expected(state: State) -> Part:
    """The built state as a generator `Part`: its program and what it must measure as."""
    if not state.built:
        raise ValueError("nothing is built yet")
    base, placed, _ = state.geometry()
    return composed.assemble(f"composed_{base.kind}", base, placed)


def build(state: State) -> str:
    """A CadQuery program for what is built so far. Run it only through forge.sandbox."""
    return expected(state).code
