"""The command catalogue: every command Forge-S1 can issue in FreeCAD, and its arguments.

This file is plain data. It imports nothing from FreeCAD and nothing from the
rest of forge, because two different Python interpreters read it:
  - our own Python 3.12 (the client, the recipes, the tests), and
  - FreeCAD's bundled Python 3.11 (the worker, which loads it by file path).

A command is one small thing a person does in FreeCAD's PartDesign or Sketcher
workbench: pick a plane, start a sketch, draw a circle, give it a diameter,
leave the sketch, pad it. Every number a command needs is an ARGUMENT; the plan
supplies it. No command works a number out for itself.

Argument types
  "mm"      a size in millimetres, greater than zero
  "pos"     a position in millimetres; zero and negative values are allowed
  "deg"     an angle in degrees
  "count"   a whole number, 2 or more
  (a, b..)  one of these words
"""

from __future__ import annotations

import math

PLANES = ("XY", "XZ", "YZ")
AXES = ("X", "Y", "Z")
EDGE_RULES = ("vertical", "top_face", "bottom_face", "all")
FACE_RULES = ("top", "bottom")
SKETCH_AXES = ("V", "H")        # a sketch's own vertical and horizontal axis


def _command(group: str, changes_document: bool, help: str, **args: object) -> dict:
    return {"group": group, "changes_document": changes_document, "help": help, "args": args}


# `changes_document` says whether the command edits the FreeCAD document. Those commands
# run inside one FreeCAD transaction, which is what `undo` rolls back. The others only
# change the session (what is selected, which sketch is open).
COMMANDS: dict[str, dict] = {
    # --- document and body -------------------------------------------------------------------
    "new_document": _command("document", True, "Close the document and start an empty one."),
    "new_body": _command("document", True, "Add a PartDesign body and make it the active one."),

    # --- selecting ---------------------------------------------------------------------------
    "select_plane": _command("select", False, "Select a base plane of the active body.",
                             plane=PLANES),
    "select_face": _command("select", False,
                            "Select the flat face at the top or the bottom of the solid.",
                            rule=FACE_RULES),
    "select_edges": _command("select", False,
                             "Select edges of the solid by a geometric rule.", rule=EDGE_RULES),
    "select_tip": _command("select", False,
                           "Select the body's tip: the last feature that was built."),
    "select_sketch": _command("select", False,
                              "Select the newest sketch that no feature uses yet."),
    "clear_selection": _command("select", False, "Select nothing."),

    # --- sketching ---------------------------------------------------------------------------
    "new_sketch": _command("sketch", True,
                           "Start a sketch on the selected plane, `offset` mm along its normal.",
                           offset="pos"),
    "edit_sketch": _command("sketch", False, "Open the selected sketch again."),
    "sketch_rectangle": _command("sketch", True, "Draw a rectangle (rough size, at the origin)."),
    "sketch_circle": _command("sketch", True, "Draw a circle (rough size, at the origin)."),
    "sketch_polygon": _command("sketch", True, "Draw a regular polygon with `sides` sides.",
                               sides="count"),
    "sketch_slot": _command("sketch", True, "Draw a slot: a rectangle with two round ends."),
    # Dimensions. Each one fixes one number of the shape that was drawn last.
    "constrain_length": _command("sketch", True,
                                 "Rectangle: its size along X. Slot: its overall length.",
                                 value="mm"),
    "constrain_width": _command("sketch", True, "Rectangle: its size along Y. Slot: its width.",
                                value="mm"),
    "constrain_diameter": _command("sketch", True,
                                   "Circle: its diameter. Polygon: the circle through its "
                                   "corners.", value="mm"),
    "constrain_across_flats": _command("sketch", True,
                                       "Polygon with an even number of sides: the distance "
                                       "between two opposite sides.", value="mm"),
    "constrain_angle": _command("sketch", True,
                                "Slot: the direction it runs in. Polygon: the direction of its "
                                "first side. 0 is along X, 90 along Y.", value="deg"),
    "constrain_x": _command("sketch", True, "The X position of the shape's centre.", value="pos"),
    "constrain_y": _command("sketch", True, "The Y position of the shape's centre.", value="pos"),
    "leave_sketch": _command("sketch", False, "Close the sketch. It stays selected."),

    # --- features that add or remove material (they use the selected sketch) -------------------
    "pad": _command("feature", True, "Extrude the selected sketch by `length`, adding material.",
                    length="mm"),
    "pocket": _command("feature", True,
                       "Cut the selected sketch `depth` mm into the solid.", depth="mm"),
    "pocket_through_all": _command("feature", True,
                                   "Cut the selected sketch right through the solid."),
    "revolution": _command("feature", True,
                           "Revolve the selected sketch about one of its own axes.",
                           angle="deg", axis=SKETCH_AXES),
    "hole_through": _command("feature", True,
                             "Drill a through hole at every circle of the selected sketch.",
                             diameter="mm"),
    "hole_blind": _command("feature", True,
                           "Drill a flat-bottomed hole of a given depth at every circle.",
                           diameter="mm", depth="mm"),
    "hole_counterbore": _command("feature", True,
                                 "Drill a through hole with a wider flat recess at its mouth.",
                                 diameter="mm", counterbore_diameter="mm",
                                 counterbore_depth="mm"),

    # --- edge and face treatments (they use the selected edges or face) ------------------------
    "fillet": _command("dressup", True, "Round the selected edges.", radius="mm"),
    "chamfer": _command("dressup", True, "Bevel the selected edges.", size="mm"),
    "thickness": _command("dressup", True,
                          "Hollow the solid: remove the selected face and leave walls of "
                          "`value` mm, measured inwards.", value="mm"),

    # --- copies of the selected feature --------------------------------------------------------
    "polar_pattern": _command("pattern", True,
                              "`count` copies of the selected feature, evenly round a base axis.",
                              count="count", axis=AXES),
    "linear_pattern": _command("pattern", True,
                               "`count` copies of the selected feature, `spacing` mm apart "
                               "along a base axis.", count="count", spacing="mm", direction=AXES),
    "mirror": _command("pattern", True,
                       "A mirror image of the selected feature across a base plane.",
                       plane=PLANES),

    # --- control -----------------------------------------------------------------------------
    "undo": _command("control", True, "Take back the last command."),
    "done": _command("control", False, "Say the part is finished."),
}

# Which sketch shape each dimension command applies to. `sketch_polygon` makes a "polygon".
DIMENSIONS: dict[str, tuple[str, ...]] = {
    "rectangle": ("length", "width", "x", "y"),
    "circle": ("diameter", "x", "y"),
    "polygon": ("across_flats", "diameter", "angle", "x", "y"),
    "slot": ("length", "width", "angle", "x", "y"),
}
# A polygon's size is one number: fixing it across flats also fixes its corner circle.
SAME_DIMENSION = {"across_flats": "size", "diameter": "size"}

SHAPE_COMMANDS = {"sketch_rectangle": "rectangle", "sketch_circle": "circle",
                  "sketch_polygon": "polygon", "sketch_slot": "slot"}
DIMENSION_COMMANDS = {f"constrain_{name}": name
                      for name in ("length", "width", "diameter", "across_flats", "angle",
                                   "x", "y")}
# Feature kinds a pattern or a mirror can copy (FreeCAD's "additive" and "subtractive" ones).
COPYABLE = ("pad", "pocket", "hole", "revolution")


def check_args(command: str, args: dict) -> str | None:
    """Why these arguments do not fit this command, or None if they do."""
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
        elif kind == "count":
            if isinstance(value, bool) or not isinstance(value, int) or value < 2:
                return f"{name} must be a whole number, 2 or more"
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                return f"{name} must be a number"
            if kind == "mm" and value <= 0:
                return f"{name} must be greater than zero"
    return None
