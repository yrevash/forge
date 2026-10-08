"""The shared vocabulary: ROLES (what a group of parts is) and PLACEMENTS (where it goes).

A structure is an ordered list of steps. Each step adds one part or a group of
like parts. It has

    a ROLE       what the parts are: legs, aprons, slats, shelves ...
    a PLACEMENT  the rule that puts them in place: "corners", "ring", "stacked" ...
    a SHAPE      box, upright cylinder, or a cylinder lying left to right
    SLOTS        the numbers the placement needs: sizes, counts, heights

The same few roles and placements are reused by every kind, so a model sees
"legs at the corners" in a chair, a stool, a bench, a table, a desk and a bed,
and "rails one above another" in a fence, a ladder and a chest of drawers.

A placement is written ONCE, as text formulas over its own slot names. Those
formulas are evaluated to get each part's exact numbers (draft.py) and are also
what the generated program contains, with the step's name put in front of each
slot name. So `place(...)` below is the whole geometry of a step: give it the
slot values and it returns the parts. A step engine needs nothing else.

Axes: X is the width (left is -X), Y is the depth (front is -Y), Z is up.
Everything is centred on the Z axis and stands on z = 0.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from forge.generators.structures.formula import Number, evaluate
from forge.generators.structures.solids import Solid

# One part, still as formulas: (name ending, size formulas x y z, position formulas x y z).
# For a cylinder the size is (diameter, diameter, height); for a lying cylinder
# (shape "cylinder_x", its axis runs left to right) it is (length, diameter, diameter).
# The position is always the middle of the part's lowest face, or lowest line.
PartSpec = tuple[str, tuple[str, str, str], tuple[str, str, str]]


@dataclass(frozen=True)
class Placement:
    words: str                           # the rule, in plain words
    slots: tuple[str, ...]               # slot names for box-shaped parts
    parts: Callable[[str, dict], list[PartSpec]]
    round_slots: tuple[str, ...] = ()    # slot names when the parts are cylinders (if allowed)
    lying_slots: tuple[str, ...] = ()    # slot names when they are lying cylinders (if allowed)


# --- one horizontal board -------------------------------------------------------------------

def _level(shape: str, v: dict) -> list[PartSpec]:
    if shape == "cylinder":
        return [("", ("diameter", "diameter", "thickness"), ("0", "0", "top - thickness"))]
    return [("", ("width", "depth", "thickness"), ("0", "0", "top - thickness"))]


# --- uprights: legs, posts ------------------------------------------------------------------

_CORNERS = {"front_left": ("-", "-"), "front_right": ("", "-"),
            "back_left": ("-", ""), "back_right": ("", "")}


def _uprights(*which: str) -> Callable[[str, dict], list[PartSpec]]:
    """Uprights at some of the four corners of a rectangle spread_x by spread_y.

    The rectangle is measured to the OUTSIDE of the uprights, so "spread 420"
    means the outer faces are 420 apart and the uprights sit flush inside it.
    """
    def parts(shape: str, v: dict) -> list[PartSpec]:
        if shape == "cylinder_x":
            # Wheels: discs standing on edge, `width` thick from left to right. The
            # rectangle is still measured to the outside: outer faces and outer rims.
            return [(name, ("width", "diameter", "diameter"),
                     (f"{_CORNERS[name][0]}(spread_x - width) / 2",
                      f"{_CORNERS[name][1]}(spread_y - diameter) / 2", "bottom"))
                    for name in which]
        across = "diameter" if shape == "cylinder" else "thickness"
        size = (across, across, "top - bottom")
        return [(name, size, (f"{_CORNERS[name][0]}(spread_x - {across}) / 2",
                              f"{_CORNERS[name][1]}(spread_y - {across}) / 2", "bottom"))
                for name in which]
    return parts


def _left_right(shape: str, v: dict) -> list[PartSpec]:
    across = "diameter" if shape == "cylinder" else "thickness"
    size = (across, across, "top - bottom")
    return [("left", size, (f"-(spread_x - {across}) / 2", "0", "bottom")),
            ("right", size, (f"(spread_x - {across}) / 2", "0", "bottom"))]


def _circle(shape: str, v: dict) -> list[PartSpec]:
    """`count` uprights evenly spaced on a circle; the first one is at the back."""
    across = "diameter" if shape == "cylinder" else "thickness"
    found = []
    for k in range(v["count"]):
        angle = math.radians(90.0 + 360.0 * k / v["count"])
        # Rounded so that cos(90 degrees) is written as 0.0 and not as 6.1e-17.
        cos, sin = round(math.cos(angle), 12), round(math.sin(angle), 12)
        found.append((str(k + 1), (across, across, "top - bottom"),
                      (f"{cos!r} * circle / 2", f"{sin!r} * circle / 2", "bottom")))
    return found


# --- horizontal rails and boards along the sides --------------------------------------------

def _rails(front_back: bool, sides: bool, levels: bool = False
           ) -> Callable[[str, dict], list[PartSpec]]:
    """Rails along the front and back (they run in X) and along the left and right (in Y).

    `spread_x` is the distance between the outer faces of the left and right
    rails, `spread_y` the same for front and back. With `levels`, the whole
    set is repeated `count` times going up, `pitch` apart.
    """
    def parts(shape: str, v: dict) -> list[PartSpec]:
        found = []
        for level in range(v["count"] if levels else 1):
            z = f"bottom + {level} * pitch" if levels else "top - height"
            end = f"_{level + 1}" if levels else ""
            centre_y = "y" if not front_back else "0"
            if front_back:
                size = ("length_x", "thickness", "height")
                found += [("front" + end, size, ("0", "-(spread_y - thickness) / 2", z)),
                          ("back" + end, size, ("0", "(spread_y - thickness) / 2", z))]
            if sides:
                size = ("thickness", "length_y", "height")
                found += [("left" + end, size, ("-(spread_x - thickness) / 2", centre_y, z)),
                          ("right" + end, size, ("(spread_x - thickness) / 2", centre_y, z))]
        return found
    return parts


def _across(shape: str, v: dict) -> list[PartSpec]:
    if shape == "cylinder_x":                 # a round bar: a handle, a hanging rail
        return [("", ("width", "diameter", "diameter"), ("0", "y", "top - diameter"))]
    return [("", ("width", "thickness", "height"), ("0", "y", "top - height"))]


def _stacked_across(shape: str, v: dict) -> list[PartSpec]:
    """`count` members running left to right, one above another: rungs, rails, drawer fronts."""
    size = (("width", "diameter", "diameter") if shape == "cylinder_x"
            else ("width", "thickness", "height"))
    return [(str(level + 1), size, ("0", "y", f"bottom + {level} * pitch"))
            for level in range(v["count"])]


def _long_row(shape: str, v: dict) -> list[PartSpec]:
    """`count` members running left to right, side by side from front to back: bed slats."""
    return [(str(i + 1), ("length_x", "width", "height"),
             ("0", f"y + {i - (v['count'] - 1) / 2!r} * pitch", "top - height"))
            for i in range(v["count"])]


def _side(shape: str, v: dict) -> list[PartSpec]:
    return [("", ("thickness", "length_y", "height"), ("x", "0", "top - height"))]


# --- evenly spaced groups -------------------------------------------------------------------

def _row(upright: bool) -> Callable[[str, dict], list[PartSpec]]:
    """`count` like parts in a row along X, `pitch` apart, the row centred on x = 0."""
    def parts(shape: str, v: dict) -> list[PartSpec]:
        found = []
        for i in range(v["count"]):
            x = f"{i - (v['count'] - 1) / 2!r} * pitch"
            if upright:
                found.append((str(i + 1), ("width", "thickness", "top - bottom"),
                              (x, "y", "bottom")))
            else:
                found.append((str(i + 1), ("width", "length_y", "height"),
                              (x, "0", "top - height")))
        return found
    return parts


def _stacked(shape: str, v: dict) -> list[PartSpec]:
    return [(str(level + 1), ("width", "depth", "thickness"),
             ("x", "y", f"bottom + {level} * pitch")) for level in range(v["count"])]


_UPRIGHT = ("thickness", "bottom", "top", "spread_x", "spread_y")
_UPRIGHT_ROUND = ("diameter", "bottom", "top", "spread_x", "spread_y")

PLACEMENTS: dict[str, Placement] = {
    "level": Placement(
        "one horizontal board (or disc) centred on the middle, its upper face at height `top`",
        ("width", "depth", "thickness", "top"), _level, ("diameter", "thickness", "top")),
    "corners": Placement(
        "four uprights, one in each corner of a rectangle `spread_x` by `spread_y` (measured "
        "to their outer faces), each running from height `bottom` to height `top`",
        _UPRIGHT, _uprights(*_CORNERS), _UPRIGHT_ROUND,
        lying_slots=("diameter", "width", "bottom", "spread_x", "spread_y")),
    "back_corners": Placement(
        "two uprights in the two BACK corners of that rectangle, from `bottom` to `top`",
        _UPRIGHT, _uprights("back_left", "back_right"), _UPRIGHT_ROUND),
    "front_corners": Placement(
        "two uprights in the two FRONT corners of that rectangle, from `bottom` to `top`",
        _UPRIGHT, _uprights("front_left", "front_right"), _UPRIGHT_ROUND),
    "left_right": Placement(
        "two uprights on the centre line, one at the left and one at the right, their outer "
        "faces `spread_x` apart, from `bottom` to `top`",
        ("thickness", "bottom", "top", "spread_x"), _left_right,
        ("diameter", "bottom", "top", "spread_x")),
    "circle": Placement(
        "`count` uprights evenly spaced on a circle of diameter `circle` (through their "
        "centres), the first one at the back, from `bottom` to `top`",
        ("count", "thickness", "bottom", "top", "circle"), _circle,
        ("count", "diameter", "bottom", "top", "circle")),
    "ring": Placement(
        "four horizontal members closing a rectangle: front and back ones `length_x` long, "
        "left and right ones `length_y` long, outer faces `spread_x` / `spread_y` apart, "
        "each `thickness` wide and `height` tall with its upper face at `top`",
        ("height", "thickness", "top", "length_x", "length_y", "spread_x", "spread_y"),
        _rails(front_back=True, sides=True)),
    "sides": Placement(
        "two members running front to back, one at the left and one at the right, outer "
        "faces `spread_x` apart, each `length_y` long and centred at `y`, upper face at `top`",
        ("height", "thickness", "top", "length_y", "spread_x", "y"),
        _rails(front_back=False, sides=True)),
    "front_back": Placement(
        "two members running left to right, one at the front and one at the back, outer "
        "faces `spread_y` apart, each `length_x` long, upper face at `top`",
        ("height", "thickness", "top", "length_x", "spread_y"),
        _rails(front_back=True, sides=False)),
    "stacked_ring": Placement(
        "a ring (as above) repeated `count` times going up: the lowest starts at height "
        "`bottom`, each next one `pitch` higher",
        ("count", "height", "thickness", "bottom", "pitch", "length_x", "length_y",
         "spread_x", "spread_y"),
        _rails(front_back=True, sides=True, levels=True)),
    "across": Placement(
        "one member running left to right, centred, `width` long, `thickness` front to back "
        "and `height` tall, at depth position `y`, its upper face at `top`",
        ("width", "height", "thickness", "y", "top"), _across,
        lying_slots=("width", "diameter", "y", "top")),
    "stacked_across": Placement(
        "`count` members running left to right, one above another, centred, each `width` "
        "long, the lowest with its underside at `bottom`, each next one `pitch` higher, at "
        "depth `y`",
        ("count", "width", "height", "thickness", "bottom", "pitch", "y"), _stacked_across,
        lying_slots=("count", "width", "diameter", "bottom", "pitch", "y")),
    "long_row": Placement(
        "`count` horizontal members running left to right, side by side from front to back "
        "`pitch` apart, the row centred at depth `y`, each `length_x` long, `width` wide and "
        "`height` tall, upper face at `top`",
        ("count", "length_x", "width", "height", "top", "pitch", "y"), _long_row),
    "side": Placement(
        "one board running front to back at position `x`, `length_y` long, `thickness` wide "
        "and `height` tall, its upper face at `top`",
        ("thickness", "length_y", "height", "top", "x"), _side),
    "upright_row": Placement(
        "`count` vertical members side by side in a row, `pitch` apart and centred, each "
        "`width` wide and `thickness` front to back, from `bottom` to `top`, at depth `y`",
        ("count", "width", "thickness", "bottom", "top", "pitch", "y"), _row(upright=True)),
    "cross_row": Placement(
        "`count` horizontal members running front to back, side by side `pitch` apart and "
        "centred, each `width` wide, `length_y` long and `height` tall, upper face at `top`",
        ("count", "width", "length_y", "height", "top", "pitch"), _row(upright=False)),
    "stacked": Placement(
        "`count` horizontal boards one above the other, the lowest with its underside at "
        "`bottom`, each next one `pitch` higher, centred at (`x`, `y`)",
        ("count", "width", "depth", "thickness", "bottom", "pitch", "x", "y"), _stacked),
}

# What each role is, and the placements it may use.
ROLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "top": ("the main horizontal surface: a seat, a table or desk top, a deck, a lid",
            ("level",)),
    "bottom": ("the floor board of a container or cabinet; a base plate", ("level",)),
    "legs": ("uprights that stand on the floor and carry the top",
             ("corners", "circle")),
    "posts": (("other uprights: back posts, arm posts, corner posts, handle posts, fence posts, "
              "standoffs between two plates"),
              ("corners", "back_corners", "front_corners", "left_right", "circle")),
    "aprons": ("rails directly under a top, joining the legs", ("ring",)),
    "stretchers": ("rails lower down that tie legs or panel ends together",
                   ("ring", "sides", "across", "side")),
    "rail": (("a cross rail: the top or lower rail of a back, a plinth; several one above "
             "another are the rails of a fence or a headboard"), ("across", "stacked_across")),
    "rungs": ("the steps of a ladder, one above another", ("stacked_across",)),
    "slats": (("thin strips with gaps between them: back slats, the sides of a crate, bed "
              "slats, the deck boards of a pallet, fence pickets"),
              ("upright_row", "stacked_ring", "long_row", "cross_row")),
    "arms": ("armrests, one each side", ("sides",)),
    "panels": (("flat boards standing on edge: side panels, panel ends, the walls of a box, "
               "a divider"), ("sides", "side", "ring", "front_back")),
    "shelves": ("horizontal boards carried between uprights or panels", ("stacked", "level")),
    "back_panel": (("a board closing the back of a shelf unit, a chair or a desk (modesty "
                   "panel); a headboard; the back board of a workbench"), ("across",)),
    "fronts": ("boards closing the front: doors side by side, drawer fronts one above another",
               ("upright_row", "stacked_across")),
    "bars": ("the members of a bare frame; the stringers and skids of a pallet or crate",
             ("front_back", "sides", "ring", "corners", "left_right", "across", "upright_row",
              "cross_row", "long_row")),
    "wheels": ("four wheels: discs standing on edge at the corners", ("corners",)),
    "axles": ("beams running left to right under a deck, a wheel at each end", ("long_row",)),
    "handle": ("something to hold: a bar between two posts, a cleat on each end of a crate",
               ("across", "sides")),
}

# The shapes a part can have. A lying cylinder's axis runs left to right (along X).
SHAPES = ("box", "cylinder", "cylinder_x")


def slots_of(placement: str, shape: str) -> tuple[str, ...]:
    """The slot names a step with this placement and part shape must fill."""
    rule = PLACEMENTS[placement]
    if shape == "cylinder":
        if not rule.round_slots:
            raise ValueError(f"placement {placement!r} has no cylinder form")
        return rule.round_slots
    if shape == "cylinder_x":
        if not rule.lying_slots:
            raise ValueError(f"placement {placement!r} has no lying cylinder form")
        return rule.lying_slots
    return rule.slots


def part_specs(placement: str, shape: str, values: dict[str, Number]) -> list[PartSpec]:
    return PLACEMENTS[placement].parts(shape, values)


def place(name: str, placement: str, shape: str, values: dict[str, Number]) -> list[Solid]:
    """The parts one step adds, as exact numbers.

    `name` is the step's name ("legs"); `values` holds one number per slot.
    This is the function a step engine calls: nothing about the rest of the
    structure is needed, because every position is already one of the slots.
    """
    missing = set(slots_of(placement, shape)) - set(values)
    if missing:
        raise ValueError(f"step {name!r} ({placement}) is missing slots {sorted(missing)}")
    solids = []
    for ending, size, at in part_specs(placement, shape, values):
        solids.append(Solid(
            name=f"{name}_{ending}" if ending else name, shape=shape,
            size=tuple(float(evaluate(f, values)) for f in size),
            at=tuple(float(evaluate(f, values)) for f in at)))
    return solids
