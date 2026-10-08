"""Find FreeCAD's real widgets: toolbars, the task panel, the model tree, the 3D view. Runs inside FreeCAD.

Everything here READS. Two rules learned the hard way (FreeCAD 1.1.4, measured by
the feasibility probe):
  - never ask `Gui.isCommandActive(name)` in a loop: for "Std_Expression" it crashes
    FreeCAD. A toolbar button's own QAction knows whether it is enabled;
  - `widget.isVisible()` is useless in a hidden application; ask whether the widget
    is visible RELATIVE TO its panel: `isVisibleTo(panel)`.
"""

from __future__ import annotations

import re

import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")
_cache: dict = {}       # widget handles that live as long as the main window
# Toolbar buttons FreeCAD leaves without an object name (their menus are the undo history).
UNNAMED = {"Undo": "Std_Undo", "Redo": "Std_Redo"}
# Buttons that are never listed and never pressed. The first group opens one of macOS's own
# panels (open, save, print), which is not a Qt widget: the modal guard cannot see or close
# it, so FreeCAD would wait for a person for ever. The rest start a mode that only a real
# mouse can end.
HIDDEN_BUTTONS = frozenset({
    "Std_Open", "Std_Save", "Std_SaveAs", "Std_SaveCopy", "Std_SaveAll", "Std_Revert",
    "Std_Import", "Std_Export", "Std_Print", "Std_PrintPreview", "Std_PrintPdf",
    "Std_MergeProjects", "Std_LinkImport", "Std_LinkImportAll", "Std_DlgMacroExecute",
    "Std_DlgMacroRecord", "Std_MacroStopRecord", "Std_DlgMacroExecuteDirect",
    "Sketcher_CarbonCopy", "PartDesign_Clone",
    "Std_WhatsThis", "Std_Measure", "Part_SelectFilter",
})


def main_window():
    return Gui.getMainWindow()


def class_name(widget) -> str:
    return widget.metaObject().className()


def _find_class(name: str):
    """The one widget of a FreeCAD class under the main window (found once, then remembered)."""
    if name not in _cache:
        for widget in main_window().findChildren(QtWidgets.QWidget):
            if class_name(widget) == name:
                _cache[name] = widget
                break
    return _cache.get(name)


def task_view():
    """The task panel: the column where a command's dialog appears."""
    return _find_class("Gui::TaskView::TaskView")


def model_tree():
    return _find_class("Gui::TreeWidget")


def dialog_open() -> bool:
    return bool(Gui.Control.activeDialog())


# --- toolbars ----------------------------------------------------------------------------------

def toolbar_actions() -> list[tuple[str, object]]:
    """(toolbar name, QAction) for every button on a visible toolbar.

    A button with a drop-down arrow (the sketcher's rectangle tools, for example)
    is a group: its entries are listed too, since a person can click each of them.
    """
    window = main_window()
    out = []
    for bar in window.findChildren(QtWidgets.QToolBar):
        # Toolbars never see the mouse (we trigger their actions, we do not click them).
        # Without this, Qt sends a button a "the pointer entered" event whenever a widget
        # is destroyed while the real pointer happens to lie over the hidden window, and
        # FreeCAD's hover code then walks widgets that are half destroyed: a crash
        # (measured twice, in ActionGroup::onHovered while a task dialog was closing).
        bar.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        if not bar.isVisibleTo(window):
            continue
        for action in bar.actions():
            if action.isSeparator():
                continue
            button = bar.widgetForAction(action)
            menu = button.menu() if isinstance(button, QtWidgets.QToolButton) else None
            entries = [entry for entry in menu.actions()
                       if not entry.isSeparator() and entry.objectName()] if menu else []
            if entries:
                out += [(bar.objectName(), entry) for entry in entries
                        if entry.objectName() not in HIDDEN_BUTTONS]
            elif action_name(action) and action_name(action) not in HIDDEN_BUTTONS:
                out.append((bar.objectName(), action))      # a plain button
    return out


def action_name(action) -> str:
    """The command name of a toolbar action."""
    return action.objectName() or UNNAMED.get(action.text().replace("&", ""), "")


def command_action(command: str):
    """The QAction behind a command name, or None."""
    try:
        actions = Gui.Command.get(command).getAction()
    except Exception:  # noqa: BLE001 - FreeCAD raises its own types for unknown commands
        return None
    return actions[0] if actions else None


def refresh_actions() -> None:
    """Run FreeCAD's own "which buttons are enabled" refresh now, not on its timer."""
    QtCore.QMetaObject.invokeMethod(main_window(), "_updateActions", QtCore.Qt.DirectConnection)


# --- the task panel ----------------------------------------------------------------------------

def number_in(text: str) -> float | None:
    found = NUMBER.search(text)
    return float(found.group(0).replace(",", ".")) if found else None


def field_value(widget) -> float | None:
    """The number a field holds. Its text is rounded for show ("12.35 mm"), so FreeCAD's
    quantity boxes are asked for the unrounded number they keep (`rawValue`)."""
    raw = widget.property("rawValue")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return number_in(widget.text())


def label_of(widget, root) -> str:
    """The words a person reads next to a control."""
    for label in root.findChildren(QtWidgets.QLabel):
        if label.buddy() is widget:
            return label.text()
    parent = widget.parentWidget()
    layout = parent.layout() if parent is not None else None
    if isinstance(layout, QtWidgets.QFormLayout):
        label = layout.labelForField(widget)
        if label is not None:
            return label.text()
    if isinstance(layout, QtWidgets.QGridLayout):
        index = layout.indexOf(widget)
        if index >= 0:
            row, column, _, _ = layout.getItemPosition(index)
            for left in range(column - 1, -1, -1):
                item = layout.itemAtPosition(row, left)
                if item is not None and isinstance(item.widget(), QtWidgets.QLabel):
                    return item.widget().text()
    if parent is not None:
        # No buddy and no form or grid layout: the label a designer puts next to a control
        # is the one created just before it. (Where widgets sit on screen is NOT used: the
        # hidden window is never laid out the same way twice, and the answer must be.)
        previous = None
        for child in parent.children():
            if child is widget:
                break
            if isinstance(child, QtWidgets.QLabel):
                previous = child
            elif isinstance(child, QtWidgets.QWidget):
                previous = None         # another control sits between that label and us
        if previous is not None:
            return previous.text()
    return ""


def panel_controls() -> list[tuple[str, object]]:
    """(kind, widget) for every control of the open task dialog a person could operate.

    Kinds: field (a number box), dropdown, check, radio, list, text, panel_button.
    """
    root = task_view()
    out = []
    if root is None:
        return out
    for widget in root.findChildren(QtWidgets.QWidget):
        name = widget.objectName()
        if not name or name.startswith("qt_") or not widget.isVisibleTo(root):
            continue
        if isinstance(widget, QtWidgets.QAbstractSpinBox):
            kind = "field"
        elif isinstance(widget, QtWidgets.QComboBox):
            kind = "dropdown"
        elif isinstance(widget, QtWidgets.QCheckBox):
            kind = "check"
        elif isinstance(widget, QtWidgets.QRadioButton):
            kind = "radio"
        elif isinstance(widget, QtWidgets.QAbstractButton):
            if isinstance(widget.parentWidget(), QtWidgets.QDialogButtonBox):
                continue                    # OK / Cancel: see dialog_buttons
            kind = "panel_button"
        elif isinstance(widget, QtWidgets.QListWidget):
            kind = "list"
        elif isinstance(widget, QtWidgets.QLineEdit):
            if isinstance(widget.parentWidget(), (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)):
                continue                    # the text part of a number box or dropdown
            kind = "text"
        else:
            continue
        out.append((kind, widget))
    return out


def list_entries(widget) -> list[str]:
    """The entries of a list widget as text.

    Asked through QListWidget itself: the sketcher's element list is a subclass
    whose own `item` sometimes answers with a layout item that has no text.
    """
    out = []
    for row in range(QtWidgets.QListWidget.count(widget)):
        item = QtWidgets.QListWidget.item(widget, row)
        out.append(item.text() if hasattr(item, "text") else "")
    return out


def find_control(name: str):
    """A visible control of the task panel by its object name."""
    root = task_view()
    if root is None:
        return None
    # A dialog that was just closed can still be there for a moment, greyed out, next to
    # the new one with the same widget names: the enabled widget is the live one.
    found = [widget for widget in root.findChildren(QtWidgets.QWidget, name)
             if widget.isVisibleTo(root)]
    live = [widget for widget in found if widget.isEnabled()]
    return (live or found or [None])[0]


def dialog_buttons() -> dict:
    """{'OK': button, 'Cancel': button, ...} of the open task dialog."""
    root = task_view()
    out = {}
    if root is None:
        return out
    for box in root.findChildren(QtWidgets.QDialogButtonBox):
        for button in box.buttons():
            label = button.text().replace("&", "")
            if button.isVisibleTo(root) and (label not in out or not out[label].isEnabled()):
                out[label] = button     # of two with one label, the enabled one is the live one
    return out


def task_titles() -> list[str]:
    """Titles of the boxes in the task panel ("Pad Parameters", ...)."""
    root = task_view()
    if root is None:
        return []
    return [box.windowTitle() for box in root.findChildren(QtWidgets.QWidget)
            if class_name(box) == "Gui::TaskView::TaskBox" and box.isVisibleTo(root)]


# --- the model tree ------------------------------------------------------------------------------

def tree_items() -> list:
    tree = model_tree()
    out = []
    if tree is None:
        return out
    walker = QtWidgets.QTreeWidgetItemIterator(tree)
    while walker.value():
        out.append(walker.value())
        walker += 1
    return out


# --- the 3D view and the sketcher's on-view fields ---------------------------------------------

def viewer():
    """The 3D view widget of the active document (the newest one)."""
    views = [w for w in main_window().findChildren(QtWidgets.QGraphicsView)
             if class_name(w) == "Gui::View3DInventorViewer"]
    return views[-1] if views else None


def view_fields() -> list:
    """The sketcher's on-view fields: the small number boxes that follow the cursor while
    a shape is being drawn. They are real widgets (unnamed Gui::QuantitySpinBox outside the
    task panel). Creation order is the tool's own parameter order."""
    panel = task_view()
    return [w for w in main_window().findChildren(QtWidgets.QAbstractSpinBox)
            if class_name(w) == "Gui::QuantitySpinBox" and not w.objectName() and not w.isHidden()
            and not (panel is not None and panel.isAncestorOf(w))]
