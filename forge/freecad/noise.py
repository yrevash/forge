"""Wrong commands, carried out on purpose while sessions are recorded.

A model trained only on perfect sessions has never seen the state that follows
a mistake, so its first mistake leaves it lost. The answer used here: now
and then carry out a wrong command instead of the teacher's, and
label the states that follow with what the teacher says there. The wrong command
is only ever EXECUTED. It is never stored as a target.

Each session draws one noise level from NOISE_LEVELS: the
chance, at every step, that a wrong command replaces the teacher's.

This is the SECOND noise mix (6 Oct 2026, slices `*_v2`). The first one is
described in the README ("The first noise mix"); its sessions are on disk and are
not recorded again. What the first mix lacked, and what changed:

  wrong numbers   were 15% of the wrong commands and are now about a third, because
                  a wrong number is the mistake that can only be seen by comparing a
                  value in the snapshot with the plan. They are also NEAR misses now
                  (see FLAVOURS): the other dimension of the same shape, the same
                  slot of another item, a value a little off, a wrong sign.
  `done` too early  was never injected; now it is (`early_done`). FreeCAD accepts it
                  whenever there is a valid solid and no open sketch: the session is
                  then marked finished, only `undo` and `new_document` stay valid, and
                  the teacher says `undo`.
  failed features (FreeCAD accepts the command and the feature comes out invalid)
                  were in under 0.1% of the steps. FreeCAD 1.1 fails few features: a pad
                  that floats above the part or a pocket that cuts nothing is carried
                  out and marked valid. What does fail is a fillet, chamfer or wall
                  that the solid has no room for. So there is a kind for exactly that
                  (`failing_feature`), and wrong numbers for those commands are often
                  `too_big`.
  stray selections right before the commands that use the selection, and above all
                  before `done`, so that `clear_selection` is a target often enough.

Kinds of wrong command (drawn with the weights in NOISE_KINDS, see `weights_at`)
    wrong_argument   a teacher command with one argument wrong (FLAVOURS)
    random_valid     any other command that is available now, with any numbers of the plan
    later_item       a command from the recipe of a later plan item
    repeat           the command that was just executed, again
    extra_undo       an `undo` the teacher did not ask for
    stray_click      a command that changes only the session: a selection, leaving a
                     sketch, opening one again
    stray_selection  a selection, when the teacher's command needs another one (or none)
    early_done       `done` while the plan is not complete
    failing_feature  two commands: edges (or the top face) are selected, then rounded,
                     bevelled or hollowed with a number far too large. The second command
                     comes from `follow_up` at the next step and is recorded under the
                     same kind.
Two more things happen to a session that are not drawn here, because they are not one
wrong command: `carry_on` and `reopen`. Both are in play.py.

`new_document` is never injected: it would throw the whole session away, and the
state after it is the ordinary start state every session already contains.

HEAVY_NOISE_LEVELS is for a separate, labelled slice of sessions with many mistakes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from forge.freecad.catalogue import COMMANDS
from forge.freecad.recipes import Context, flatten, recipe
from forge.freecad.teacher import Advice, same
from forge.system1.steps import Step

NOISE_LEVELS = (0.0, 0.0, 0.1, 0.2, 0.3)
HEAVY_NOISE_LEVELS = (0.4, 0.5)
MIX = "v2"      # written into every session header; "v1" is the first mix (no such field)
# Relative weights. A kind that does not apply at a step (nothing to repeat, no later
# item) is left out and the others share the step, so the share of a kind among the
# wrong commands is its weight times how often it applies. Measured shares: README.
NOISE_KINDS = {"wrong_argument": 16.0, "random_valid": 1.0, "later_item": 1.0, "repeat": 0.4,
               "extra_undo": 1.0, "stray_click": 0.6, "stray_selection": 1.2, "early_done": 0.8,
               "failing_feature": 0.8}
# What forge.freecad_multi's own noise leaves out (it imports this name; do not change it).
NEVER_INJECTED = ("done", "new_document")
# What THIS file never injects. `done` is injected by `early_done` only.
NOT_INJECTED = ("new_document",)

# The chance of a wrong command is the session's level, except at two kinds of moment
# that are rare in a session (a few steps each), where it is raised to these values so
# that the mistakes belonging to them are seen often enough. Only in sessions with noise.
CHANCE_BEFORE_DONE = 0.6        # the plan is complete: a stray selection needs clear_selection
CHANCE_AT_TREATMENT = 0.35      # a fillet, chamfer, wall, pattern or mirror is next
BOOST_BEFORE_DONE = 12.0        # weight of stray_selection at that moment
BOOST_BETWEEN_ITEMS = 3.0       # weight of early_done when an item was just completed

# How a wrong number is chosen (wrong_argument), with relative weights. A flavour that
# gives no usable value at a step is left out there.
FLAVOURS = {
    "swapped": 2.0,         # another slot of the same plan item (length for width, x for y)
    "other_item": 1.5,      # the same slot of another plan item (the other hole's diameter)
    "plan_number": 2.0,     # any other number of the plan
    "a_little_off": 2.5,    # 1, 2 or 0.5 more or less, 10% more or less, digits swapped
    "wrong_sign": 1.5,      # -x for x (positions only)
    "random": 1.0,          # any plausible number
    "too_big": 2.0,         # fillet, chamfer, wall, spacing: larger than the part can take
}
TOO_BIG_FOR = ("fillet", "chamfer", "thickness", "linear_pattern")
# Commands that use what is selected: a stray selection right before them matters.
USES_SELECTION = ("new_sketch", "edit_sketch", "pad", "pocket", "pocket_through_all",
                  "hole_counterbore", "fillet", "chamfer", "thickness", "polar_pattern",
                  "linear_pattern", "mirror", "done")

Command = tuple[str, dict]


@dataclass(frozen=True)
class Moment:
    """What a wrong command may depend on: the plan and the state of the session."""

    plan: list[Step]
    context: Context
    advice: Advice                  # what the teacher says here
    valid: list[str]                # commands available now
    previous: Command | None        # the command executed just before
    item_start: bool = False        # is the teacher's command the first of its plan item?


def numbers_of(plan: list[Step]) -> list[float]:
    """Every number of the plan: the only numbers a command can ever be given."""
    return [value for step in plan for value in step.slots.values()]


def chance_at(level: float, advice: Advice) -> float:
    """The chance of a wrong command at this step of a session with this noise level."""
    if level <= 0 or not advice.on_plan:
        return level
    names = advice.names()
    if names == ["done"]:
        return max(level, CHANCE_BEFORE_DONE)
    if any(COMMANDS[name]["group"] in ("dressup", "pattern") for name in names):
        return max(level, CHANCE_AT_TREATMENT)
    return level


def weights_at(m: Moment) -> dict[str, float]:
    """NOISE_KINDS, with the two boosts that belong to this moment."""
    weights = dict(NOISE_KINDS)
    if m.advice.names() == ["done"]:
        weights["stray_selection"] *= BOOST_BEFORE_DONE
    if m.advice.on_plan and m.item_start:
        weights["early_done"] *= BOOST_BETWEEN_ITEMS
    return weights


def _values_for(kind: object, plan: list[Step]) -> list:
    """The values an argument of this type may take, drawn from the plan."""
    if isinstance(kind, tuple):
        return list(kind)
    numbers = numbers_of(plan)
    if kind == "count":
        return sorted({int(n) for n in numbers if float(n).is_integer() and 2 <= n <= 12}
                      | {2, 3, 4, 6})
    if kind == "mm":
        return [float(n) for n in numbers if n > 0]
    if kind == "deg":
        return [0.0, 90.0, 45.0]
    return [0.0, *(float(n) for n in numbers)]      # a position


def _any_args(rng: random.Random, name: str, plan: list[Step]) -> dict:
    return {arg: rng.choice(_values_for(kind, plan)) for arg, kind in COMMANDS[name]["args"].items()}


# --- wrong numbers -------------------------------------------------------------------------------

def _digits_swapped(value: float) -> float | None:
    """85 -> 58, 12.5 -> 21.5: the slip of typing two digits in the wrong order."""
    whole = str(int(abs(value)))
    if len(whole) < 2 or whole[-1] == whole[-2]:
        return None
    swapped = float(whole[:-2] + whole[-1] + whole[-2]) + (abs(value) - int(abs(value)))
    return swapped if value >= 0 else -swapped


def _a_little_off(rng: random.Random, value: float) -> list[float]:
    step = rng.choice((0.5, 1.0, 1.0, 2.0))
    near = [value + step, value - step, round(value * 1.1, 1), round(value * 0.9, 1),
            value * 10, value / 10 if float(value / 10).is_integer() else value + 5.0]
    swapped = _digits_swapped(value)
    return [rng.choice(near), *([] if swapped is None else [swapped])]


def near_misses(rng: random.Random, plan: list[Step], command: str, arg: str, value: object,
                item: int | None, source: str | None) -> dict[str, list]:
    """Wrong values for one argument, by flavour (FLAVOURS). `item` is the plan item the
    command belongs to and `source` where its right value comes from ("slot:diameter")."""
    kind = COMMANDS[command]["args"][arg]
    if isinstance(kind, tuple):                     # a plane, a rule, an axis: another word
        return {"other_word": [word for word in kind if word != value]}
    if kind == "count":
        return {"a_little_off": [value + 1, *([value - 1] if value > 2 else [])],
                "plan_number": _values_for("count", plan), "random": [rng.randint(2, 12)]}
    slot = source.removeprefix("slot:") if source and source.startswith("slot:") else None
    own = plan[item].slots if item is not None else {}
    numbers = [float(n) for n in numbers_of(plan)]
    largest = max((abs(n) for n in numbers), default=10.0)
    found: dict[str, list] = {
        "swapped": [float(n) for name, n in own.items() if name != slot],
        "other_item": [float(step.slots[slot]) for index, step in enumerate(plan)
                       if slot in step.slots and index != item],
        "plan_number": numbers,
        "a_little_off": _a_little_off(rng, float(value)),
        "random": [float(rng.randint(1, max(2, int(2 * largest))))],
    }
    if kind == "deg":
        found = {"plan_number": [0.0, 90.0, 45.0],
                 "a_little_off": [value + 90.0, value - 90.0, value + 45.0],
                 "random": [float(rng.choice(range(0, 180, 15)))]}
    if kind == "pos":
        found["wrong_sign"] = [-float(value)]
        found["random"] = [rng.choice((-1.0, 1.0)) * n for n in found["random"]]
    if command in TOO_BIG_FOR and kind == "mm":
        found["too_big"] = [largest * rng.choice((1.0, 2.0, 5.0)), float(value) * 10]
    return found


def wrong_value(rng: random.Random, plan: list[Step], command: str, arg: str, value: object,
                item: int | None, source: str | None) -> tuple[object, str] | None:
    """(a wrong value for this argument, its flavour), or None if there is none."""
    kind = COMMANDS[command]["args"][arg]

    def usable(other: object) -> bool:
        if same(other, value):
            return False
        return isinstance(kind, tuple) or kind != "mm" or other > 0

    found = {flavour: [other for other in values if usable(other)]
             for flavour, values in near_misses(rng, plan, command, arg, value, item, source).items()}
    found = {flavour: values for flavour, values in found.items() if values}
    if not found:
        return None
    flavours = list(found)
    flavour = rng.choices(flavours, weights=[FLAVOURS.get(name, 1.0) for name in flavours])[0]
    return rng.choice(found[flavour]), flavour


def _wrong_argument(rng: random.Random, m: Moment) -> tuple[str, dict, str] | None:
    choices = [(target, arg) for target in m.advice.targets for arg in target.args]
    if not choices:
        return None
    target, arg = rng.choice(choices)
    wrong = wrong_value(rng, m.plan, target.command, arg, target.args[arg], target.item,
                        target.sources.get(arg))
    if wrong is None:
        return None
    return target.command, {**target.args, arg: wrong[0]}, wrong[1]


# --- the other kinds -----------------------------------------------------------------------------

def _random_valid(rng: random.Random, m: Moment) -> Command | None:
    names = [name for name in m.valid if name not in NEVER_INJECTED]
    if not names:
        return None
    name = rng.choice(names)
    return name, _any_args(rng, name, m.plan)


def _stray_click(rng: random.Random, m: Moment) -> Command | None:
    names = [name for name in m.valid if name not in NEVER_INJECTED
             and not COMMANDS[name]["changes_document"]]
    if not names:
        return None
    name = rng.choice(names)
    return name, _any_args(rng, name, m.plan)


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
    return name, _any_args(rng, name, m.plan)


def _early_done(rng: random.Random, m: Moment) -> Command | None:
    # Not when the plan is complete: `done` would then be right, selection or no selection,
    # and the session would be over without the teacher having said so.
    if m.advice.on_plan and m.advice.active is None:
        return None
    return ("done", {}) if "done" in m.valid else None


FAILING = {"select_edges": ("fillet", "chamfer"), "select_face": ("thickness",)}
TOO_BIG = 3.0       # times the largest number of the plan: no part has room for that


def _failing_feature(rng: random.Random, m: Moment) -> Command | None:
    """The first of two commands: select the edges (or a face) that `follow_up` will treat."""
    names = [name for name in FAILING if name in m.valid]
    if not names:
        return None
    name = rng.choice(names if rng.random() < 0.2 else names[:1])      # mostly edges
    return name, _any_args(rng, name, m.plan)


def follow_up(rng: random.Random, plan: list[Step], kind: str, command: str) -> list[Command]:
    """The commands that complete a wrong command of this kind (only failing_feature has any)."""
    if kind != "failing_feature" or command not in FAILING:
        return []
    treatment = rng.choice(FAILING[command])
    largest = max(abs(float(n)) for n in numbers_of(plan))
    arg = next(iter(COMMANDS[treatment]["args"]))
    return [(treatment, {arg: TOO_BIG * largest})]


def _later_item(rng: random.Random, m: Moment) -> Command | None:
    first = (m.advice.active if m.advice.active is not None else m.advice.built) + 1
    later = [(command.name, dict(command.args)) for step in m.plan[first:]
             for command in flatten(recipe(step, m.context)) if command.name in m.valid]
    return rng.choice(later) if later else None


def _repeat(rng: random.Random, m: Moment) -> Command | None:
    return m.previous


def _extra_undo(rng: random.Random, m: Moment) -> Command | None:
    return ("undo", {}) if "undo" in m.valid else None


_MAKERS = {"random_valid": _random_valid, "wrong_argument": _wrong_argument,
           "later_item": _later_item, "repeat": _repeat, "extra_undo": _extra_undo,
           "stray_click": _stray_click, "stray_selection": _stray_selection,
           "early_done": _early_done, "failing_feature": _failing_feature}


def is_target(command: Command, advice: Advice) -> bool:
    """Is this exactly one of the teacher's commands (same name, same arguments)?"""
    name, args = command
    return any(target.command == name and set(target.args) == set(args)
               and all(same(target.args[arg], args[arg]) for arg in args)
               for target in advice.targets)


def wrong_command_with_detail(rng: random.Random, m: Moment) -> tuple[str, dict, str, str | None] | None:
    """(command, arguments, kind of mistake, flavour of a wrong number or None).

    The kinds are tried in a random order, heavier ones more often first; one that
    does not apply (nothing to repeat yet, no later item) passes to the next. None
    if no kind applies.
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
        name, args, detail = (*made, None) if len(made) == 2 else made
        if name not in NOT_INJECTED and not is_target((name, args), m.advice):
            return name, args, kind, detail
    return None


def wrong_command(rng: random.Random, m: Moment) -> tuple[str, dict, str] | None:
    """A command the teacher does not accept here, and the kind of mistake it is."""
    made = wrong_command_with_detail(rng, m)
    return None if made is None else made[:3]
