"""Which commands are available right now, for a session with several bodies.

    valid_commands(snapshot) -> ["select_plane", "move_body", ...]
    why_not(snapshot, "move_body") -> "there is no body: use new_body"

The base rules (forge/freecad/valid.py) already speak about "the active body", so most
of them carry over as they are: with three bodies, `select_plane` selects a base plane
of the active one. This file adds the rules for the new commands and changes four base
rules; everything else is handed to the base function.

    changed       why
    new_sketch    also after `select_side` (a sketch on a side of the placed body)
    thickness     also after `select_side` (hollow the body, open at that side)
    constrain_*   an outline has no dimensions to give (the base table does not know it)
    done          the STRUCTURE is finished: every body must hold a valid solid

Read by both Pythons, like the base file; imports only the two catalogues.
"""

from __future__ import annotations

try:                                    # our Python
    from forge.freecad import valid as base
    from forge.freecad_multi.multi_catalogue import (
        COMMANDS,
        DIMENSION_COMMANDS,
        MULTI_COMMANDS,
        PLACING,
        PRIMITIVES,
    )
except ImportError:                     # FreeCAD's Python: loaded by path
    import valid as base
    from multi_catalogue import (
        COMMANDS,
        DIMENSION_COMMANDS,
        MULTI_COMMANDS,
        PLACING,
        PRIMITIVES,
    )

EMPTY_SNAPSHOT: dict = {**base.EMPTY_SNAPSHOT, "structure": None}
find, current_shape, unused_sketch = base.find, base.current_shape, base.unused_sketch


def bodies(snapshot: dict) -> list[dict]:
    """The body items, in the order the bodies were made."""
    return [item for item in snapshot["items"] if item["type"] == "body"]


def _why_not_ours(snapshot: dict, command: str) -> str | None:
    """The rule for a command this package adds."""
    session = snapshot["session"]
    if not session["document"]:
        return "there is no document: start with new_document"
    if session["finished"]:
        return "the structure is finished: undo, or start a new document"
    sketching = session["open_sketch"] is not None
    if command == "sketch_outline":
        return None if sketching else "no sketch is open"
    if sketching:
        return "leave the sketch first"
    if command == "activate_body":
        return None if len(bodies(snapshot)) >= 2 else "there is no other body"
    if session["active_body"] is None:
        return "there is no body: use new_body"
    if command in PRIMITIVES:
        return None if session["tip"] is None else "the body already has a shape"
    if command in PLACING:
        return None
    if command == "pad_symmetric":
        return base.why_not(snapshot, "pad")
    if command in ("select_side", "select_side_edges"):
        return None if snapshot["solid"] else "there is no solid yet"
    return f"no rule for {command}"


def why_not(snapshot: dict, command: str) -> str | None:
    """Why `command` is not available in this state, or None if it is."""
    if command not in COMMANDS:
        return f"{command} is not a command"
    if command in MULTI_COMMANDS:
        return _why_not_ours(snapshot, command)
    session = snapshot["session"]
    selection = session["selection"]
    on_side = bool(selection) and selection["type"] == "side"
    ready = session["document"] and not session["finished"] and session["open_sketch"] is None
    if command in ("new_sketch", "thickness") and on_side and ready:
        return None
    if command in DIMENSION_COMMANDS and session["open_sketch"] is not None:
        shape = current_shape(snapshot)
        if shape is not None and shape["shape"] == "outline":
            return "an outline is fixed as it is drawn"
    if command == "done" and ready:
        made = bodies(snapshot)
        if not made:
            return "there is no body yet"
        for index, body in enumerate(made, start=1):
            if not body["solid"] or not body["solid"]["valid"]:
                return f"body {index} has no valid solid yet"
        return None
    return base.why_not(snapshot, command)


def valid_commands(snapshot: dict) -> list[str]:
    """Every command that is available in this state, in catalogue order."""
    return [command for command in COMMANDS if why_not(snapshot, command) is None]
