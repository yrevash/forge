"""Recipes for structures: the ordered commands that build every part of a resolved plan.

    items = structure_items(resolution.bodies)      # one Item per part, one per feature line
    for index, entry in script_of(items): ...       # the whole build as (item number, entry)

A resolved plan (forge.resolve) states each part as a `Body`: a shape with its sizes,
the middle of its frame (`centre`) and its turn (`matrix`), plus the features cut into
it. A recipe turns one Body into commands. The executor never does arithmetic: every
number in a command is copied from the resolved plan, and `Cmd.sources` says from where.

    "size:height"          a size written in (or resolved for) the part's own line
    "body:centre"          where the resolver put the middle of the part's frame
    "body:matrix"          the resolver's turn, written as yaw, pitch and roll
    "standing:outline"     the resolver's outline of the shape (bars, wedges, odd prisms)
    "cut:depth" ...        a slot of a feature; "cut:spot" its position on the face
    "const"                a fixed choice of the recipe (the XY plane, centred at 0)
    "derived:..."          worked out here from plan numbers (a pipe bar's bore)

One part, in order
    new_body
    its shape, drawn with the middle of its frame on the body's origin      (_shape)
    { move_body centre, turn_body turn }      either order; `turn_body` only if it is turned
    features that came with the part

Why placing is two commands and not one, and not three. The shape is drawn centred, so
the position is exactly the resolver's `centre` and the turn is exactly its `matrix`, and
the two do not interact: turning about the middle does not move the middle. Two small
commands with three numbers each are then easier to get right and to check than one with
six (a wrong position is undone without touching a right turn), and most parts stand
upright and need no turn at all. A separate "select the body" step is not needed: the
body just drawn is the active one, and `activate_body` exists for coming back later.

Why one body per copy. `legs: ..., at each corner of seat` makes four parts the plan can
later point at one by one ("legs 2"), put features on one by one, and check for contact
one by one. A PartDesign pattern makes ONE body (and a body must be a single solid, which
four separate legs are not); an App::Link copy shares its original's shape for ever, so it
cannot take its own feature, and cannot be a mirror image. Four bodies cost four short
recipes and keep every later line of the plan expressible.

Mirror images. A body's placement can turn it but cannot mirror it. A mirrored copy (its
matrix has determinant -1) is therefore DRAWN as its mirror image and then turned by
what is left: matrix = turn * mirror, with the mirror across the body's own YZ plane.
Every shape but two is its own mirror image there; the L bar and the wedge are drawn
with their outline's x negated.

Features. A feature line names a face as the part sits. `select_side` takes that same
word, the sketch on it uses the face's first and second direction (inside/sides.py), and
so the feature's position is the plan's own (u, v). Features are built after the part is
placed. The one exception is a part turned by an odd angle (a copy of a group placed
around something): it has no "front as it sits", so its features are built before it is
turned, while its sides are still its own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from forge.freecad.recipes import AnyOrder, Cmd, Recipe, flatten
from forge.resolve import space as sp
from forge.resolve.bodies import Body, Cut

__all__ = ["AnyOrder", "Cmd", "Item", "Recipe", "Unsupported", "describe_cut", "flatten",
           "placement_kind", "script_of", "structure_items", "yaw_pitch_roll"]

MIRROR = sp.mirror(0)       # across the body's own YZ plane
SIDE_OF = {(0, -1): "left", (0, 1): "right", (1, -1): "front", (1, 1): "back",
           (2, -1): "bottom", (2, 1): "top"}
FACE_DIRECTIONS = {2: (0, 1), 1: (0, 2), 0: (1, 2)}     # as forge/resolve/featuring.py


class Unsupported(Exception):
    """The resolved plan holds something no command can build yet. The text says what."""


@dataclass(frozen=True)
class Item:
    """One thing the executor builds: a part (a body), or one feature line on one body."""

    kind: str                   # "part" or "feature"
    body: int                   # the body's number: 1 for the first body made
    source: int                 # where that body is in the resolved plan's list of bodies
    name: str                   # the part's name; for a feature also its kind
    line: int                   # the plan line it comes from
    entries: Recipe
    shape: str = ""             # part: its shape word ("box", "bar l" ...)
    placement: str = ""         # part: its placement kind (placement_kind)
    cuts: tuple[str, ...] = field(default=())   # the feature kinds built by this item
    # The same features as the PLAN states them to the executor (describe_cut), in order.
    features: tuple[dict, ...] = field(default=())


def _clean(value: float) -> float:
    """Float dust away: 89.99999999999999 is 90, -0.0 is 0."""
    nearest = round(value, 9)
    return 0.0 if nearest == 0 else nearest


# --- placing ---------------------------------------------------------------------------------

def yaw_pitch_roll(turn: sp.Mat) -> tuple[float, float, float]:
    """A pure turn as FreeCAD's three angles, in degrees.

    FreeCAD composes them as  turn = Rz(yaw) * Ry(pitch) * Rx(roll): roll first about X,
    then pitch about Y, then yaw about Z, all about the structure's fixed axes. Reading
    the product's entries gives the three angles back. When the pitch is +-90 the first
    and the last turn are about the same line and only their sum matters; roll is then
    set to 0.
    """
    sine = max(-1.0, min(1.0, -turn[2][0]))
    pitch = math.degrees(math.asin(sine))
    if abs(abs(sine) - 1.0) < 1e-12:
        roll, yaw = 0.0, math.degrees(math.atan2(-turn[0][1], turn[1][1]))
    else:
        roll = math.degrees(math.atan2(turn[2][1], turn[2][2]))
        yaw = math.degrees(math.atan2(turn[1][0], turn[0][0]))
    return _clean(yaw), _clean(pitch), _clean(roll)


def from_yaw_pitch_roll(yaw: float, pitch: float, roll: float) -> sp.Mat:
    """The matrix FreeCAD builds from those three angles (the check on `yaw_pitch_roll`)."""
    return sp.multiply(sp.rotation(2, yaw), sp.multiply(sp.rotation(1, pitch),
                                                        sp.rotation(0, roll)))


def split(matrix: sp.Mat) -> tuple[sp.Mat, bool]:
    """(the pure turn, is the shape drawn mirrored) for a body's matrix."""
    if sp.determinant(matrix) < 0:
        return sp.multiply(matrix, MIRROR), True
    return matrix, False


def placement_kind(body: Body) -> str:
    """How the part sits, for the counts in the proof."""
    turn, mirrored = split(body.matrix)
    if turn == sp.IDENTITY:
        kind = "standing"
    elif sp.is_square(turn):
        kind = "quarter turn"
    elif body.may_overlap:
        kind = "spanning (tilted)"      # only a span is both tilted and allowed to overlap
    elif abs(turn[2][2] - 1.0) < 1e-12:
        kind = "odd turn about the upright axis"
    else:
        kind = "tilted"
    return f"{kind}, mirror image" if mirrored else kind


def _place(body: Body) -> Recipe:
    """Move and turn. A move to the origin or a turn by nothing is left out: the body is
    already there, and a command that changes nothing leaves no trace a teacher could see."""
    turn, _ = split(body.matrix)
    x, y, z = (_clean(v) for v in body.centre)
    commands = []
    if (x, y, z) != (0.0, 0.0, 0.0):
        commands.append(Cmd("move_body", {"x": x, "y": y, "z": z},
                            {"x": "body:centre", "y": "body:centre", "z": "body:centre"}))
    if turn != sp.IDENTITY:
        yaw, pitch, roll = yaw_pitch_roll(turn)
        commands.append(Cmd("turn_body", {"yaw": yaw, "pitch": pitch, "roll": roll},
                            {"yaw": "body:matrix", "pitch": "body:matrix", "roll": "body:matrix"}))
    return [AnyOrder(tuple(commands))] if commands else []


# --- shapes ----------------------------------------------------------------------------------

def _dimension(name: str, value: float, source: str) -> Cmd:
    return Cmd(f"constrain_{name}", {"value": float(value)}, {"value": source})


def _centred(dimensions: list[Cmd]) -> AnyOrder:
    return AnyOrder((*dimensions, _dimension("x", 0.0, "const"), _dimension("y", 0.0, "const")))


def _sketch(plane: str = "XY") -> Recipe:
    return [Cmd("select_plane", {"plane": plane}, {"plane": "const"}),
            Cmd("new_sketch", {"offset": 0.0}, {"offset": "const"})]


def _padded(length: float, source: str) -> Recipe:
    return [Cmd("leave_sketch"), Cmd("pad_symmetric", {"length": float(length)},
                                     {"length": source})]


def _outline(points: list[tuple[float, float]], mirrored: bool) -> Cmd:
    listed = [[_clean(-x if mirrored else x), _clean(y)] for x, y in points]
    return Cmd("sketch_outline", {"points": listed}, {"points": "standing:outline"})


def _wedge(body: Body, mirrored: bool) -> Recipe:
    """A wedge is a triangle padded along the one direction it does not slope in."""
    corners = body.standing.corners         # two triangles: corners 0-2 and 3-5
    across = next(k for k in range(3) if abs(corners[0][k] - corners[3][k]) > sp.TOL)
    # Which body directions a sketch's x and y are, per base plane (FreeCAD's own layout).
    plane, (first, second) = {2: ("XY", (0, 1)), 1: ("XZ", (0, 2)), 0: ("YZ", (1, 2))}[across]
    points = []
    for corner in corners[:3]:
        local = sp.apply(MIRROR, corner) if mirrored else corner
        points.append([_clean(local[first]), _clean(local[second])])
    slot = ("length", "depth", "height")[across]
    return [*_sketch(plane),
            Cmd("sketch_outline", {"points": points}, {"points": "standing:corners"}),
            *_padded(body.sizes[slot], f"size:{slot}")]


def _shape(body: Body, mirrored: bool) -> Recipe:
    """The commands that draw the body's shape, frame middle on the body's origin."""
    s, shape = body.sizes, body.shape
    circle, rectangle = Cmd("sketch_circle"), Cmd("sketch_rectangle")

    def size(slot: str, argument: str | None = None) -> Cmd:
        return _dimension(argument or slot, s[slot], f"size:{slot}")

    if shape == "box":
        return [*_sketch(), rectangle,
                _centred([size("length"), _dimension("width", s["depth"], "size:depth")]),
                *_padded(s["height"], "size:height")]
    if shape == "cylinder":
        return [*_sketch(), circle, _centred([size("diameter")]),
                *_padded(s["height"], "size:height")]
    if shape == "tube":
        return [*_sketch(), circle, _centred([size("outer diameter", "diameter")]),
                circle, _centred([size("inner diameter", "diameter")]),
                *_padded(s["height"], "size:height")]
    if shape == "bar" and body.profile == "tube":
        bore = s["outer diameter"] - 2 * s["wall"]
        return [*_sketch(), circle, _centred([size("outer diameter", "diameter")]),
                circle, _centred([_dimension("diameter", bore,
                                             "derived:outer diameter - 2 * wall")]),
                *_padded(s["length"], "size:length")]
    if shape == "bar":
        return [*_sketch(), _outline(body.standing.outline, mirrored),
                *_padded(s["length"], "size:length")]
    if shape == "prism":
        sides = int(s["sides"])
        polygon = Cmd("sketch_polygon", {"sides": sides}, {"sides": "size:sides"})
        # One flat side faces the front: the polygon's first side runs along x (angle 0).
        flat_front = _dimension("angle", 0.0, "const")
        if sides % 2 == 0:
            dimensions = _centred([size("across", "across_flats"), flat_front])
        else:
            # An odd prism is written flat side to opposite corner, and the middle of its
            # frame is not on its axis. The resolver's outline holds both numbers.
            axis_y = body.standing.axis_at[1]
            corner = max(math.hypot(x, y - axis_y) for x, y in body.standing.outline)
            dimensions = AnyOrder((
                _dimension("diameter", 2 * corner, "standing:outline"), flat_front,
                _dimension("x", 0.0, "const"),
                _dimension("y", _clean(axis_y), "standing:axis_at")))
        return [*_sketch(), polygon, dimensions, *_padded(s["height"], "size:height")]
    if shape == "wedge":
        return _wedge(body, mirrored)
    primitives = {
        "cone": ("add_cone", ("bottom diameter", "top diameter", "height")),
        "sphere": ("add_sphere", ("diameter",)),
        "dome": ("add_dome", ("diameter", "height")),
        "tapered box": ("add_tapered_box", ("bottom length", "bottom depth", "top length",
                                            "top depth", "height")),
    }
    if shape not in primitives:
        raise Unsupported(f"no recipe for the shape {shape}")
    command, slots = primitives[shape]
    return [Cmd(command, {slot.replace(" ", "_"): float(s[slot]) for slot in slots},
                {slot.replace(" ", "_"): f"size:{slot}" for slot in slots})]


# --- features --------------------------------------------------------------------------------

def _side(direction: sp.Vec) -> tuple[str, int]:
    """(the side's word, its axis) for a direction that lies along an axis."""
    axis = max(range(3), key=lambda k: abs(direction[k]))
    if abs(abs(direction[axis]) - 1.0) > 1e-9:
        raise Unsupported("a feature on a face that is not square to the structure")
    return SIDE_OF[(axis, 1 if direction[axis] > 0 else -1)], axis


class _Face:
    """A feature's face as the body sits at the moment the feature is built.

    `sits` turns the resolver's body directions into the directions the body has then:
    its full matrix once it is placed, or only the mirror while it is still unplaced.
    """

    def __init__(self, cut: Cut, sits: sp.Mat) -> None:
        normal = sp.apply(sits, cut.normal)
        self.side, axis = _side(normal)
        first, second = (sp.unit(k) for k in FACE_DIRECTIONS[axis])
        own_second = sp.scale(sp.cross(cut.normal, cut.first), cut.second_sign)
        u, v, origin = sp.apply(sits, cut.first), sp.apply(sits, own_second), \
            sp.apply(sits, cut.origin)
        # The feature's own two directions, written in the side's first and second direction.
        self._u = (sp.dot(u, first), sp.dot(u, second))
        self._v = (sp.dot(v, first), sp.dot(v, second))
        self._origin = (sp.dot(origin, first), sp.dot(origin, second))
        self.offset = _clean(sp.dot(origin, normal))
        self.swapped = abs(self._u[0]) < 0.5        # the feature's length runs along `second`

    def at(self, spot: tuple[float, float]) -> tuple[float, float]:
        return (_clean(self._origin[0] + spot[0] * self._u[0] + spot[1] * self._v[0]),
                _clean(self._origin[1] + spot[0] * self._u[1] + spot[1] * self._v[1]))

    def angle(self, degrees: float) -> float:
        turn = math.radians(degrees)
        along = (math.cos(turn) * self._u[0] + math.sin(turn) * self._v[0],
                 math.cos(turn) * self._u[1] + math.sin(turn) * self._v[1])
        return _clean(math.degrees(math.atan2(along[1], along[0])))

    def sketch(self) -> Recipe:
        return [Cmd("select_side", {"side": self.side}, {"side": "cut:face"}),
                Cmd("new_sketch", {"offset": self.offset}, {"offset": "cut:origin"})]


def _where(face: _Face, spot: tuple[float, float]) -> list[Cmd]:
    x, y = face.at(spot)
    return [_dimension("x", x, "cut:spot"), _dimension("y", y, "cut:spot")]


def _circles(face: _Face, cut: Cut, slot: str) -> Recipe:
    recipe = face.sketch()
    for spot in cut.spots:
        recipe += [Cmd("sketch_circle"),
                   AnyOrder((_dimension("diameter", cut.slots[slot], f"cut:{slot}"),
                             *_where(face, spot)))]
    return [*recipe, Cmd("leave_sketch")]


def _rectangles(face: _Face, cut: Cut) -> Recipe:
    length, width = ("width", "length") if face.swapped else ("length", "width")
    recipe = face.sketch()
    for spot in cut.spots:
        recipe += [Cmd("sketch_rectangle"),
                   AnyOrder((_dimension("length", cut.slots[length], f"cut:{length}"),
                             _dimension("width", cut.slots[width], f"cut:{width}"),
                             *_where(face, spot)))]
    return [*recipe, Cmd("leave_sketch")]


def _pocket(depth: float, source: str) -> Cmd:
    return Cmd("pocket", {"depth": float(depth)}, {"depth": source})


def _cut(cut: Cut, sits: sp.Mat) -> Recipe:
    """The commands for one feature. `sits`: see _Face."""
    kind, s = cut.kind, cut.slots
    face = _Face(cut, sits)
    # "Through" is the material under the face, a number the resolver states: a hole in an
    # inner face goes through the wall only, as the reference solid's does.
    through = _pocket(cut.through, "cut:through")
    if kind in ("hole", "hole_pair"):
        return [*_circles(face, cut, "diameter"), through]
    if kind in ("polar", "row"):
        return [*_circles(face, cut, "hole diameter"), through]
    if kind == "blind_hole":
        return [*_circles(face, cut, "diameter"), _pocket(s["depth"], "cut:depth")]
    if kind == "counterbore":
        return [*_circles(face, cut, "hole diameter"), through,
                *_circles(face, cut, "diameter"), _pocket(s["depth"], "cut:depth")]
    if kind in ("boss", "boss_pair"):
        return [*_circles(face, cut, "diameter"),
                Cmd("pad", {"length": float(s["height"])}, {"length": "cut:height"})]
    if kind == "pad":
        return [*_rectangles(face, cut),
                Cmd("pad", {"length": float(s["height"])}, {"length": "cut:height"})]
    if kind in ("pocket", "pocket_pair"):
        return [*_rectangles(face, cut), _pocket(s["depth"], "cut:depth")]
    if kind == "slot":
        recipe = face.sketch()
        for spot in cut.spots:
            recipe += [Cmd("sketch_slot"),
                       AnyOrder((_dimension("length", s["length"], "cut:length"),
                                 _dimension("width", s["width"], "cut:width"),
                                 _dimension("angle", face.angle(s["angle"]), "cut:angle"),
                                 *_where(face, spot)))]
        return [*recipe, Cmd("leave_sketch"), _pocket(s["depth"], "cut:depth")]
    if kind == "corner_radius":
        return [Cmd("select_side_edges", {"side": face.side, "rule": "along"},
                    {"side": "cut:face", "rule": "const"}),
                Cmd("fillet", {"radius": float(s["radius"])}, {"radius": "cut:radius"})]
    if kind in ("top_chamfer", "top_fillet"):
        finish = (Cmd("chamfer", {"size": float(s["size"])}, {"size": "cut:size"})
                  if kind == "top_chamfer"
                  else Cmd("fillet", {"radius": float(s["radius"])}, {"radius": "cut:radius"}))
        return [Cmd("select_side_edges", {"side": face.side, "rule": "around"},
                    {"side": "cut:face", "rule": "const"}), finish]
    if kind == "shell":
        if cut.open_normal is None:
            raise Unsupported("hollowed out with no open face: PartDesign's Thickness tool "
                              "always removes a face")
        opened, _ = _side(sp.apply(sits, cut.open_normal))
        return [Cmd("select_side", {"side": opened}, {"side": "cut:open at"}),
                Cmd("thickness", {"value": float(s["wall"])}, {"value": "cut:wall"})]
    raise Unsupported(f"no recipe for the feature {kind}")


def describe_cut(cut: Cut, sits: sp.Mat) -> dict:
    """One feature as the plan states it to the executor: its kind, the side it is on and
    every number its commands take, already in the sketch's own directions.

    `_cut` above and this function read the same `_Face`, so a command never holds a
    number that is not here (tests/test_freecad_multi_plan.py checks that for every
    feature kind). Nothing here names the part or the plan line.
    """
    kind, s = cut.kind, cut.slots
    if kind == "shell":
        if cut.open_normal is None:
            raise Unsupported("hollowed out with no open face: PartDesign's Thickness tool "
                              "always removes a face")
        return {"feature": kind, "side": _side(sp.apply(sits, cut.open_normal))[0],
                "wall": float(s["wall"])}
    face = _Face(cut, sits)
    made: dict = {"feature": kind, "side": face.side}
    if kind == "corner_radius":
        return {**made, "edges": "along", "radius": float(s["radius"])}
    if kind == "top_chamfer":
        return {**made, "edges": "around", "size": float(s["size"])}
    if kind == "top_fillet":
        return {**made, "edges": "around", "radius": float(s["radius"])}
    made["offset"] = face.offset
    made["at"] = [list(face.at(spot)) for spot in cut.spots]
    length, width = ("width", "length") if face.swapped else ("length", "width")
    if kind in ("hole", "hole_pair"):
        made.update(diameter=float(s["diameter"]), through=float(cut.through))
    elif kind in ("polar", "row"):
        made.update(diameter=float(s["hole diameter"]), through=float(cut.through))
    elif kind == "blind_hole":
        made.update(diameter=float(s["diameter"]), depth=float(s["depth"]))
    elif kind == "counterbore":
        made.update(diameter=float(s["hole diameter"]), through=float(cut.through),
                    counterbore_diameter=float(s["diameter"]), counterbore_depth=float(s["depth"]))
    elif kind in ("boss", "boss_pair"):
        made.update(diameter=float(s["diameter"]), height=float(s["height"]))
    elif kind == "pad":
        made.update(length=float(s[length]), width=float(s[width]), height=float(s["height"]))
    elif kind in ("pocket", "pocket_pair"):
        made.update(length=float(s[length]), width=float(s[width]), depth=float(s["depth"]))
    elif kind == "slot":
        made.update(length=float(s["length"]), width=float(s["width"]),
                    angle=face.angle(s["angle"]), depth=float(s["depth"]))
    else:
        raise Unsupported(f"no recipe for the feature {kind}")
    return made


# --- a whole structure -----------------------------------------------------------------------

def shape_word(body: Body) -> str:
    return f"bar {body.profile}" if body.shape == "bar" else body.shape


def structure_items(bodies: list[Body]) -> list[Item]:
    """Every part and every feature line of a resolved structure, in the plan's order.

    A feature written after its part (the usual case) is its own item at its own line, so
    the build follows the plan line by line. A feature that came with the part (the part
    is a copy of a group that already had it) is built inside the part's item.
    """
    # Bodies are made in the order of their plan lines; that order gives them their numbers.
    order = sorted(range(len(bodies)), key=lambda k: (bodies[k].line, k))
    items: list[tuple[tuple, Item]] = []
    for number, source in enumerate(order, start=1):
        body = bodies[source]
        turn, mirrored = split(body.matrix)
        odd = not sp.is_square(turn)
        own = [cut for cut in body.cuts if odd or cut.line <= body.line]
        later = [cut for cut in body.cuts if not (odd or cut.line <= body.line)]
        recipe: Recipe = [Cmd("new_body"), *_shape(body, mirrored)]
        # How the body sits while its own features are built (see the top of this file).
        sits = (MIRROR if mirrored else sp.IDENTITY) if odd else body.matrix
        if odd:         # no "front as it sits" once turned: features first, sides still its own
            for cut in own:
                recipe += _cut(cut, sits)
            recipe += _place(body)
        else:
            recipe += _place(body)
            for cut in own:
                recipe += _cut(cut, sits)
        items.append(((body.line, 0, number), Item(
            "part", number, source, body.name, body.line, recipe, shape_word(body),
            placement_kind(body), tuple(cut.kind for cut in own),
            tuple(describe_cut(cut, sits) for cut in own))))
        for cut in later:
            items.append(((cut.line, 1, number), Item(
                "feature", number, source, f"{body.name}: {cut.name}", cut.line,
                _cut(cut, body.matrix), cuts=(cut.kind,),
                features=(describe_cut(cut, body.matrix),))))
    return [item for _, item in sorted(items, key=lambda pair: pair[0])]


Script = list[tuple[int | None, Cmd | AnyOrder]]


def script_of(items: list[Item]) -> Script:
    """The whole build as (item number, entry): new_document, every item, done.

    A feature item on a body that is not the active one starts with `activate_body`.
    """
    script: Script = [(None, Cmd("new_document"))]
    active = None
    for index, item in enumerate(items):
        if item.kind == "feature" and item.body != active:
            script.append((index, Cmd("activate_body", {"index": item.body},
                                      {"index": "plan:part number"})))
        script += [(index, entry) for entry in item.entries]
        active = item.body
    script.append((None, Cmd("done")))
    return script


def command_count(items: list[Item]) -> int:
    return len(flatten([entry for _, entry in script_of(items)]))
