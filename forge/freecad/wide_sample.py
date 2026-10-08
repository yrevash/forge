"""Draw one WIDE plan: a single part whose plan is far freer than the composed generator's.

The composed generator (forge/generators/families/composed.py) has habits a real planner
does not have, and the first Forge-S1 model learned every one of them as a rule.
This sampler breaks them on purpose:

    composed generator                              here
    ----------------------------------------------  ------------------------------------------
    every number on a grid (5, 1 or 0.5 mm)         a step per part or per number: 1, 0.5, 0.25,
                                                    0.1, 0.05 or 0.01 (PROFILES)
    bases 30 to 160 mm                              the base's largest side from 5 to 500 mm,
                                                    log-uniform (tiny parts as likely as large)
    a block's length is its longer side; no base    either side may be longer; plates of 3% and
    taller than wide                                towers of 250% of the width
    2 to 6 plan items, never a base alone           1 to 16 items; a base alone is a plan
    at most one edge treatment, always item 2       0 to 3 treatments at any place after the
                                                    base: early, in the middle, last
    features 2 mm clear of each other and of        anywhere the kernel still makes ONE valid
    every edge                                      solid: touching, overlapping, breaking out
                                                    of an edge, on top of each other
    every round feature its own diameter            the same diameter may repeat (and often does)
    a row only on a block, a circle of holes        every feature kind on every base
    only on a round base, no pocket pair on a ring
    a pair's first feature at x > 0, twins apart    either side; the twins may overlap
    a slot along X or along Y                       a slot at any angle
    feature sizes tied to how many features         each size drawn by itself, from 1.5% to 80%
    there are                                       of the base
    a shelled base takes only bosses                cuts through a shelled base are allowed

What stays fixed is what the FreeCAD recipes fix (forge/freecad/recipes.py): every sketch is
on the XY plane, cuts start at the base's top face, a pair is mirrored across YZ, a row runs
along X and is centred, a circle of holes starts on +X and goes round Z, a hexagon has two
flats facing +X and -X. A plan with a shell stands every boss on the shell's floor, so here
a shell comes before the first boss or pad.

Nothing is decided by arithmetic: every plan item is given to the CAD kernel
(wide_reference.Builder) and kept only if the result is one valid solid that the item
changed. That is what "anywhere it physically fits" means in code.

No part, goal or number of any outside system is read here. The ranges are our own choice.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass

from forge.freecad.wide_reference import Builder, Rejected
from forge.system1.splits import HELD_OUT_PAIRS
from forge.system1.steps import FEATURES, Step

BASES = ("block", "cylinder", "hex", "ring")
FEATURE_KINDS = tuple(FEATURES)
ADDED = ("boss", "pad", "boss_pair")            # features that add material
SMALLEST = 0.05                                 # mm; no size below this
TRIES_PER_ITEM = 14                             # kernel tries before a plan is given up

# ORDER patterns held out for the test slice `wide_order_v3`: plan item A IMMEDIATELY followed
# by plan item B. No other wide set holds any of them (B before A, or A and B with something
# between them, is allowed everywhere). None is one of the four held-out PAIRINGS, which are
# kept out of every wide set.
HELD_OUT_ORDERS: tuple[tuple[str, str], ...] = (
    ("polar", "boss"),              # something added right after a circle of holes
    ("pocket", "hole"),             # a drilled hole right after a milled pocket
    ("row", "pad"),                 # a rectangle added right after a row of holes
    ("hole_pair", "top_chamfer"),   # an edge treatment right after a mirrored pair
    ("slot", "blind_hole"),         # a blind hole right after a slot
    ("boss_pair", "counterbore"),   # a stepped hole right after a mirrored pair of bosses
)


@dataclass(frozen=True)
class Profile:
    """Where the numbers of a set come from."""

    name: str
    # (smallest, largest, weight) of the base's largest in-plane side, mm; log-uniform inside.
    scale_bands: tuple[tuple[float, float, float], ...]
    steps: dict[float, float]       # the step a number is rounded to -> how often
    angle_steps: dict[float, float]
    # Every part must hold at least one number written with this many decimals (0: no rule).
    needs_decimals: int = 0


# TRAIN leaves one band of scales out (70 to 85 mm) and stops at 5 and 500 mm; its numbers
# have at most two decimals. NUMBERS (test slice `wide_numbers_v3`) is exactly what is left
# out: the band in the middle, smaller and larger parts, and numbers with three and four
# decimals or in eighths.
TRAIN = Profile("train", ((5.0, 70.0, 0.6), (85.0, 500.0, 0.4)),
                {1.0: 0.20, 0.5: 0.12, 0.25: 0.10, 0.1: 0.26, 0.05: 0.08, 0.01: 0.24},
                {90.0: 0.2, 45.0: 0.15, 15.0: 0.2, 5.0: 0.15, 1.0: 0.2, 0.5: 0.1})
NUMBERS = Profile("numbers", ((70.0, 85.0, 0.5), (2.5, 5.0, 0.25), (500.0, 1000.0, 0.25)),
                  {0.001: 0.4, 0.125: 0.3, 0.0001: 0.3},
                  {0.001: 0.4, 0.125: 0.3, 7.5: 0.3}, needs_decimals=3)
PROFILES = {"train": TRAIN, "numbers": NUMBERS}
HELD_OUT_SCALES = tuple((low, high) for low, high, _ in NUMBERS.scale_bands)


def scale_of(start: Step) -> float:
    """The base's largest in-plane side: the number the scale bands are about."""
    s = start.slots
    return float({"block": max(s.get("length", 0), s.get("width", 0)),
                  "cylinder": s.get("diameter", 0), "hex": s.get("across_flats", 0),
                  "ring": s.get("outer_diameter", 0)}[start.kind])


def decimals_of(value: float) -> int:
    """How many decimals a plan number is written with (41.5 -> 1, 7.25 -> 2, 12 -> 0)."""
    text = repr(round(float(value), 6))
    return 0 if text.endswith(".0") else len(text.split(".")[1])


class Numbers:
    """Draws the numbers of one part: each is rounded to a step, not taken from a grid range."""

    def __init__(self, rng: random.Random, profile: Profile, scale: float) -> None:
        self.rng, self.profile = rng, profile
        # A step must leave room for sizes much smaller than the part.
        self.steps = {step: weight for step, weight in profile.steps.items() if step <= scale / 8}
        if not self.steps:
            self.steps = {min(profile.steps): 1.0}
        # Most parts are written with one step throughout (a drawing in 0.1 mm); the rest mix.
        self.one = self._step() if rng.random() < 0.55 else None

    def _step(self) -> float:
        return self.rng.choices(list(self.steps), weights=list(self.steps.values()))[0]

    def size(self, value: float) -> float:
        """A size near `value`, on this number's step, never zero."""
        step = self.one or self._step()
        return max(round(round(value / step) * step, 4), round(step, 4), SMALLEST)

    def place(self, value: float) -> float:
        """A position near `value` (zero and negative values are fine)."""
        step = self.one or self._step()
        return round(round(value / step) * step, 4) + 0.0   # + 0.0: never -0.0

    def angle(self) -> float:
        steps = self.profile.angle_steps
        step = self.rng.choices(list(steps), weights=list(steps.values()))[0]
        return round(round(self.rng.uniform(0.0, 180.0) / step) * step % 180.0, 4) + 0.0

    def log(self, low: float, high: float) -> float:
        """Log-uniform: 0.02 to 0.2 is as likely as 0.2 to 2."""
        return math.exp(self.rng.uniform(math.log(low), math.log(high)))


def draw_scale(rng: random.Random, profile: Profile) -> float:
    low, high, _ = rng.choices(profile.scale_bands,
                               weights=[band[2] for band in profile.scale_bands])[0]
    return math.exp(rng.uniform(math.log(low), math.log(high)))


def in_band(scale: float, profile: Profile) -> bool:
    return any(low <= scale < high for low, high, _ in profile.scale_bands)


# --- the base ----------------------------------------------------------------------------------

def draw_start(rng: random.Random, kind: str, profile: Profile) -> tuple[Step, Numbers] | None:
    """A base whose largest in-plane side is in one of the profile's bands (None: draw again)."""
    scale = draw_scale(rng, profile)
    n = Numbers(rng, profile, scale)
    side = n.size(scale)
    if kind == "block":
        other = n.size(scale * rng.uniform(0.2, 1.0))
        length, width = (side, other) if rng.random() < 0.7 else (other, side)
        slots = {"length": length, "width": width, "height": n.size(scale * n.log(0.03, 1.5))}
    elif kind == "cylinder":
        slots = {"diameter": side, "height": n.size(scale * n.log(0.03, 2.5))}
    elif kind == "hex":
        slots = {"across_flats": side, "height": n.size(scale * n.log(0.03, 2.5))}
    else:
        inner = n.size(scale * rng.uniform(0.15, 0.92))
        if inner >= side:
            return None
        slots = {"outer_diameter": side, "inner_diameter": inner,
                 "height": n.size(scale * n.log(0.03, 2.0))}
    start = Step(kind, slots)
    # Rounding can push the side over the edge of its band; the bands must hold exactly.
    return (start, n) if in_band(scale_of(start), profile) else None


@dataclass
class Outline:
    """What placing needs to know about the base."""

    kind: str
    half: tuple[float, float]       # half the bounding box in X and Y
    reach: float | None             # round bases: the radius features stay inside
    hollow: float                   # ring: radius of the opening
    width: float                    # the smaller in-plane side
    height: float

    @classmethod
    def of(cls, start: Step) -> Outline:
        s = start.slots
        if start.kind == "block":
            return cls("block", (s["length"] / 2, s["width"] / 2), None, 0.0,
                       min(s["length"], s["width"]), s["height"])
        if start.kind == "ring":
            return cls("ring", (s["outer_diameter"] / 2,) * 2, s["outer_diameter"] / 2,
                       s["inner_diameter"] / 2, s["outer_diameter"], s["height"])
        side = s["diameter"] if start.kind == "cylinder" else s["across_flats"]
        return cls(start.kind, (side / 2, side / 2), side / 2, 0.0, side, s["height"])

    def fits(self, x: float, y: float, hx: float, hy: float, gap: float) -> bool:
        """Is a footprint of half extents (hx, hy) at (x, y) on the base, `gap` from its edge?"""
        if self.reach is None:
            return abs(x) + hx <= self.half[0] - gap and abs(y) + hy <= self.half[1] - gap
        far = math.hypot(abs(x) + hx, abs(y) + hy)
        near = math.hypot(max(abs(x) - hx, 0.0), max(abs(y) - hy, 0.0))
        return far <= self.reach - gap and (not self.hollow or near >= self.hollow + gap)

    def anywhere(self, rng: random.Random) -> tuple[float, float]:
        """A point of the base's footprint, uniformly."""
        if self.reach is None:
            return (rng.uniform(-self.half[0], self.half[0]),
                    rng.uniform(-self.half[1], self.half[1]))
        low = (self.hollow / self.reach) ** 2
        radius = self.reach * math.sqrt(rng.uniform(low, 1.0))
        turn = rng.uniform(0.0, 2 * math.pi)
        return radius * math.cos(turn), radius * math.sin(turn)


# --- features ----------------------------------------------------------------------------------

def _place(rng: random.Random, n: Numbers, base: Outline, hx: float, hy: float,
           ) -> tuple[float, float, str] | None:
    """(x, y, how it was placed). Four ways, so that centres, axes, clear spots and spots
    that break out of an edge or land on another feature all occur."""
    mode = rng.choices(("centre", "axis", "inside", "free"), weights=(0.08, 0.17, 0.45, 0.30))[0]
    if mode == "centre":
        return 0.0, 0.0, mode
    for _ in range(30):
        x, y = base.anywhere(rng)
        if mode == "axis":
            x, y = rng.choice(((x, 0.0), (0.0, y)))
        x, y = n.place(x), n.place(y)
        # "inside": the whole footprint on the base, from a hair to a tenth of it from the edge.
        if mode != "inside" or base.fits(x, y, hx, hy, base.width * n.log(0.002, 0.1)):
            return x, y, mode
    return None


def draw_feature(rng: random.Random, n: Numbers, base: Outline, kind: str,
                 ) -> tuple[Step, str] | None:
    """One feature with every number drawn (None when no place was found for it)."""
    single = kind.removesuffix("_pair")
    w, h, tall = base.width, base.height, max(base.width, base.height)
    depth = n.size(h * rng.uniform(0.05, 0.97))
    if depth >= h:              # a cut that is not "through" keeps a floor
        return None
    if single == "hole":
        slots = {"diameter": n.size(w * n.log(0.015, 0.55))}
        half = (slots["diameter"] / 2,) * 2
    elif single == "blind_hole":
        slots = {"diameter": n.size(w * n.log(0.015, 0.55)), "depth": depth}
        half = (slots["diameter"] / 2,) * 2
    elif single == "counterbore":
        bore = n.size(w * n.log(0.015, 0.35))
        wide = n.size(bore * rng.uniform(1.15, 2.6))
        if wide <= bore:
            return None
        slots = {"hole_diameter": bore, "diameter": wide, "depth": depth}
        half = (wide / 2,) * 2
    elif single == "boss":
        slots = {"diameter": n.size(w * n.log(0.03, 0.7)), "height": n.size(tall * n.log(0.02, 1.2))}
        half = (slots["diameter"] / 2,) * 2
    elif single in ("pad", "pocket"):
        slots = {"length": n.size(w * n.log(0.03, 0.8)), "width": n.size(w * n.log(0.03, 0.8))}
        slots |= {"height": n.size(tall * n.log(0.02, 1.2))} if single == "pad" else {"depth": depth}
        half = (slots["length"] / 2, slots["width"] / 2)
    elif single == "slot":
        width = n.size(w * n.log(0.02, 0.3))
        length = n.size(width * rng.uniform(1.3, 6.0))
        if length <= width:
            return None
        slots = {"length": length, "width": width, "depth": depth, "angle": n.angle()}
        half = (length / 2, length / 2)         # at any angle it stays inside this square
    elif single == "polar":
        count = rng.randint(2, 12)
        bore = n.size(w * n.log(0.015, 0.25))
        circle = n.size(2 * math.hypot(*base.anywhere(rng)))
        spots = [(circle / 2 * math.cos(2 * math.pi * k / count),
                  circle / 2 * math.sin(2 * math.pi * k / count)) for k in range(count)]
        # Every hole's centre is on the base, so every copy cuts something.
        if not all(base.fits(x, y, 0.0, 0.0, 0.0) for x, y in spots):
            return None
        return Step(kind, {"count": count, "hole_diameter": bore, "circle_diameter": circle}), \
            "pattern"
    else:                       # row
        count = rng.randint(2, 9)
        bore = n.size(w * n.log(0.015, 0.25))
        spacing = n.size(rng.choice((bore * rng.uniform(1.05, 4.0),
                                     2 * base.half[0] * rng.uniform(0.05, 0.9) / (count - 1))))
        y = n.place(rng.choice((0.0, base.anywhere(rng)[1])))
        ends = (count - 1) * spacing / 2
        if not all(base.fits(x, y, 0.0, 0.0, 0.0) for x in (-ends, ends)):
            return None
        return Step(kind, {"count": count, "hole_diameter": bore, "spacing": spacing, "y": y}), \
            "pattern"
    placed = _place(rng, n, base, *half)
    if placed is None:
        return None
    x, y, mode = placed
    if kind.endswith("_pair") and abs(x) < SMALLEST:    # the twin must be another feature
        return None
    return Step(kind, {**slots, "x": x, "y": y}), mode


def draw_treatment(rng: random.Random, n: Numbers, base: Outline, kind: str) -> Step:
    small = min(base.width, base.height)
    if kind == "corner_radius":
        return Step(kind, {"radius": n.size(base.width * n.log(0.01, 0.45))})
    if kind == "top_chamfer":
        return Step(kind, {"size": n.size(small * n.log(0.01, 0.3))})
    if kind == "top_fillet":
        return Step(kind, {"radius": n.size(small * n.log(0.01, 0.3))})
    return Step(kind, {"wall_thickness": n.size(small * rng.uniform(0.04, 0.35))})


# --- the plan ----------------------------------------------------------------------------------

def pairing_with(kinds: list[str], kind: str) -> bool:
    """Would `kind` complete one of the four held-out pairings with what the plan has?"""
    present = set(kinds)
    return any((a == kind and b in present) or (b == kind and a in present)
               for a, b in HELD_OUT_PAIRS)


def held_out_orders_in(kinds: list[str]) -> list[tuple[str, str]]:
    return [pair for pair in itertools.pairwise(kinds) if pair in HELD_OUT_ORDERS]


def _treatment_kinds(base: str, kinds: list[str]) -> list[str]:
    # Only a block and a hexagon have vertical edges of their own to round.
    options = ["top_chamfer", "top_fillet"] + (["corner_radius"] if base in ("block", "hex") else [])
    if "shell" not in kinds and not any(kind in ADDED for kind in kinds):
        options.append("shell")
    return options


def sample_plan(rng: random.Random, base_kind: str, items: int, profile: Profile = TRAIN,
                held_out_order: bool = False) -> tuple[Builder, dict] | None:
    """One plan of `items` items (the base included), built with the kernel as it is drawn.

    Returns (the builder holding the plan, its program and its volumes; facts about how it
    was drawn), or None when this draw did not work out (the caller draws again).
    With `held_out_order` the plan holds one of HELD_OUT_ORDERS; without, it never does.
    """
    drawn = None
    for _ in range(20):
        drawn = draw_start(rng, base_kind, profile)
        if drawn is not None:
            break
    if drawn is None:
        return None
    start, n = drawn
    try:
        builder = Builder(start)
    except Rejected:
        return None
    base = Outline.of(start)

    # Which places hold an edge treatment: 0 to 3 of them, anywhere after the base.
    wanted = min(items - 1, rng.choices((0, 1, 2, 3), weights=(0.40, 0.38, 0.17, 0.05))[0])
    treated = set(rng.sample(range(1, items), wanted))
    forced: dict[int, str] = {}
    if held_out_order:
        if items < 3:
            return None
        first, second = rng.choice(HELD_OUT_ORDERS)
        at = rng.randint(1, items - 2)
        forced = {at: first, at + 1: second}
        treated -= set(forced)

    kinds: list[str] = [base_kind]
    modes: list[str] = []
    for index in range(1, items):
        for _ in range(TRIES_PER_ITEM):
            if index in forced:
                kind = forced[index]
            elif index in treated:
                kind = rng.choice(_treatment_kinds(base_kind, kinds))
            else:
                kind = rng.choice(FEATURE_KINDS)
            if kind in FEATURES and pairing_with(kinds, kind):
                continue
            if index not in forced and (kinds[-1], kind) in HELD_OUT_ORDERS:
                continue
            if kind in FEATURES:
                made = draw_feature(rng, n, base, kind)
                if made is None:
                    continue
                step, mode = made
            else:
                step, mode = draw_treatment(rng, n, base, kind), "treatment"
            try:
                builder.add(step)
            except Rejected:
                # A treatment the solid has no room for: let a feature take its place.
                if index in treated and index not in forced and rng.random() < 0.25:
                    treated.discard(index)
                continue
            kinds.append(kind)
            modes.append(mode)
            break
        else:
            return None
    found = held_out_orders_in(kinds)
    if bool(found) != held_out_order:
        return None
    numbers = [value for step in builder.steps for slot, value in step.slots.items()
               if slot != "count"]
    if max(decimals_of(value) for value in numbers) < profile.needs_decimals:
        return None
    return builder, {"profile": profile.name, "scale": scale_of(start),
                     "one_step": n.one, "placed": modes}
