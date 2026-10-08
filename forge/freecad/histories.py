"""Noise-like histories for the undo proof: wrong work of the kind recorded sessions contain.

`undo_proof.py` parts A to D undo single commands and a few scripted mistakes. The
livelock of 6 Oct 2026 came from something they never tried: an extra rectangle
drawn on top of the first, a stray pad of that sketch, then six undos. So this
file makes such chains at random, in whatever state the session is in:

    stray_feature   a sketch on any plane at any height with one to three shapes (now
                    and then the same shape twice, exactly on top of itself), some
                    dimensions, then a pad, pocket, hole or revolution of it, and
                    sometimes a mirror, a pattern, a fillet or a chamfer on top
    random_walk     three to ten commands picked at random from the valid list,
                    with numbers of the plan
    in_the_sketch   (only while a sketch is open) extra shapes and dimensions in the
                    open sketch, then leave it and pad or pocket it

Every number comes from the plan, as in noise.py. Nothing here knows the teacher.
A chain is a list of INTENTIONS: a command that is not valid when its turn comes
is skipped, so a chain can be run from any state.
"""

from __future__ import annotations

import random

from forge.freecad.catalogue import COMMANDS, DIMENSIONS, EDGE_RULES, PLANES
from forge.freecad.client import FreeCADClient

Command = tuple[str, dict]
CHAIN_KINDS = ("stray_feature", "random_walk", "in_the_sketch")
NOT_IN_A_CHAIN = ("new_document", "undo", "done")
SHAPES = ("rectangle", "circle", "polygon", "slot")


def _number(rng: random.Random, numbers: list[float]) -> float:
    return float(rng.choice([n for n in numbers if n > 0]))


def _args(rng: random.Random, name: str, numbers: list[float]) -> dict:
    """Arguments for a command: any allowed word, any number of the plan."""
    args = {}
    for arg, kind in COMMANDS[name]["args"].items():
        if isinstance(kind, tuple):
            args[arg] = rng.choice(kind)
        elif kind == "count":
            args[arg] = rng.choice((2, 3, 4, 6))
        elif kind == "deg":
            args[arg] = rng.choice((0.0, 45.0, 90.0, 360.0))
        elif kind == "pos":
            args[arg] = rng.choice((0.0, 1.0, -1.0)) * _number(rng, numbers)
        else:
            args[arg] = _number(rng, numbers)
    return args


def _shapes(rng: random.Random, numbers: list[float]) -> list[Command]:
    """One to three shapes with some of their dimensions. One time in three the last shape
    is drawn twice with nothing in between: two outlines exactly on top of each other."""
    commands: list[Command] = []
    for _ in range(rng.randint(1, 3)):
        shape = rng.choice(SHAPES)
        draw = (f"sketch_{shape}", {"sides": rng.choice((3, 5, 6, 8))} if shape == "polygon" else {})
        commands.append(draw)
        if rng.random() < 0.33:
            commands.append(draw)
        names = [name for name in DIMENSIONS[shape] if name != "diameter" or shape == "circle"]
        rng.shuffle(names)
        for name in names[:rng.randint(0, len(names))]:
            commands.append((f"constrain_{name}", _args(rng, f"constrain_{name}", numbers)))
    return commands


def _feature(rng: random.Random, numbers: list[float]) -> list[Command]:
    """Leave the sketch, make a feature of it, and sometimes something on top of that."""
    name = rng.choice(("pad", "pad", "pocket", "pocket_through_all", "hole_through",
                       "hole_counterbore", "revolution"))
    commands: list[Command] = [("leave_sketch", {}), (name, _args(rng, name, numbers))]
    on_top = rng.random()
    if on_top < 0.3:
        copy = rng.choice(("mirror", "polar_pattern", "linear_pattern"))
        commands += [("select_tip", {}), (copy, _args(rng, copy, numbers))]
    elif on_top < 0.5:
        treat = rng.choice(("fillet", "chamfer"))
        commands += [("select_edges", {"rule": rng.choice(EDGE_RULES)}),
                     (treat, _args(rng, treat, numbers))]
    return commands


def chain(rng: random.Random, kind: str, numbers: list[float]) -> list[Command]:
    """The intentions of one wrong chain (see the top of this file)."""
    if kind == "in_the_sketch":
        return [*_shapes(rng, numbers), *_feature(rng, numbers)]
    if kind == "stray_feature":
        offset = rng.choice((0.0, _number(rng, numbers)))
        return [("select_plane", {"plane": rng.choice(PLANES)}), ("new_sketch", {"offset": offset}),
                *_shapes(rng, numbers), *_feature(rng, numbers)]
    return [("?", {})] * rng.randint(3, 10)        # random_walk: chosen when its turn comes


def run_chain(fc: FreeCADClient, rng: random.Random, kind: str, numbers: list[float],
              valid: list[str]) -> tuple[list[Command], dict]:
    """Carry a chain out. Returns (the commands FreeCAD accepted, the last reply or {})."""
    carried_out: list[Command] = []
    reply: dict = {}
    for name, args in chain(rng, kind, numbers):
        if name == "?":
            names = [n for n in valid if n not in NOT_IN_A_CHAIN]
            if not names:
                break
            name = rng.choice(names)
            args = _args(rng, name, numbers)
        if name not in valid:
            continue
        answer = fc.command(name, **args)
        if answer["status"] == "ok":
            reply, valid = answer, answer["valid"]
            carried_out.append((name, args))
    return carried_out, reply

