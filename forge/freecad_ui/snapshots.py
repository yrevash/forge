"""The lean interface snapshot: what a session record keeps of one reply.

    {"buttons":  ["button:Std_New", ...]      ids of the enabled toolbar buttons
     "elements": [{kind, id, role, value, ...}]   every other element, as the runtime gave it
     "context":  {...}                         dialog, open sketch, tool, selection, tip, undo
     "items":    [...]                         the document: what has been built
     "solid":    {...} or None}                the measured solid, volume rounded

A toolbar button has nothing to show but that it can be pressed, so only its id
is kept (a record has about 80 of them). A field's display text is dropped: its
unrounded `value` is kept. The volume is rounded for the reason given in
forge/freecad/lean.py (its last digits differ between two identical builds).

The teacher, the audit and later the model all read this form.
"""

from __future__ import annotations

from forge.freecad.lean import VOLUME_DIGITS, VOLUME_TOLERANCE, lean_solid


def lean(reply: dict) -> dict:
    """A reply of `UIClient.act(..., document=True)` or `UIClient.elements(document=True)`."""
    return {
        "buttons": [e["id"] for e in reply["elements"] if e["kind"] == "button"],
        "elements": [{key: value for key, value in e.items() if key not in ("text", "toolbar")}
                     for e in reply["elements"] if e["kind"] != "button"],
        "context": reply["context"],
        "items": reply["document"]["items"],
        "solid": lean_solid(reply["document"]["solid"]),
    }


def same_snapshot(a: dict, b: dict, buttons: bool = True) -> str | None:
    """None if two lean snapshots are the same state, else the first part that differs.

    Volumes may differ in their last digits. With `buttons=False` the toolbar
    buttons are not compared (FreeCAD refreshes them on a timer of its own).
    """
    for key in ("context", "items", "elements") + (("buttons",) if buttons else ()):
        if a[key] != b[key]:
            return key
    solid_a, solid_b = a["solid"], b["solid"]
    if solid_a is None or solid_b is None:
        return None if solid_a is solid_b else "solid"
    rest_a = {key: value for key, value in solid_a.items() if key != "volume"}
    rest_b = {key: value for key, value in solid_b.items() if key != "volume"}
    close = abs(solid_a["volume"] - solid_b["volume"]) <= max(
        VOLUME_TOLERANCE * abs(solid_a["volume"]), 2 * 10.0 ** -VOLUME_DIGITS)
    return None if rest_a == rest_b and close else "solid"
