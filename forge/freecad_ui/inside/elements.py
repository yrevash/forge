"""The interface elements: everything a person could act on right now, as a plain list. Runs inside FreeCAD.

Each element is a dict with the same four keys, plus a few that depend on its kind:

    kind    what sort of thing it is (see below)
    id      a stable name: the same widget has the same id in every session and every FreeCAD restart
    role    the words a person reads on it or next to it ("Pad", "Length", "XY-plane")
    value   what it shows now (a number, the chosen entry, ticked or not); None if it shows nothing

    kind           id                      extra keys           how it is acted on
    button         button:<Command>        toolbar              pressed
    field          field:<name>            text                 a number is typed into it
    dropdown       dropdown:<name>         entries              one of its entries is chosen
    check          check:<name>                                 clicked (ticks or unticks)
    radio          radio:<name>                                 clicked
    list           list:<name>             entries              one of its entries is chosen
    panel_button   panel:<name>                                 clicked
    dialog_button  dialog:OK|Cancel|Close                       clicked
    view_field     view:<role>             text, entered        a number is typed into it, then Enter
    tree           tree:<ObjectName>       type, selected       clicked (selects that object)
    pick           pick:<what>[:<rule>]                         a semantic pick (see picks.py)

Only what is usable is listed: enabled buttons on visible toolbars, the visible
and enabled controls of the open task dialog. Ids come from FreeCAD's own names
(command names, widget object names, document object names), never from positions.
"""

from __future__ import annotations

import document
import FreeCADGui as Gui
import picks
import session
import widgets

try:
    import shiboken6 as shiboken
except ImportError:                     # pragma: no cover - FreeCAD 1.1 ships PySide6
    shiboken = None

_buttons: dict = {}     # (workbench, sketch open?) -> [(toolbar, command, QAction)]
_seen_workbenches: set = set()

# Type names shown for tree items (FreeCAD's type -> a short word).
TREE_TYPES = {
    "PartDesign::Body": "body", "App::Origin": "origin", "App::Plane": "base_plane",
    "App::Line": "base_axis", "App::Point": "base_point", "Sketcher::SketchObject": "sketch",
    "Part::DatumPlane": "datum_plane", "PartDesign::Plane": "datum_plane",
    "PartDesign::Pad": "pad", "PartDesign::Pocket": "pocket", "PartDesign::Hole": "hole",
    "PartDesign::Fillet": "fillet", "PartDesign::Chamfer": "chamfer",
    "PartDesign::Thickness": "thickness", "PartDesign::PolarPattern": "polar_pattern",
    "PartDesign::LinearPattern": "linear_pattern", "PartDesign::Mirrored": "mirror",
    "PartDesign::Revolution": "revolution",
}


def _alive(obj) -> bool:
    return shiboken is None or shiboken.isValid(obj)


def toolbar_buttons() -> list[tuple[str, str, object]]:
    """(toolbar, command, QAction) of every toolbar button, remembered per workbench.

    Walking the toolbars is the slow part of reading the interface, and FreeCAD's
    main window grows a little with every workbench switch, so the handles are kept
    and only looked up again when one of them has been deleted.
    """
    key = (Gui.activeWorkbench().name(), session.sketch_in_edit() is not None)
    cached = _buttons.get(key)
    if key not in _seen_workbenches:
        _seen_workbenches.add(key)
        cached = None               # a first visit also marks that workbench's toolbars
    if cached is None or not all(_alive(action) for _, _, action in cached):
        cached = [(bar, widgets.action_name(action), action)
                  for bar, action in widgets.toolbar_actions()]
        _buttons[key] = cached
    return cached


def find_button(command: str):
    """The QAction of a toolbar button, or None if no visible toolbar has it."""
    for _, name, action in toolbar_buttons():
        if name == command:
            return action
    return None


def dialog_name() -> str | None:
    """A stable name for the open task dialog ("TaskPadPocketParameters", "Sketcher", ...)."""
    if not widgets.dialog_open():
        return None
    root = widgets.task_view()
    names = []
    for widget in root.findChildren(widgets.QtWidgets.QWidget):
        name = widget.objectName()
        if "__Task" in name and widget.isVisibleTo(root):
            names.append(name.split("__", 1)[1])
    if names:
        return names[0]
    if session.sketch_in_edit() is not None:
        return "Sketcher"
    for kind, widget in widgets.panel_controls():
        if kind == "list" and widget.objectName() == "listWidget":
            return "TaskFeaturePick"
        if widget.objectName() == "attachmentOffsetZ":
            return "TaskAttacher"
    return "Dialog"


def selection() -> list[dict]:
    """What is selected, without FreeCAD's face and edge numbers.

    Selected edges or faces are named by the rule that gives exactly them
    ("vertical", "top_face", ...), or None if no rule does.
    """
    out = []
    for item in Gui.Selection.getSelectionEx():
        names = list(item.SubElementNames)
        faces = [name for name in names if name.startswith("Face")]
        edges = [name for name in names if name.startswith("Edge")]
        rule = None
        if faces and not edges:
            rule = document.rule_of(item.Object, faces, "faces")
        elif edges and not faces:
            rule = document.rule_of(item.Object, edges, "edges")
        out.append({"object": item.ObjectName, "faces": len(faces), "edges": len(edges),
                    "rule": rule})
    return out


def _tip_name() -> str | None:
    """The body's tip: the feature whose solid is the body right now (bold in the tree)."""
    body = session.active_body()
    return body.Tip.Name if body is not None and body.Tip is not None else None


def context() -> dict:
    doc = session.document()
    sketch = session.sketch_in_edit()
    if sketch is None:
        session.tool.stop()
    return {
        "workbench": Gui.activeWorkbench().name(),
        "document": doc is not None,
        "dialog": dialog_name(),
        "sketch_open": sketch.Name if sketch is not None else None,
        "tool": session.tool.command,
        "selection": selection(),
        "tip": _tip_name(),
        "undo": doc.UndoCount if doc is not None else 0,
    }


def _panel_element(kind: str, widget, root) -> dict:
    name = widget.objectName()
    element = {"kind": kind, "id": f"{'panel' if kind == 'panel_button' else kind}:{name}"}
    if kind == "field":
        element.update(role=widgets.label_of(widget, root), value=widgets.field_value(widget),
                       text=widget.text())
    elif kind == "dropdown":
        element.update(role=widgets.label_of(widget, root), value=widget.currentText(),
                       entries=[widget.itemText(i) for i in range(widget.count())])
    elif kind in ("check", "radio"):
        element.update(role=widget.text().replace("&", ""), value=widget.isChecked())
    elif kind == "list":
        current = widgets.QtWidgets.QListWidget.currentItem(widget)
        element.update(role=widgets.label_of(widget, root),
                       value=current.text() if hasattr(current, "text") else None,
                       entries=widgets.list_entries(widget))
    elif kind == "text":
        element.update(role=widgets.label_of(widget, root), value=widget.text())
    else:
        element.update(role=widget.text().replace("&", "") or widget.toolTip(), value=None)
    return element


def tree_elements() -> list[dict]:
    """One element per object of the document: the rows of the model tree.

    Read from the document, in the order the objects were made, not from the tree
    widget: FreeCAD adds and removes tree rows on a timer, so right after a feature is
    made its row may or may not be there yet. (Clicking waits for the row, see act.py.)
    "selected" is FreeCAD's selection, which is what the next command will use.
    """
    doc = session.document()
    if doc is None:
        return []
    chosen = {item.ObjectName for item in Gui.Selection.getSelectionEx()}
    return [{"kind": "tree", "id": f"tree:{obj.Name}", "role": obj.Label, "value": None,
             "type": TREE_TYPES.get(obj.TypeId, obj.TypeId), "selected": obj.Name in chosen}
            for obj in doc.Objects]


def view_field_elements() -> list[dict]:
    fields = widgets.view_fields()
    roles = session.tool.roles()
    out = []
    for index, widget in enumerate(fields):
        role = roles[index] if index < len(roles) else str(index)
        entered = role in session.tool.entered
        # Before a number is typed the field follows the pointer; that number means nothing
        # (the pointer is parked), so it is not reported.
        out.append({"kind": "view_field", "id": f"view:{role}", "role": role,
                    "value": widgets.field_value(widget) if entered else None,
                    "text": widget.text() if entered else "", "entered": entered})
    return out


_refreshed_for: tuple | None = None


def _refresh_if_changed() -> None:
    """Ask FreeCAD to work out which buttons are enabled, but only when that can have changed.

    FreeCAD refreshes its buttons on a timer; we need them fresh right after an action.
    The refresh gets slower as the main window ages (it walks every menu FreeCAD ever
    made), and typing into a field cannot enable or disable a toolbar button, so it is
    only run when the selection, the dialog, the sketch or the document has changed.
    """
    global _refreshed_for
    doc = session.document()
    sketch = session.sketch_in_edit()
    now = (doc.Name if doc else None, len(doc.Objects) if doc else 0,
           doc.UndoCount if doc else 0, doc.RedoCount if doc else 0, widgets.dialog_open(),
           sketch.Name if sketch else None, sketch.GeometryCount if sketch else 0,
           Gui.activeWorkbench().name(),
           tuple((s.ObjectName, tuple(s.SubElementNames)) for s in Gui.Selection.getSelectionEx()))
    if now != _refreshed_for:
        widgets.refresh_actions()
        _refreshed_for = now


def elements(include_disabled: bool = False) -> list[dict]:
    """The whole list, in a fixed order: toolbars, task dialog, on-view fields, tree, picks."""
    _refresh_if_changed()
    out = []
    for bar, command, action in toolbar_buttons():
        if action.isEnabled() or include_disabled:
            element = {"kind": "button", "id": f"button:{command}",
                       "role": action.text().replace("&", ""), "value": None, "toolbar": bar}
            if include_disabled:
                element["enabled"] = action.isEnabled()
            out.append(element)
    if widgets.dialog_open():
        root = widgets.task_view()
        seen = set()
        for kind, widget in widgets.panel_controls():
            if not widget.isEnabled() and not include_disabled:
                continue
            element = _panel_element(kind, widget, root)
            if element["id"] in seen:
                continue                # two widgets with one name: only the first can be reached
            seen.add(element["id"])
            out.append(element)
        for label, button in widgets.dialog_buttons().items():
            if button.isEnabled() or include_disabled:
                out.append({"kind": "dialog_button", "id": f"dialog:{label}", "role": label,
                            "value": None})
    if session.sketch_in_edit() is not None:
        out += view_field_elements()
    out += tree_elements()
    out += picks.available()
    return out
