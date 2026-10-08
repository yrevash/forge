"""Composed parts: one base shape plus a random mix of features in random order.

Every other family is a fixed recipe: a flange always has a bore and a bolt
circle. A model could learn 65 recipes without ever learning to combine
features. Here the structure itself is random: pick a base, then add one to
five features of random kinds, in random order, and the same kind may repeat.

The design: a base, then bosses, holes, pockets, patterns, mirrors and at most
one edge treatment. A random structure forces a model to read the request
instead of memorizing a template.

How the volume stays exact
- Every feature has a footprint on the XY plane. Footprints are kept MIN_WALL
  apart from each other and from the edge, so no two features ever interact and
  the volume is the base plus every boss minus every cut.
- An edge treatment (fillet, chamfer, shell) is applied to the bare base, before
  any feature, and features stay clear of the treated edge. The treated base has
  a closed-form volume (see `_treated_volume`).
- Each feature is its own solid at an absolute position, then cut or joined.

Two layers
- Sampling (functions that take `rng`): choose the sizes and the places.
- Building (`make_base`, `apply_treatment`, `draft_of`, `spots_of`, `clear`,
  `assemble`): turn chosen numbers into footprints, a program and its expected
  measurements. No randomness. The step engine (forge/system1/engine.py) calls
  this layer directly, so a part built step by step and a part sampled in one
  go come from the same code.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from forge.generators.base import HEX_AREA_FACTOR, HEX_CORNER_FACTOR, Part, circle_area, program
from forge.generators.families._common import MIN_WALL, count_cylinders, no_standard_parts, span

Number = float | int
Spot = tuple[float, float]

# A chamfer, fillet or wall takes at most this share of the smallest base dimension.
TREATMENT_SHARE = 0.2
# A feature may take this share of the free width, by how many features are wanted:
# the more features, the smaller each must be for all of them to fit.
SIZE_SHARE = {1: 0.5, 2: 0.45, 3: 0.4, 4: 0.35, 5: 0.3}
# Past five features (only asked for explicitly, see `Composed.sample`) the share keeps
# falling the same way: 1.5 / 5 is the 0.3 above.
LONG_SHARE = 1.5
CUTS = ("hole", "blind_hole", "counterbore", "pocket", "slot", "polar", "row",
        "hole_pair", "pocket_pair")

# How often each feature kind is tried, per base. Kinds that do not suit a base are absent:
# a linear row belongs on a block, a polar pattern on a round base.
KIND_WEIGHTS: dict[str, dict[str, int]] = {
    "block": {"hole": 3, "blind_hole": 2, "counterbore": 2, "boss": 2, "pad": 2, "pocket": 2,
              "slot": 2, "row": 3, "hole_pair": 2, "pocket_pair": 1, "boss_pair": 1},
    "cylinder": {"hole": 3, "blind_hole": 2, "counterbore": 2, "boss": 2, "pad": 1, "pocket": 2,
                 "slot": 2, "polar": 3, "hole_pair": 2, "pocket_pair": 1, "boss_pair": 1},
    "hex": {"hole": 3, "blind_hole": 2, "counterbore": 2, "boss": 2, "pad": 1, "pocket": 2,
            "slot": 2, "polar": 3, "hole_pair": 2, "pocket_pair": 1, "boss_pair": 1},
    "ring": {"hole": 3, "blind_hole": 2, "counterbore": 2, "boss": 1, "pad": 1, "pocket": 1,
             "slot": 1, "polar": 4, "hole_pair": 2, "boss_pair": 1},
}
TREATMENTS = {"block": ("corner_radius", "top_chamfer", "top_fillet_radius", "wall_thickness"),
              "cylinder": ("top_chamfer", "top_fillet_radius", "wall_thickness"),
              "hex": ("top_chamfer", "top_fillet_radius", "wall_thickness"),
              "ring": ("top_chamfer", "top_fillet_radius")}


def _pick(rng: random.Random, low: float, high: float, step: float) -> float:
    """A grid value in [low, high], always a float (a whole-number parameter means a count)."""
    return float(span(rng, float(low), float(high), step))


# ---------------------------------------------------------------------------
# The base
# ---------------------------------------------------------------------------

@dataclass
class Base:
    kind: str
    params: dict[str, Number]
    build: list[str]              # chained calls that make the solid `base`
    height: float
    half: Spot                    # half the bounding box in X and Y
    # Cross-section area after moving every side inwards by d:
    #   A(d) = area - perimeter * d + corner * d**2
    area: float
    perimeter: float
    corner: float
    smallest: float               # smallest dimension; limits chamfers, fillets and walls
    reach: float | None = None    # round bases: features stay inside this radius
    hollow: float = 0.0           # ring: radius of the centre opening
    margin: float = MIN_WALL      # distance kept between a feature and the edge
    floor: str = "base_height"    # the z where a boss starts
    floor_z: float = 0.0
    shelled: bool = False
    volume: float = 0.0
    cylinders: list[tuple[float, int | None]] = field(default_factory=list)

    @property
    def room(self) -> float:
        """Free width a single feature could use."""
        if self.reach is None:
            return 2 * (min(self.half) - self.margin)
        if self.hollow:
            return self.reach - self.hollow - 2 * self.margin
        return 2 * (self.reach - self.margin)


def _bare_base(rng: random.Random, kind: str) -> Base:
    """Sample the sizes of a bare base."""
    if kind == "block":
        length = _pick(rng, 40, 160, 5)
        width = _pick(rng, 30, min(length, 120), 5)    # the length is the longer side
        height = _pick(rng, 6, min(40, width), 1)      # a block, not a tower
        return make_base(kind, {"base_length": length, "base_width": width,
                                "base_height": height})
    if kind == "cylinder":
        diameter = _pick(rng, 30, 120, 5)
        height = _pick(rng, 6, min(60, diameter), 1)   # no taller than it is wide
        return make_base(kind, {"base_diameter": diameter, "base_height": height})
    if kind == "hex":
        flats = _pick(rng, 30, 100, 2)
        height = _pick(rng, 6, min(50, flats), 1)      # no taller than it is wide
        return make_base(kind, {"base_across_flats": flats, "base_height": height})
    outer = _pick(rng, 50, 140, 5)
    # The wall is wide enough to carry holes, and the opening stays at least 16 mm.
    wall = _pick(rng, 10, min(30, outer / 2 - 8), 1)
    inner = outer - 2 * wall
    height = _pick(rng, 6, 40, 1)
    return make_base(kind, {"base_outer_diameter": outer, "base_inner_diameter": inner,
                            "base_height": height})


def make_base(kind: str, params: dict[str, Number]) -> Base:
    """The bare base with the given sizes: its program, footprint and section arithmetic."""
    params = dict(params)
    height = params["base_height"]
    if kind == "block":
        length, width = params["base_length"], params["base_width"]
        base = Base(kind, params,
                    [".box(base_length, base_width, base_height, centered=(True, True, False))"],
                    height, (length / 2, width / 2), length * width, 2 * (length + width), 4.0,
                    min(length, width, height))
    elif kind == "cylinder":
        diameter = params["base_diameter"]
        base = Base(kind, params,
                    [".circle(base_diameter / 2)", ".extrude(base_height)"],
                    height, (diameter / 2, diameter / 2), circle_area(diameter),
                    math.pi * diameter, math.pi, min(diameter, height), reach=diameter / 2,
                    cylinders=[(diameter, 1)])
    elif kind == "hex":
        flats = params["base_across_flats"]
        # A hexagon moved in by d on every side is a hexagon of across-flats (flats - 2d).
        # Features stay inside the inscribed circle, which is simple and a little cautious.
        base = Base(kind, params,
                    [".polygon(6, base_across_flats, circumscribed=True)",
                     ".extrude(base_height)"],
                    # Drawn this way the hexagon has flats facing +X and -X, corners on Y.
                    height, (flats / 2, flats * HEX_CORNER_FACTOR / 2), HEX_AREA_FACTOR * flats**2,
                    2 * math.sqrt(3) * flats, 2 * math.sqrt(3), min(flats, height),
                    reach=flats / 2)
    else:
        outer, inner = params["base_outer_diameter"], params["base_inner_diameter"]
        wall = (outer - inner) / 2
        # Moving both walls in by d: pi*(R-d)^2 - pi*(r+d)^2, so the d^2 terms cancel.
        base = Base(kind, params,
                    [".circle(base_outer_diameter / 2)", ".circle(base_inner_diameter / 2)",
                     ".extrude(base_height)"],
                    height, (outer / 2, outer / 2), circle_area(outer) - circle_area(inner),
                    math.pi * (outer + inner), 0.0, min(wall, height), reach=outer / 2,
                    hollow=inner / 2, cylinders=[(outer, 1), (inner, 1)])
    base.volume = base.area * base.height
    base.floor_z = base.height            # bosses stand on the top face, unless shelled
    return base


def _treated_volume(base: Base, treatment: str | None, size: float) -> float:
    """Exact volume of the base after its edge treatment.

    Top chamfer and top fillet: in the top layer the section shrinks by d(z) on
    every side, so the volume lost is perimeter * I1 - corner * I2, where
    I1 = integral of d and I2 = integral of d^2 over the layer.
      chamfer c:  d goes 0..c in a straight line   I1 = c^2 / 2            I2 = c^3 / 3
      fillet r:   d = r - sqrt(r^2 - z^2)          I1 = r^2 * (1 - pi/4)   I2 = r^3 * (5/3 - pi/2)
    Shell with wall t: the cavity is the section moved in by t, (height - t) deep.
    Vertical fillet r on a block: each corner loses (r^2 - pi*r^2/4) of area.
    """
    full = base.area * base.height
    if treatment == "top_chamfer":
        return full - base.perimeter * size**2 / 2 + base.corner * size**3 / 3
    if treatment == "top_fillet_radius":
        return (full - base.perimeter * size**2 * (1 - math.pi / 4)
                + base.corner * size**3 * (5 / 3 - math.pi / 2))
    if treatment == "wall_thickness":
        cavity = base.area - base.perimeter * size + base.corner * size**2
        return full - cavity * (base.height - size)
    if treatment == "corner_radius":
        return (base.area - (4 - math.pi) * size**2) * base.height
    return full


def treatment_range(base: Base, treatment: str) -> tuple[float, float]:
    """Smallest and largest size an edge treatment may have on this base."""
    limit = TREATMENT_SHARE * base.smallest
    if treatment == "corner_radius":
        # Rounded vertical corners: up to a quarter of the shorter side.
        return 1, min(base.half) / 2
    if treatment in ("top_chamfer", "top_fillet_radius"):
        return 0.5, min(5, limit)
    return 1.5, min(5, limit)


def _treat(rng: random.Random, base: Base, treatment: str) -> None:
    """Sample the size of one edge treatment and apply it to the bare base."""
    low, high = treatment_range(base, treatment)
    apply_treatment(base, treatment, _pick(rng, low, high, 0.5))


def apply_treatment(base: Base, treatment: str, size: float) -> None:
    """Apply one edge treatment to the bare base and note what it changes."""
    if treatment == "corner_radius":
        base.build += ['.edges("|Z")', ".fillet(base_corner_radius)"]
        # A feature corner at distance m from two sides is r - sqrt(2)*(r - m) from the
        # arc; asking for MIN_WALL there gives this margin.
        base.margin = max(MIN_WALL, size - (size - MIN_WALL) / math.sqrt(2))
        base.cylinders.append((2 * size, 4))
    elif treatment == "top_chamfer":
        base.build += ['.faces(">Z")', ".edges()", ".chamfer(base_top_chamfer)"]
        base.margin = size + MIN_WALL       # features sit on the flat part of the top
    elif treatment == "top_fillet_radius":
        base.build += ['.faces(">Z")', ".edges()", ".fillet(base_top_fillet_radius)"]
        base.margin = size + MIN_WALL
        # Straight top edges become quarter cylinders; round ones become part of a torus.
        straight = {"block": 4, "hex": 6}.get(base.kind)
        if straight:
            base.cylinders.append((2 * size, straight))
    else:
        base.build += ['.faces(">Z")', ".shell(-base_wall_thickness)"]
        base.margin = size + MIN_WALL       # bosses stand on the floor, clear of the walls
        base.floor, base.floor_z, base.shelled = "base_wall_thickness", size, True
        if base.kind == "cylinder":
            base.cylinders.append((base.params["base_diameter"] - 2 * size, 1))
    base.params[f"base_{treatment}"] = size
    base.volume = _treated_volume(base, treatment, size)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

@dataclass
class Draft:
    """A feature with its sizes chosen but not yet its place."""
    kind: str
    sizes: dict[str, Number]
    half: Spot                    # half the footprint of one instance, in X and Y
    volume: float                 # of one instance; negative for a cut
    calls: list[str]              # chained calls building the solid; {p} is the name
    is_round: bool = False        # the footprint is a disc of radius half[0]
    rise: float = 0.0             # how far a boss stands above the floor
    diameters: tuple[tuple[float, int], ...] = ()   # (diameter, faces per instance)
    layout: str = "single"        # single | pair | polar | row


def _visible(big: float) -> float:
    """Smallest side of a boss or pocket: 5 mm, or a quarter of the largest allowed if that
    is more, so a feature is never a speck on a large base."""
    return max(5.0, math.ceil(big / 4))


def _centre(offset: str | None) -> list[str]:
    start = [f".workplane(offset={offset})"] if offset else []
    return [*start, ".center({p}_x, {p}_y)"]


# Each feature kind has two functions. `_<kind>` samples its sizes; `<kind>_draft`
# turns given sizes into a Draft, with no randomness.

def _hole(rng: random.Random, base: Base, big: float) -> Draft:
    return hole_draft(base, _pick(rng, 3, min(big, 30), 0.5))


def hole_draft(base: Base, diameter: float) -> Draft:
    d = diameter
    return Draft("hole", {"diameter": d}, (d / 2, d / 2), -circle_area(d) * base.height,
                 [*_centre(None), ".circle({p}_diameter / 2)", ".extrude(base_height)"],
                 is_round=True, diameters=((d, 1),))


def _blind_hole(rng: random.Random, base: Base, big: float) -> Draft:
    d = _pick(rng, 3, min(big, 30), 0.5)
    # A drilled hole is rarely deeper than three diameters, and a floor of MIN_WALL stays.
    depth = _pick(rng, 1, min(base.height - MIN_WALL, 3 * d), 0.5)
    return blind_hole_draft(base, d, depth)


def blind_hole_draft(base: Base, diameter: float, depth: float) -> Draft:
    d = diameter
    return Draft("blind_hole", {"diameter": d, "depth": depth}, (d / 2, d / 2),
                 -circle_area(d) * depth,
                 [*_centre("base_height - {p}_depth"), ".circle({p}_diameter / 2)",
                  ".extrude({p}_depth)"],
                 is_round=True, diameters=((d, 1),))


def _counterbore(rng: random.Random, base: Base, big: float) -> Draft:
    d = _pick(rng, 3, min(big - 3, 12), 0.5)
    # The recess is 2 to 6 mm wider than the hole (room for a screw head) and no deeper
    # than it is wide.
    wide = math.ceil(d + 2) + _pick(rng, 0, 4, 0.5)
    depth = _pick(rng, 1.5, min(base.height - MIN_WALL, wide), 0.5)
    return counterbore_draft(base, d, wide, depth)


def counterbore_draft(base: Base, hole_diameter: float, diameter: float, depth: float) -> Draft:
    d, wide = hole_diameter, diameter
    removed = circle_area(d) * base.height + (circle_area(wide) - circle_area(d)) * depth
    return Draft("counterbore", {"hole_diameter": d, "diameter": wide, "depth": depth},
                 (wide / 2, wide / 2), -removed,
                 [*_centre(None), ".circle({p}_hole_diameter / 2)", ".extrude(base_height)",
                  ".union(", '    cq.Workplane("XY")',
                  "    .workplane(offset=base_height - {p}_depth)",
                  "    .center({p}_x, {p}_y)", "    .circle({p}_diameter / 2)",
                  "    .extrude({p}_depth)", ")"],
                 is_round=True, diameters=((d, 1), (wide, 1)))


def _boss(rng: random.Random, base: Base, big: float) -> Draft:
    d = _pick(rng, _visible(big), big, 1)
    # Neither a film nor a needle: from a quarter of its diameter to twice its diameter.
    h = _pick(rng, max(2, math.ceil(d / 4)), min(30, 2 * d), 1)
    return boss_draft(base, d, h)


def boss_draft(base: Base, diameter: float, height: float) -> Draft:
    d, h = diameter, height
    return Draft("boss", {"diameter": d, "height": h}, (d / 2, d / 2), circle_area(d) * h,
                 [*_centre(base.floor), ".circle({p}_diameter / 2)", ".extrude({p}_height)"],
                 is_round=True, rise=h, diameters=((d, 1),))


def _pad(rng: random.Random, base: Base, big: float) -> Draft:
    length, width = _pick(rng, _visible(big), big, 1), _pick(rng, _visible(big), big, 1)
    # From a quarter of its narrow side to twice its narrow side.
    narrow = min(length, width)
    h = _pick(rng, max(2, math.ceil(narrow / 4)), min(30, 2 * narrow), 1)
    return pad_draft(base, length, width, h)


def pad_draft(base: Base, length: float, width: float, height: float) -> Draft:
    h = height
    return Draft("pad", {"length": length, "width": width, "height": h},
                 (length / 2, width / 2), length * width * h,
                 [*_centre(base.floor), ".rect({p}_length, {p}_width)", ".extrude({p}_height)"],
                 rise=h)


def _pocket(rng: random.Random, base: Base, big: float) -> Draft:
    length, width = _pick(rng, _visible(big), big, 1), _pick(rng, _visible(big), big, 1)
    # A floor of MIN_WALL stays, and a milled pocket is no deeper than its narrow side.
    depth = _pick(rng, 1, min(base.height - MIN_WALL, length, width), 0.5)
    return pocket_draft(base, length, width, depth)


def pocket_draft(base: Base, length: float, width: float, depth: float) -> Draft:
    return Draft("pocket", {"length": length, "width": width, "depth": depth},
                 (length / 2, width / 2), -length * width * depth,
                 [*_centre("base_height - {p}_depth"), ".rect({p}_length, {p}_width)",
                  ".extrude({p}_depth)"])


def _slot(rng: random.Random, base: Base, big: float) -> Draft:
    width = _pick(rng, 3, max(3, big / 2), 0.5)
    # A slot is at least twice as long as it is wide; shorter would read as a hole.
    length = _pick(rng, math.ceil(2 * width), max(2 * width, 1.5 * big), 1)
    depth = _pick(rng, 1, min(base.height - MIN_WALL, 2 * width), 0.5)   # at most twice its width
    angle = rng.choice([0.0, 90.0])     # along X or along Y
    return slot_draft(base, length, width, depth, angle)


def slot_draft(base: Base, length: float, width: float, depth: float, angle: float) -> Draft:
    area = (length - width) * width + circle_area(width)
    half = (length / 2, width / 2) if angle == 0 else (width / 2, length / 2)
    return Draft("slot", {"length": length, "width": width, "depth": depth, "angle": angle},
                 half, -area * depth,
                 [*_centre("base_height - {p}_depth"),
                  ".slot2D({p}_length, {p}_width, {p}_angle)", ".extrude({p}_depth)"],
                 diameters=((width, 2),))    # the two rounded ends


def _polar(rng: random.Random, base: Base, big: float) -> Draft:
    d = _pick(rng, 3, min(big, 12), 0.5)
    return polar_draft(base, rng.randint(3, 8), d)


def polar_draft(base: Base, count: int, hole_diameter: float) -> Draft:
    d = hole_diameter
    return Draft("polar", {"count": count, "hole_diameter": d}, (d / 2, d / 2),
                 -circle_area(d) * base.height,
                 [".polarArray({p}_circle_diameter / 2, 0, 360, {p}_count)",
                  ".circle({p}_hole_diameter / 2)", ".extrude(base_height)"],
                 is_round=True, diameters=((d, 1),), layout="polar")


def _row(rng: random.Random, base: Base, big: float) -> Draft:
    d = _pick(rng, 3, min(big, 12), 0.5)
    return row_draft(base, rng.randint(2, 5), d)


def row_draft(base: Base, count: int, hole_diameter: float) -> Draft:
    d = hole_diameter
    return Draft("row", {"count": count, "hole_diameter": d}, (d / 2, d / 2),
                 -circle_area(d) * base.height,
                 [".center(0, {p}_y)", ".rarray({p}_spacing, 1, {p}_count, 1)",
                  ".circle({p}_hole_diameter / 2)", ".extrude(base_height)"],
                 is_round=True, diameters=((d, 1),), layout="row")


def _pair(single: Callable[[random.Random, Base, float], Draft]) -> Callable:
    """The same feature twice, mirrored about the YZ plane: one at +x, its twin at -x."""
    def make(rng: random.Random, base: Base, big: float) -> Draft:
        return _as_pair(single(rng, base, 0.8 * big))
    return make


def _as_pair(draft: Draft) -> Draft:
    draft.kind += "_pair"
    draft.calls.append('.mirror("YZ", union=True)')
    draft.layout = "pair"
    return draft


MAKERS: dict[str, Callable[[random.Random, Base, float], Draft]] = {
    "hole": _hole, "blind_hole": _blind_hole, "counterbore": _counterbore, "boss": _boss,
    "pad": _pad, "pocket": _pocket, "slot": _slot, "polar": _polar, "row": _row,
    "hole_pair": _pair(_hole), "pocket_pair": _pair(_pocket), "boss_pair": _pair(_boss),
}

DRAFTS: dict[str, Callable[..., Draft]] = {
    "hole": hole_draft, "blind_hole": blind_hole_draft, "counterbore": counterbore_draft,
    "boss": boss_draft, "pad": pad_draft, "pocket": pocket_draft, "slot": slot_draft,
    "polar": polar_draft, "row": row_draft,
}
# The numbers that say where a feature goes, by layout. Every other number is a size.
PLACED_FIELDS = {"single": ("x", "y"), "pair": ("x", "y"), "polar": ("circle_diameter",),
                 "row": ("spacing", "y")}


def draft_of(kind: str, base: Base, sizes: dict[str, Number]) -> Draft:
    """The draft of a feature whose sizes are already known (`hole_pair` is a mirrored `hole`)."""
    if kind.endswith("_pair"):
        return _as_pair(DRAFTS[kind.removesuffix("_pair")](base, **sizes))
    return DRAFTS[kind](base, **sizes)


# A placed footprint: centre, half extents, and whether it is a disc.
Footprint = tuple[float, float, float, float, bool]


def _apart(a: Footprint, b: Footprint) -> bool:
    """True if two footprints keep MIN_WALL between them."""
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    if a[4] and b[4]:
        return math.hypot(dx, dy) - a[2] - b[2] >= MIN_WALL
    # Anything with a rectangle in it is compared as two boxes, which is cautious.
    return max(dx - a[2] - b[2], dy - a[3] - b[3]) >= MIN_WALL


def _inside(base: Base, f: Footprint) -> bool:
    """True if a footprint lies on the base, `margin` away from every edge."""
    x, y, hx, hy, is_round = f
    if base.reach is None:
        return (abs(x) + hx <= base.half[0] - base.margin
                and abs(y) + hy <= base.half[1] - base.margin)
    far = math.hypot(x, y) + hx if is_round else math.hypot(abs(x) + hx, abs(y) + hy)
    if far > base.reach - base.margin:
        return False
    if not base.hollow:
        return True
    near = (math.hypot(x, y) - hx if is_round
            else math.hypot(max(abs(x) - hx, 0), max(abs(y) - hy, 0)))
    return near >= base.hollow + base.margin


def _spot(rng: random.Random, base: Base) -> Spot:
    """A position on a 1 mm grid. Designers like the centre and the axes, so those come up often."""
    x = _pick(rng, -math.floor(base.half[0]), math.floor(base.half[0]), 1)
    y = _pick(rng, -math.floor(base.half[1]), math.floor(base.half[1]), 1)
    roll = rng.random()
    if roll < 0.2 and not base.hollow:
        return 0.0, 0.0
    if roll < 0.5:
        return rng.choice([(x, 0.0), (0.0, y)])
    return x, y


def polar_range(base: Base, count: int, diameter: float) -> tuple[float, float]:
    """Smallest and largest pitch circle for `count` holes of this diameter."""
    # Neighbouring holes keep MIN_WALL between them: the chord between two
    # centres, circle * sin(pi / n), is at least d + MIN_WALL.
    low = math.ceil(max((diameter + MIN_WALL) / math.sin(math.pi / count),
                        2 * (base.hollow + base.margin) + diameter))
    return low, 2 * (base.reach - base.margin) - diameter


def row_range(base: Base, count: int, diameter: float) -> tuple[float, float]:
    """Smallest and largest pitch for a centred row of `count` holes along X."""
    # Holes at least a diameter apart (pitch 2d); the row must fit the length.
    return math.ceil(2 * diameter), (2 * (base.half[0] - base.margin) - diameter) / (count - 1)


def spots_of(layout: str, sizes: dict[str, Number], placed: dict[str, Number]) -> list[Spot]:
    """Where each instance of a placed feature sits."""
    if layout == "polar":
        n, circle = sizes["count"], placed["circle_diameter"]
        # polarArray puts the first hole on +X and spaces the rest evenly.
        return [(circle / 2 * math.cos(2 * math.pi * k / n),
                 circle / 2 * math.sin(2 * math.pi * k / n)) for k in range(n)]
    if layout == "row":
        n, spacing, y = sizes["count"], placed["spacing"], placed["y"]
        # rarray centres the row on the workplane origin.
        return [((i - (n - 1) / 2) * spacing, y) for i in range(n)]
    x, y = placed["x"], placed["y"]
    return [(x, y), (-x, y)] if layout == "pair" else [(x, y)]


def footprints(draft: Draft, spots: list[Spot]) -> list[Footprint]:
    hx, hy = draft.half
    return [(x, y, hx, hy, draft.is_round) for x, y in spots]


def clear(base: Base, draft: Draft, prints: list[Footprint], taken: list[Footprint]) -> bool:
    """True if a feature's footprints are on the base and clear of everything already there."""
    twins_apart = draft.layout != "pair" or _apart(prints[0], prints[1])
    return (twins_apart and all(_inside(base, f) for f in prints)
            and all(_apart(f, other) for f in prints for other in taken))


def _place(rng: random.Random, base: Base, draft: Draft,
           taken: list[Footprint]) -> tuple[dict[str, Number], list[Spot]] | None:
    """Find a place for a draft: its position parameters and where each instance sits."""
    hx = draft.half[0]
    for _ in range(40):
        if draft.layout == "polar":
            low, high = polar_range(base, draft.sizes["count"], draft.sizes["hole_diameter"])
            if high < low:
                return None
            placed = {"circle_diameter": _pick(rng, low, high, 1)}
        elif draft.layout == "row":
            low, high = row_range(base, draft.sizes["count"], draft.sizes["hole_diameter"])
            if high < low:
                return None
            spacing = _pick(rng, low, min(high, low + 30), 1)
            y = rng.choice([0.0, _spot(rng, base)[1]])
            placed = {"spacing": spacing, "y": y}
        elif draft.layout == "pair":
            x, y = _spot(rng, base)
            x = abs(x) if x else float(math.ceil(hx + MIN_WALL))
            placed = {"x": x, "y": y}
        else:
            x, y = _spot(rng, base)
            placed = {"x": x, "y": y}
        spots = spots_of(draft.layout, draft.sizes, placed)
        prints = footprints(draft, spots)
        if clear(base, draft, prints, taken):
            taken.extend(prints)
            return placed, spots
    return None


def _solid(name: str, calls: list[str]) -> str:
    lines = [f"{name} = (", '    cq.Workplane("XY")', *(f"    {call}" for call in calls), ")"]
    return "\n".join(lines).replace("{p}", name)


# A feature that has its sizes and its place: the draft, where it goes, where each instance sits.
Placed = tuple[Draft, dict[str, Number], list[Spot]]


def assemble(family: str, base: Base, features: list[Placed]) -> Part:
    """The program and the expected measurements of a base with its features, in build order."""
    params = dict(base.params)
    solids = ["base = (", '    cq.Workplane("XY")', *(f"    {c}" for c in base.build), ")"]
    steps: list[str] = []
    cylinders = list(base.cylinders)
    volume, top = base.volume, base.height
    for number, (draft, placed, spots) in enumerate(features, start=1):
        name = f"{draft.kind}_{number}"     # numbered in build order
        for key, value in {**draft.sizes, **placed}.items():
            params[f"{name}_{key}"] = value
        solids.append(_solid(name, draft.calls))
        steps.append(f".union({name})" if draft.rise else f".cut({name})")
        volume += draft.volume * len(spots)
        top = max(top, base.floor_z + draft.rise) if draft.rise else top
        cylinders += [(diameter, faces * len(spots)) for diameter, faces in draft.diameters]
    body = "\n".join([*solids, "result = (", "    base", *(f"    {s}" for s in steps), ")"])
    return Part(family, params, program(params, body),
                [2 * base.half[0], 2 * base.half[1], top], volume,
                count_cylinders(*cylinders))


# ---------------------------------------------------------------------------
# The families
# ---------------------------------------------------------------------------

class Composed:
    """One family per base shape; the features on it are random in number, kind and order."""

    TABLE = None
    standard_parts = staticmethod(no_standard_parts)

    def __init__(self, base_kind: str, description: str) -> None:
        self.base_kind = base_kind
        self.NAME = f"composed_{base_kind}"
        self.DESCRIPTION = description

    def sample(self, rng: random.Random, features: tuple[int, int] | None = None) -> Part:
        """One random part. By default it has 1 to 5 features (an edge treatment counts as
        one). `features=(low, high)` asks for a count in that range instead and returns only
        parts that reach it; it exists for the longer test parts of forge/system1 and changes
        nothing when left out."""
        while True:
            part = self._attempt(rng, features)
            if part is not None:
                return part

    def _attempt(self, rng: random.Random, features: tuple[int, int] | None = None) -> Part | None:
        base = _bare_base(rng, self.base_kind)
        if features is None:
            wanted = rng.choices([1, 2, 3, 4, 5], weights=[2, 3, 4, 4, 3])[0]
        else:
            wanted = rng.randint(*features)
        # At most one edge treatment, and it counts as one of the features. A part is
        # never a treated base alone: other families already cover those.
        if wanted >= 2 and rng.random() < 0.45:
            options = [t for t in TREATMENTS[base.kind]
                       # A shell needs depth to be worth it: 10 mm high or more.
                       if t != "wall_thickness" or base.height >= 10]
            _treat(rng, base, rng.choice(options))
            wanted -= 1

        weights = dict(KIND_WEIGHTS[base.kind])
        if base.shelled:
            # A shelled base takes no cuts: only bosses, standing on its floor.
            weights = {k: v for k, v in weights.items() if k not in CUTS}
        big = max(base.room * SIZE_SHARE.get(wanted, LONG_SHARE / wanted), min(base.room, 8.0))

        chosen: list[Placed] = []
        taken: list[Footprint] = []
        used = {d for d, _ in base.cylinders}   # every round feature gets its own diameter

        for _ in range(8 * wanted):
            if len(chosen) == wanted:
                break
            kind = rng.choices(list(weights), weights=list(weights.values()))[0]
            draft = MAKERS[kind](rng, base, big)
            if any(d in used for d, _ in draft.diameters):
                continue
            found = _place(rng, base, draft, taken)
            if found is None:
                continue
            chosen.append((draft, *found))
            used.update(diameter for diameter, _ in draft.diameters)
        if not chosen or (features is not None and len(chosen) < wanted):
            return None
        return assemble(self.NAME, base, chosen)


COMPOSED_FAMILIES = (
    Composed("block", "Rectangular block with a random mix of bosses, holes, pockets, slots, "
                      "patterns and one optional edge treatment"),
    Composed("cylinder", "Cylinder with a random mix of bosses, holes, pockets, slots, polar "
                         "patterns and one optional edge treatment"),
    Composed("hex", "Hexagonal prism with a random mix of bosses, holes, pockets, slots, polar "
                    "patterns and one optional edge treatment"),
    Composed("ring", "Ring (thick tube) with a random mix of holes, bosses, polar patterns and "
                     "one optional edge treatment"),
)
