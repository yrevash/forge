"""The requests the in-app server answers. Runs inside FreeCAD.

    hello      who am I (pid, FreeCAD version)
    elements   the interface elements a person could act on right now, and the context
    act        act on one element (id, optional value); reply carries the new elements
    document   the document as plain data, and the measured solid
    probe      for each point: is it inside the solid?
    reset      close every document (housekeeping between parts; not an interface action)
    stats      memory and widget counts
    exec       run a Python snippet (development only; refused unless FORGE_UI_DEBUG=1)
"""

from __future__ import annotations

import contextlib
import io
import os

import FreeCAD as App
import FreeCADGui as Gui
import qt
from PySide import QtCore, QtWidgets

DEBUG = os.environ.get("FORGE_UI_DEBUG") == "1"
_namespace: dict = {}


def prepare() -> None:
    """One-time setup once FreeCAD is up."""
    import macos
    macos.no_app_nap()
    macos.no_dock_icon()
    if os.environ.get("FORGE_UI_PAINT") != "1":
        # The window is hidden, so drawing it is pure cost (measured: parts build 2.5 times
        # faster without). Nothing we read depends on drawing: widgets keep their state,
        # and points are projected by the camera, not read from the picture.
        Gui.getMainWindow().setUpdatesEnabled(False)
    import widgets
    widgets.toolbar_actions()       # marks the toolbars as deaf to the mouse (see there)
    _namespace.update(App=App, Gui=Gui, QtCore=QtCore, QtWidgets=QtWidgets, qt=qt)


def _exec(request: dict, server) -> dict:
    if not DEBUG:
        return {"status": "rejected", "reason": "exec is a development op (FORGE_UI_DEBUG=1)"}
    out = io.StringIO()
    _namespace.pop("result", None)
    _namespace["server"] = server
    with contextlib.redirect_stdout(out):
        exec(compile(request["code"], "<forge-ui>", "exec"), _namespace)  # noqa: S102 - our own code, debug only
    return {"status": "ok", "result": _namespace.get("result"), "stdout": out.getvalue()}


def handle(request: dict, server) -> dict:
    op = request.get("op")
    if op == "hello":
        return {"status": "ok", "pid": os.getpid(), "version": ".".join(App.Version()[:3])}
    if op == "exec":
        return _exec(request, server)
    import dispatch  # imported late so `exec` works even while it is being written
    return dispatch.handle(request, server)
