"""Semantic picks: the few things a person does in the 3D view that no widget shows. Runs inside FreeCAD.

A person picks a face or an edge by clicking it in the picture. The widget tree
has nothing to read there (the 3D view is one OpenGL surface), and a click at a
pixel is only as exact as the pixel. So these picks are named by MEANING and the
runtime resolves them:

    pick:plane:XY | XZ | YZ          one of the body's base planes
    pick:face:top | bottom           the flat face at the very top / bottom of the solid
    pick:edges:vertical | top_face | bottom_face | all
    pick:origin                      (in a sketch, while a tool waits for a point) the sketch origin

Planes, faces and edges are put into FreeCAD's selection, exactly as clicks on
them would. Edges and faces are found by the geometric rules of
forge/freecad/inside/rules.py (the same ones the command-level runtime uses), so
FreeCAD's changing "Edge7" names never leave this file. `pick:origin` is a real
mouse click sent to the view at the pixel of the origin; FreeCAD snaps it exactly.
"""

from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui
import qt
import rules  # forge/freecad/inside/rules.py (on the path, see dispatch.py)
import session
import widgets
from PySide import QtCore, QtGui, QtWidgets

PLANES = ("XY", "XZ", "YZ")
EDGE_RULES = ("vertical", "top_face", "bottom_face", "all")
FACE_RULES = ("top", "bottom")


def _tip_solid():
    body = session.active_body()
    tip = body.Tip if body is not None else None
    if tip is None or tip.Shape.isNull() or not tip.Shape.Solids:
        return None
    return tip


def available() -> list[dict]:
    """The picks that mean something right now, as interface elements."""
    out = []
    if widgets.dialog_open() and session.sketch_in_edit() is None:
        return out
    if session.sketch_in_edit() is not None:
        if session.tool.command and session.tool.phase == 0 and not session.tool.entered:
            out.append({"kind": "pick", "id": "pick:origin", "role": "sketch origin", "value": None})
        return out
    body = session.active_body()
    if body is None:
        return out
    out += [{"kind": "pick", "id": f"pick:plane:{plane}", "role": f"{plane} base plane",
             "value": None} for plane in PLANES]
    tip = _tip_solid()
    if tip is None:
        return out
    for rule in FACE_RULES:
        count = len(rules.faces(tip.Shape, rule))
        if count:
            out.append({"kind": "pick", "id": f"pick:face:{rule}", "role": f"{rule} face",
                        "value": count})
    for rule in EDGE_RULES:
        count = len(rules.edges(tip.Shape, rule))
        if count:
            out.append({"kind": "pick", "id": f"pick:edges:{rule}", "role": f"{rule} edges",
                        "value": count})
    return out


def _select(obj, names: list[str]) -> None:
    Gui.Selection.clearSelection()
    doc = session.document()
    for name in names or [""]:
        Gui.Selection.addSelection(doc.Name, obj.Name, name)
    qt.pump()


def pick(what: str, rule: str) -> tuple[bool, str]:
    """Carry out a plane, face or edge pick. Returns (done?, reason if not)."""
    body = session.active_body()
    if body is None:
        return False, "there is no body"
    if what == "plane":
        if rule not in PLANES:
            return False, f"no such plane: {rule}"
        for feature in body.Origin.OriginFeatures:
            if feature.Role == f"{rule}_Plane":
                _select(feature, [])
                return True, ""
        return False, "the body has no such plane"
    tip = _tip_solid()
    if tip is None:
        return False, "there is no solid"
    if what == "face" and rule in FACE_RULES:
        names = rules.faces(tip.Shape, rule)
    elif what == "edges" and rule in EDGE_RULES:
        names = rules.edges(tip.Shape, rule)
    else:
        return False, f"no such pick: {what}:{rule}"
    if not names:
        return False, "nothing fits that rule"
    _select(tip, names)
    return True, ""


# --- the pointer in the 3D view ------------------------------------------------------------------

def _mouse(kind, position, button=QtCore.Qt.NoButton, buttons=QtCore.Qt.NoButton) -> None:
    """Send one mouse event to the 3D view. No real cursor moves; works while hidden."""
    viewport = widgets.viewer().viewport()
    event = QtGui.QMouseEvent(kind, position, viewport.mapToGlobal(position.toPoint()), button,
                              buttons, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(viewport, event)
    qt.pump(2)


def _pixel_of(point: App.Vector) -> QtCore.QPointF:
    """Where a 3D point is drawn, in the view widget's own pixels."""
    viewport = widgets.viewer().viewport()
    ratio = viewport.devicePixelRatioF()    # FreeCAD answers in device pixels, origin bottom-left
    x, y = Gui.ActiveDocument.ActiveView.getPointOnViewport(point)
    return QtCore.QPointF(x / ratio, viewport.height() - y / ratio)


def park_pointer(jitter: int = 0, anchor: tuple[float, float] | None = None) -> None:
    """Move the pointer into the view: up and to the right of the sketch origin, off both axes.

    The on-view fields only appear once the pointer is over the view. The typed
    numbers decide the shape, with two exceptions that make the place matter:
      - a pointer ON the origin or an axis makes the tool add a snap constraint of its own;
      - a typed ANGLE takes its sign from the side the pointer is on (measured: a slot
        typed at 90 degrees ran downwards while the pointer was below its start).
    Up-right of the origin is the side where typed angles mean what they say.
    """
    sketch = session.sketch_in_edit()
    viewport = widgets.viewer().viewport()
    width, height = viewport.width(), viewport.height()
    origin = _pixel_of(sketch.getGlobalPlacement().Base)
    x = 0.88 * width
    if x - origin.x() < 30:                 # the origin is at the right edge: stay right of it
        x = min(width - 2, origin.x() + 15)
    y = 0.12 * height                       # pixel rows count downwards: small y is "up"
    if origin.y() - y < 30:
        y = max(2, origin.y() - 15)
    if anchor is not None:
        # The shape's first point is placed. A typed angle is measured from THAT point, so
        # the pointer must be up-right of it too. When the first point is high in the view
        # (or off it) the usual spot is below it: go 40 pixels up-right of the point instead,
        # even if that is outside the view (FreeCAD projects the position all the same).
        start = _pixel_of(sketch.getGlobalPlacement().multVec(App.Vector(anchor[0], anchor[1], 0)))
        if not (x > start.x() + 10 and y < start.y() - 10):
            x, y = start.x() + 40, start.y() - 40
    _mouse(QtCore.QEvent.MouseMove, QtCore.QPointF(x + jitter, y + jitter))


def click_origin() -> None:
    """Left-click the sketch origin (FreeCAD snaps a click there exactly onto it)."""
    sketch = session.sketch_in_edit()
    position = _pixel_of(sketch.getGlobalPlacement().Base)
    _mouse(QtCore.QEvent.MouseMove, position)
    _mouse(QtCore.QEvent.MouseButtonPress, position, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
    _mouse(QtCore.QEvent.MouseButtonRelease, position, QtCore.Qt.LeftButton)
    qt.pump(2)


def press_key(widget, key) -> None:
    for kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
        QtWidgets.QApplication.sendEvent(widget, QtGui.QKeyEvent(kind, key, QtCore.Qt.NoModifier))
    qt.pump(5)
