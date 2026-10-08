"""Wrong commands for structure sessions, carried out on purpose while sessions are recorded.

The pattern is forge/freecad/noise.py (its second mix): now and then a wrong command is
EXECUTED instead of the teacher's, and the states that follow are labelled with what the
teacher says there. A wrong command is never stored as a target. Each session draws one
noise level from NOISE_LEVELS (0, 0, 0.1, 0.2, 0.3): the chance, at every step, that a
wrong command replaces the teacher's.

This is the structure mix "s2" (6 Oct 2026). The first structure recording (`boxes_v1`)
used the first single-part mix plus four structure kinds; it lacked the
improved single-part kinds and mistakes on features, which it could not have.

WRONG NUMBERS: one of the teacher's commands with ONE argument wrong. They are the
mistakes that can only be seen by comparing a number on the screen with the plan, so they
get the largest weight (about a third of the wrong commands carried out; measured share
in the manifest). The kind says what the number belongs to:

    wrong_size        a dimension, a pad or a primitive of a part's shape, or the offset of
                      its sketch
    misplaced_body    one coordinate of `move_body` (or the whole place: another part's,
                      or two coordinates swapped)
    misturned_body    one angle of `turn_body`
    wrong_feature     a number of a feature: its size, its place on the face, its depth,
                      the offset of its sketch, a radius
    wrong_side        `select_side` / `select_side_edges` with another side or rule
    wrong_word        another plane, another number of sides
and the FLAVOUR says how the wrong value was chosen (near misses, FLAVOURS below).

THE OTHER KINDS
    wrong_body        `activate_body` on a body the next command does not belong to. With
                      `carry_on` (play.py) the feature is then built on that body.
    extra_body        a `new_body` nobody asked for
    early_done        `done` while the plan is not complete
    failing_feature   two commands: edges or a side are selected, then rounded, bevelled or
                      hollowed with a number far too large, so the feature comes out
                      invalid. The second command comes from `follow_up`.
    stray_selection   a selection, when the teacher's command needs another one (or none)
    random_valid, later_item, repeat, extra_undo, stray_click    as for single parts

`new_document` is never injected (it would throw the session away). `done` is injected by
`early_done` only.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from forge.freecad.noise import HEAVY_NOISE_LEVELS, NOISE_LEVELS, _a_little_off, is_target
from forge.freecad.teacher import Advice, same
from forge.freecad_multi.multi_catalogue import COMMANDS
from forge.freecad_multi.recipes import Item, flatten

__all__ = ["FLAVOURS", "HEAVY_NOISE_LEVELS", "MIX", "NOISE_KINDS", "NOISE_LEVELS", "WRONG_NUMBERS",
           "Moment", "chance_at", "follow_up", "numbers_of", "wrong_command"]

MIX = "s2"
# Relative weights. A kind that does not apply at a step is left out there and the others
# share the step, so a kind's share of the wrong commands is its weight times how often it
# applies (measured shares: the manifest, `noisy_steps_share_by_kind`).
NOISE_KINDS = {"wrong_number": 22.0, "random_valid": 0.8, "later_item": 0.6, "repeat": 0.4,
               "extra_undo": 1.0, "stray_click": 0.6, "stray_selection": 1.2, "early_done": 0.8,
               "failing_feature": 0.8, "wrong_body": 1.2, "extra_body": 0.8}
# The kinds a `wrong_number` is recorded as (see the top of this file).
WRONG_NUMBERS = ("wrong_size", "misplaced_body", "misturned_body", "wrong_feature", "wrong_side",
                 "wrong_word")
NOT_INJECTED = ("new_document",)

CHANCE_BEFORE_DONE = 0.6        # the plan is complete: a stray selection needs clear_selection
CHANCE_AT_FEATURE = 0.35        # a side, an edge treatment or `activate_body` is next: rare moments
CHANCE_AT_PLACING = 0.2         # `move_body` / `turn_body` is next: one or two steps per body, and
                                # a misplaced body is the mistake only a structure can have
BOOST_BEFORE_DONE = 12.0        # weight of stray_selection at that moment
BOOST_BETWEEN_ITEMS = 3.0       # weight of early_done and wrong_body when an item starts

# How a wrong number is chosen, with relative weights. A flavour that gives no usable
# value at a step is left out there.
FLAVOURS = {
    "swapped": 2.5,         # another number of the same part or feature (length for width)
    "other_item": 2.0,      # the same argument of another part or feature (the other leg's height)
    "plan_number": 2.0,     # any other number of the plan
    "a_little_off": 2.5,    # 0.5, 1 or 2 more or less, 10% more or less, digits swapped
    "wrong_sign": 1.5,      # -x for x (positions only)
    "random": 1.0,          # any plausible number
    "too_big": 2.0,         # fillet, chamfer, wall: larger than the part can take
    "other_place": 2.0,     # move_body: another part's place
    "swapped_axes": 2.0,    # move_body: two coordinates of the right place swapped
    "other_word": 1.0,      # another side, rule, plane
}
TOO_BIG_FOR = ("fillet", "chamfer", "thickness")
QUARTER_TURNS = (0.0, 90.0, -90.0, 180.0)
TRIANGLE = [[0.0, 0.0], [20.0, 0.0], [0.0, 10.0]]      # an outline when the plan has none
# Commands that use what is selected: a stray selection right before them matters.
USES_SELECTION = ("new_sketch", "edit_sketch", "pad", "pad_symmetric", "pocket", "fillet",
                  "chamfer", "thickness", "done")
# Two-command mistakes that leave an invalid feature: the selection, then what uses it.
FAILING = {"select_side_edges": ("fillet", "chamfer"), "select_side": ("thickness",),
           "select_edges": ("fillet", "chamfer"), "select_face": ("thickness",)}
TOO_BIG = 3.0       # times the largest number of the plan: no part has room for that

Command = tuple[str, dict]


@dataclass(frozen=True)
class Moment:
    """What a wrong command may depend on: the plan and the state of the session."""

    items: list[Item]
    numbers: list[float]            # every number of the plan (numbers_of)
    places: dict                    # body number -> (position, turn), from the teacher
    advice: Advice                  # what the teacher says here
    valid: list[str]                # commands available now
    previous: Command | None        # the command executed just before
    bodies: int                     # how many bodies the document holds
    item_start: bool = False        # is the teacher's command the first of its plan item?


def _numbers(args: dict) -> list[float]:
    return [float(value) for value in args.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)]


def numbers_of(items: list[Item]) -> list[float]:
    """Every number of the plan's commands: the only numbers a command can ever be given."""
    return [number for item in items for command in flatten(item.entries)
            for number in _numbers(command.args)]


def chance_at(level: float, advice: Advice) -> float:
    """The chance of a wrong command at this step of a session with this noise level."""
    if level <= 0 or not advice.on_plan:
        return level
    names = advice.names()
    if names == ["done"]:
        return max(level, CHANCE_BEFORE_DONE)
    if any(name in ("select_side", "select_side_edges", "activate_body", "fillet", "chamfer",
                    "thickness") for name in names):
        return max(level, CHANCE_AT_FEATURE)
    if any(name in ("move_body", "turn_body") for name in names):
        return max(level, CHANCE_AT_PLACING)
    return level


def weights_at(m: Moment) -> dict[str, float]:
    """NOISE_KINDS, with the boosts that belong to this moment."""
    weights = dict(NOISE_KINDS)
    if m.advice.names() == ["done"]:
        weights["stray_selection"] *= BOOST_BEFORE_DONE
    if m.advice.on_plan and m.item_start:
        weights["early_done"] *= BOOST_BETWEEN_ITEMS
        weights["wrong_body"] *= BOOST_BETWEEN_ITEMS
    return weights


def _outlines(items: list[Item]) -> list[list]:
    return [command.args["points"] for item in items for command in flatten(item.entries)
            if command.name == "sketch_outline"] or [TRIANGLE]


def _values_for(kind: object, m: Moment) -> list:
    """The values an argument of this type may take, drawn from the plan."""
    if isinstance(kind, tuple):
        return list(kind)
    if kind == "count":
        return [2, 3, 4, 6]
    if kind == "index":
        return list(range(1, max(m.bodies, 1) + 1))
    if kind == "points":
        return _outlines(m.items)
    if kind == "mm":
        return [n for n in m.numbers if n > 0] or [10.0]
    if kind == "size0":
        return [0.0, *(n for n in m.numbers if n > 0)]
    if kind == "deg":
        return [0.0, 90.0, 45.0, 180.0, -90.0]
    return [0.0, *m.numbers]        # a position


def _any_args(rng: random.Random, m: Moment, name: str) -> dict:
    return {arg: rng.choice(_values_for(kind, m)) for arg, kind in COMMANDS[name]["args"].items()}


# --- wrong numbers -------------------------------------------------------------------------------

def kind_of(command: str, source: str | None) -> str:
    """Which kind of wrong number a wrong argument of this command is recorded as."""
    if command == "move_body":
        return "misplaced_body"
    if command == "turn_body":
        return "misturned_body"
    if command in ("select_side", "select_side_edges"):
        return "wrong_side"
    if source and source.startswith("cut:"):
        return "wrong_feature"
    if command in ("select_plane", "sketch_polygon"):
        return "wrong_word"
    return "wrong_size"


def near_misses(rng: random.Random, m: Moment, target, arg: str) -> dict[str, list]:
    """Wrong values for one argument of a teacher command, by flavour (FLAVOURS)."""
    command, value = target.command, target.args[arg]
    kind = COMMANDS[command]["args"][arg]
    if isinstance(kind, tuple):                     # a side, a rule, a plane: another word
        return {"other_word": [word for word in kind if word != value]}
    if kind == "count":
        return {"a_little_off": [value + 1, *([value - 1] if value > 3 else [])],
                "random": [rng.randint(3, 8)]}
    if kind == "index":
        return {"other_item": list(range(1, m.bodies + 1))}
    if kind == "points":
        return {"other_item": [points for points in _outlines(m.items) if points != value]}
    own = m.items[target.item] if target.item is not None else None
    own_numbers = [n for c in flatten(own.entries) for n in _numbers(c.args)] if own else []
    others = [float(c.args[arg]) for index, item in enumerate(m.items) if index != target.item
              for c in flatten(item.entries) if c.name == command and arg in c.args]
    largest = max((abs(n) for n in m.numbers), default=10.0)
    found: dict[str, list] = {
        "swapped": own_numbers, "other_item": others, "plan_number": list(m.numbers),
        "a_little_off": _a_little_off(rng, float(value)),
        "random": [float(rng.randint(1, max(2, int(2 * largest))))],
    }
    if kind == "deg":
        found = {"a_little_off": [value + 90.0, value - 90.0, value + 180.0],
                 "random": [rng.choice(QUARTER_TURNS)], "plan_number": [45.0]}
    if kind == "pos":
        found["wrong_sign"] = [-float(value)]
        found["random"] = [rng.choice((-1.0, 1.0)) * n for n in found["random"]]
    if command in TOO_BIG_FOR and kind == "mm":
        found["too_big"] = [largest * rng.choice((1.0, 2.0, 5.0)), float(value) * 10]
    return found


def _whole_place(rng: random.Random, m: Moment, target) -> dict[str, list]:
    """Wrong places for `move_body`, all three coordinates at once."""
    x, y, z = (target.args[name] for name in ("x", "y", "z"))
    places = [list(place[0]) for place in m.places.values()]
    return {"other_place": [{"x": a, "y": b, "z": c} for a, b, c in places],
            "swapped_axes": [{"x": y, "y": x, "z": z}, {"x": x, "y": z, "z": y},
                             {"x": z, "y": y, "z": x}]}


def _wrong_number(rng: random.Random, m: Moment) -> tuple[str, dict, str, str] | None:
    """(command, arguments, kind, flavour): a teacher command with one thing wrong."""
    choices = [(target, arg) for target in m.advice.targets for arg in target.args
               if target.command != "activate_body"]
    if not choices:
        return None
    target, arg = rng.choice(choices)
    kind = COMMANDS[target.command]["args"][arg]

    def usable(other: object) -> bool:
        if same(other, target.args[arg]):
            return False
        if kind == "mm":
            return other > 0
        return kind != "size0" or other >= 0

    found = {flavour: [other for other in values if usable(other)]
             for flavour, values in near_misses(rng, m, target, arg).items()}
    if target.command == "move_body":
        found |= {flavour: [{"whole": args} for args in places
                            if any(not same(args[n], target.args[n]) for n in args)]
                  for flavour, places in _whole_place(rng, m, target).items()}
    found = {flavour: values for flavour, values in found.items() if values}
    if not found:
        return None
    flavours = list(found)
    flavour = rng.choices(flavours, weights=[FLAVOURS.get(name, 1.0) for name in flavours])[0]
    wrong = rng.choice(found[flavour])
    args = dict(wrong["whole"]) if isinstance(wrong, dict) else {**target.args, arg: wrong}
    return target.command, args, kind_of(target.command, target.sources.get(arg)), flavour


# --- the other kinds -----------------------------------------------------------------------------

def _random_valid(rng: random.Random, m: Moment) -> Command | None:
    names = [name for name in m.valid if name not in ("done", "new_document")]
    if not names:
        return None
    name = rng.choice(names)
    return name, _any_args(rng, m, name)


def _stray_click(rng: random.Random, m: Moment) -> Command | None:
    names = [name for name in m.valid if name not in ("done", "new_document")
             and not COMMANDS[name]["changes_document"]]
    if not names:
        return None
    name = rng.choice(names)
    return name, _any_args(rng, m, name)


def _stray_selection(rng: random.Random, m: Moment) -> Command | None:
    """A selection, at a moment when the teacher's command uses what is selected."""
    if not any(name in USES_SELECTION for name in m.advice.names()):
        return None
    names = [name for name in m.valid if COMMANDS[name]["group"] == "select"]
    if m.advice.names() == ["done"]:        # nothing must be selected: select something
        names = [name for name in names if name != "clear_selection"]
    if not names:
        return None
    name = rng.choice(names)
    return name, _any_args(rng, m, name)


def _early_done(rng: random.Random, m: Moment) -> Command | None:
    # Not when the plan is complete: `done` would then be right.
    if m.advice.on_plan and m.advice.active is None:
        return None
    return ("done", {}) if "done" in m.valid else None


def _failing_feature(rng: random.Random, m: Moment) -> Command | None:
    """The first of two commands: select the edges (or a side) that `follow_up` will treat."""
    names = [name for name in FAILING if name in m.valid]
    if not names:
        return None
    name = rng.choice(names)
    return name, _any_args(rng, m, name)


def follow_up(rng: random.Random, numbers: list[float], kind: str, command: str) -> list[Command]:
    """The commands that complete a wrong command of this kind (only failing_feature has any)."""
    if kind != "failing_feature" or command not in FAILING:
        return []
    treatment = rng.choice(FAILING[command])
    largest = max((abs(n) for n in numbers), default=10.0)
    arg = next(iter(COMMANDS[treatment]["args"]))
    return [(treatment, {arg: TOO_BIG * largest})]


def _later_item(rng: random.Random, m: Moment) -> Command | None:
    first = (m.advice.active if m.advice.active is not None else m.advice.built) + 1
    later = [(command.name, dict(command.args)) for item in m.items[first:]
             for command in flatten(item.entries) if command.name in m.valid]
    return rng.choice(later) if later else None


def _repeat(rng: random.Random, m: Moment) -> Command | None:
    return m.previous


def _extra_undo(rng: random.Random, m: Moment) -> Command | None:
    return ("undo", {}) if "undo" in m.valid else None


def _wrong_body(rng: random.Random, m: Moment) -> Command | None:
    if "activate_body" not in m.valid or m.bodies < 2:
        return None
    return "activate_body", {"index": rng.randint(1, m.bodies)}


def _extra_body(rng: random.Random, m: Moment) -> Command | None:
    return ("new_body", {}) if "new_body" in m.valid else None


_MAKERS = {"wrong_number": _wrong_number, "random_valid": _random_valid,
           "later_item": _later_item, "repeat": _repeat, "extra_undo": _extra_undo,
           "stray_click": _stray_click, "stray_selection": _stray_selection,
           "early_done": _early_done, "failing_feature": _failing_feature,
           "wrong_body": _wrong_body, "extra_body": _extra_body}


def wrong_command(rng: random.Random, m: Moment) -> tuple[str, dict, str, str | None] | None:
    """(command, arguments, kind of mistake, flavour of a wrong number or None).

    The kinds are tried in a weighted random order; one that does not apply passes to the
    next. None if no kind applies. Never one of the teacher's own commands.
    """
    left = weights_at(m)
    order = []
    while left:     # a weighted shuffle: draw without putting back
        kind = rng.choices(list(left), weights=list(left.values()))[0]
        order.append(kind)
        del left[kind]
    for kind in order:
        made = _MAKERS[kind](rng, m)
        if made is None:
            continue
        name, args = made[0], made[1]
        kind, detail = (made[2], made[3]) if len(made) == 4 else (kind, None)
        if name not in NOT_INJECTED and not is_target((name, args), m.advice):
            return name, args, kind, detail
    return None
