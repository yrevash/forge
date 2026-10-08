"""Waiting, the Qt way. Runs inside FreeCAD.

Our code runs inside one of FreeCAD's own timer callbacks, on the one thread
that draws the interface. Nothing FreeCAD does "later" (close a dialog, enable a
button, redraw) can happen until we let its event loop turn. So every action
here is: act, then turn the event loop until a CONDITION holds. Never sleep.
"""

from __future__ import annotations

import time

from PySide import QtCore, QtWidgets


def reap() -> None:
    """Destroy widgets that were closed with deleteLater().

    Qt only deletes them when control returns to the event-loop level that asked
    for it. We sit one level deeper (inside a timer callback), so without this a
    closed dialog and its OK button stay alive and findable.
    """
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


def pump(times: int = 3) -> None:
    """Let FreeCAD handle what is waiting in its event queue."""
    for _ in range(times):
        QtWidgets.QApplication.processEvents()
    reap()


def wait_until(condition, timeout: float = 3.0) -> bool:
    """Turn the event loop until `condition()` is true. False if it never was."""
    end = time.time() + timeout
    while not condition():
        if time.time() > end:
            return False
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 5)
        reap()
    return True
