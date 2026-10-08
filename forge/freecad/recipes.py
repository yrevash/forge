"""Recipes: the FreeCAD commands that build each step kind of forge/system1/steps.py.

A STEP is one item of a plan ("hole, diameter 6, at x 10 y -5"). A RECIPE is the
list of commands that builds that step in FreeCAD:

    hole(diameter, x, y)  ->  select_plane XY, new_sketch at the top, sketch_circle,
                              { constrain_diameter, constrain_x, constrain_y },
                              leave_sketch, pocket_through_all

The braces are an `AnyOrder` group: the dimensions of one shape can be given in
any order and the result is the same, so a teacher must accept all of them.
Everything outside braces has one right order.

Where each argument comes from is written next to it (`Cmd.sources`):
    "slot:diameter"   copied from the step's own slot
    "base:height"     copied from the start step (a hole is sketched on the top face)
    "floor"           where bosses stand: the top of the base, or the floor of a shelled base
    "const"           a fixed choice of the recipe (the XY plane, a hexagon's 6 sides)
    "derived:..."     arithmetic on slots; only the two patterns need it (see `_feature`)

This file is plain Python: it does not start FreeCAD. `forge/freecad/build.py`
sends a recipe's commands to a worker.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from forge.system1.steps import FEATURES, STARTS, TREATMENTS, Step


@dataclass(frozen=True)
class Cmd:
    """One command with its arguments, and where each argument comes from."""

    name: str
    args: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AnyOrder:
    """Commands that may be issued in any order (the dimensions of one sketch shape)."""

    commands: tuple[Cmd, ...]


Recipe = list[Cmd | AnyOrder]


@dataclass(frozen=True)
class Context:
    """What a recipe needs to know about the part besides its own step."""

    base: str           # block, cylinder, hex or ring
    height: float       # the base's height: cuts are sketched on its top face
    floor: float        # where bosses and pads stand: the top, or a shell's floor


def context_of(steps: list[Step]) -> Context:
    """The context of a part, read off its start step and its edge treatment."""
    start = steps[0]
    if start.kind not in STARTS:
        raise ValueError("the first step must be a start")
    height = start.slots["height"]
    shell = [step for step in steps[1:] if step.kind == "shell"]
    return Context(start.kind, height, shell[0].slots["wall_thickness"] if shell else height)


# --- building blocks -------------------------------------------------------------------------

def _dimension(name: str, value: float, source: str) -> Cmd:
    return Cmd(f"constrain_{name}", {"value": value}, {"value": source})


def _sketch(offset: float, offset_source: str, shape: Cmd, dimensions: list[Cmd]) -> Recipe:
    """A new sketch on the XY plane at a height, one shape, its dimensions in any order."""
    return [Cmd("select_plane", {"plane": "XY"}, {"plane": "const"}),
            Cmd("new_sketch", {"offset": offset}, {"offset": offset_source}),
            shape, AnyOrder(tuple(dimensions))]


def _centred(dimensions: list[Cmd]) -> list[Cmd]:
    return [*dimensions, _dimension("x", 0.0, "const"), _dimension("y", 0.0, "const")]


def _at(step: Step) -> list[Cmd]:
    return [_dimension("x", step.slots["x"], "slot:x"), _dimension("y", step.slots["y"], "slot:y")]


def _slot_dimensions(step: Step, names: tuple[str, ...]) -> list[Cmd]:
    return [_dimension(name, step.slots[name], f"slot:{name}") for name in names]


# --- starts ----------------------------------------------------------------------------------

def _start(step: Step) -> Recipe:
    s = step.slots
    pad = [Cmd("leave_sketch"), Cmd("pad", {"length": s["height"]}, {"length": "slot:height"})]
    if step.kind == "block":
        outline = _sketch(0.0, "const", Cmd("sketch_rectangle"),
                          _centred(_slot_dimensions(step, ("length", "width"))))
    elif step.kind == "cylinder":
        outline = _sketch(0.0, "const", Cmd("sketch_circle"),
                          _centred(_slot_dimensions(step, ("diameter",))))
    elif step.kind == "hex":
        # Flats face +X and -X, corners lie on the Y axis: the first side runs along Y.
        outline = _sketch(0.0, "const", Cmd("sketch_polygon", {"sides": 6}, {"sides": "const"}),
                          _centred([*_slot_dimensions(step, ("across_flats",)),
                                    _dimension("angle", 90.0, "const")]))
    else:
        # A ring is one sketch with two circles: FreeCAD pads the area between them.
        outline = _sketch(0.0, "const", Cmd("sketch_circle"), _centred(
            [_dimension("diameter", s["outer_diameter"], "slot:outer_diameter")]))
        outline += [Cmd("sketch_circle"), AnyOrder(tuple(_centred(
            [_dimension("diameter", s["inner_diameter"], "slot:inner_diameter")])))]
    return outline + pad


# --- edge treatments ---------------------------------------------------------------------------

def _treatment(step: Step) -> Recipe:
    value = step.slots[TREATMENTS[step.kind][0]]
    source = f"slot:{TREATMENTS[step.kind][0]}"
    if step.kind == "shell":
        return [Cmd("select_face", {"rule": "top"}, {"rule": "const"}),
                Cmd("thickness", {"value": value}, {"value": source})]
    rule = "vertical" if step.kind == "corner_radius" else "top_face"
    if step.kind == "top_chamfer":
        finish = Cmd("chamfer", {"size": value}, {"size": source})
    else:
        finish = Cmd("fillet", {"radius": value}, {"radius": source})
    return [Cmd("select_edges", {"rule": rule}, {"rule": "const"}), finish]


# --- features ----------------------------------------------------------------------------------

def _circle_at(context: Context, diameter: Cmd, where: list[Cmd], on_floor: bool = False) -> Recipe:
    offset, source = (context.floor, "floor") if on_floor else (context.height, "base:height")
    return [*_sketch(offset, source, Cmd("sketch_circle"), [diameter, *where]),
            Cmd("leave_sketch")]


def _feature(step: Step, context: Context) -> Recipe:
    s = step.slots
    kind = step.kind.removesuffix("_pair")
    through = Cmd("pocket_through_all")

    if kind == "hole":
        recipe = [*_circle_at(context, *_slot_dimensions(step, ("diameter",)), _at(step)), through]
    elif kind == "blind_hole":
        recipe = [*_circle_at(context, *_slot_dimensions(step, ("diameter",)), _at(step)),
                  Cmd("pocket", {"depth": s["depth"]}, {"depth": "slot:depth"})]
    elif kind == "counterbore":
        # FreeCAD's Hole feature makes the hole and its recess in one go. The sketch circle
        # marks the position; giving it the hole's diameter leaves the sketch fully fixed.
        recipe = [*_circle_at(context, _dimension("diameter", s["hole_diameter"],
                                                  "slot:hole_diameter"), _at(step)),
                  Cmd("hole_counterbore",
                      {"diameter": s["hole_diameter"], "counterbore_diameter": s["diameter"],
                       "counterbore_depth": s["depth"]},
                      {"diameter": "slot:hole_diameter", "counterbore_diameter": "slot:diameter",
                       "counterbore_depth": "slot:depth"})]
    elif kind == "boss":
        recipe = [*_circle_at(context, *_slot_dimensions(step, ("diameter",)), _at(step),
                              on_floor=True),
                  Cmd("pad", {"length": s["height"]}, {"length": "slot:height"})]
    elif kind in ("pad", "pocket"):
        raised = kind == "pad"
        offset, source = (context.floor, "floor") if raised else (context.height, "base:height")
        finish = (Cmd("pad", {"length": s["height"]}, {"length": "slot:height"}) if raised
                  else Cmd("pocket", {"depth": s["depth"]}, {"depth": "slot:depth"}))
        recipe = [*_sketch(offset, source, Cmd("sketch_rectangle"),
                           [*_slot_dimensions(step, ("length", "width")), *_at(step)]),
                  Cmd("leave_sketch"), finish]
    elif kind == "slot":
        recipe = [*_sketch(context.height, "base:height", Cmd("sketch_slot"),
                           [*_slot_dimensions(step, ("length", "width", "angle")), *_at(step)]),
                  Cmd("leave_sketch"),
                  Cmd("pocket", {"depth": s["depth"]}, {"depth": "slot:depth"})]
    elif kind == "polar":
        # One hole on the +X axis at the pitch radius, then copies round the Z axis.
        # The radius is half the pitch circle diameter: arithmetic on a slot.
        where = [_dimension("x", s["circle_diameter"] / 2, "derived:circle_diameter / 2"),
                 _dimension("y", 0.0, "const")]
        recipe = [*_circle_at(context, _dimension("diameter", s["hole_diameter"],
                                                  "slot:hole_diameter"), where), through,
                  Cmd("select_tip"),
                  Cmd("polar_pattern", {"count": s["count"], "axis": "Z"},
                      {"count": "slot:count", "axis": "const"})]
    elif kind == "row":
        # The row is centred on x = 0, so its first hole sits half the row's span to the left.
        first = -(s["count"] - 1) * s["spacing"] / 2
        where = [_dimension("x", first, "derived:-(count - 1) * spacing / 2"),
                 _dimension("y", s["y"], "slot:y")]
        recipe = [*_circle_at(context, _dimension("diameter", s["hole_diameter"],
                                                  "slot:hole_diameter"), where), through,
                  Cmd("select_tip"),
                  Cmd("linear_pattern",
                      {"count": s["count"], "spacing": s["spacing"], "direction": "X"},
                      {"count": "slot:count", "spacing": "slot:spacing", "direction": "const"})]
    else:
        raise ValueError(f"no recipe for {step.kind}")

    if step.kind.endswith("_pair"):
        # The twin: a mirror image of the feature just built, across the YZ plane.
        recipe += [Cmd("select_tip"), Cmd("mirror", {"plane": "YZ"}, {"plane": "const"})]
    return recipe


# --- what callers use --------------------------------------------------------------------------

def recipe(step: Step, context: Context) -> Recipe:
    """The commands that build one step."""
    if step.kind in STARTS:
        return [Cmd("new_document"), Cmd("new_body"), *_start(step)]
    if step.kind in TREATMENTS:
        return _treatment(step)
    if step.kind in FEATURES:
        return _feature(step, context)
    if step.kind == "done":
        return [Cmd("done")]
    if step.kind == "undo":
        raise ValueError("undo has no recipe: it takes back a whole step (see build.undo_step)")
    raise ValueError(f"no recipe for {step.kind}")


def flatten(steps: Recipe, rng: random.Random | None = None) -> list[Cmd]:
    """A recipe as a plain list of commands. With `rng`, every any-order group is shuffled."""
    commands: list[Cmd] = []
    for entry in steps:
        if isinstance(entry, Cmd):
            commands.append(entry)
        else:
            group = list(entry.commands)
            if rng is not None:
                rng.shuffle(group)
            commands += group
    return commands


def orders(steps: Recipe) -> int:
    """How many different command orders the recipe accepts."""
    total = 1
    for entry in steps:
        if isinstance(entry, AnyOrder):
            for k in range(2, len(entry.commands) + 1):
                total *= k
    return total
