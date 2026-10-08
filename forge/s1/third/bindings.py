"""Bindings: where each argument of a command comes from, as a choice the model can make.

The first two models named a command and the teacher typed the numbers. The third model
names, for the command it chose:

    the plan item it is working on            a pointer at one row of the plan
    for each argument, one KIND of source     a word of the closed list KINDS below

and `resolve` turns (plan, command, item, kinds) into the arguments by plain code. It reads
the PLAN ONLY: no teacher, no recording, no FreeCAD. So the model can run alone, and it can
be wrong: point at the wrong item, or take the width where the length belongs.

    plan item 2 is {"kind": "pad", "slots": {"length": 15, "width": 26, "height": 11, ...}}
    constrain_length, item 2, ("slot:length",)   ->  {"value": 15.0}
    constrain_length, item 2, ("slot:width",)    ->  {"value": 26.0}     a wrong number, executed
    new_sketch,       item 2, ("base_height",)   ->  {"offset": <height of plan item 0>}

The kinds are exactly the sources the recipes write (forge/freecad/recipes.py), measured on
the recordings: slot, const, base:height, floor, and two pieces of arithmetic. `kinds_of_target`
reads a teacher's target into this form (LABEL side), and `expressible` checks that
resolving it gives the recorded arguments back.
"""

from __future__ import annotations

from forge.freecad.catalogue import AXES, COMMANDS, EDGE_RULES, FACE_RULES, PLANES, SKETCH_AXES
from forge.system1.steps import SLOTS

MAX_ARGS = max(len(command["args"]) for command in COMMANDS.values())      # 3: hole_counterbore
SLOT_NAMES = tuple(sorted({slot for slots in SLOTS.values() for slot in slots}))
# The constants the recipes use. Numbers: a centred x or y and a sketch on the base plane
# (0), the hexagon's angle (90) and its sides (6). Words: every word an argument can be.
NUMBER_CONSTANTS = (0.0, 90.0, 6)
WORD_CONSTANTS = tuple(dict.fromkeys((*PLANES, *AXES, *SKETCH_AXES, *EDGE_RULES, *FACE_RULES)))
RULES = ("base_height", "floor", "half:circle_diameter", "row_first")
# Kind 0 is "this command has no such argument". The order is fixed: ids go into the arrays.
KINDS = ("none", *(f"slot:{name}" for name in SLOT_NAMES), *RULES,
         *(f"const:{value:g}" for value in NUMBER_CONSTANTS),
         *(f"const:{word}" for word in WORD_CONSTANTS))
KIND_ID = {kind: place for place, kind in enumerate(KINDS)}
NO_ITEM = 255       # stored where a binding needs no plan item
# Kinds whose value is read from the pointed plan item (the others ignore the pointer).
_SOURCE_KIND = {"base:height": "base_height", "floor": "floor",
                "derived:circle_diameter / 2": "half:circle_diameter",
                "derived:-(count - 1) * spacing / 2": "row_first"}


def needs_item(kind: str) -> bool:
    return kind.startswith(("slot:", "half:")) or kind == "row_first"


def floor_of(plan: list[dict]) -> float:
    """Where bosses stand: the floor of a shelled base, else the top of the base
    (the same rule as forge.freecad.recipes.context_of, written out on the plain plan)."""
    shells = [item for item in plan[1:] if item["kind"] == "shell"]
    return float(shells[0]["slots"]["wall_thickness"] if shells else plan[0]["slots"]["height"])


def value_of(plan: list[dict], item: int | None, kind: str) -> object | None:
    """The value a kind stands for in this plan, or None when it cannot be read (the item
    has no such slot, or the kind needs an item and there is none)."""
    if kind.startswith("const:"):
        text = kind[6:]
        return text if text in WORD_CONSTANTS else (6 if text == "6" else float(text))
    if kind == "base_height":
        return float(plan[0]["slots"]["height"]) if "height" in plan[0]["slots"] else None
    if kind == "floor":
        return floor_of(plan) if "height" in plan[0]["slots"] else None
    if item is None or not 0 <= item < len(plan):
        return None
    slots = plan[item]["slots"]
    if kind.startswith("slot:"):
        return slots.get(kind[5:])
    if kind == "half:circle_diameter":
        return slots["circle_diameter"] / 2 if "circle_diameter" in slots else None
    if kind == "row_first":
        return -(slots["count"] - 1) * slots["spacing"] / 2 \
            if "count" in slots and "spacing" in slots else None
    return None


def fits(kind_of_argument: object, value: object) -> bool:
    """Is this value of the type the catalogue asks for (a word of the list, or a number)?"""
    if isinstance(kind_of_argument, tuple):
        return value in kind_of_argument
    return isinstance(value, (int, float)) and not isinstance(value, (bool, str))


def allowed(plan: list[dict], command: str, item: int | None) -> list[list[bool]]:
    """Per argument of the command, which kinds can be read at all for this item: the mask
    the driver puts on the model's choice. It asks only "does such a value exist and is it
    a word where a word is needed"; it never asks whether the value is the right one."""
    return [[place > 0 and (value := value_of(plan, item, kind)) is not None
             and fits(wanted, value) for place, kind in enumerate(KINDS)]
            for wanted in COMMANDS[command]["args"].values()]


def resolve(plan: list[dict], command: str, item: int | None,
            kinds: list[str]) -> dict | None:
    """The command's arguments from the plan alone. None when a kind cannot be read."""
    args = {}
    for (name, wanted), kind in zip(COMMANDS[command]["args"].items(), kinds, strict=True):
        value = value_of(plan, item, kind)
        if value is None or not fits(wanted, value):
            return None
        if wanted == "count":
            args[name] = int(value)
        else:
            args[name] = value if isinstance(value, str) else float(value)
    return args


def kinds_of_target(target: dict, source: dict) -> tuple[int | None, list[str]]:
    """LABEL side. A label entry {"command", "args"} and its `sources` entry -> (the plan
    item the binding points at, or None when no argument needs one; the kind per argument)."""
    kinds = []
    for name in COMMANDS[target["command"]]["args"]:
        said = source["sources"][name]
        if said == "const":
            value = target["args"][name]
            kinds.append(f"const:{value}" if isinstance(value, str) else f"const:{value:g}")
        else:
            kinds.append(said if said.startswith("slot:") else _SOURCE_KIND[said])
        if kinds[-1] not in KIND_ID:
            raise ValueError(f"{target['command']}.{name}: {kinds[-1]!r} is not a kind of "
                             f"forge.s1.third.bindings.KINDS")
    return source["item"], kinds


def expressible(plan: list[dict], target: dict, source: dict, tolerance: float = 1e-6) -> bool:
    """Does resolving the teacher's own binding give the teacher's arguments back?"""
    try:
        item, kinds = kinds_of_target(target, source)
    except (KeyError, ValueError, TypeError):
        return False
    args = resolve(plan, target["command"], item, kinds)
    if args is None or set(args) != set(target["args"]):
        return False
    return all(a == b if isinstance(a, str) or isinstance(b, str) else abs(a - b) <= tolerance
               for a, b in ((args[name], target["args"][name]) for name in args))
