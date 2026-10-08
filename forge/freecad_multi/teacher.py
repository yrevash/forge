"""The teacher for structures: for any resolved plan and any snapshot, the acceptable next commands.

    advice = teacher(items, snapshot)        # items = recipes.structure_items(bodies)
    advice.targets     the SET of commands a careful person could issue next
    advice.on_plan     is everything in the session so far what the plan asks for?
    advice.built       how many plan items are completely built
    advice.why         one plain sentence, for people

It follows the single-part teacher (forge/freecad/teacher.py) rule for rule, and like it
is a pure function of (plan, snapshot): no memory of how the session got here. It reads
the lean snapshot (lean.py); the full one works too.

How it reads the session
    The plan is one long SCRIPT (recipes.script_of): new_document, then every item (a
    part, or a feature on a part) in the plan's order, then done. FreeCAD lists a
    document's objects in the order they were made, across all bodies, and that is the
    order of the script. So the teacher walks the script and ticks each command off
    against the next object: the n-th `new_body` against the n-th body, a sketch against
    a sketch IN THE RIGHT BODY at the right place, a pad against a pad of that length.
    Placing leaves no object; it is read off the body: `move_body` is ticked when the
    body's position is the plan's, `turn_body` when its turn is.

The rules
    1. ON PLAN, something still to do. The walk stops at the first command whose trace is
       missing. That command is the answer if the session is ready for it; otherwise the
       answer is the one command that makes it ready (the single-part repairs: select the
       plane or side, open or select the sketch, select the edges), and one more:
           the command belongs to a body that is not the active one  -> activate_body
       A wrong selection or a wrong active body is replaced, not undone. The two placing
       commands of a part may come in either order, like the dimensions of a shape.
    2. OFF PLAN -> `undo`, until what is left is on plan (`new_document` if nothing can be
       undone). Off plan is: a wrong number, a wrong or extra object, an object in the
       wrong body, an invalid feature, and for structures
           a body that the plan has not reached yet (an extra `new_body`)
           a body that is neither where it was made nor where the plan puts it, or
           neither unturned nor turned as the plan says (a wrongly PLACED body)
           a body whose shape has a wrong number (a wrongly SIZED body)
       A misplaced body is found at once, whichever body it is and however much has been
       built since, because every body's placement is looked at on every call.
    3. DONE. Everything built and correct: `clear_selection` if needed, then `done`.

Why undo and not "move it again": a second `move_body` would repair a misplaced body, but
then every kind of mistake would need its own repair. Undo is exact (undo_proof.py) and
one rule covers all of them.
"""

from __future__ import annotations

from forge.freecad import teacher as base
from forge.freecad.teacher import Advice, Target, same
from forge.freecad_multi.multi_catalogue import (
    COMMANDS,
    DIMENSION_COMMANDS,
    PLACING,
    PRIMITIVES,
    SHAPE_COMMANDS,
)
from forge.freecad_multi.recipes import (
    AnyOrder,
    Cmd,
    Item,
    Script,
    flatten,
    from_yaw_pitch_roll,
    script_of,
)
from forge.resolve import space as sp

__all__ = ["Advice", "Target", "clean_length", "places_of", "teacher"]

ORIGIN = (0.0, 0.0, 0.0)
PLACE_TOLERANCE = 1e-6      # mm, and the same number for the entries of a turn

# What each feature command leaves in the snapshot (see the base table). A pad made by
# `pad` must not be symmetric and one made by `pad_symmetric` must be.
TRACE: dict[str, tuple[str, dict[str, str], dict[str, object]]] = {
    **base.TRACE,
    "pad": ("pad", {"length": "length"}, {"symmetric": False}),
    "pad_symmetric": ("pad", {"length": "length"}, {"symmetric": True}),
    "add_cone": ("cone", {name: name for name in ("bottom_diameter", "top_diameter",
                                                  "height")}, {}),
    "add_sphere": ("sphere", {"diameter": "diameter"}, {}),
    "add_dome": ("dome", {"diameter": "diameter", "height": "height"}, {}),
    "add_tapered_box": ("tapered_box", {name: name for name in (
        "bottom_length", "bottom_depth", "top_length", "top_depth", "height")}, {}),
}


class _OffPlan(Exception):
    """Something in the session is not what the plan has at that place."""


def clean_length(items: list[Item]) -> int:
    """How many commands a session without mistakes takes, `done` included."""
    return len(flatten([entry for _, entry in script_of(items)]))


def places_of(items: list[Item]) -> dict[int, tuple[sp.Vec, sp.Mat]]:
    """body number -> (position, turn) the plan gives it; the origin, unturned, if it says
    nothing."""
    places = {}
    for item in items:
        if item.kind != "part":
            continue
        position, turn = ORIGIN, sp.IDENTITY
        for command in flatten(item.entries):
            if command.name == "move_body":
                position = (command.args["x"], command.args["y"], command.args["z"])
            elif command.name == "turn_body":
                turn = from_yaw_pitch_roll(command.args["yaw"], command.args["pitch"],
                                           command.args["roll"])
        places[item.body] = (position, turn)
    return places


def _target(index: int | None, command: Cmd | str) -> Target:
    if isinstance(command, str):
        return Target(command, {}, index, {})
    return Target(command.name, dict(command.args), index, dict(command.sources))


def _at(position: list[float], wanted: sp.Vec) -> bool:
    return all(abs(a - b) <= PLACE_TOLERANCE for a, b in zip(position, wanted))


def _turned(matrix: list[list[float]], wanted: sp.Mat) -> bool:
    return all(abs(a - b) <= PLACE_TOLERANCE
               for row, wanted_row in zip(matrix, wanted) for a, b in zip(row, wanted_row))


def _check_places(bodies: list[dict], places: dict[int, tuple[sp.Vec, sp.Mat]]) -> None:
    """Every body is where it was made or where the plan puts it; else it is misplaced."""
    for body in bodies:
        position, turn = places[body["index"]]
        place = body["placement"]
        if not (_at(place["position"], position) or _at(place["position"], ORIGIN)):
            raise _OffPlan(f"body {body['index']} is at {place['position']}, the plan has "
                           f"{list(position)}")
        if not (_turned(place["matrix"], turn) or _turned(place["matrix"], sp.IDENTITY)):
            raise _OffPlan(f"body {body['index']} is turned {place['turn']}, which is not "
                           "the plan's turn")


def _selected(selection: dict | None, need: Cmd, tip: str | None) -> bool:
    """Is the selection what this select command would make it?"""
    if selection is None:
        return False
    if need.name == "select_plane":
        return selection == {"type": "plane", "name": need.args["plane"]}
    if need.name == "select_side":
        return selection == {"type": "side", "side": need.args["side"]}
    if need.name == "select_side_edges":
        return (selection["type"] == "edges" and selection.get("side") == need.args["side"]
                and selection.get("rule") == need.args["rule"] and selection.get("of") == tip)
    return base._selected(selection, need, tip)


def _check_feature(item: dict, entry: Cmd, body: str, sketch: dict | None, newest: str | None,
                   before: list[Cmd]) -> None:
    """Raise _OffPlan unless `item` is exactly what `entry` leaves behind, in that body."""
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
    if entry.name in PRIMITIVES:
        return
    group = COMMANDS[entry.name]["group"]
    if group == "feature" and item["sketch"] != sketch["name"]:
        raise _OffPlan(f"{name} does not use {sketch['name']}")
    if group == "dressup":
        chose = before[-1]
        right = item["on"] == newest and item.get("side") == chose.args.get("side")
        if chose.name != "select_side":         # a side alone (a hollowing) has no edge rule
            right = right and item.get("rule") == chose.args["rule"]
        if not right:
            raise _OffPlan(f"{name} is not on the edges or face the plan names")
    if group == "pattern" and item["of"] != newest:
        raise _OffPlan(f"{name} does not copy {newest}")


def _same_points(drawn: list[list[float]], wanted: list[list[float]]) -> bool:
    return len(drawn) == len(wanted) and all(
        same(a, b) for point, other in zip(drawn, wanted) for a, b in zip(point, other))


def _walk(script: Script, items: list[Item], places: dict, snapshot: dict,
          stopped: list[int | None]) -> tuple[list[Target], int | None, str]:
    """Tick the script off against the snapshot. Returns (targets, active item, why);
    raises _OffPlan (with the item index as its second argument) when the session strays.
    `stopped[0]` is left at the place in the script where the walk stopped."""
    session, listed = snapshot["session"], snapshot["items"]
    bodies = [item for item in listed if item["type"] == "body"]
    objects = [item for item in listed if item["type"] != "body"]
    selection, open_sketch = session["selection"], session["open_sketch"]
    done = 0                        # how many of `objects` are ticked off
    reached = 0                     # how many bodies the walk has come to
    sketch: dict | None = None      # the sketch the walk is in
    shape_at = -1                   # its newest shape that is ticked off
    newest: dict[str, str] = {}     # per body: its newest feature ticked off
    before: list[Cmd] = []          # session commands since the last document command
    index: int | None = None

    def body_of(item_index: int) -> dict:
        return bodies[items[item_index].body - 1]

    def stop(targets: list[Target], why: str, in_body: bool = True,
             ) -> tuple[list[Target], int | None, str]:
        """Every answer leaves through here: the checks that look at the whole session."""
        if len(bodies) > reached:
            raise _OffPlan(f"body {reached + 1} is not asked for yet")
        if len(bodies) > len(places):
            raise _OffPlan("there are more bodies than the plan has parts")
        _check_places(bodies, places)
        if in_body and not body_of(index)["active"]:
            number = items[index].body
            return ([Target("activate_body", {"index": number}, index,
                            {"index": "plan:part number"})], index,
                    f"the next command works on body {number}: make it the active one")
        return targets, index, why

    def in_sketch(commands: list[Cmd], why: str) -> tuple[list[Target], int | None, str]:
        """Commands that need `sketch` open: open it first if it is not."""
        if open_sketch == sketch["name"]:
            return stop([_target(index, command) for command in commands], why)
        if selection == {"type": "sketch", "name": sketch["name"]}:
            return stop([_target(index, "edit_sketch")], "the sketch is not finished: open it")
        return stop([_target(index, "select_sketch")], "the sketch is not finished: select it")

    try:
        for at, (index, entry) in enumerate(script):
            stopped[0] = at
            if isinstance(entry, AnyOrder) and entry.commands[0].name in PLACING:
                place, (position, turn) = body_of(index)["placement"], places[items[index].body]
                missing = [command for command in entry.commands
                           if not (_at(place["position"], position)
                                   if command.name == "move_body"
                                   else _turned(place["matrix"], turn))]
                if missing:
                    if done != len(objects) or len(bodies) > reached:
                        raise _OffPlan(f"something was made after body {items[index].body} "
                                       "was left unplaced")
                    return stop([_target(index, command) for command in missing],
                                "put the body in its place")
                continue
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
                    return [_target(None, entry)], 0, "there is no document"
            elif name == "activate_body":
                continue                # never ticked: `stop` asks for it whenever it is needed
            elif name == "new_body":
                number = items[index].body
                if len(bodies) < number:
                    if done != len(objects):
                        raise _OffPlan(f"{objects[done]['name']} is not in the plan")
                    return stop([_target(index, entry)], f"start body {number}", in_body=False)
                if not bodies[number - 1]["valid"]:
                    raise _OffPlan(f"body {number} is broken")
                reached, sketch, shape_at, before = number, None, -1, []
            elif not COMMANDS[name]["changes_document"]:
                before.append(entry)    # select_plane, select_side, leave_sketch, select edges
            elif name == "new_sketch":
                chose = before[-1]
                if done == len(objects):
                    if _selected(selection, chose, body_of(index)["tip"]) \
                            and body_of(index)["active"]:
                        return stop([_target(index, entry)], "start the sketch")
                    return stop([_target(index, chose)], "select where the sketch goes")
                item = objects[done]
                if item["type"] != "sketch":
                    raise _OffPlan(f"{item['name']} is a {item['type']}, the plan has a sketch here")
                where = (item["plane"], item.get("side"))
                wanted_where = ((chose.args["plane"], None) if chose.name == "select_plane"
                                else (None, chose.args["side"]))
                if where != wanted_where or not same(item["offset"], entry.args["offset"]):
                    raise _OffPlan(f"{item['name']} is on {where} at {item['offset']:g}, the "
                                   f"plan has {wanted_where} at {entry.args['offset']:g}")
                if not item["valid"] or item["body"] != body_of(index)["name"]:
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
                if name == "sketch_outline" and not _same_points(shape["points"],
                                                                 entry.args["points"]):
                    raise _OffPlan(f"{sketch['name']} has another outline than the plan's")
                shape_at += 1
            else:                       # a primitive, a feature, an edge treatment
                group = COMMANDS[name]["group"]
                body = body_of(index)
                from_sketch = group == "feature" and name not in PRIMITIVES
                if from_sketch and len(sketch["shapes"]) != shape_at + 1:
                    raise _OffPlan(f"{sketch['name']} has a shape the plan does not have")
                if done == len(objects):
                    if from_sketch:
                        if open_sketch == sketch["name"]:
                            return stop([_target(index, before[-1])],
                                        "the sketch is complete: leave it")
                        if selection != {"type": "sketch", "name": sketch["name"]} \
                                or not body["active"]:
                            return stop([_target(index, "select_sketch")],
                                        f"select {sketch['name']} to use it")
                    elif name not in PRIMITIVES and not (
                            _selected(selection, before[-1], body["tip"]) and body["active"]):
                        return stop([_target(index, before[-1])], f"select what {name} works on")
                    return stop([_target(index, entry)], f"{name} is next")
                _check_feature(objects[done], entry, body["name"], sketch,
                               newest.get(body["name"]), before)
                newest[body["name"]] = objects[done]["name"]
                done, before = done + 1, []
        index = len(items)              # every plan item is ticked off
        stopped[0] = None
        if done != len(objects):
            raise _OffPlan(f"{objects[done]['name']} is not in the plan")
        if len(bodies) > reached:
            raise _OffPlan("there are more bodies than the plan has parts")
        _check_places(bodies, places)
    except _OffPlan as off:
        raise _OffPlan(str(off), index) from None

    if session["finished"]:
        return [], None, "the structure is finished"
    if selection is not None:
        return [_target(None, "clear_selection")], None, \
            "the structure is complete: clear the selection"
    return [_target(None, "done")], None, "the structure is complete"


def teacher(items: list[Item], snapshot: dict, script: Script | None = None,
            places: dict | None = None) -> Advice:
    """The acceptable next commands for this plan in this state (see the rules above).

    Pass `script_of(items)` and `places_of(items)` when calling many times for one plan.
    """
    script = script_of(items) if script is None else script
    places = places_of(items) if places is None else places
    session = snapshot["session"]
    stopped: list[int | None] = [None]
    try:
        targets, active, why = _walk(script, items, places, snapshot, stopped)
        built = len(items) if active is None else active
        if not session["finished"] or not targets:
            # `position`: the script entry the targets come from (None once the plan is
            # complete). play.py uses it for "the rest of this item" and "an item starts".
            position = stopped[0] if active is not None else None
            return Advice(tuple(targets), True, built, active, why, position)
        problem = "the structure was marked finished too early"
    except _OffPlan as off:
        problem, built = off.args
        built = built or 0
    repair = "undo" if session["undo_depth"] > 0 else "new_document"
    return Advice((_target(None, repair),), False, built, None, f"off plan: {problem}")
