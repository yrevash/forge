"""One part of a structure as plain numbers, and what arithmetic can say about a set of parts.

Every part is a BOX, an upright CYLINDER, or a cylinder LYING left to right
("cylinder_x": a wheel, a rung, a handle bar) at an exact place. That is all the
kinds of structure need, and it keeps everything checkable by hand:

- a part's volume and bounding box are one line of arithmetic;
- whether two parts overlap, share a face, or miss each other is a comparison
  of intervals (and, for a cylinder, of a circle with a rectangle).

Nothing in this file touches the CAD kernel. The kernel measures the same
things from the generated program (forge/assembly_geometry.py) and the two
answers are compared in check.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EPS = 1e-6  # mm; two faces closer than this are "touching"
ROUND_AXIS = {"cylinder": 2, "cylinder_x": 0}

Triple = tuple[float, float, float]


@dataclass(frozen=True)
class Solid:
    name: str
    shape: str      # "box", "cylinder" (upright) or "cylinder_x" (lying left to right)
    size: Triple    # its extent along x, y, z. cylinder: diameter, diameter, height.
                    # cylinder_x: length, diameter, diameter
    at: Triple      # the middle of its lowest face (for a lying cylinder: of its lowest line)

    @property
    def volume(self) -> float:
        x, y, z = self.size
        if self.shape == "box":
            return x * y * z
        if self.shape == "cylinder":
            return math.pi * x * x / 4.0 * z
        return math.pi * z * z / 4.0 * x

    @property
    def axis(self) -> int | None:
        """The axis a round part runs along (0 = X, 2 = Z); None for a box."""
        return ROUND_AXIS.get(self.shape)

    @property
    def low(self) -> Triple:
        return (self.at[0] - self.size[0] / 2, self.at[1] - self.size[1] / 2, self.at[2])

    @property
    def high(self) -> Triple:
        return (self.at[0] + self.size[0] / 2, self.at[1] + self.size[1] / 2,
                self.at[2] + self.size[2])


def bounding_box(solids: list[Solid]) -> tuple[Triple, Triple]:
    """Lowest and highest corner of everything together."""
    low = tuple(min(s.low[axis] for s in solids) for axis in range(3))
    high = tuple(max(s.high[axis] for s in solids) for axis in range(3))
    return low, high


def _depth(a: Solid, b: Solid, axis: int) -> float:
    """How far the two parts' extents run into each other along one axis (negative: a gap)."""
    return min(a.high[axis], b.high[axis]) - max(a.low[axis], b.low[axis])


def _across_axis(a: Solid, b: Solid) -> str:
    """Seen along the axis of round part `a`: do the two outlines keep 'apart', 'touch' or
    share 'area'? `b` is a box, or a round part with the same axis."""
    u, v = (i for i in range(3) if i != a.axis)       # the two axes the circle lies in
    radius = (a.high[u] - a.low[u]) / 2
    centre = ((a.low[u] + a.high[u]) / 2, (a.low[v] + a.high[v]) / 2)
    if b.shape == "box":
        # Distance from the circle's centre to the rectangle (zero if the centre is inside).
        du = max(b.low[u] - centre[0], 0.0, centre[0] - b.high[u])
        dv = max(b.low[v] - centre[1], 0.0, centre[1] - b.high[v])
        gap = math.hypot(du, dv) - radius
    else:
        other = ((b.low[u] + b.high[u]) / 2, (b.low[v] + b.high[v]) / 2)
        gap = math.dist(centre, other) - radius - (b.high[u] - b.low[u]) / 2
    return "apart" if gap > EPS else "touch" if gap > -EPS else "area"


def relation(a: Solid, b: Solid) -> str:
    """How two parts meet.

    apart    they do not meet
    overlap  they share volume: never allowed
    face     they share a patch of surface with real area: a proper joint
    line     a round part touches a flat one along a line (a rail meeting a round leg)
    edge     they meet only along an edge or at a corner: not counted as joined
    """
    depths = [_depth(a, b, axis) for axis in range(3)]
    if min(depths) < -EPS:
        return "apart"                                # their bounding boxes do not even meet
    if a.shape == "box" and b.shape == "box":
        through = sum(d > EPS for d in depths)        # axes along which they truly overlap
        return "overlap" if through == 3 else "face" if through == 2 else "edge"
    if a.shape == "box":
        a, b = b, a                                   # now `a` is round
    if b.axis is not None and b.axis != a.axis:
        # An upright round part against a lying one. No kind needs it, and the answer is
        # not a comparison of a circle with a rectangle, so it is refused, not guessed.
        raise NotImplementedError(f"{a.name} and {b.name}: round parts with crossed axes")
    # Along the round part's axis the two are intervals; across it, a circle and an outline.
    along = depths[a.axis]
    outline = _across_axis(a, b)
    if outline == "apart":
        return "apart"
    if outline == "area":
        return "overlap" if along > EPS else "face"   # end face against a flat face
    return "line" if along > EPS else "edge"


def contacts(solids: list[Solid]) -> dict[tuple[str, str], str]:
    """Every pair of parts that meets, with how it meets. Pairs that are apart are left out."""
    found = {}
    for i, a in enumerate(solids):
        for b in solids[i + 1:]:
            how = relation(a, b)
            if how != "apart":
                found[(a.name, b.name)] = how
    return found


def groups(names: list[str], joined: list[tuple[str, str]]) -> list[set[str]]:
    """Split parts into groups that hang together through the given joints."""
    group_of = {name: {name} for name in names}
    for a, b in joined:
        if group_of[a] is not group_of[b]:
            merged = group_of[a] | group_of[b]
            for name in merged:
                group_of[name] = merged
    unique = []
    for group in group_of.values():
        if not any(group is seen for seen in unique):
            unique.append(group)
    return unique


def problems(solids: list[Solid]) -> list[str]:
    """What arithmetic finds wrong with a set of parts. Empty means: nothing overlaps
    and every part is joined to the rest by a face (or a round leg's line)."""
    found = []
    names = [s.name for s in solids]
    if len(set(names)) != len(names):
        found.append("two parts share a name")
    meets = contacts(solids)
    found += [f"{a} and {b} overlap" for (a, b), how in meets.items() if how == "overlap"]
    joints = [pair for pair, how in meets.items() if how in ("face", "line")]
    parts = groups(names, joints)
    if len(parts) > 1:
        loose = sorted(min(parts, key=len))
        found.append(f"not one connected structure: {len(parts)} groups, e.g. {loose[:3]}")
    return found
