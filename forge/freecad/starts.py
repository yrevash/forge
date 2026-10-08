"""Start states: a session does not always begin in an empty FreeCAD.

A person sits down at whatever is on the screen: nothing, an empty document, a
half-drawn sketch someone left, a file opened from disk. The executor must cope
with all of them, so sessions are recorded from all of them. A start state is a
short list of commands carried out BEFORE the first recorded step; the teacher
then has to read the session it finds.

    kind           what is there at step 0                     what the teacher does first
    empty          no document                                 new_document
    document       an empty document                           new_body
    body           a document with a body                      select_plane
    partial        the first commands of a correct build       carries on from there
    stray_sketch   a sketch the plan never asked for,          undo until it is gone
                   on any plane, half drawn, open or closed
    stray_solid    a padded shape that is not the plan's       undo until it is gone
    wrong_partial  the first commands of a correct build,      undo back to the wrong number,
                   ONE of them with a wrong number             however deep it lies
    opened         one of the last four, with the undo         carries on (partial), or
                   history gone (a file opened from disk)      new_document (anything else)

The chances are our own choice: about four sessions in ten start empty, as a first
use would, and the rest are shared between the others.

Second mix (6 Oct 2026, with noise.py's): `wrong_partial` is new, and `opened` went
from 8% to 14% of the sessions. Both cover what the first mix lacked: a wrong
number that is already there when the session starts can only be found by reading
the snapshot against the plan, and an opened document that is off plan is the one
state where the repair is `new_document` (it was a target in 0.13% of the steps).
"""

from __future__ import annotations

import random

from forge.freecad.catalogue import DIMENSIONS, PLANES
from forge.freecad.noise import numbers_of, wrong_value
from forge.freecad.recipes import context_of, flatten, recipe
from forge.system1.steps import Step

START_KINDS = {"empty": 0.42, "document": 0.07, "body": 0.07, "partial": 0.10,
               "stray_sketch": 0.08, "stray_solid": 0.06, "wrong_partial": 0.06, "opened": 0.14}
SHAPES = ("rectangle", "circle", "polygon", "slot")

Command = tuple[str, dict]
BODY: list[Command] = [("new_document", {}), ("new_body", {})]


def _sizes(plan: list[Step]) -> list[float]:
    return [float(n) for n in numbers_of(plan) if n > 0]


def _partial(rng: random.Random, plan: list[Step]) -> list[Command]:
    """The first few commands of a correct build (dimensions in a random order)."""
    context = context_of(plan)
    commands = [(c.name, dict(c.args)) for step in plan for c in flatten(recipe(step, context), rng)]
    return commands[:rng.randint(3, len(commands) - 1)]


def _wrong_partial(rng: random.Random, plan: list[Step]) -> list[Command]:
    """The first few commands of a correct build with one wrong number among them.

    The wrong number is a near miss (noise.wrong_value). The commands after it are the
    ones a builder who had not noticed would issue; FreeCAD may refuse some of them (a
    pad of a sketch that was never closed), and run_start leaves those out.
    """
    context = context_of(plan)
    commands = [(index, c) for index, step in enumerate(plan)
                for c in flatten(recipe(step, context), rng)]
    commands = commands[:rng.randint(3, len(commands) - 1)]
    with_numbers = [at for at, (_, c) in enumerate(commands) if c.args]
    out: list[Command] = [(c.name, dict(c.args)) for _, c in commands]
    if with_numbers:
        at = rng.choice(with_numbers)
        index, command = commands[at]
        arg = rng.choice(sorted(command.args))
        wrong = wrong_value(rng, plan, command.name, arg, command.args[arg], index,
                            command.sources.get(arg))
        if wrong is not None:
            out[at] = (command.name, {**command.args, arg: wrong[0]})
    return out


def _draw(rng: random.Random, plan: list[Step], dimensions: int | None = None) -> list[Command]:
    """One shape with some of its dimensions (all of them if `dimensions` is None)."""
    shape = rng.choice(SHAPES)
    names = [name for name in DIMENSIONS[shape] if not (shape == "polygon" and name == "diameter")]
    rng.shuffle(names)
    sizes = _sizes(plan)
    commands: list[Command] = [(f"sketch_{shape}", {"sides": rng.choice((3, 5, 6, 8))}
                                if shape == "polygon" else {})]
    for name in names[:dimensions]:
        value = rng.choice((0.0, 90.0)) if name == "angle" else rng.choice(sizes)
        if name in ("x", "y"):
            value = rng.choice((0.0, value, -value))
        commands.append((f"constrain_{name}", {"value": value}))
    return commands


def _stray_sketch(rng: random.Random, plan: list[Step]) -> list[Command]:
    offset = rng.choice([0.0, *_sizes(plan)])
    commands = [*BODY, ("select_plane", {"plane": rng.choice(PLANES)}),
                ("new_sketch", {"offset": offset})]
    if rng.random() < 0.8:
        commands += _draw(rng, plan, rng.randint(0, 3))
    if rng.random() < 0.5:
        commands.append(("leave_sketch", {}))
    return commands


def _stray_solid(rng: random.Random, plan: list[Step]) -> list[Command]:
    return [*BODY, ("select_plane", {"plane": "XY"}), ("new_sketch", {"offset": 0.0}),
            *_draw(rng, plan), ("leave_sketch", {}), ("pad", {"length": rng.choice(_sizes(plan))})]


_MAKERS = {"partial": _partial, "stray_sketch": _stray_sketch, "stray_solid": _stray_solid,
           "wrong_partial": _wrong_partial}


def draw_start(rng: random.Random, plan: list[Step]) -> tuple[str, list[Command], bool]:
    """(kind, the commands to carry out before step 0, forget the undo history afterwards?)"""
    kind = rng.choices(list(START_KINDS), weights=list(START_KINDS.values()))[0]
    if kind == "empty":
        return kind, [], False
    if kind == "document":
        return kind, BODY[:1], False
    if kind == "body":
        return kind, list(BODY), False
    if kind == "opened":
        inner = rng.choice(tuple(_MAKERS))
        return f"opened_{inner}", _MAKERS[inner](rng, plan), True
    return kind, _MAKERS[kind](rng, plan), False
