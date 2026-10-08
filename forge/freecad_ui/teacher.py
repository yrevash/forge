"""The teacher at interface level: for any plan and any interface snapshot, the acceptable next actions.

    advice = teacher(plan, snapshot)
    advice.targets     the SET of interface actions a careful person could do next
    advice.on_plan     is everything built so far what the plan asks for?
    advice.built       how many plan items are completely built
    advice.why         one plain sentence, for people

It is a pure function of (plan, snapshot), like the command-level teacher
(forge/freecad/teacher.py), and follows the same three rules. The snapshot is
the LEAN one of `snapshots.py`: the interface elements, the context, and the
document's items.

How it reads the session
    The plan is laid out as SEGMENTS, read off the interface recipes (recipes.py):
    a new document, a body, a sketch on the XY plane, a datum plane, a sketch on
    it, a drawn shape, leaving the sketch, a dialog (pad, pocket, hole, fillet,
    pattern, ...). Every segment leaves something in the document: a datum plane
    at a height, a sketch on it, a circle in the sketch, a pad of a length. The
    teacher walks the segments and ticks each off against the document's items,
    in the order they were made.

The rules
    1. ON PLAN, something still to do. The walk stops at the first segment whose
       trace is missing, and the answer is what that segment needs NOW, read from
       the interface:
         - nothing selected that it needs      -> the tree item or the pick
         - its button not pressed yet          -> the button
         - its dialog open                     -> every setting that does not show the plan's
                                                  value (a wrong number is simply typed again);
                                                  when all of them do: OK
         - its drawing tool waiting            -> the on-view fields not entered yet;
                                                  a field entered wrong: the tool's button again
         - its sketch complete and still open  -> leave the sketch
       A dialog or a sketch that is open but not needed is closed first (Cancel,
       Leave Sketch); that is tidying, not a repair of the document.
    2. OFF PLAN. The document holds something the plan does not have at that place
       (a wrong number that was confirmed, a wrong or extra object, a failed
       feature), or, between two plan items, the measured solid does not have the
       volume the plan gives so far (see `_check_solid`). A dialog that is open is cancelled: FreeCAD then removes the
       feature the dialog made. Otherwise the answer is Undo, and it stays Undo
       until what is left is on plan.
    3. DONE. Everything is built, nothing is open: `done`. This is the one target
       that is not an interface element; FreeCAD has no button for "finished".

"What the plan has" compares values, not history.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.freecad_ui.recipes import Act, AnyOrder, context_of, recipe
from forge.generators.base import VOLUME_RELATIVE_TOLERANCE
from forge.system1 import engine
from forge.system1.steps import Step

TOLERANCE = 2e-6    # mm or degrees; the sketcher keeps six decimals of a typed number
DONE = "done"
UNDO = "button:Std_Undo"
CANCEL = "dialog:Cancel"
CLOSE = "dialog:Close"      # the sketcher panel's own way out of a sketch
LEAVE = "button:Sketcher_LeaveSketch"
EDIT_SKETCH = "button:Sketcher_EditSketch"
PLANE_LIST_DIALOG = "TaskFeaturePick"

# The document item each dialog button makes.
BUTTON_ITEM = {
    "button:PartDesign_Pad": "pad", "button:PartDesign_Pocket": "pocket",
    "button:PartDesign_Hole": "hole", "button:PartDesign_Fillet": "fillet",
    "button:PartDesign_Chamfer": "chamfer", "button:PartDesign_Thickness": "thickness",
    "button:PartDesign_PolarPattern": "polar_pattern",
    "button:PartDesign_LinearPattern": "linear_pattern", "button:PartDesign_Mirrored": "mirror",
}
# What a confirmed dialog setting leaves in the item: (item type, element id) -> item field.
# A dropdown entry is checked through the field and value it stands for.
FIELD_TRACE = {
    ("pad", "field:lengthEdit"): "length", ("pocket", "field:lengthEdit"): "depth",
    ("hole", "field:Diameter"): "diameter",
    ("hole", "field:HoleCutDiameter"): "counterbore_diameter",
    ("hole", "field:HoleCutDepth"): "counterbore_depth",
    ("fillet", "field:filletRadius"): "radius", ("chamfer", "field:chamferSize"): "size",
    ("thickness", "field:Value"): "value",
    ("polar_pattern", "field:spinOccurrences"): "count",
    ("linear_pattern", "field:spinOccurrences"): "count",
    ("linear_pattern", "field:spinSpacing"): "spacing",
}
ENTRY_TRACE = {
    ("pocket", "dropdown:changeMode", "Through all"): ("through_all", True),
    ("hole", "dropdown:DepthType", "Through all"): ("through_all", True),
    ("thickness", "dropdown:joinComboBox", "Intersection"): ("join", "Intersection"),
    ("polar_pattern", "dropdown:comboDirection", "Base Z-axis"): ("axis", "Z"),
    ("linear_pattern", "dropdown:comboDirection", "Base X-axis"): ("direction", "X"),
    ("linear_pattern", "dropdown:comboMode", "Spacing"): ("mode", "Spacing"),
    ("mirror", "dropdown:comboPlane", "Base YZ-plane"): ("plane", "YZ"),
}
SHAPE_OF_TOOL = {"button:Sketcher_CreateCircle": "circle",
                 "button:Sketcher_CreateRectangle_Center": "rectangle",
                 "button:Sketcher_CreateHexagon": "hexagon", "button:Sketcher_CreateSlot": "slot"}


@dataclass(frozen=True)
class Target:
    """One acceptable action: the element, the value, and where the value comes from."""

    id: str
    value: object = None
    item: int | None = None     # the plan item this action works on; None for repairs and done
    source: str | None = None

    def to_json(self) -> dict:
        return {"id": self.id, "value": self.value, "item": self.item, "source": self.source}


@dataclass(frozen=True)
class Advice:
    targets: tuple[Target, ...]     # empty only when the teacher sees no way on (it should not happen)
    on_plan: bool
    built: int                      # plan items completely built, counted from the first
    active: int | None              # the plan item the targets work on
    why: str

    def ids(self) -> list[str]:
        return [target.id for target in self.targets]


@dataclass(frozen=True)
class Segment:
    """One stretch of a recipe that leaves one trace in the document."""

    kind: str               # new_document, body, sketch_xy, datum, sketch_on_datum, shape, leave, dialog
    item: int               # index of the plan item
    button: Act
    select: Act | None = None               # what must be selected before the button
    settings: tuple[Act, ...] = ()          # dialog fields, dropdown entries, the plane list entry
    phases: tuple[tuple[Act, ...], ...] = ()    # a drawing tool's on-view fields, phase by phase
    close: Act | None = None                # dialog:OK


class _OffPlan(Exception):
    """Something in the document is not what the plan has at that place."""


# --- the plan as segments ----------------------------------------------------------------------

def _members(entry: Act | AnyOrder) -> list[Act]:
    return list(entry.actions) if isinstance(entry, AnyOrder) else [entry]


def _segments_of_step(index: int, entries: list[Act | AnyOrder]) -> list[Segment]:
    out: list[Segment] = []
    at = 0
    while at < len(entries):
        entry = entries[at]
        name = entry.id
        if name == "button:Std_New":
            out.append(Segment("new_document", index, entry))
            at += 1
        elif name == "button:PartDesign_Body":
            out.append(Segment("body", index, entry))
            at += 1
        elif name == "button:PartDesign_NewSketch":
            out.append(Segment("sketch_xy", index, entry, settings=(entries[at + 1],),
                               close=entries[at + 2]))
            at += 3
        elif name == "tree:XY_Plane":
            out.append(Segment("datum", index, entries[at + 1], select=entry,
                               settings=(entries[at + 2],), close=entries[at + 3]))
            at += 4
        elif name == "tree:{datum}":
            out.append(Segment("sketch_on_datum", index, entries[at + 1], select=entry))
            at += 2
        elif name.startswith("button:Sketcher_Create"):
            phases = []
            at += 1
            while at < len(entries) and all(a.id.startswith("view:") for a in _members(entries[at])):
                phases.append(tuple(_members(entries[at])))
                at += 1
            out.append(Segment("shape", index, entry, phases=tuple(phases)))
        elif name == LEAVE:
            out.append(Segment("leave", index, entry))
            at += 1
        else:
            select = None
            if name.split(":")[0] in ("pick", "tree"):
                select, at = entry, at + 1
            button = entries[at]
            at += 1
            settings: list[Act] = []
            while _members(entries[at])[0].id != "dialog:OK":
                settings += _members(entries[at])
                at += 1
            out.append(Segment("dialog", index, button, select=select, settings=tuple(settings),
                               close=entries[at]))
            at += 1
    return out


def segments_of(plan: list[Step]) -> list[Segment]:
    """The whole plan as one list of segments."""
    context = context_of(plan)
    return [segment for index, step in enumerate(plan)
            for segment in _segments_of_step(index, recipe(step, context))]


def volumes_of(plan: list[Step]) -> list[float]:
    """The solid's volume after each plan item, by the reference engine's arithmetic."""
    state = engine.State([])
    volumes = []
    for step in plan:
        engine.apply(state, step)
        volumes.append(engine.expected(state).expected_volume)
    return volumes


def clean_length(plan: list[Step]) -> int:
    """How many actions a session without mistakes takes, `done` included."""
    context = context_of(plan)
    return 1 + sum(len(_members(entry)) for step in plan for entry in recipe(step, context))


# --- comparing -----------------------------------------------------------------------------------

def same(a: object, b: object) -> bool:
    numbers = (int, float)
    if isinstance(a, numbers) and isinstance(b, numbers) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= TOLERANCE
    return a == b


def _target(index: int | None, action: Act | str) -> Target:
    if isinstance(action, str):
        return Target(action, None, index, None)
    return Target(action.id, action.value, index, action.source)


def expected_shape(segment: Segment) -> dict:
    """The shape a drawing segment leaves in the sketch (as `inside/document.py` reads it)."""
    values = {action.id.removeprefix("view:"): action.value
              for phase in segment.phases for action in phase}
    shape = {"shape": SHAPE_OF_TOOL[segment.button.id], **values}
    if shape["shape"] == "hexagon":
        shape["angle"] = shape["angle"] % 60    # a hexagon looks the same every 60 degrees
    return shape


def _same_shape(found: dict, wanted: dict) -> bool:
    return found.keys() == wanted.keys() and all(same(found[key], wanted[key]) for key in wanted)


def _only_selected(selection: list[dict], name: str | None) -> bool:
    """Is exactly this whole object selected (no faces or edges of it)?"""
    return (len(selection) == 1 and selection[0]["object"] == name
            and not selection[0]["faces"] and not selection[0]["edges"])


def _pick_selected(selection: list[dict], pick: str, tip: str | None) -> bool:
    """Is the selection what this pick would make it (pick:edges:vertical, pick:face:top)?"""
    _, what, rule = pick.split(":")
    if len(selection) != 1 or selection[0]["object"] != tip or selection[0]["rule"] != rule:
        return False
    return bool(selection[0]["edges"] if what == "edges" else selection[0]["faces"])


def _check_item(item: dict, segment: Segment, sketch: dict | None, newest: str | None) -> None:
    """Raise _OffPlan unless a confirmed item is exactly what the dialog segment leaves."""
    kind, name = BUTTON_ITEM[segment.button.id], item["name"]
    if item["type"] != kind:
        raise _OffPlan(f"{name} is a {item['type']}, the plan has a {kind} here")
    if not item["valid"]:
        raise _OffPlan(f"{name} failed to build")
    for action in segment.settings:
        if action.id.startswith("field:"):
            field = FIELD_TRACE[(kind, action.id)]
            if not same(item.get(field), action.value):
                raise _OffPlan(f"{name} has {field} {item.get(field)}, the plan has {action.value}")
        elif (kind, action.id, action.value) in ENTRY_TRACE:
            field, value = ENTRY_TRACE[(kind, action.id, action.value)]
            if item.get(field) != value:
                raise _OffPlan(f"{name} has {field} {item.get(field)}, the plan has {value}")
    ids = {action.id for action in segment.settings}
    if kind == "pocket" and "dropdown:changeMode" not in ids and item.get("through_all"):
        raise _OffPlan(f"{name} goes through all, the plan has a depth")
    if kind == "hole" and item.get("counterbore_diameter") is None:
        raise _OffPlan(f"{name} has no counterbore")
    if kind == "polar_pattern" and not same(item.get("angle"), 360.0):
        raise _OffPlan(f"{name} does not go all the way round")
    if kind == "thickness" and not item.get("inwards"):
        raise _OffPlan(f"{name} grows outwards")
    if kind in ("pad", "pocket", "hole") and item.get("sketch") != sketch["name"]:
        raise _OffPlan(f"{name} does not use {sketch['name']}")
    if kind in ("fillet", "chamfer", "thickness") and (
            item.get("on") != newest or item.get("rule") != segment.select.id.split(":")[2]):
        raise _OffPlan(f"{name} is not on the {segment.select.id.split(':')[2]} of {newest}")
    if kind in ("polar_pattern", "linear_pattern", "mirror") and item.get("of") != newest:
        raise _OffPlan(f"{name} does not copy {newest}")


def _dialog_targets(segment: Segment, showing: dict, what: str) -> tuple[list[Target], str]:
    """The dialog of `segment` is open: which settings still show something else?"""
    wrong = [action for action in segment.settings
             if action.id in showing and not same(showing[action.id]["value"], action.value)]
    if wrong:
        return [_target(segment.item, action) for action in wrong], f"fill in the {what} dialog"
    if any(action.id not in showing for action in segment.settings):
        raise _OffPlan(f"the {what} dialog does not show a setting the plan needs")
    return [_target(segment.item, segment.close)], f"the {what} dialog is complete: OK"


# --- the walk --------------------------------------------------------------------------------------

def _walk(segments: list[Segment], snapshot: dict) -> tuple[list[Target], int | None, str, str]:
    """Tick the segments off against the document.

    Returns (targets, active plan item, why, needs) where `needs` says what the
    interface must be for those targets: "idle" (no dialog, no sketch open),
    "dialog" or "sketch". Raises _OffPlan(why, plan item, inside the open sketch?).
    """
    context, items = snapshot["context"], snapshot["items"]
    showing = {element["id"]: element for element in snapshot["elements"]}
    selection = context["selection"]
    open_sketch = context["sketch_open"]
    dialog = context["dialog"] if open_sketch is None else None
    bodies = [item for item in items if item["type"] == "body"]
    objects = [item for item in items if item["type"] != "body"]
    done = 0                    # how many of `objects` are ticked off
    sketch: dict | None = None  # the sketch the walk is in
    shapes = 0                  # how many of its shapes are ticked off
    datum: dict | None = None   # the newest datum plane ticked off
    newest: str | None = None   # the newest feature ticked off: what a fillet or a mirror uses
    index = 0
    in_sketch = False           # is the trouble inside the sketch that is open?

    def editing(position: int) -> bool:
        """Is objects[position] the feature of the dialog that is open now?"""
        return dialog is not None and dialog != PLANE_LIST_DIALOG and position == len(objects) - 1

    try:
        for segment in segments:
            index, kind = segment.item, segment.kind
            in_sketch = False
            if kind == "new_document":
                if not context["document"]:
                    return [_target(index, segment.button)], index, "there is no document", "idle"
            elif kind == "body":
                if not bodies:
                    return [_target(index, segment.button)], index, "the document has no body", "idle"
                if len(bodies) != 1:
                    raise _OffPlan("there is more than one body")
            elif kind == "sketch_xy":
                if done == len(objects):
                    if dialog == PLANE_LIST_DIALOG:
                        targets, why = _dialog_targets(segment, showing, "sketch plane")
                        return targets, index, why, "dialog"
                    if any(chosen["object"] != bodies[0]["name"] for chosen in selection):
                        # With a plane or a face selected, "new sketch" would use it unasked.
                        return [_target(index, f"tree:{bodies[0]['name']}")], index, \
                            "select the body, so the sketch plane is asked for", "idle"
                    return [_target(index, segment.button)], index, "start the sketch", "idle"
                item = objects[done]
                if item["type"] != "sketch" or item.get("on") != "XY_Plane" or not item["valid"]:
                    raise _OffPlan(f"{item['name']} is not a sketch on the XY plane")
                done, sketch, shapes = done + 1, item, 0
            elif kind == "datum":
                height = segment.settings[0].value
                if done == len(objects):
                    if _only_selected(selection, "XY_Plane"):
                        return [_target(index, segment.button)], index, "make the datum plane", "idle"
                    return [_target(index, segment.select)], index, \
                        "select the XY plane for the datum plane", "idle"
                item = objects[done]
                if item["type"] != "datum_plane":
                    raise _OffPlan(f"{item['name']} is a {item['type']}, the plan has a datum plane")
                if editing(done):
                    if item.get("plane") != "XY":
                        raise _OffPlan(f"{item['name']} is not on the XY plane")
                    targets, why = _dialog_targets(segment, showing, "datum plane")
                    return targets, index, why, "dialog"
                if item.get("plane") != "XY" or not same(item["offset"], height) or not item["valid"]:
                    raise _OffPlan(f"{item['name']} is at {item['offset']:g}, the plan has {height:g}")
                done, datum = done + 1, item
            elif kind == "sketch_on_datum":
                if done == len(objects):
                    if _only_selected(selection, datum["name"]):
                        return [_target(index, segment.button)], index, "start the sketch", "idle"
                    return [_target(index, f"tree:{datum['name']}")], index, \
                        "select the datum plane for the sketch", "idle"
                item = objects[done]
                if item["type"] != "sketch" or item.get("on") != datum["name"] or not item["valid"]:
                    raise _OffPlan(f"{item['name']} is not a sketch on {datum['name']}")
                done, sketch, shapes = done + 1, item, 0
            elif kind == "shape":
                wanted = expected_shape(segment)
                in_sketch = open_sketch == sketch["name"]
                if shapes < len(sketch["shapes"]):
                    found = sketch["shapes"][shapes]
                    if not _same_shape(found, wanted):
                        raise _OffPlan(f"{sketch['name']} has {found}, the plan has {wanted}")
                    shapes += 1
                    continue
                if done != len(objects):
                    raise _OffPlan(f"something was made after the unfinished {sketch['name']}")
                if not in_sketch:
                    if _only_selected(selection, sketch["name"]):
                        return [_target(index, EDIT_SKETCH)], index, \
                            "the sketch is not finished: open it", "idle"
                    return [_target(index, f"tree:{sketch['name']}")], index, \
                        "the sketch is not finished: select it", "idle"
                press = [_target(index, segment.button)]
                if context["tool"] != segment.button.id.removeprefix("button:"):
                    return press, index, f"take the {wanted['shape']} tool", "sketch"
                fields = {e["role"]: e for e in snapshot["elements"] if e["kind"] == "view_field"}
                phase = next((p for p in segment.phases
                              if {a.id.removeprefix("view:") for a in p} == set(fields)), None)
                if phase is None:
                    return press, index, f"take the {wanted['shape']} tool again", "sketch"
                for action in phase:
                    field = fields[action.id.removeprefix("view:")]
                    if field["entered"] and not same(field["value"], action.value):
                        return press, index, "a number was entered wrong: start the shape again", \
                            "sketch"
                todo = [action for action in phase
                        if not fields[action.id.removeprefix("view:")]["entered"]]
                return [_target(index, action) for action in todo], index, \
                    f"type the {wanted['shape']}'s numbers", "sketch"
            elif kind == "leave":
                in_sketch = open_sketch == sketch["name"]
                if len(sketch["shapes"]) != shapes:
                    raise _OffPlan(f"{sketch['name']} has a shape the plan does not have")
                if in_sketch and done == len(objects):
                    return [_target(index, segment.button)], index, \
                        "the sketch is complete: leave it", "sketch"
                # (A finished sketch that was opened again is left by the "not needed" rule.)
            else:                       # a dialog: a feature, an edge treatment or a copy
                what = BUTTON_ITEM[segment.button.id]
                if done == len(objects):
                    if segment.select is None:
                        ready = _only_selected(selection, sketch["name"])
                        select = f"tree:{sketch['name']}"
                    elif segment.select.id.startswith("pick:"):
                        ready = _pick_selected(selection, segment.select.id, newest)
                        select = segment.select
                    else:
                        ready = _only_selected(selection, newest)
                        select = f"tree:{newest}"
                    if not ready:
                        return [_target(index, select)], index, f"select what the {what} works on", \
                            "idle"
                    return [_target(index, segment.button)], index, f"the {what} is next", "idle"
                item = objects[done]
                if editing(done):
                    if item["type"] != what:
                        raise _OffPlan(f"the open dialog makes a {item['type']}, not a {what}")
                    if what in ("fillet", "chamfer", "thickness") and (
                            item.get("on") != newest
                            or item.get("rule") != segment.select.id.split(":")[2]):
                        raise _OffPlan(f"the open dialog is not on the "
                                       f"{segment.select.id.split(':')[2]} of {newest}")
                    if what in ("pad", "pocket", "hole") and item.get("sketch") != sketch["name"]:
                        raise _OffPlan(f"the open dialog does not use {sketch['name']}")
                    if what in ("polar_pattern", "linear_pattern", "mirror") \
                            and item.get("of", newest) != newest:
                        raise _OffPlan(f"the open dialog does not copy {newest}")
                    targets, why = _dialog_targets(segment, showing, what)
                    return targets, index, why, "dialog"
                _check_item(item, segment, sketch, newest)
                done, newest = done + 1, item["name"]
        index = segments[-1].item + 1       # every plan item is ticked off
        if done != len(objects):
            raise _OffPlan(f"{objects[done]['name']} is not in the plan")
    except _OffPlan as off:
        raise _OffPlan(str(off), index, in_sketch) from None
    return [_target(None, DONE)], None, "the part is complete", "idle"


def mid_shape(snapshot: dict) -> bool:
    """Is a drawing tool part-way through a shape (a number entered, or past the first point)?"""
    fields = [e for e in snapshot["elements"] if e["kind"] == "view_field"]
    return bool(snapshot["context"]["tool"]) and bool(fields) and (
        any(e["entered"] for e in fields) or {e["role"] for e in fields} != {"x", "y"})


def _check_solid(plan: list[Step], segments: list[Segment], snapshot: dict,
                 targets: list[Target], active: int | None) -> None:
    """Between two plan items the SOLID must be what the plan gives so far.

    Every setting of a feature can read right while its solid is not: FreeCAD does not
    always work a feature out again after its dialog was changed back and forth
    (measured: a hole switched to "Countersink" and back to "Counterbore" kept every
    number and lost 729 cubic millimetres too many). The measured volume shows it.
    The check is made only where the document holds whole plan items and nothing else:
    when the next action is the first of an item, or `done`.
    """
    built = len(plan) if active is None else active
    if built == 0 or snapshot["context"]["dialog"] is not None:
        return
    if active is not None:
        first = next(segment for segment in segments if segment.item == active)
        opening = {first.button.id, first.select.id if first.select else None}
        objects = sum(item["type"] != "body" for item in snapshot["items"])
        expected = sum(BUTTON_ITEM.get(segment.button.id) is not None or segment.kind in (
            "sketch_xy", "datum", "sketch_on_datum") for segment in segments if segment.item < active)
        if objects != expected or not {target.id for target in targets} & opening:
            return                      # in the middle of an item
    solid = snapshot["solid"]
    wanted = volumes_of(plan[:built])[-1]
    if solid is None or abs(solid["volume"] - wanted) > VOLUME_RELATIVE_TOLERANCE * wanted \
            or solid["solids"] != 1 or not solid["valid"]:
        found = None if solid is None else solid["volume"]
        raise _OffPlan(f"the solid has volume {found}, the plan gives {wanted:.6f} so far",
                       built, False)


def teacher(plan: list[Step], snapshot: dict, segments: list[Segment] | None = None) -> Advice:
    """The acceptable next actions for this plan in this state (see the rules above).

    Pass `segments_of(plan)` as `segments` when calling many times for one plan.
    """
    segments = segments_of(plan) if segments is None else segments
    context = snapshot["context"]
    open_sketch = context["sketch_open"]
    dialog = context["dialog"] if open_sketch is None else None
    buttons = set(snapshot["buttons"])
    try:
        targets, active, why, needs = _walk(segments, snapshot)
        _check_solid(plan, segments, snapshot, targets, active)
    except _OffPlan as off:
        problem, built, in_sketch = off.args
        # Rule 2. A dialog is cancelled (FreeCAD removes what it made); a sketch that is not
        # itself the trouble is left; everything else is undone.
        if dialog is not None:
            repair = CANCEL
        elif open_sketch is not None and in_sketch and UNDO in buttons and mid_shape(snapshot):
            # A drawing tool holds numbers of a half-drawn shape. Undo under its feet has
            # crashed FreeCAD; taking the tool again first makes it let go of them.
            repair = f"button:{context['tool']}"
        elif open_sketch is not None and not (in_sketch and UNDO in buttons):
            repair = LEAVE if LEAVE in buttons else CLOSE
        elif UNDO in buttons and context["undo"] > 0:
            repair = UNDO
        else:
            return Advice((), False, built, None, f"off plan and nothing can be undone: {problem}")
        return Advice((_target(None, repair),), False, built, None, f"off plan: {problem}")
    built = len(plan) if active is None else active
    missing = [t.id for t in targets if t.id.startswith("button:") and t.id not in buttons]
    if missing and open_sketch is not None:
        # The sketch is open but the sketcher's toolbars are not (it can happen after an
        # Undo inside a sketch). The task panel's own Close button still leaves the sketch;
        # opening it again brings the toolbars back.
        return Advice((_target(None, CLOSE),), True, built, None,
                      f"{missing[0]} is not showing: close the sketch from its panel")
    if needs == "idle" and dialog is not None:
        return Advice((_target(None, CANCEL),), True, built, None,
                      "a dialog is open that is not needed: cancel it")
    if needs == "idle" and open_sketch is not None:
        return Advice((_target(None, LEAVE if LEAVE in buttons else CLOSE),), True, built, None,
                      "a sketch is open that is not needed: leave it")
    return Advice(tuple(targets), True, built, active, why)


# --- plans as stored in session files --------------------------------------------------------

def plan_json(plan: list[Step]) -> list[dict]:
    return [{"kind": step.kind, "slots": dict(step.slots)} for step in plan]


def plan_from_json(stored: list[dict]) -> list[Step]:
    return [Step(item["kind"], dict(item["slots"])) for item in stored]
