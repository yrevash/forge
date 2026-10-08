"""The catalogue for structures: the 37 single-part commands plus the ones several bodies need.

Like forge/freecad/catalogue.py this file is plain data and is read by two Pythons
(ours, and FreeCAD's own inside the worker), so it imports nothing but the base
catalogue. The base file is not changed: `COMMANDS` here is a NEW dict holding the
base entries followed by ours.

The added commands, in four groups

  parts      shapes the base commands cannot draw, each centred on the body's origin:
               pad_symmetric   pad a sketch half to each side of its plane
               sketch_outline  a closed outline through given points (profile bars, wedges)
               add_cone / add_sphere / add_dome / add_tapered_box   FreeCAD's own primitives
  placing    move_body (where the body's origin goes), turn_body (how it is turned)
  bodies     activate_body (which body the next commands work on)
  sides      select_side (a face of the body AS IT SITS: top, front ...; a sketch or a
             hollowing then goes on it), select_side_edges (edges for a fillet or chamfer)

Why every part is drawn centred on its body's origin: the resolved plan states every
part as "this shape, with the middle of its frame at CENTRE, turned by MATRIX". When the
drawing is centred the same way, `move_body` takes CENTRE unchanged, `turn_body` takes
the turn unchanged, and the two do not disturb each other (a turn about the middle
leaves the middle where it is), so they may be issued in either order.

Argument types added to the base ones
  "size0"   a size in millimetres that may be zero (a cone's pointed end)
  "index"   a whole number, 1 or more (the n-th body made)
  "points"  a list of three or more [x, y] pairs
"""

from __future__ import annotations

import math

try:                                    # our Python: as part of the forge package
    from forge.freecad import catalogue as base
except ImportError:                     # FreeCAD's Python: the worker loads files by path
    import catalogue as base

SIDES = ("top", "bottom", "left", "right", "front", "back")
SIDE_EDGE_RULES = ("around", "along")   # the edges round that face / the edges square to it
_command = base._command

MULTI_COMMANDS: dict[str, dict] = {
    # --- which body ----------------------------------------------------------------------------
    "activate_body": _command("document", False,
                              "Make the n-th body (in the order they were made) the active one.",
                              index="index"),

    # --- drawing a part ------------------------------------------------------------------------
    "sketch_outline": _command("sketch", True,
                               "Draw a closed outline through these points, fixed where they are.",
                               points="points"),
    "pad_symmetric": _command("feature", True,
                              "Extrude the selected sketch by `length`, half to each side of "
                              "its plane.", length="mm"),
    "add_cone": _command("feature", True,
                         "A cone (or a cone with its tip cut off) standing on its axis, its "
                         "middle on the body's origin.",
                         bottom_diameter="size0", top_diameter="size0", height="mm"),
    "add_sphere": _command("feature", True, "A ball with its middle on the body's origin.",
                           diameter="mm"),
    "add_dome": _command("feature", True,
                         "The top `height` of a ball, `diameter` wide at its flat base; the "
                         "middle of its frame on the body's origin.", diameter="mm", height="mm"),
    "add_tapered_box": _command("feature", True,
                                "A box whose top is a smaller (or larger) rectangle than its "
                                "bottom; the middle of its frame on the body's origin.",
                                bottom_length="mm", bottom_depth="mm", top_length="size0",
                                top_depth="size0", height="mm"),

    # --- placing the active body ---------------------------------------------------------------
    "move_body": _command("place", True,
                          "Put the active body's origin at this point of the structure.",
                          x="pos", y="pos", z="pos"),
    "turn_body": _command("place", True,
                          "Turn the active body about its own origin: yaw about Z, then pitch "
                          "about the turned Y, then roll about the turned X.",
                          yaw="deg", pitch="deg", roll="deg"),

    # --- faces and edges of a placed body, by the way they face ---------------------------------
    "select_side": _command("select", False,
                            "Select a side of the active body as it sits (top, front ...).",
                            side=SIDES),
    "select_side_edges": _command("select", False,
                                  "Select the edges round a side of the active body, or the "
                                  "straight edges square to it.",
                                  side=SIDES, rule=SIDE_EDGE_RULES),
}

# Base entries first, so the base commands keep their order; then ours. `undo` and `done`
# stay where they are in the base order, which nothing depends on.
COMMANDS: dict[str, dict] = {**base.COMMANDS, **MULTI_COMMANDS}

# What the base file exports and the base Session uses, passed through unchanged.
PLANES, AXES, EDGE_RULES, FACE_RULES = base.PLANES, base.AXES, base.EDGE_RULES, base.FACE_RULES
DIMENSIONS = {**base.DIMENSIONS, "outline": ()}      # an outline is fixed as it is drawn
SAME_DIMENSION, COPYABLE = base.SAME_DIMENSION, base.COPYABLE
SHAPE_COMMANDS = {**base.SHAPE_COMMANDS, "sketch_outline": "outline"}
DIMENSION_COMMANDS = base.DIMENSION_COMMANDS
PRIMITIVES = ("add_cone", "add_sphere", "add_dome", "add_tapered_box")
PLACING = ("move_body", "turn_body")


def _is_number(value: object) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def check_args(command: str, args: dict) -> str | None:
    """Why these arguments do not fit this command, or None if they do."""
    if command in base.COMMANDS:
        return base.check_args(command, args)
    if command not in COMMANDS:
        return f"{command} is not a command"
    wanted = COMMANDS[command]["args"]
    if set(args) != set(wanted):
        return f"{command} takes exactly: {', '.join(wanted) or 'no arguments'}"
    for name, kind in wanted.items():
        value = args[name]
        if isinstance(kind, tuple):
            if value not in kind:
                return f"{name} must be one of {', '.join(kind)}"
        elif kind == "index":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                return f"{name} must be a whole number, 1 or more"
        elif kind == "points":
            good = (isinstance(value, list) and len(value) >= 3
                    and all(isinstance(p, list) and len(p) == 2 and all(map(_is_number, p))
                            for p in value))
            if not good:
                return f"{name} must be three or more [x, y] pairs"
        else:
            if not _is_number(value):
                return f"{name} must be a number"
            if kind == "mm" and value <= 0:
                return f"{name} must be greater than zero"
            if kind == "size0" and value < 0:
                return f"{name} must not be negative"
    return None
