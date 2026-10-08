"""Which commands are available right now, read off a snapshot.

    valid_commands(snapshot) -> ["select_plane", "undo", ...]
    why_not(snapshot, "pad") -> "select a sketch first"   (None when the command is available)

This is the same question FreeCAD answers by greying out toolbar buttons: you
cannot draw a circle unless a sketch is open, and you cannot pad unless a closed
sketch is selected. The rules look only at the snapshot, never at FreeCAD, so
the model, the teacher and the worker all get the same answer. The worker calls
`why_not` before every command and refuses the ones that are not available.

"Available" is about the session, not about the numbers: `fillet` is available
whenever edges are selected, even if the radius you then give is too large.

Like catalogue.py, this file is read by two Python interpreters (ours and
FreeCAD's), so it must not import FreeCAD or the rest of forge.
"""

from __future__ import annotations

try:                                    # as part of the forge package (our Python)
    from forge.freecad.catalogue import (
        COMMANDS,
        COPYABLE,
        DIMENSION_COMMANDS,
        DIMENSIONS,
        SAME_DIMENSION,
        SHAPE_COMMANDS,
    )
except ImportError:                     # inside FreeCAD, where the worker loads it by path
    from catalogue import (
        COMMANDS,
        COPYABLE,
        DIMENSION_COMMANDS,
        DIMENSIONS,
        SAME_DIMENSION,
        SHAPE_COMMANDS,
    )

EMPTY_SNAPSHOT: dict = {
    "items": [],
    "session": {"document": False, "active_body": None, "tip": None, "open_sketch": None,
                "selection": None, "undo_depth": 0, "finished": False},
    "solid": None,
}


def find(snapshot: dict, name: str | None) -> dict | None:
    """The item with this name, or None."""
    for item in snapshot["items"]:
        if item["name"] == name:
            return item
    return None


def current_shape(snapshot: dict) -> dict | None:
    """The shape drawn last in the open sketch: the one a dimension command applies to."""
    sketch = find(snapshot, snapshot["session"]["open_sketch"])
    return sketch["shapes"][-1] if sketch and sketch["shapes"] else None


def is_fixed(shape: dict, dimension: str) -> bool:
    """Has this dimension of the shape been given already?"""
    same = SAME_DIMENSION.get(dimension, dimension)
    return any(SAME_DIMENSION.get(name, name) == same for name in shape["fixed"])


def unused_sketch(snapshot: dict) -> dict | None:
    """The newest sketch of the active body that no feature uses."""
    body = snapshot["session"]["active_body"]
    for item in reversed(snapshot["items"]):
        if item["type"] == "sketch" and item["body"] == body and item["used_by"] is None:
            return item
    return None


def _needs_sketch(snapshot: dict, cuts: bool, circles: bool = False) -> str | None:
    """The check shared by pad, pocket, revolution and the hole commands."""
    selection = snapshot["session"]["selection"]
    if not selection or selection["type"] != "sketch":
        return "select a sketch first"
    sketch = find(snapshot, selection["name"])
    if sketch["used_by"] is not None:
        return f"{sketch['name']} is already used by {sketch['used_by']}"
    if not sketch["closed"]:
        return "the sketch has no closed outline"
    if cuts and snapshot["solid"] is None:
        return "there is no solid to cut into"
    if circles and not any(g["kind"] == "circle" and not g["construction"]
                           for g in sketch["geometry"]):
        return "a hole needs a circle in the sketch"
    return None


def why_not(snapshot: dict, command: str) -> str | None:
    """Why `command` is not available in this state, or None if it is."""
    if command not in COMMANDS:
        return f"{command} is not a command"
    session = snapshot["session"]
    selection = session["selection"]
    selected = selection["type"] if selection else None
    sketching = session["open_sketch"] is not None

    if command == "new_document":
        return None
    if not session["document"]:
        return "there is no document: start with new_document"
    if command == "undo":
        return None if session["undo_depth"] > 0 else "there is nothing to undo"
    if session["finished"]:
        return "the part is finished: undo, or start a new document"

    # Inside a sketch only sketch commands work, as in FreeCAD's Sketcher workbench.
    group = COMMANDS[command]["group"]
    in_sketch_only = command in SHAPE_COMMANDS or command in DIMENSION_COMMANDS \
        or command == "leave_sketch"
    if sketching and not in_sketch_only:
        return "leave the sketch first"
    if not sketching and in_sketch_only:
        return "no sketch is open"

    if command in SHAPE_COMMANDS or command == "leave_sketch":
        return None
    if command in DIMENSION_COMMANDS:
        shape, dimension = current_shape(snapshot), DIMENSION_COMMANDS[command]
        if shape is None:
            return "draw a shape first"
        if dimension not in DIMENSIONS[shape["shape"]]:
            return f"a {shape['shape']} has no {dimension}"
        if dimension == "across_flats" and shape["sides"] % 2:
            return "across flats needs an even number of sides"
        return f"its {dimension} is already fixed" if is_fixed(shape, dimension) else None

    if command == "new_body":
        return None
    if session["active_body"] is None:
        return "there is no body: use new_body"

    if command == "select_plane":
        return None
    if command in ("select_face", "select_edges"):
        return None if snapshot["solid"] else "there is no solid yet"
    if command == "select_tip":
        tip = find(snapshot, session["tip"])
        return None if tip else "the body has no feature yet"
    if command == "select_sketch":
        return None if unused_sketch(snapshot) else "there is no unused sketch"
    if command == "clear_selection":
        return None if selection else "nothing is selected"

    if command == "new_sketch":
        return None if selected == "plane" else "select a plane first"
    if command == "edit_sketch":
        if selected != "sketch":
            return "select a sketch first"
        used_by = find(snapshot, selection["name"])["used_by"]
        return f"the sketch is already used by {used_by}" if used_by else None

    if command in ("pad", "revolution"):
        return _needs_sketch(snapshot, cuts=False)
    if command in ("pocket", "pocket_through_all"):
        return _needs_sketch(snapshot, cuts=True)
    if command.startswith("hole_"):
        return _needs_sketch(snapshot, cuts=True, circles=True)

    if command in ("fillet", "chamfer"):
        return None if selected == "edges" else "select edges first"
    if command == "thickness":
        return None if selected == "face" else "select a face first"
    if group == "pattern":
        if selected != "feature":
            return "select the tip first"
        kind = find(snapshot, selection["name"])["type"]
        return None if kind in COPYABLE else f"a {kind} cannot be copied"

    if command == "done":
        solid = snapshot["solid"]
        return None if solid and solid["valid"] else "there is no valid solid yet"
    return f"no rule for {command}"     # a new command was added without a rule: fail loudly


def valid_commands(snapshot: dict) -> list[str]:
    """Every command that is available in this state, in catalogue order."""
    return [command for command in COMMANDS if why_not(snapshot, command) is None]
