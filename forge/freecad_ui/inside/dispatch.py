"""Requests that touch the interface (see ops.py for the list). Runs inside FreeCAD."""

from __future__ import annotations

import os
import sys

# The command-level runtime's rules for edges and faces, and its reading of features,
# are reused as they are (forge/freecad/inside is read, never changed).
_COMMAND_INSIDE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "freecad", "inside")
if _COMMAND_INSIDE not in sys.path:
    sys.path.append(_COMMAND_INSIDE)

import act as acting
import document
import elements
import FreeCAD as App
import FreeCADGui as Gui
import qt
import session
import widgets
from PySide import QtCore, QtWidgets


def observe(request: dict) -> dict:
    out = {"elements": elements.elements(bool(request.get("include_disabled"))),
           "context": elements.context()}
    if request.get("document"):
        out["document"] = {"items": document.items(), "solid": document.solid()}
    return out


def drop_stale_menus() -> int:
    """Delete the menus FreeCAD has left behind. Returns how many.

    On macOS FreeCAD empties the menu bar and builds new menus every time the
    workbench changes, and entering or leaving a sketch is a workbench change. The
    old menus are no longer in the menu bar, and nothing refers to them, but they are
    never deleted: about 180 widgets per part. FreeCAD's own button refresh walks all
    of them, so every action gets slower (measured: a 1.9 s part takes 4.4 s ten
    parts later). A menu is stale when the menu bar owns it but does not show it.
    """
    bar = Gui.getMainWindow().menuBar()
    shown = {id(action) for action in bar.actions()}
    stale = [menu for menu in bar.findChildren(QtWidgets.QMenu, options=QtCore.Qt.FindDirectChildrenOnly)
             if id(menu.menuAction()) not in shown]
    for menu in stale:
        menu.deleteLater()
    qt.pump(2)
    return len(stale)


def reset() -> int:
    """Close every document without saving. Housekeeping between parts, not an interface action."""
    if widgets.dialog_open():
        Gui.Control.closeDialog()
    for name in list(App.listDocuments()):
        App.closeDocument(name)
    session.tool.stop()
    qt.pump(5)
    return drop_stale_menus()


def handle(request: dict, server) -> dict:
    op = request.get("op")
    if op == "elements":
        return {"status": "ok", **observe(request)}
    if op == "act":
        reply = acting.act(request["id"], request.get("value"), server)
        if request.get("observe", True):
            reply.update(observe(request))
        return reply
    if op == "document":
        return {"status": "ok", "items": document.items(), "solid": document.solid(),
                "context": elements.context()}
    if op == "probe":
        return {"status": "ok", "inside": document.inside(request["points"])}
    if op == "reset":
        return {"status": "ok", "stale_menus": reset()}
    if op == "stats":
        window = Gui.getMainWindow()
        return {"status": "ok", "pid": os.getpid(),
                "peak_mb": round(server.peak_memory() / (1 << 20), 1),
                "widgets": len(window.findChildren(QtWidgets.QWidget)),
                "menus": len(window.findChildren(QtWidgets.QMenu)),
                "documents": len(App.listDocuments())}
    return {"status": "rejected", "reason": f"unknown op: {op}"}
