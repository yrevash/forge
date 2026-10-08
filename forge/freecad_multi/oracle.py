"""A second opinion on the labels of structure sessions, sharing no code with the teacher.

    on_plan, commands = oracle(snapshot, final_snapshot)

The teacher (teacher.py) derives the right next commands from the PLAN, through the
recipes (recipes.py). An audit that asks the teacher again agrees with itself. This file
derives them from something else: the session's FINAL DOCUMENT, which play.end_check
measured against the resolver (every body's frame and volume, who touches whom), so it
is known to be right whatever produced it. Idea and single-part version:
forge/freecad/oracle.py. Nothing is imported from teacher.py, recipes.py, play.py or
forge/freecad/oracle.py (a test checks that): the file is written out in full.

How a state is judged against the final document

    THE TIMELINE. FreeCAD lists a document's objects in the order they were made, bodies
    included. That list, taken from the final document, is the order of the build. One
    kind of step leaves no object: putting a body in its place. So a PLACE step is put
    into the list for every body that ends up moved or turned:
        after the body's shape (its first sketch and pad, or its primitive)  - normally;
        after the body's last object  - when it ends up turned by an odd angle, because
        such a body has no "front as it sits" and gets its features before it is turned.
    ON PLAN  if the state's objects are the first objects of the final document (same
             types, numbers and links, the last one perhaps an unfinished sketch), every
             body is either where it was made or where it ends up, likewise for its turn,
             and every PLACE step before the last object made is complete.
    NEXT     the first step of the timeline that is missing: the missing `move_body` /
             `turn_body`, the next dimension, shape or object, or first what that command
             needs: the right active body, a selection, the sketch opened or left.
    OFF PLAN otherwise: `undo`, or `new_document` when nothing can be undone.

What it shares with the teacher: the catalogue's idea of a command, the convention
"repair by undo", and the three rules written above (centred shapes are placed after
their shape; odd turns last; a side is chosen by `select_side`). Those are the contract
of the runtime (README), not the teacher's code.
"""

from __future__ import annotations

TOLERANCE = 1e-6
LINKS = ("sketch", "on", "of", "body")      # fields that name another object
ORIGIN = [0.0, 0.0, 0.0]
UNTURNED = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
PRIMITIVES = {"cone": "add_cone", "sphere": "add_sphere", "dome": "add_dome",
              "tapered_box": "add_tapered_box"}
PRIMITIVE_ARGS = {"cone": ("bottom_diameter", "top_diameter", "height"), "sphere": ("diameter",),
                  "dome": ("diameter", "height"),
                  "tapered_box": ("bottom_length", "bottom_depth", "top_length", "top_depth",
                                  "height")}

Command = tuple[str, dict]


def same(a: object, b: object) -> bool:
    numbers = (int, float)
    if isinstance(a, numbers) and isinstance(b, numbers) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= TOLERANCE
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def _is_square(matrix: list[list[float]]) -> bool:
    """A turn by quarter turns only: every entry is -1, 0 or 1."""
    return all(min(abs(v), abs(abs(v) - 1.0)) <= TOLERANCE for row in matrix for v in row)


def _command_for(feature: dict) -> Command:
    """The command that makes this object of the final document."""
    kind = feature["type"]
    if kind in PRIMITIVES:
        return PRIMITIVES[kind], {arg: feature[arg] for arg in PRIMITIVE_ARGS[kind]}
    if kind == "pad":
        return ("pad_symmetric" if feature["symmetric"] else "pad"), {"length": feature["length"]}
    if kind == "pocket":
        return "pocket", {"depth": feature["depth"]}
    if kind == "fillet":
        return "fillet", {"radius": feature["radius"]}
    if kind == "chamfer":
        return "chamfer", {"size": feature["size"]}
    if kind == "thickness":
        return "thickness", {"value": feature["value"]}
    raise ValueError(f"the final document has a {kind}, which no structure plan makes")


def _same_feature(item: dict, place: dict[str, int], final: dict, final_place: dict[str, int],
                  ) -> bool:
    """Is `item` the object the final document has at this place? Links are compared by
    position in the document, not by name."""
    if item["type"] != final["type"] or not item["valid"]:
        return False
    for field, value in final.items():
        if field in ("name", "valid", "type"):
            continue
        if field in LINKS:
            if place.get(item.get(field)) != final_place.get(value):
                return False
        elif not same(item.get(field), value):
            return False
    return True


def _shape_against(shape: dict, final: dict) -> str:
    """'full', 'partial' or 'wrong': a drawn shape against the final document's shape."""
    if shape["shape"] != final["shape"] or shape.get("sides") != final.get("sides") \
            or not same(shape.get("points"), final.get("points")):
        return "wrong"
    for dimension in shape["fixed"]:
        if dimension not in final["fixed"] or not same(shape[dimension], final[dimension]):
            return "wrong"
    return "full" if set(shape["fixed"]) == set(final["fixed"]) else "partial"


def _place_after(wanted: list[dict]) -> dict[int, int]:
    """body's place in the final list -> the place of the last object made BEFORE that
    body is put in its place (see THE TIMELINE). Only bodies that end up moved or turned."""
    after = {}
    for at, body in enumerate(wanted):
        if body["type"] != "body":
            continue
        placement = body["placement"]
        if same(placement["position"], ORIGIN) and same(placement["matrix"], UNTURNED):
            continue
        own = [k for k, item in enumerate(wanted) if item.get("body") == body["name"]]
        if not own:
            continue
        if _is_square(placement["matrix"]):
            after[at] = own[0] if wanted[own[0]]["type"] in PRIMITIVES else own[1]
        else:
            after[at] = own[-1]
    return after


def _placing(body: dict, goal: dict) -> list[Command] | None:
    """The placing commands this body still needs ([] if none); None if it is somewhere it
    should never be."""
    have, want = body["placement"], goal["placement"]
    missing: list[Command] = []
    if not same(have["position"], want["position"]):
        if not same(have["position"], ORIGIN):
            return None
        x, y, z = want["position"]
        missing.append(("move_body", {"x": x, "y": y, "z": z}))
    if not same(have["matrix"], want["matrix"]):
        if not same(have["matrix"], UNTURNED):
            return None
        missing.append(("turn_body", {"matrix": want["matrix"]}))
    return missing


def oracle(snapshot: dict, final: dict) -> tuple[bool, list[Command]]:
    """(is the state on plan, the acceptable next commands), judged against the final
    document. A `turn_body` is returned with the matrix it must produce (`agrees` compares
    turns as matrices: three angles can be written in more than one way)."""
    session, items, wanted = snapshot["session"], snapshot["items"], final["items"]
    selection, open_sketch = session["selection"], session["open_sketch"]
    repair = [("undo", {})] if session["undo_depth"] > 0 else [("new_document", {})]

    if not session["document"]:
        return True, [("new_document", {})]
    if len(items) > len(wanted):
        return False, repair
    place = {item["name"]: number for number, item in enumerate(items)}
    final_place = {item["name"]: number for number, item in enumerate(wanted)}
    body_at = {item["name"]: item for item in items if item["type"] == "body"}

    def body_ready(name: str, commands: list[Command]) -> list[Command]:
        """Commands that work on a body: that body must be the active one first."""
        body = body_at[name]
        return commands if body["active"] else [("activate_body", {"index": body["index"]})]

    # 1. Every object so far is the final document's object at that place.
    unfinished: tuple[dict, dict, list[Command]] | None = None
    for number, (item, goal) in enumerate(zip(items, wanted)):
        if item["type"] == "body" or goal["type"] == "body":
            if item["type"] != goal["type"] or not item["valid"]:
                return False, repair
            continue
        if place.get(item.get("body")) != final_place.get(goal.get("body")):
            return False, repair
        if item["type"] != "sketch":
            if not _same_feature(item, place, goal, final_place):
                return False, repair
            continue
        if goal["type"] != "sketch" or item["plane"] != goal["plane"] \
                or item.get("side") != goal.get("side") \
                or not same(item["offset"], goal["offset"]) or not item["valid"] \
                or len(item["shapes"]) > len(goal["shapes"]):
            return False, repair
        states = [_shape_against(shape, goal_shape)
                  for shape, goal_shape in zip(item["shapes"], goal["shapes"])]
        if "wrong" in states or "partial" in states[:-1]:
            return False, repair
        if len(states) == len(goal["shapes"]) and "partial" not in states:
            continue                                # this sketch is complete
        if number != len(items) - 1:                # something was made after an unfinished sketch
            return False, repair
        if states and states[-1] == "partial":
            shape, goal_shape = item["shapes"][-1], goal["shapes"][len(states) - 1]
            commands = [(f"constrain_{dimension}", {"value": goal_shape[dimension]})
                        for dimension in goal_shape["fixed"] if dimension not in shape["fixed"]]
        else:
            goal_shape = goal["shapes"][len(states)]
            if goal_shape["shape"] == "outline":
                commands = [("sketch_outline", {"points": goal_shape["points"]})]
            else:
                commands = [(f"sketch_{goal_shape['shape']}",
                             {"sides": goal_shape["sides"]} if "sides" in goal_shape else {})]
        unfinished = (item, goal, commands)

    # 2. Every body is where it was made or where it ends up; a body that has to be in
    #    place before something that already exists IS in place.
    after = _place_after(wanted)
    pending: list[Command] = []
    pending_body = None
    for at, goal in enumerate(wanted[:len(items)]):
        if goal["type"] != "body":
            continue
        missing = _placing(items[at], goal)
        if missing is None:
            return False, repair
        if not missing:
            continue
        last_before = after[at]
        if len(items) - 1 > last_before:            # something was made after it was left unplaced
            return False, repair
        if len(items) - 1 == last_before and unfinished is None:
            pending, pending_body = missing, items[at]["name"]

    if session["finished"]:
        complete = len(items) == len(wanted) and unfinished is None and not pending
        return (True, []) if complete else (False, repair)

    # 3. The next step of the timeline.
    if unfinished is not None:
        item, _, commands = unfinished
        if open_sketch == item["name"]:
            return True, body_ready(item["body"], commands)
        if selection == {"type": "sketch", "name": item["name"]}:
            return True, body_ready(item["body"], [("edit_sketch", {})])
        return True, body_ready(item["body"], [("select_sketch", {})])
    if pending:
        return True, body_ready(pending_body, pending)
    if len(items) == len(wanted):
        return True, [("clear_selection", {})] if selection is not None else [("done", {})]

    goal = wanted[len(items)]
    if goal["type"] == "body":
        return True, [("new_body", {})]
    body_name = wanted[final_place[goal["body"]]]["name"]
    # The body's name in the state: bodies are compared by place, so take the state's.
    body_name = items[final_place[body_name]]["name"]
    body = body_at[body_name]
    if goal["type"] in PRIMITIVES:
        return True, body_ready(body_name, [_command_for(goal)])
    if goal["type"] == "sketch":
        if goal.get("side") is not None:
            chosen = selection == {"type": "side", "side": goal["side"]}
            select: Command = ("select_side", {"side": goal["side"]})
        else:
            chosen = selection == {"type": "plane", "name": goal["plane"]}
            select = ("select_plane", {"plane": goal["plane"]})
        if chosen and body["active"]:
            return True, [("new_sketch", {"offset": goal["offset"]})]
        return True, body_ready(body_name, [select])
    if "sketch" in goal:                            # a pad or pocket of the last sketch
        last = items[-1]
        if last["type"] != "sketch":
            return False, repair
        if open_sketch == last["name"]:
            return True, body_ready(body_name, [("leave_sketch", {})])
        if selection != {"type": "sketch", "name": last["name"]} or not body["active"]:
            return True, body_ready(body_name, [("select_sketch", {})])
        return True, [_command_for(goal)]
    if goal["type"] == "thickness":
        chosen = selection == {"type": "side", "side": goal["side"]}
        select = ("select_side", {"side": goal["side"]})
    else:                                           # a fillet or a chamfer
        chosen = bool(selection) and selection["type"] == "edges" \
            and selection.get("side") == goal["side"] and selection.get("rule") == goal["rule"] \
            and selection.get("of") == body["tip"]
        select = ("select_side_edges", {"side": goal["side"], "rule": goal["rule"]})
    if chosen and body["active"]:
        return True, [_command_for(goal)]
    return True, body_ready(body_name, [select])


def _turn_matrix(yaw: float, pitch: float, roll: float) -> list[list[float]]:
    """The matrix of FreeCAD's Rotation(yaw, pitch, roll): Rz(yaw) * Ry(pitch) * Rx(roll)."""
    import math
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr]]


def _same_command(name: str, args: dict, target: dict) -> bool:
    if name != target["command"]:
        return False
    if name == "turn_body":
        return same(_turn_matrix(**target["args"]), args["matrix"])
    return set(args) == set(target["args"]) and all(same(args[arg], target["args"][arg])
                                                    for arg in args)


def agrees(commands: list[Command], targets: list[dict]) -> bool:
    """Is the oracle's set the recorded target set (same commands, same numbers)?"""
    if len(commands) != len(targets):
        return False
    left = list(commands)
    for target in targets:
        for number, (name, args) in enumerate(left):
            if _same_command(name, args, target):
                del left[number]
                break
        else:
            return False
    return True
