"""A Body is one solid of the structure, with everything known: shape, sizes, turn, place.

    world point = matrix * (point on the standing shape) + centre

`matrix` holds the turn (lying, pointing, a copy turned about an axis) and, for a
mirrored copy, a mirror. `centre` is where the middle of the standing frame ends up.
The frame (the upright box that holds the body) is computed from the shape's hull, so
it is exact for any turn, not only quarter turns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from functools import cached_property

from forge.resolve import shapes
from forge.resolve import space as sp
from forge.resolve.space import Box, Mat, Vec

# How the turning words of section 4 turn a standing shape (README: "Turning").
# Tipped over to the right: the top end goes right, the left face becomes the top.
TURN = {
    "standing": sp.IDENTITY,
    "lying along length": sp.rotation(1, 90),      # written height now runs left to right
    "lying along depth": sp.rotation(0, -90),      # written height now runs front to back
}
POINT = {
    "up": sp.IDENTITY, "down": sp.rotation(0, 180), "right": sp.rotation(1, 90),
    "left": sp.rotation(1, -90), "back": sp.rotation(0, -90), "front": sp.rotation(0, 90),
}


def turn_matrix(shape: str, orientation: str, pointing: str) -> Mat:
    if shape == "wedge":
        return sp.IDENTITY          # a wedge's sizes are its frame as it sits; never turned
    return TURN[orientation] if orientation != "standing" else POINT[pointing]


@dataclass
class Cut:
    """One feature applied to a body, kept in the STANDING shape's own coordinates.

    Stored that way so the feature travels with the body when a group is placed, turned
    or mirrored later. `origin` is the feature's middle on the face, `normal` points out
    of the face, `first` is the face's first direction (section 9).
    """

    kind: str                           # the step kind: hole, boss, shell ...
    name: str                           # as written: "blind hole"
    slots: dict[str, float]
    origin: Vec = (0.0, 0.0, 0.0)
    normal: Vec = (0.0, 0.0, 1.0)
    first: Vec = (1.0, 0.0, 0.0)
    second_sign: float = 1.0            # +1 when normal x first is the face's second direction
    through: float = 0.0                # how much material lies under the face
    spots: list[tuple[float, float]] = field(default_factory=lambda: [(0.0, 0.0)])
    open_normal: Vec | None = None      # hollowed out: the face left open
    line: int = 0


@dataclass
class Body:
    name: str                           # unique in the structure: "legs 2"
    owner: str                          # the plan name it belongs to: "legs"
    line: int                           # the plan line that made it
    shape: str
    profile: str | None
    sizes: dict[str, float]
    matrix: Mat
    centre: Vec
    pointing: str = "up"                # wedge only (other shapes are turned by `matrix`)
    flat: str | None = None
    cuts: list[Cut] = field(default_factory=list)
    may_overlap: frozenset[str] = frozenset()   # declared: `sunk N into X`, a span's two ends

    # --- derived ----------------------------------------------------------------------------

    # A Body is never changed after it is made (features make a new Body with `replace`),
    # so the two costly answers below are worked out once and kept.

    @cached_property
    def standing(self) -> shapes.Standing:
        return shapes.describe(self.shape, self.profile, self.sizes, self.pointing, self.flat)

    def to_world(self, local: Vec) -> Vec:
        return sp.add(sp.apply(self.matrix, local), self.centre)

    def to_local(self, world: Vec) -> Vec:
        return sp.apply(sp.transpose(self.matrix), sp.sub(world, self.centre))

    def direction_to_local(self, world: Vec) -> Vec:
        return sp.apply(sp.transpose(self.matrix), world)

    @cached_property
    def frame(self) -> Box:
        """The upright box that holds the whole body, bosses and pads included."""
        return self._box(self.standing.hull + [item for cut in self.cuts
                                               for item in _added_hull(cut)])

    @cached_property
    def core(self) -> Box:
        """The frame of the shape alone. Features are placed on ITS faces: `on deck:` means
        the deck's own top, also after a boss has been raised on it."""
        return self._box(self.standing.hull)

    def _box(self, hull: list[tuple]) -> Box:
        low, high = [], []
        for k in range(3):
            reach = [_reach(item, self, sp.unit(k)) for item in hull]
            low.append(min(r[0] for r in reach))
            high.append(max(r[1] for r in reach))
        return Box(tuple(low), tuple(high))

    @property
    def volume(self) -> float | None:
        """Exact by formula for a bare shape; None once features have changed it."""
        return None if self.cuts else self.standing.volume

    @property
    def axis(self) -> tuple[Vec, Vec]:
        """(a point on it, its direction). A round part's own axis as it sits; for any
        other part the upright line through the middle of its frame (section 7)."""
        standing = self.standing
        if standing.is_round and self.shape != "sphere":
            return self.to_world(standing.axis_at), sp.apply(self.matrix, (0.0, 0.0, 1.0))
        return self.frame.mid, (0.0, 0.0, 1.0)

    @property
    def hollow(self) -> Cut | None:
        return next((cut for cut in self.cuts if cut.kind == "shell"), None)

    @property
    def inner(self) -> Box | None:
        """The frame of the hollow inside, or None for a part with no inside.

        A tube: its hole, over the tube's whole length. A hollowed part: the frame
        shrunk by the wall, reaching the frame again on the open face.
        """
        if not sp.is_square(self.matrix):
            return None                 # turned by an odd angle: its inside has no upright frame
        x, y, z = self.standing.dims
        low, high = [-x / 2, -y / 2, -z / 2], [x / 2, y / 2, z / 2]
        if self.shape == "tube" or (self.shape == "bar" and self.profile == "tube"):
            bore = (self.sizes["inner diameter"] if self.shape == "tube"
                    else self.sizes["outer diameter"] - 2 * self.sizes["wall"])
            low[0] = low[1] = -bore / 2
            high[0] = high[1] = bore / 2
        elif self.hollow is not None:
            wall = self.hollow.slots["wall"]
            for k in range(3):
                opened = self.hollow.open_normal[k] if self.hollow.open_normal else 0.0
                low[k] += 0.0 if opened < 0 else wall
                high[k] -= 0.0 if opened > 0 else wall
        else:
            return None
        return sp.box_of_points([self.to_world(tuple(low)), self.to_world(tuple(high))])

    def inner_exact(self, face: str) -> bool:
        """Is that inner face where `inner` says, exactly?

        A tube, a hollowed box and a hollowed cylinder: always. Other hollowed shapes are
        shelled by the CAD kernel; their inside is exact only where the wall is flat (it
        is then `wall` thick), on the open face, and for the round side of a ball or dome
        (the inside is a smaller ball with the same middle). A cone's sloping wall is not.
        """
        if self.hollow is None or self.shape in ("box", "cylinder"):
            return True
        axis, side = sp.FACE[face]
        local = tuple(0.0 if abs(v) < 1e-9 else round(v, 9)
                      for v in self.direction_to_local(sp.unit(axis, float(side))))
        if local in self.standing.planes:
            return True
        return self.shape == "sphere" or (self.shape == "dome" and local == (0.0, 0.0, 1.0))

    @property
    def kind(self) -> str:
        """"box" or "cylinder" when overlap and contact can be done by arithmetic; else "other"."""
        if self.cuts or not sp.is_square(self.matrix):
            return "other"
        return self.shape if self.shape in ("box", "cylinder") else "other"

    def moved(self, turn: Mat, shift: Vec, name: str | None = None) -> Body:
        """A copy carried along by `world -> turn * world + shift`."""
        return replace(self, name=name or self.name, matrix=sp.multiply(turn, self.matrix),
                       centre=sp.add(sp.apply(turn, self.centre), shift), cuts=list(self.cuts))


def _added_hull(cut: Cut) -> list[tuple]:
    """A boss or a pad sticks out of its face, so it makes the frame bigger."""
    if cut.kind not in ("boss", "boss_pair", "pad"):
        return []
    height = cut.slots["height"]
    second = sp.scale(sp.cross(cut.normal, cut.first), cut.second_sign)
    items = []
    for u, v in cut.spots:
        top = sp.add(sp.add(cut.origin, sp.add(sp.scale(cut.first, u), sp.scale(second, v))),
                     sp.scale(cut.normal, height))
        if cut.kind == "pad":
            half = (cut.slots["length"] / 2, cut.slots["width"] / 2)
            items += [("point", sp.add(top, sp.add(sp.scale(cut.first, a * half[0]),
                                                   sp.scale(second, b * half[1]))))
                      for a in (-1, 1) for b in (-1, 1)]
        else:
            items.append(("circle", top, cut.normal, cut.slots["diameter"] / 2))
    return items


def _reach(item: tuple, body: Body, direction: Vec) -> tuple[float, float]:
    """How far one hull item reaches, low and high, along a world direction."""
    kind = item[0]
    if kind == "point":
        at = sp.dot(body.to_world(item[1]), direction)
        return at, at
    middle = sp.dot(body.to_world(item[1]), direction)
    if kind == "ball":
        return middle - item[2], middle + item[2]
    lean = sp.dot(sp.apply(body.matrix, item[2]), direction)   # cosine between normal and direction
    if kind == "circle":
        spread = item[3] * math.sqrt(max(0.0, 1.0 - lean * lean))
        return middle - spread, middle + spread
    # A cap: the part of a ball within `height` of its top, measured along the normal.
    ball, height = item[3], item[4]
    edge = (ball - height) / ball           # cosine of the cap's half angle
    rim_centre = middle + lean * (ball - height)
    rim = math.sqrt(max(0.0, ball * ball - (ball - height) ** 2))
    spread = rim * math.sqrt(max(0.0, 1.0 - lean * lean))
    high = middle + ball if lean >= edge else rim_centre + spread
    low = middle - ball if -lean >= edge else rim_centre - spread
    return low, high


def unit_frame(bodies: list[Body]) -> Box:
    return sp.around([body.frame for body in bodies])
