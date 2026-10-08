"""Interface recipes: the interface actions that build each step kind of forge/system1/steps.py.

The command-level runtime (forge/freecad/recipes.py) builds a hole with commands:
    select_plane XY, new_sketch at the top, sketch_circle, constrain_*, leave_sketch, pocket_through_all
Here the same hole is built the way a person builds it in FreeCAD's window:

    tree:XY_Plane                        click the XY plane in the model tree
    button:Part_DatumPlane               press "Datum Plane"; its dialog opens
    field:attachmentOffsetZ = H          type the height into "In Z-direction"
    dialog:OK
    tree:{datum}                         click the new datum plane in the tree
    button:PartDesign_NewSketch          the sketcher opens on that plane
    button:Sketcher_CreateCircle         the circle tool; its on-view fields appear
    { view:x = x, view:y = y }           type the centre (either first)
    view:diameter = d                    type the diameter: the circle is drawn and dimensioned
    button:Sketcher_LeaveSketch
    button:PartDesign_Pocket             the Pocket dialog opens
    dropdown:changeMode = Through all
    dialog:OK

Every position and size is a number typed into a field, so FreeCAD turns it into
a dimension constraint. Nothing is placed by a free click.

What is in an action
    id        the interface element (forge/freecad_ui/README.md lists the kinds)
    value     what is typed or chosen; None for a press or a click
    source    where the value comes from: "slot:x", "base:height", "floor", "const",
              or "derived:<formula>" (arithmetic on slots)
    semantic  True for the few picks that are not a widget (edges and faces of the solid)

`{datum}` and `{tip}` in an id stand for an object that only exists once the
earlier actions have run: the datum plane just made, and the body's tip (its
newest feature). `resolve` fills them in from what the interface shows.

`AnyOrder` groups may be acted on in any order (fields of one dialog, the two
numbers of a point). A member can only be acted on once its element is showing:
the counterbore's diameter field appears after "Counterbore" is chosen.

Three things differ from the command recipes, all forced by the interface:
  1. HEIGHT. The sketcher's "new sketch" has no height field, so a sketch above the
     XY plane sits on a datum plane made first (4 actions). The command recipes set
     the sketch's own attachment offset, which no toolbar button edits.
  2. HEXAGON and SLOT. FreeCAD's tools ask for a hexagon's corner radius and for a
     slot's end centre, centre distance and end radius. Those are worked out from the
     step's slots (see `_hexagon`, `_slot`): "derived" values.
  3. SIX DECIMALS. The sketcher writes every typed number into the sketch with six
     decimals. The plan's own numbers are far shorter, so only the hexagon's corner
     radius (across flats / sqrt 3) is rounded: by at most 0.0000005 mm.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from forge.freecad.recipes import Context, context_of
from forge.system1.steps import FEATURES, STARTS, TREATMENTS, Step

__all__ = [
    "SEMANTIC_KINDS",
    "Act",
    "AnyOrder",
    "Context",
    "Recipe",
    "context_of",
    "flatten",
    "recipe",
    "resolve",
]

XY_ENTRY = "XY-plane (Base plane)"      # the entry of the sketch-plane list
SEMANTIC_KINDS = ("pick",)


@dataclass(frozen=True)
class Act:
    """One interface action."""

    id: str
    value: object = None
    source: str | None = None

    @property
    def semantic(self) -> bool:
        return self.id.split(":", 1)[0] in SEMANTIC_KINDS


@dataclass(frozen=True)
class AnyOrder:
    """Actions that may be carried out in any order."""

    actions: tuple[Act, ...]


Recipe = list[Act | AnyOrder]


# --- building blocks -------------------------------------------------------------------------

def _press(command: str) -> Act:
    return Act(f"button:{command}")


OK = Act("dialog:OK")
LEAVE = _press("Sketcher_LeaveSketch")


def _sketch_on_xy() -> Recipe:
    """A new sketch on the XY plane itself: the plane is chosen in the dialog's list."""
    return [_press("PartDesign_NewSketch"), Act("list:listWidget", XY_ENTRY, "const"), OK]


def _sketch_at(height: float, source: str) -> Recipe:
    """A new sketch on a datum plane `height` above the XY plane."""
    return [Act("tree:XY_Plane"), _press("Part_DatumPlane"),
            Act("field:attachmentOffsetZ", height, source), OK,
            Act("tree:{datum}"), _press("PartDesign_NewSketch")]


def _point(x: float, y: float, x_source: str, y_source: str) -> AnyOrder:
    return AnyOrder((Act("view:x", x, x_source), Act("view:y", y, y_source)))


def _circle(x: float, y: float, diameter: float, sources: tuple[str, str, str]) -> Recipe:
    return [_press("Sketcher_CreateCircle"), _point(x, y, sources[0], sources[1]),
            Act("view:diameter", diameter, sources[2])]


def _rectangle(x: float, y: float, length: float, width: float,
               sources: tuple[str, str]) -> Recipe:
    return [_press("Sketcher_CreateRectangle_Center"), _point(x, y, *sources),
            AnyOrder((Act("view:length", length, "slot:length"),
                      Act("view:width", width, "slot:width")))]


def _hexagon(across_flats: float) -> Recipe:
    """A hexagon centred on the origin with flats facing +X and -X.

    FreeCAD's tool asks for the centre and then for one CORNER: how far it is from
    the centre and in which direction. The corner goes straight up (90 degrees), and
    its distance is across_flats / sqrt(3) (a regular hexagon's corner radius).
    """
    return [_press("Sketcher_CreateHexagon"), _point(0.0, 0.0, "const", "const"),
            AnyOrder((Act("view:corner_radius", across_flats / math.sqrt(3),
                          "derived:across_flats / sqrt(3)"),
                      Act("view:angle", 90.0, "const")))]


def _slot(s: dict) -> Recipe:
    """A slot of overall `length` and `width` centred on (x, y), along `angle`.

    FreeCAD's tool draws a slot from the centre of one round end to the centre of the
    other, then asks for the end radius. The ends are width / 2 round, so their
    centres are (length - width) apart, half of that either side of the middle.
    """
    half = (s["length"] - s["width"]) / 2
    along_x = s["angle"] == 0       # the generator only makes slots along X (0) or Y (90)
    x = s["x"] - half if along_x else s["x"]
    y = s["y"] if along_x else s["y"] - half
    x_source = "derived:x - (length - width) / 2" if along_x else "slot:x"
    y_source = "slot:y" if along_x else "derived:y - (length - width) / 2"
    return [_press("Sketcher_CreateSlot"), _point(x, y, x_source, y_source),
            AnyOrder((Act("view:centre_distance", s["length"] - s["width"],
                          "derived:length - width"),
                      Act("view:angle", s["angle"], "slot:angle"))),
            Act("view:end_radius", s["width"] / 2, "derived:width / 2")]


def _pad(height: float) -> Recipe:
    return [_press("PartDesign_Pad"), Act("field:lengthEdit", height, "slot:height"), OK]


def _pocket(depth: float | None) -> Recipe:
    setting = (Act("dropdown:changeMode", "Through all", "const") if depth is None
               else Act("field:lengthEdit", depth, "slot:depth"))
    return [_press("PartDesign_Pocket"), setting, OK]


# --- starts ----------------------------------------------------------------------------------

def _start(step: Step) -> Recipe:
    s = step.slots
    if step.kind == "block":
        outline = _rectangle(0.0, 0.0, s["length"], s["width"], ("const", "const"))
    elif step.kind == "cylinder":
        outline = _circle(0.0, 0.0, s["diameter"], ("const", "const", "slot:diameter"))
    elif step.kind == "hex":
        outline = _hexagon(s["across_flats"])
    else:   # a ring: two circles in one sketch; FreeCAD pads the area between them
        outline = [*_circle(0.0, 0.0, s["outer_diameter"], ("const", "const", "slot:outer_diameter")),
                   *_circle(0.0, 0.0, s["inner_diameter"], ("const", "const", "slot:inner_diameter"))]
    return [_press("Std_New"), _press("PartDesign_Body"), *_sketch_on_xy(), *outline, LEAVE,
            *_pad(s["height"])]


# --- edge treatments ---------------------------------------------------------------------------

def _treatment(step: Step) -> Recipe:
    value = step.slots[TREATMENTS[step.kind][0]]
    source = f"slot:{TREATMENTS[step.kind][0]}"
    if step.kind == "shell":
        # "Make thickness inwards" is ticked when the dialog opens, so the outside keeps its size.
        return [Act("pick:face:top"), _press("PartDesign_Thickness"),
                AnyOrder((Act("field:Value", value, source),
                          Act("dropdown:joinComboBox", "Intersection", "const"))), OK]
    if step.kind == "top_chamfer":
        return [Act("pick:edges:top_face"), _press("PartDesign_Chamfer"),
                Act("field:chamferSize", value, source), OK]
    rule = "vertical" if step.kind == "corner_radius" else "top_face"
    return [Act(f"pick:edges:{rule}"), _press("PartDesign_Fillet"),
            Act("field:filletRadius", value, source), OK]


# --- features ----------------------------------------------------------------------------------

def _copy(command: str, settings: list[Act]) -> Recipe:
    """A pattern or mirror of the feature just built: click it in the tree, press, fill in, OK."""
    return [Act("tree:{tip}"), _press(command), AnyOrder(tuple(settings)), OK]


def _feature(step: Step, context: Context) -> Recipe:
    s = step.slots
    kind = step.kind.removesuffix("_pair")
    on_top = _sketch_at(context.height, "base:height")
    on_floor = _sketch_at(context.floor, "floor")

    if kind in ("hole", "blind_hole"):
        recipe = [*on_top, *_circle(s["x"], s["y"], s["diameter"], ("slot:x", "slot:y", "slot:diameter")),
                  LEAVE, *_pocket(s.get("depth"))]
    elif kind == "counterbore":
        # FreeCAD's Hole feature drills at the centre of the sketch circle and takes its sizes
        # from the dialog. "Standard" is None when the dialog opens, so the diameter is ours.
        recipe = [*on_top, *_circle(s["x"], s["y"], s["hole_diameter"],
                                    ("slot:x", "slot:y", "slot:hole_diameter")), LEAVE,
                  _press("PartDesign_Hole"),
                  AnyOrder((Act("dropdown:HoleCutType", "Counterbore", "const"),
                            Act("dropdown:DepthType", "Through all", "const"),
                            Act("field:Diameter", s["hole_diameter"], "slot:hole_diameter"))),
                  AnyOrder((Act("field:HoleCutDiameter", s["diameter"], "slot:diameter"),
                            Act("field:HoleCutDepth", s["depth"], "slot:depth"))), OK]
    elif kind == "boss":
        recipe = [*on_floor, *_circle(s["x"], s["y"], s["diameter"], ("slot:x", "slot:y", "slot:diameter")),
                  LEAVE, *_pad(s["height"])]
    elif kind == "pad":
        recipe = [*on_floor, *_rectangle(s["x"], s["y"], s["length"], s["width"], ("slot:x", "slot:y")),
                  LEAVE, *_pad(s["height"])]
    elif kind == "pocket":
        recipe = [*on_top, *_rectangle(s["x"], s["y"], s["length"], s["width"], ("slot:x", "slot:y")),
                  LEAVE, *_pocket(s["depth"])]
    elif kind == "slot":
        recipe = [*on_top, *_slot(s), LEAVE, *_pocket(s["depth"])]
    elif kind == "polar":
        # One hole on the +X axis at the pitch radius, then copies round the Z axis.
        recipe = [*on_top, *_circle(s["circle_diameter"] / 2, 0.0, s["hole_diameter"],
                                    ("derived:circle_diameter / 2", "const", "slot:hole_diameter")),
                  LEAVE, *_pocket(None),
                  *_copy("PartDesign_PolarPattern",
                         [Act("dropdown:comboDirection", "Base Z-axis", "const"),
                          Act("field:spinOccurrences", s["count"], "slot:count")])]
    elif kind == "row":
        # The row is centred on x = 0, so its first hole sits half the row's span to the left.
        first = -(s["count"] - 1) * s["spacing"] / 2
        recipe = [*on_top, *_circle(first, s["y"], s["hole_diameter"],
                                    ("derived:-(count - 1) * spacing / 2", "slot:y", "slot:hole_diameter")),
                  LEAVE, *_pocket(None),
                  Act("tree:{tip}"), _press("PartDesign_LinearPattern"),
                  AnyOrder((Act("dropdown:comboDirection", "Base X-axis", "const"),
                            Act("dropdown:comboMode", "Spacing", "const"),
                            Act("field:spinOccurrences", s["count"], "slot:count"))),
                  Act("field:spinSpacing", s["spacing"], "slot:spacing"), OK]
    else:
        raise ValueError(f"no interface recipe for {step.kind}")

    if step.kind.endswith("_pair"):
        # The twin: a mirror image of the feature just built, across the YZ plane.
        recipe += _copy("PartDesign_Mirrored", [Act("dropdown:comboPlane", "Base YZ-plane", "const")])
    return recipe


# --- what callers use --------------------------------------------------------------------------

def recipe(step: Step, context: Context) -> Recipe:
    """The interface actions that build one step."""
    if step.kind in STARTS:
        return _start(step)
    if step.kind in TREATMENTS:
        return _treatment(step)
    if step.kind in FEATURES:
        return _feature(step, context)
    raise ValueError(f"no interface recipe for {step.kind}")


def flatten(steps: Recipe, rng: random.Random | None = None) -> list[Act]:
    """A recipe as a plain list of actions. With `rng`, every any-order group is shuffled.

    (Shuffling ignores that some fields only appear after a dropdown is set; `build.py`
    orders a group against the live interface instead. This is for counting and printing.)
    """
    actions: list[Act] = []
    for entry in steps:
        if isinstance(entry, Act):
            actions.append(entry)
        else:
            group = list(entry.actions)
            if rng is not None:
                rng.shuffle(group)
            actions += group
    return actions


def resolve(action: Act, reply: dict) -> Act:
    """Fill in `{datum}` and `{tip}` from an interface reply (its elements and context)."""
    if "{" not in action.id:
        return action
    if "{tip}" in action.id:
        name = reply["context"].get("tip")
    else:
        # The datum plane just made is the newest one: FreeCAD numbers them DatumPlane,
        # DatumPlane001, ... and the tree lists objects of one kind in the order they were made.
        planes = [element["id"].split(":", 1)[1] for element in reply["elements"]
                  if element["kind"] == "tree" and element.get("type") == "datum_plane"]
        name = max(planes, key=lambda n: int(n.removeprefix("DatumPlane") or 0)) if planes else None
    if name is None:
        raise LookupError(f"nothing to fill in for {action.id}")
    return Act(action.id.replace("{tip}", name).replace("{datum}", name), action.value, action.source)
