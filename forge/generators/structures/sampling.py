"""Draw one random structure of a kind, at one of three levels of detail.

The level decides what the person is taken to have SAID, and so which slots are
`stated` and which are `default`:

    a  the name and the headline sizes only. Every option is at its default,
       every other size comes from the default rules.
    b  the same, plus options in words ("with arms") and counts ("5 shelves").
    c  the same as b, plus one to three normally-default sizes stated with a
       value the rules would not have chosen ("legs 50 square"). The value is
       0.7 to 1.5 times the rule's, on that size's own grid.

A structure has ONE level and all its prompts are written at that level, so a
slot's source never disagrees with any prompt of that structure.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from types import ModuleType

from forge.generators.structures.draft import Invalid, Slot, Structure

LEVELS = ("a", "b", "c")
LEVEL_WEIGHTS = (0.2, 0.45, 0.35)
SAY_COUNT = 0.75          # at levels b and c, the chance that a count is stated
MAX_TRIES = 200
OVERRIDE_RANGE = (0.7, 1.5)   # a stated default lies between these multiples of the rule's value

# A unit more than four times as tall as its smaller footprint side tips over easily.
# Such units are kept RARE, not excluded (decided 6 Oct 2026): about this share
# of draws ask for one, every other draw must not be one. A kind that is tall and
# narrow by its nature (a ladder, a fence panel set in the ground) sets TALL_BY_NATURE.
TALL_NARROW_RATIO = 4.0
TALL_NARROW_SHARE = 0.03
TRIES_FOR_TALL = 25           # a kind that cannot be tall and narrow stops trying after this

# How many draws were thrown away because the sizes did not fit together, per
# kind and reason. Nothing is hidden: the trial run prints this (generate.py).
REJECTED: Counter = Counter()


def is_tall_narrow(structure: Structure) -> bool:
    """Is its height more than four times the smaller of its width and depth?"""
    width, depth, height = structure.expected_bbox
    return height > TALL_NARROW_RATIO * min(width, depth)


def sample(kind: ModuleType, rng: random.Random, level: str | None = None) -> Structure:
    """One random valid structure. Sizes that do not fit together are drawn again."""
    level = level or rng.choices(LEVELS, LEVEL_WEIGHTS)[0]
    any_proportion = getattr(kind, "TALL_BY_NATURE", False)
    want_tall = rng.random() < TALL_NARROW_SHARE
    for attempt in range(MAX_TRIES):
        if want_tall and attempt >= TRIES_FOR_TALL:
            want_tall = False        # this kind (or this level's default variant) is never tall
        # `plain` asks for the default variant. A variant lists only the options
        # that apply to it (a table on panel ends has no leg shape).
        chosen = kind.choices(rng, plain=level == "a")
        given = kind.headline(rng, chosen)
        if level != "a":
            given.update({param: value for param, value in kind.counts(rng, chosen, given).items()
                          if rng.random() < SAY_COUNT})
        try:
            structure = kind.build(chosen, given)
        except Invalid as why:
            REJECTED[(kind.NAME, str(why).split(": ")[-1].split(";")[0][:60])] += 1
            continue
        if level == "c":
            structure = _with_overrides(kind, structure, rng)
        if not any_proportion and is_tall_narrow(structure) != want_tall:
            if not want_tall:
                REJECTED[(kind.NAME, "tall and narrow (kept rare, not excluded)")] += 1
            continue
        # If no override fitted, nothing normally-default is stated: that is level b.
        structure.level = "c" if "override" in structure.stated().values() else \
            "a" if level == "a" else "b"
        structure.names = kind.names(structure.choices, structure.params)
        return structure
    raise RuntimeError(f"{kind.NAME}: no valid structure in {MAX_TRIES} tries")


def _with_overrides(kind: ModuleType, base: Structure, rng: random.Random) -> Structure:
    """State one to three default sizes with a different value, keeping the structure sound."""
    open_to_change = [param for param in kind.OVERRIDES
                      if param in base.params and base.slot(param)[2].source == "default"]
    rng.shuffle(open_to_change)
    wanted = min(len(open_to_change), rng.choice([1, 1, 1, 2, 2, 3]))
    structure, done = base, 0
    for param in open_to_change:
        if done == wanted:
            break
        # Values on the parameter's grid from 0.7 to 1.5 times what the rule gives:
        # different enough to matter, close enough to stay sensible furniture.
        step = kind.OVERRIDES[param]
        usual = structure.slot(param)[2].value
        first, last = math.ceil(OVERRIDE_RANGE[0] * usual / step), \
            math.floor(OVERRIDE_RANGE[1] * usual / step)
        values = [float(step * i) for i in range(first, last + 1)
                  if abs(step * i - usual) > 1e-9]
        rng.shuffle(values)
        for value in values[:4]:
            try:
                tried = kind.build(structure.choices, {**structure.given, param: value})
            except Invalid:
                continue
            # One default can feed another rule (a thicker leg makes a taller apron), so
            # an earlier stated size is looked at again against what its rule gives now.
            if all(_in_range(tried.slot(p)[2]) for p, sort in tried.stated().items()
                   if sort == "override"):
                structure, done = tried, done + 1
                break
    return structure


def _in_range(slot: Slot) -> bool:
    """Is a stated default different from, but not far from, what its rule gives?"""
    low, high = (factor * slot.default_value for factor in OVERRIDE_RANGE)
    return low - 1e-9 <= slot.value <= high + 1e-9 and abs(slot.value - slot.default_value) > 1e-9
