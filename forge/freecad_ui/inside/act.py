"""Act on one interface element, then wait until FreeCAD has caught up. Runs inside FreeCAD.

    act("button:PartDesign_Pad")            press a toolbar button
    act("field:lengthEdit", 12.5)           type a number into a dialog field
    act("dropdown:changeMode", "Through all")
    act("check:checkReverse")               click a check box
    act("list:listWidget", "XY-plane (Base plane)")
    act("dialog:OK")
    act("view:diameter", 6)                 type into an on-view field and press Enter
    act("tree:Pocket")                      click an item of the model tree
    act("pick:edges:vertical")              a semantic pick (picks.py)

The answer is a status and, when it is not "ok", a reason:
    ok        done, and FreeCAD has settled
    rejected  not possible now (no such element, it is disabled, no such entry); nothing changed
    refused   FreeCAD itself said no: an error box appeared, or OK did not close the dialog

How each kind is acted on is what a person's hand does to that widget:
  - a button: its own QAction is triggered (what a click on it does);
  - a number field: the text is replaced, then the widget is told to read it and that
    editing is finished (what Tab does). `setValue` is never used: FreeCAD's count boxes
    (Gui::UIntSpinBox) store their number shifted, and setValue writes the wrong one;
  - a dropdown: the entry is made current and `activated` is emitted, as a pick does.
"""

from __future__ import annotations

import elements
import FreeCADGui as Gui
import picks
import qt
import session
import widgets
from numbers_text import number_text
from PySide import QtCore, QtWidgets

# Buttons that open a task dialog (or an error box when they cannot).
OPENS_DIALOG = frozenset({
    "PartDesign_Pad", "PartDesign_Pocket", "PartDesign_Hole", "PartDesign_Revolution",
    "PartDesign_Groove", "PartDesign_Fillet", "PartDesign_Chamfer", "PartDesign_Draft",
    "PartDesign_Thickness", "PartDesign_Mirrored", "PartDesign_LinearPattern",
    "PartDesign_PolarPattern", "PartDesign_MultiTransform", "Part_DatumPlane",
})
CLOSING = ("OK", "Cancel", "Close")


def _ok() -> dict:
    return {"status": "ok"}


def _no(reason: str) -> dict:
    return {"status": "rejected", "reason": reason}


def _signature() -> tuple:
    """A cheap fingerprint of the session, to see that a press did something."""
    doc = session.document()
    sketch = session.sketch_in_edit()
    return (doc.Name if doc else None, len(doc.Objects) if doc else 0,
            doc.UndoCount if doc else 0, widgets.dialog_open(),
            sketch.Name if sketch else None, Gui.activeWorkbench().name())


# --- one function per kind -----------------------------------------------------------------------

def press_button(command: str, server) -> dict:
    action = elements.find_button(command)
    if action is None:
        return _no("no visible toolbar has that button")
    if not action.isEnabled():
        widgets.refresh_actions()
        if not action.isEnabled():
            return _no("the button is disabled")
    before, boxes = _signature(), len(server.modals)
    was_drawing = session.sketch_in_edit() is not None
    action.trigger()
    qt.pump()
    refused = lambda: len(server.modals) > boxes
    if command in OPENS_DIALOG:
        qt.wait_until(lambda: widgets.dialog_open() or refused(), 3.0)
    elif command == "PartDesign_NewSketch":
        qt.wait_until(lambda: _signature() != before or refused(), 3.0)
    elif command == "Sketcher_LeaveSketch":
        qt.wait_until(lambda: session.sketch_in_edit() is None, 3.0)
        session.tool.stop()
    elif command.startswith("Sketcher_Create") and was_drawing:
        session.tool.start(command)
        picks.park_pointer()
        qt.wait_until(lambda: bool(widgets.view_fields()), 2.0)
    else:
        qt.wait_until(lambda: _signature() != before or refused(), 0.4)
    qt.pump(5)
    if refused():
        return {"status": "refused", "reason": server.modals[-1]["text"] or server.modals[-1]["title"]}
    return _ok()


def type_field(name: str, value) -> dict:
    widget = widgets.find_control(name)
    if widget is None or not isinstance(widget, QtWidgets.QAbstractSpinBox):
        return _no("the dialog has no such field")
    if not widget.isEnabled():
        return _no("the field is disabled")
    try:
        text = number_text(value)
    except ValueError as error:
        return _no(str(error))
    edit = widget.lineEdit()
    edit.setFocus()
    edit.selectAll()
    edit.insert(text)               # the slot real typing ends in
    widget.interpretText()          # the widget reads the text (what Tab does)
    widget.editingFinished.emit()   # ... and FreeCAD's dialog takes the number
    qt.pump()
    return {"status": "ok", "shows": widget.text()}


def choose_entry(kind: str, name: str, entry) -> dict:
    widget = widgets.find_control(name)
    if widget is None or not widget.isEnabled():
        return _no(f"the dialog has no such enabled {kind}")
    if kind == "dropdown":
        if not isinstance(widget, QtWidgets.QComboBox):
            return _no("that is not a dropdown")
        index = widget.findText(str(entry))
        if index < 0:
            return _no(f"no such entry: {entry}")
        widget.setCurrentIndex(index)
        widget.activated.emit(index)    # a pick by a person emits this; setCurrentIndex does not
    else:
        if not isinstance(widget, QtWidgets.QListWidget):
            return _no("that is not a list")
        entries = widgets.list_entries(widget)
        if str(entry) not in entries:
            return _no(f"no such entry: {entry}")
        item = QtWidgets.QListWidget.item(widget, entries.index(str(entry)))
        widget.setCurrentItem(item)
        item.setSelected(True)
    qt.pump()
    return _ok()


def click_control(name: str) -> dict:
    widget = widgets.find_control(name)
    if widget is None or not isinstance(widget, QtWidgets.QAbstractButton):
        return _no("the dialog has no such control")
    if not widget.isEnabled():
        return _no("the control is disabled")
    widget.click()
    qt.pump()
    return {"status": "ok", "checked": widget.isChecked()}


def click_dialog_button(label: str, server) -> dict:
    button = widgets.dialog_buttons().get(label)
    if button is None or not button.isEnabled():
        return _no("the dialog has no such enabled button")
    name, boxes = elements.dialog_name(), len(server.modals)
    sketch = session.sketch_in_edit()
    button.click()
    if label in CLOSING:
        # Done when this dialog is gone (closed, or replaced by the next one: OK on the
        # sketch-plane list opens the sketcher's panel), or when FreeCAD put up an error box.
        qt.wait_until(lambda: elements.dialog_name() != name or len(server.modals) > boxes
                      or session.sketch_in_edit() is not sketch, 3.0)
    qt.pump(5)
    if len(server.modals) > boxes:
        return {"status": "refused", "reason": server.modals[-1]["text"] or server.modals[-1]["title"]}
    if label in CLOSING and elements.dialog_name() == name and session.sketch_in_edit() is sketch:
        return {"status": "refused", "reason": f"{label} did not close the dialog"}
    if session.sketch_in_edit() is None:
        session.tool.stop()
    return _ok()


def type_view_field(role: str, value) -> dict:
    sketch = session.sketch_in_edit()
    roles = session.tool.roles()
    fields = widgets.view_fields()
    if sketch is None or role not in roles or roles.index(role) >= len(fields):
        return _no("no such on-view field is showing")
    try:
        text = number_text(value)
    except ValueError as error:
        return _no(str(error))
    widget = fields[roles.index(role)]
    drawn = sketch.GeometryCount
    widget.lineEdit().selectAll()
    widget.lineEdit().insert(text)
    widget.interpretText()
    qt.pump(3)
    picks.press_key(widget, QtCore.Qt.Key_Return)   # Enter locks the number and moves on
    if session.tool.phase == 0 and role in ("x", "y"):
        session.tool.point[role] = float(value)
    progress = session.tool.enter(role)
    if progress == "shape_done":
        if not qt.wait_until(lambda: sketch.GeometryCount > drawn, 2.0):
            return {"status": "refused", "reason": "the sketcher did not draw the shape"}
    elif progress == "next_phase":
        picks.park_pointer(jitter=1, anchor=session.tool.first_point())
        expected = len(session.tool.roles())
        if not qt.wait_until(lambda: len(widgets.view_fields()) == expected, 2.0):
            return {"status": "refused", "reason": "the tool did not ask for its next numbers"}
    qt.pump(3)
    return {"status": "ok", "progress": progress}


def select_tree_item(name: str) -> dict:
    doc = session.document()
    obj = doc.getObject(name) if doc is not None else None
    if obj is None:
        return _no("the document has no such object")
    tree = widgets.model_tree()
    # Start from nothing selected, so that the click below is always a CHANGE (a click on
    # a row the tree still shows as selected would tell FreeCAD nothing). The row is looked
    # up only afterwards: clearing can make FreeCAD rebuild rows.
    tree.selectionModel().clear()
    qt.pump(2)
    # A row appears a moment after its object is made: wait for it.
    qt.wait_until(lambda: any(row.text(0) == obj.Label for row in widgets.tree_items()), 1.5)
    for item in widgets.tree_items():
        if item.text(0) == obj.Label:
            parent = item.parent()
            while parent is not None:       # a person opens the folders above it first
                parent.setExpanded(True)
                parent = parent.parent()
            # ONE call, and `item` is not touched afterwards. Selecting can make FreeCAD
            # rebuild tree rows (measured: with the sketch-plane dialog open), which deletes
            # the row object we hold; a second call on it crashed FreeCAD.
            before = _signature()
            busy = widgets.dialog_open()        # a task dialog, or the sketcher's panel
            tree.setCurrentItem(item, 0, QtCore.QItemSelectionModel.ClearAndSelect)
            # The tree hands its selection to FreeCAD on a timer: wait for it. The click
            # is "ok" either way. Whether the object ends up selected is for the next
            # snapshot to show: an open dialog or sketch may take the click for itself and
            # clear the selection again within milliseconds, and an answer that depended on
            # catching that moment would differ from run to run (measured: 3 in 1,286).
            qt.wait_until(lambda: any(s.ObjectName == name
                                      for s in Gui.Selection.getSelectionEx()),
                          0.4 if busy else 1.5)
            if busy:
                # An open dialog may USE the click a moment later (the sketch-plane list takes
                # a clicked plane as its answer and closes): give that time to happen, so the
                # reply shows the interface after it, not before.
                qt.wait_until(lambda before=before: _signature() != before, 0.4)
                qt.pump(5)
            return _ok()
    return _no("the model tree has no such item")


def do_pick(parts: list[str]) -> dict:
    if parts == ["origin"]:
        tool = session.tool
        if session.sketch_in_edit() is None or not tool.command or tool.phase or tool.entered:
            return _no("no drawing tool is waiting for its first point")
        picks.click_origin()
        for role in list(tool.roles()):
            tool.enter(role)                # the click gave both x and y
        picks.park_pointer(jitter=1)
        qt.wait_until(lambda: len(widgets.view_fields()) == len(tool.roles()), 2.0)
        return _ok()
    if len(parts) != 2:
        return _no("no such pick")
    if widgets.dialog_open():
        return _no("a dialog is open")
    done, reason = picks.pick(*parts)
    return _ok() if done else _no(reason)


def act(element_id: str, value, server) -> dict:
    kind, _, name = element_id.partition(":")
    if kind == "button":
        return press_button(name, server)
    if kind == "field":
        return type_field(name, value)
    if kind in ("dropdown", "list"):
        return choose_entry(kind, name, value)
    if kind in ("check", "radio", "panel"):
        return click_control(name)
    if kind == "dialog":
        return click_dialog_button(name, server)
    if kind == "view":
        return type_view_field(name, value)
    if kind == "tree":
        return select_tree_item(name)
    if kind == "pick":
        return do_pick(name.split(":"))
    return _no(f"no such kind of element: {kind}")
