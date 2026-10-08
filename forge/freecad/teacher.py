"""The teacher at command level: for any plan and any snapshot, the acceptable next commands.

    advice = teacher(plan, snapshot)
    advice.targets     the SET of commands a careful person could issue next
    advice.on_plan     is everything in the session so far what the plan asks for?
    advice.built       how many plan items are completely built
    advice.why         one plain sentence, for people

It is a pure function of (plan, snapshot). It keeps no memory of how the session
got here, so it can label any state: a clean build, a session with injected
mistakes, a session that did not start empty, later a state a model drove into.
It reads the LEAN snapshot (lean.py); the full one works too.

How it reads the session
    The plan is laid out as one long SCRIPT: the recipe of item 0, then item 1,
    and so on (recipes.py). Every command of the script that edits the document
    leaves something behind that the snapshot shows: a sketch at a height, a
    shape in it, a dimension that is `fixed`, a pad of a length. The teacher
    walks the script and ticks off each such command against the snapshot's
    items, in the order they were made.

The rules
    1. ON PLAN, something still to do. The walk stops at the first command whose
       trace is missing. That command is the answer if the session is ready for
       it; otherwise the answer is the one command that makes it ready:
           a new sketch needs its plane selected      -> select_plane
           drawing needs the sketch open              -> edit_sketch (select_sketch first)
           a pad, pocket or hole needs the sketch
             closed and selected                      -> leave_sketch, or select_sketch
           a fillet, chamfer or shell needs its edges -> select_edges / select_face
           a pattern or mirror needs the tip          -> select_tip
       A wrong selection is simply replaced by the right one; nothing is undone.
       Inside a sketch, the dimensions of the newest shape may come in any order,
       so the answer is every dimension not given yet.
    2. OFF PLAN. If the walk meets anything the plan does not have at that place
       (a wrong number, a wrong or extra object, an invalid feature, a second
       body, a part marked finished too early) the answer is `undo`, and it stays
       `undo` until what is left is on plan. If nothing can be undone (a document
       that was opened, not made here) the answer is `new_document`.
    3. DONE. Everything is built and correct: `clear_selection` if something is
       still selected, then `done`. After `done` there is nothing to do and the
       set is empty.

Why undo and never "fix in place": it needs no knowledge of what went wrong, and
one rule covers every mistake. The price is that a mistake buried under good work
costs that work too. The rule leans on undo being EXACT: the state after an undo
must be the state before the command in every respect, also in what the snapshot
does not show. FreeCAD's own undo is not (inside/session.py, "Exact undo"); the
runtime makes it so, and undo_proof.py part E checks it on noise-like histories.

The teacher cannot loop by itself (it has no memory), but a teacher and a runtime
together can: if a command the teacher asks for fails in a way the snapshot does
not explain, the answer is `undo`, and then the same command again, for ever.
`Watch` (below) is the exit: whoever drives a session feeds it every step and
stops when it says the session is stuck.

"What the plan has" compares values, not history: a 40 mm circle is right
whichever number it was copied from.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.freecad.catalogue import COMMANDS, DIMENSION_COMMANDS, SHAPE_COMMANDS
from forge.freecad.recipes import AnyOrder, Cmd, context_of, recipe
from forge.system1.steps import Step

TOLERANCE = 1e-6    # mm or degrees: two numbers closer than this are the same number

# What each feature command leaves in the snapshot: the item's type, which item field
# holds each argument, and the fields that command always sets.
_NO_BORE = {"counterbore_diameter": None, "counterbore_depth": None}
TRACE: dict[str, tuple[str, dict[str, str], dict[str, object]]] = {
    "pad": ("pad", {"length": "length"}, {}),
    "pocket": ("pocket", {"depth": "depth"}, {"through_all": False}),
    "pocket_through_all": ("pocket", {}, {"through_all": True, "depth": None}),
    "revolution": ("revolution", {"angle": "angle", "axis": "axis"}, {}),
    "hole_through": ("hole", {"diameter": "diameter"},
                     {"through_all": True, "depth": None, **_NO_BORE}),
    "hole_blind": ("hole", {"diameter": "diameter", "depth": "depth"},
                   {"through_all": False, **_NO_BORE}),
    "hole_counterbore": ("hole", {"diameter": "diameter",
                                  "counterbore_diameter": "counterbore_diameter",
                                  "counterbore_depth": "counterbore_depth"},
                         {"through_all": True, "depth": None}),
    "fillet": ("fillet", {"radius": "radius"}, {}),
    "chamfer": ("chamfer", {"size": "size"}, {}),
    "thickness": ("thickness", {"value": "value"}, {}),
    "polar_pattern": ("polar_pattern", {"count": "count", "axis": "axis"}, {"angle": 360.0}),
    "linear_pattern": ("linear_pattern", {"count": "count", "spacing": "spacing",
                                          "direction": "direction"}, {}),
    "mirror": ("mirror", {"plane": "plane"}, {}),
}


@dataclass(frozen=True)
class Target:
    """One acceptable command: its name, its arguments and where they come from."""

    command: str
    args: dict
    item: int | None    # the plan item this command works on; None for undo, done and the like
    sources: dict       # argument -> "slot:x", "base:height", "floor", "const" or "derived:..."

    def to_json(self) -> dict:
        return {"command": self.command, "args": self.args, "item": self.item,
                "sources": self.sources}


@dataclass(frozen=True)
class Advice:
    targets: tuple[Target, ...]     # empty only when the part is finished and correct
    on_plan: bool
    built: int                      # plan items completely built, counted from the first
    active: int | None              # the plan item the targets work on
    why: str
    # Where in the script (script_of) the walk stopped: the entry the targets come from.
    # None when the session is off plan or the plan is complete.
    position: int | None = None

    def names(self) -> list[str]:
        return [target.command for target in self.targets]


Script = list[tuple[int, Cmd | AnyOrder]]


def script_of(plan: list[Step]) -> Script:
    """The whole plan as one list of (plan item index, recipe entry)."""
    context = context_of(plan)
    return [(index, entry) for index, step in enumerate(plan)
            for entry in recipe(step, context)]


def clean_length(plan: list[Step]) -> int:
    """How many commands a session without mistakes takes, `done` included."""
    return 1 + sum(len(entry.commands) if isinstance(entry, AnyOrder) else 1
                   for _, entry in script_of(plan))


def same(a: object, b: object) -> bool:
    numbers = (int, float)
    if isinstance(a, numbers) and isinstance(b, numbers) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= TOLERANCE
    return a == b


class _OffPlan(Exception):
    """Something in the session is not what the plan has at that place."""


def _target(index: int | None, command: Cmd | str) -> Target:
    if isinstance(command, str):
        return Target(command, {}, index, {})
    return Target(command.name, dict(command.args), index, dict(command.sources))


def _selected(selection: dict | None, need: Cmd, tip: str | None) -> bool:
    """Is the selection what this select command would make it?"""
    if selection is None:
        return False
    if need.name == "select_plane":
        return selection == {"type": "plane", "name": need.args["plane"]}
    if need.name in ("select_edges", "select_face"):
        kind = "edges" if need.name == "select_edges" else "face"
        return (selection["type"] == kind and selection.get("rule") == need.args["rule"]
                and selection.get("of") == tip)
    return selection == {"type": "feature", "name": tip}       # select_tip


def _check_feature(item: dict, entry: Cmd, body: str, sketch: dict | None, newest: str | None,
                   before: list[Cmd]) -> None:
    """Raise _OffPlan unless `item` is exactly what `entry` leaves behind."""
    kind, fields, always = TRACE[entry.name]
    name = item["name"]
    if item["type"] != kind:
        raise _OffPlan(f"{name} is a {item['type']}, the plan has a {kind} here")
    if not item["valid"]:
        raise _OffPlan(f"{name} failed to build")
    if item["body"] != body:
        raise _OffPlan(f"{name} is in another body")
    for field, arg in fields.items():
        if not same(item[field], entry.args[arg]):
            raise _OffPlan(f"{name} has {field} {item[field]}, the plan has {entry.args[arg]}")
    for field, value in always.items():
        if not same(item[field], value):
            raise _OffPlan(f"{name} has {field} {item[field]}, the plan has {value}")
    group = COMMANDS[entry.name]["group"]
    if group == "feature" and item["sketch"] != sketch["name"]:
        raise _OffPlan(f"{name} does not use {sketch['name']}")
    if group == "dressup" and (item["on"] != newest or item.get("rule") != before[-1].args["rule"]):
        raise _OffPlan(f"{name} is not on the {before[-1].args['rule']} edges or face of {newest}")
    if group == "pattern" and item["of"] != newest:
        raise _OffPlan(f"{name} does not copy {newest}")


def _walk(script: Script, snapshot: dict, trail: list[int] | None = None,
          ) -> tuple[list[Target], int | None, str]:
    """Tick the script off against the snapshot. Returns (targets, active item, why);
    raises _OffPlan (with the item index as its second argument) when the session strays.
    `trail`, if given, receives the script positions the walk visited, in order."""
    session, items = snapshot["session"], snapshot["items"]
    bodies = [item for item in items if item["type"] == "body"]
    objects = [item for item in items if item["type"] != "body"]
    selection, open_sketch = session["selection"], session["open_sketch"]
    done = 0                    # how many of `objects` are ticked off
    sketch: dict | None = None  # the sketch the walk is in
    shape_at = -1               # its newest shape that is ticked off
    newest: str | None = None   # the newest feature ticked off: what a fillet or a mirror uses
    before: list[Cmd] = []      # session commands since the last document command
    index = 0

    def in_sketch(commands: list[Cmd], why: str) -> tuple[list[Target], int, str]:
        """Commands that need `sketch` open: open it first if it is not."""
        if open_sketch == sketch["name"]:
            return [_target(index, command) for command in commands], index, why
        if selection == {"type": "sketch", "name": sketch["name"]}:
            return [_target(index, "edit_sketch")], index, "the sketch is not finished: open it"
        return [_target(index, "select_sketch")], index, "the sketch is not finished: select it"

    try:
        for at, (index, entry) in enumerate(script):
            if trail is not None:
                trail.append(at)
            if isinstance(entry, AnyOrder):
                shape = sketch["shapes"][shape_at]
                wanted = {DIMENSION_COMMANDS[command.name]: command for command in entry.commands}
                for dimension in shape["fixed"]:
                    if dimension not in wanted:
                        raise _OffPlan(f"the {shape['shape']} was given a {dimension}, "
                                       f"which the plan does not give")
                    value = wanted[dimension].args["value"]
                    if not same(shape[dimension], value):
                        raise _OffPlan(f"the {shape['shape']} has {dimension} "
                                       f"{shape[dimension]:g}, the plan has {value:g}")
                missing = [command for dimension, command in wanted.items()
                           if dimension not in shape["fixed"]]
                if missing:
                    if done != len(objects) or shape_at != len(sketch["shapes"]) - 1:
                        raise _OffPlan(f"something was made after an unfinished {shape['shape']}")
                    return in_sketch(missing, f"give the {shape['shape']} its dimensions")
                continue

            name = entry.name
            if name == "new_document":
                if not session["document"]:
                    return [_target(index, entry)], index, "there is no document"
            elif name == "new_body":
                if not bodies:
                    return [_target(index, entry)], index, "the document has no body"
                if len(bodies) != 1 or items[0] is not bodies[0] or not bodies[0]["active"] \
                        or not bodies[0]["valid"]:
                    raise _OffPlan("there is more than one body")
            elif not COMMANDS[name]["changes_document"]:
                before.append(entry)    # select_plane, leave_sketch, select_edges, select_tip
            elif name == "new_sketch":
                plane = before[-1].args["plane"]
                if done == len(objects):
                    if _selected(selection, before[-1], session["tip"]):
                        return [_target(index, entry)], index, "start the sketch"
                    return [_target(index, before[-1])], index, "select the plane for the sketch"
                item = objects[done]
                if item["type"] != "sketch":
                    raise _OffPlan(f"{item['name']} is a {item['type']}, the plan has a sketch here")
                if item["plane"] != plane or not same(item["offset"], entry.args["offset"]):
                    raise _OffPlan(f"{item['name']} is on {item['plane']} at {item['offset']:g}, "
                                   f"the plan has {plane} at {entry.args['offset']:g}")
                if not item["valid"] or item["body"] != bodies[0]["name"]:
                    raise _OffPlan(f"{item['name']} is broken or in another body")
                done, sketch, shape_at, before = done + 1, item, -1, []
            elif name in SHAPE_COMMANDS:
                if shape_at + 1 == len(sketch["shapes"]):
                    if done != len(objects):
                        raise _OffPlan(f"something was made after the unfinished {sketch['name']}")
                    return in_sketch([entry], f"draw the {SHAPE_COMMANDS[name]}")
                shape = sketch["shapes"][shape_at + 1]
                if shape["shape"] != SHAPE_COMMANDS[name] \
                        or shape.get("sides") != entry.args.get("sides"):
                    raise _OffPlan(f"{sketch['name']} has a {shape['shape']}, the plan has a "
                                   f"{SHAPE_COMMANDS[name]}")
                shape_at += 1
            else:                       # a feature, an edge treatment or a pattern
                group = COMMANDS[name]["group"]
                if group == "feature" and len(sketch["shapes"]) != shape_at + 1:
                    raise _OffPlan(f"{sketch['name']} has a shape the plan does not have")
                if done == len(objects):
                    if group == "feature":
                        if open_sketch == sketch["name"]:
                            return [_target(index, before[-1])], index, \
                                "the sketch is complete: leave it"
                        if selection != {"type": "sketch", "name": sketch["name"]}:
                            return [_target(index, "select_sketch")], index, \
                                f"select {sketch['name']} to use it"
                    elif not _selected(selection, before[-1], session["tip"]):
                        return [_target(index, before[-1])], index, \
                            f"select what {name} works on"
                    return [_target(index, entry)], index, f"{name} is next"
                _check_feature(objects[done], entry, bodies[0]["name"], sketch, newest, before)
                done, newest, before = done + 1, objects[done]["name"], []
        index = script[-1][0] + 1       # every plan item is ticked off
        if done != len(objects):
            raise _OffPlan(f"{objects[done]['name']} is not in the plan")
    except _OffPlan as off:
        raise _OffPlan(str(off), index) from None

    if session["finished"]:
        return [], None, "the part is finished"
    if selection is not None:
        return [_target(None, "clear_selection")], None, "the part is complete: clear the selection"
    return [_target(None, "done")], None, "the part is complete"


def teacher(plan: list[Step], snapshot: dict, script: Script | None = None) -> Advice:
    """The acceptable next commands for this plan in this state (see the rules above).

    Pass `script_of(plan)` as `script` when calling many times for one plan.
    """
    script = script_of(plan) if script is None else script
    session = snapshot["session"]
    trail: list[int] = []
    try:
        targets, active, why = _walk(script, snapshot, trail)
        built = len(plan) if active is None else active
        if not session["finished"] or not targets:
            position = trail[-1] if trail and active is not None else None
            return Advice(tuple(targets), True, built, active, why, position)
        problem = "the part was marked finished too early"
    except _OffPlan as off:
        problem, built = off.args
    # Rule 2: off plan. Undo; when nothing can be undone, start a new document.
    repair = "undo" if session["undo_depth"] > 0 else "new_document"
    return Advice((_target(None, repair),), False, built, None, f"off plan: {problem}")


# --- the exit: a driver must never loop ------------------------------------------------------

class Watch:
    """Notices a session that cannot make progress, so its driver can stop.

    Feed it every step: what the teacher said before the command, the command, whether
    it was the teacher's own, FreeCAD's status, and what the teacher says afterwards.
    It counts the one thing that must never happen twice: the teacher's own command,
    issued on an on-plan state, was carried out and left the session OFF plan (the
    feature failed although the plan and the snapshot say it should work), or was not
    carried out at all. Once is put down to FreeCAD (a time-out); the second time for
    the same command at the same place the session is stuck.
    """

    LIMIT = 2

    def __init__(self) -> None:
        self.failed: dict[tuple, int] = {}

    def stuck(self, before: Advice, command: str, args: dict, own: bool, status: str,
              after: Advice) -> bool:
        if not own or not before.on_plan or (status == "ok" and after.on_plan):
            return False
        key = (before.built, before.active, command, tuple(sorted(args.items())))
        self.failed[key] = self.failed.get(key, 0) + 1
        return self.failed[key] >= self.LIMIT


# --- plans as stored in session files --------------------------------------------------------

def plan_json(plan: list[Step]) -> list[dict]:
    return [{"kind": step.kind, "slots": dict(step.slots)} for step in plan]


def plan_from_json(stored: list[dict]) -> list[Step]:
    return [Step(item["kind"], dict(item["slots"])) for item in stored]
