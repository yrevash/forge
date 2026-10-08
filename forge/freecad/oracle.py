"""A second opinion on the labels, which shares no code with the teacher.

    on_plan, commands = oracle(snapshot, final_snapshot)

The teacher (teacher.py) derives the right next commands from the PLAN, through
the recipes (recipes.py). If a recipe or the teacher's walk were wrong, an audit
that asks the teacher again would agree with itself and see nothing.

This file derives them from something else: the session's FINAL DOCUMENT. A
finished session ends on a document that was measured against the part's stored
solid (play.end_check), so that document is known to be right, whatever produced
it. Every earlier state of the session is then judged against it:

    the state is ON PLAN   if its objects are the first objects of the final document
                           (same types, same numbers, same links), the last one perhaps
                           an unfinished sketch;
    the next command       is whatever makes the next missing piece of the final
                           document: the next dimension, the next shape, the next
                           object, or first the selection that command needs;
    OFF PLAN               otherwise: `undo`, or `new_document` when nothing can be undone.

It imports neither teacher.py nor recipes.py (a test checks that), and it knows
nothing about plans or step kinds. What it does share with the teacher is the
catalogue's idea of a command and the convention "repair by undo".
"""

from __future__ import annotations

TOLERANCE = 1e-6
DIMENSIONS = ("length", "width", "diameter", "across_flats", "angle", "x", "y")
LINKS = ("sketch", "on", "of", "body")      # fields that name another object

Command = tuple[str, dict]


def same(a: object, b: object) -> bool:
    numbers = (int, float)
    if isinstance(a, numbers) and isinstance(b, numbers) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= TOLERANCE
    return a == b


def _command_for(feature: dict) -> Command:
    """The command that makes this feature of the final document."""
    kind = feature["type"]
    if kind == "pad":
        return "pad", {"length": feature["length"]}
    if kind == "pocket":
        if feature["through_all"]:
            return "pocket_through_all", {}
        return "pocket", {"depth": feature["depth"]}
    if kind == "hole":
        return "hole_counterbore", {"diameter": feature["diameter"],
                                    "counterbore_diameter": feature["counterbore_diameter"],
                                    "counterbore_depth": feature["counterbore_depth"]}
    if kind == "fillet":
        return "fillet", {"radius": feature["radius"]}
    if kind == "chamfer":
        return "chamfer", {"size": feature["size"]}
    if kind == "thickness":
        return "thickness", {"value": feature["value"]}
    if kind == "polar_pattern":
        return "polar_pattern", {"count": feature["count"], "axis": feature["axis"]}
    if kind == "linear_pattern":
        return "linear_pattern", {"count": feature["count"], "spacing": feature["spacing"],
                                  "direction": feature["direction"]}
    if kind == "mirror":
        return "mirror", {"plane": feature["plane"]}
    raise ValueError(f"the final document has a {kind}, which no plan makes")


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
            if place.get(item[field]) != final_place.get(value):
                return False
        elif not same(item.get(field), value):
            return False
    return True


def _shape_against(shape: dict, final: dict) -> str:
    """'full', 'partial' or 'wrong': a drawn shape against the final document's shape."""
    if shape["shape"] != final["shape"] or shape.get("sides") != final.get("sides"):
        return "wrong"
    for dimension in shape["fixed"]:
        if dimension not in final["fixed"] or not same(shape[dimension], final[dimension]):
            return "wrong"
    return "full" if set(shape["fixed"]) == set(final["fixed"]) else "partial"


def oracle(snapshot: dict, final: dict) -> tuple[bool, list[Command]]:
    """(is the state on plan, the acceptable next commands), judged against the final document."""
    session, items, wanted = snapshot["session"], snapshot["items"], final["items"]
    selection, open_sketch = session["selection"], session["open_sketch"]
    repair = [("undo", {})] if session["undo_depth"] > 0 else [("new_document", {})]

    if not session["document"]:
        return True, [("new_document", {})]
    if not items:
        return True, [("new_body", {})]
    body = items[0]
    if len(items) > len(wanted) or body["type"] != "body" or not body["active"] \
            or not body["valid"] or sum(item["type"] == "body" for item in items) != 1:
        return False, repair
    place = {item["name"]: number for number, item in enumerate(items)}
    final_place = {item["name"]: number for number, item in enumerate(wanted)}

    for number, (item, goal) in enumerate(zip(items[1:], wanted[1:]), start=1):
        if item["type"] != "sketch":
            if not _same_feature(item, place, goal, final_place):
                return False, repair
            continue
        if goal["type"] != "sketch" or item["plane"] != goal["plane"] \
                or not same(item["offset"], goal["offset"]) or not item["valid"] \
                or item["body"] != body["name"] or len(item["shapes"]) > len(goal["shapes"]):
            return False, repair
        states = [_shape_against(shape, goal_shape)
                  for shape, goal_shape in zip(item["shapes"], goal["shapes"])]
        if "wrong" in states or "partial" in states[:-1]:
            return False, repair
        if len(states) == len(goal["shapes"]) and "partial" not in states:
            continue                                # this sketch is complete
        if number != len(items) - 1:                # something was built on an unfinished sketch
            return False, repair
        if session["finished"]:
            return False, repair
        if states and states[-1] == "partial":
            shape, goal_shape = item["shapes"][-1], goal["shapes"][len(states) - 1]
            commands = [(f"constrain_{dimension}", {"value": goal_shape[dimension]})
                        for dimension in goal_shape["fixed"] if dimension not in shape["fixed"]]
        else:
            goal_shape = goal["shapes"][len(states)]
            commands = [(f"sketch_{goal_shape['shape']}",
                         {"sides": goal_shape["sides"]} if "sides" in goal_shape else {})]
        if open_sketch == item["name"]:
            return True, commands
        if selection == {"type": "sketch", "name": item["name"]}:
            return True, [("edit_sketch", {})]
        return True, [("select_sketch", {})]

    complete = len(items) == len(wanted)
    if session["finished"]:
        return (True, []) if complete else (False, repair)
    if complete:
        return True, [("clear_selection", {})] if selection is not None else [("done", {})]

    goal, last, tip = wanted[len(items)], items[-1], session["tip"]
    if goal["type"] == "sketch":
        if selection == {"type": "plane", "name": goal["plane"]}:
            return True, [("new_sketch", {"offset": goal["offset"]})]
        return True, [("select_plane", {"plane": goal["plane"]})]
    if "sketch" in goal:                            # a pad, pocket or hole of the last sketch
        if last["type"] != "sketch":
            return False, repair
        if open_sketch == last["name"]:
            return True, [("leave_sketch", {})]
        if selection != {"type": "sketch", "name": last["name"]}:
            return True, [("select_sketch", {})]
        return True, [_command_for(goal)]
    if goal["type"] in ("fillet", "chamfer", "thickness"):
        kind = "face" if goal["type"] == "thickness" else "edges"
        if selection and selection["type"] == kind and selection.get("rule") == goal["rule"] \
                and selection.get("of") == tip:
            return True, [_command_for(goal)]
        return True, [(f"select_{kind}", {"rule": goal["rule"]})]
    if selection == {"type": "feature", "name": tip}:   # a pattern or a mirror of the tip
        return True, [_command_for(goal)]
    return True, [("select_tip", {})]


def agrees(commands: list[Command], targets: list[dict]) -> bool:
    """Is the oracle's set the recorded target set (same commands, same numbers)?"""
    if len(commands) != len(targets):
        return False
    left = list(commands)
    for target in targets:
        for number, (name, args) in enumerate(left):
            if name == target["command"] and set(args) == set(target["args"]) \
                    and all(same(args[arg], target["args"][arg]) for arg in args):
                del left[number]
                break
        else:
            return False
    return True
