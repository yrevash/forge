"""The in-app server. Runs inside a hidden FreeCAD window that OUR launcher started.

It listens on a Unix socket. Each request is one JSON object with an "op"; it is
carried out on FreeCAD's interface thread (from a Qt socket notifier) and answered
with one JSON object. Frames are a 4-byte length followed by the JSON bytes.

Three guards run on timers of their own:
  - the MODAL guard: FreeCAD reports some refusals in a pop-up box that blocks
    everything until a person clicks it. We note its text and close it, so a
    refusal becomes data ("modals" in the reply) and never a hang;
  - the SELF guard: exit when the deadline passes, when memory is over the cap,
    or when the process that launched us is gone;
  - (outside this process, launch.py also runs a watchdog that can kill us even
    if this thread is stuck.)
"""

from __future__ import annotations

import json
import os
import resource
import socket
import struct
import sys
import time
import traceback

from PySide import QtCore, QtWidgets

SOCKET_PATH = os.environ["FORGE_UI_SOCK"]
DEADLINE = time.time() + float(os.environ.get("FORGE_UI_TIMEOUT", "3600"))
MEMORY_LIMIT = float(os.environ.get("FORGE_UI_MEM_GB", "3")) * (1 << 30)
PARENT = int(os.environ.get("FORGE_UI_PARENT", "0"))


class Server(QtCore.QObject):
    def __init__(self, handle) -> None:
        super().__init__()
        self.handle = handle        # ops.handle(request, server) -> reply
        self.modals: list[dict] = []    # pop-up boxes the guard closed since the last reply
        self.busy = False
        self.guard_ticks = 0            # how often the modal guard has run (a liveness check)
        self.connection = None
        self.buffer = b""
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(SOCKET_PATH)
        self.listener.listen(1)
        self.listener.setblocking(False)
        # Requests arrive through SOCKET NOTIFIERS, not a polling timer. This matters on
        # macOS: Qt delivers all timers from one run-loop source, and that source does not
        # fire again while one of its own callbacks is still running. Some FreeCAD commands
        # open a blocking dialog from inside the action we trigger; if we were inside a timer
        # callback then, the modal guard's timer could never fire and FreeCAD would sit
        # there for ever (measured: PartDesign_Pocket with no sketch).
        self._accept = QtCore.QSocketNotifier(self.listener.fileno(), QtCore.QSocketNotifier.Read, self)
        self._accept.activated.connect(self.accept)
        self._reader = None
        self._timers = []
        for interval, slot in ((250, self.self_guard), (30, self.modal_guard)):
            timer = QtCore.QTimer(self)
            timer.setInterval(interval)
            timer.timeout.connect(slot)
            timer.start()
            self._timers.append(timer)

    # --- guards ----------------------------------------------------------------------------

    @staticmethod
    def peak_memory() -> int:
        """Peak resident memory in bytes (macOS reports ru_maxrss in bytes)."""
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    def self_guard(self) -> None:
        parent_gone = False
        if PARENT:
            try:
                os.kill(PARENT, 0)          # signal 0 only asks "does it exist?"
            except ProcessLookupError:
                parent_gone = True
            except PermissionError:
                pass
        if time.time() > DEADLINE or self.peak_memory() > MEMORY_LIMIT or parent_gone:
            sys.stderr.write("forge ui server: self-guard exit\n")
            os._exit(3)                     # a plain exit code, not a crash

    def modal_guard(self) -> None:
        self.guard_ticks += 1
        box = QtWidgets.QApplication.activeModalWidget()
        if box is None:
            return
        if hasattr(box, "text"):
            text = box.text()
        else:                           # a custom dialog: its labels are what a person reads
            text = " ".join(label.text() for label in box.findChildren(QtWidgets.QLabel))
        self.modals.append({"title": box.windowTitle(), "text": str(text)[:300],
                            "class": box.metaObject().className()})
        try:
            box.reject()
        except Exception:  # noqa: BLE001 - any failure to reject: close it instead
            box.close()

    # --- the request loop --------------------------------------------------------------------

    def accept(self, *_: object) -> None:
        try:
            connection, _ = self.listener.accept()
        except BlockingIOError:
            return
        if self._reader is not None:
            self._reader.setEnabled(False)
        connection.setblocking(False)
        self.connection, self.buffer = connection, b""
        self._reader = QtCore.QSocketNotifier(connection.fileno(), QtCore.QSocketNotifier.Read, self)
        self._reader.activated.connect(self.readable)

    def readable(self, *_: object) -> None:
        if self.busy or self.connection is None:
            return                          # a nested event loop of our own request
        try:
            chunk = self.connection.recv(1 << 20)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if not chunk:                       # the client went away
            self._reader.setEnabled(False)
            self.connection.close()
            self.connection = None
            return
        self.buffer += chunk
        while len(self.buffer) >= 4:
            (size,) = struct.unpack("!I", self.buffer[:4])
            if len(self.buffer) < 4 + size:
                return
            request = json.loads(self.buffer[4:4 + size])
            self.buffer = self.buffer[4 + size:]
            self.busy = True
            started = time.perf_counter()
            try:
                reply = self.handle(request, self)
            except BaseException:  # noqa: BLE001 - a bug in our code must not take FreeCAD down
                reply = {"status": "error", "reason": traceback.format_exc()[-1500:]}
            finally:
                self.busy = False
            reply["ms"] = round(1000 * (time.perf_counter() - started), 2)
            reply["modals"], self.modals = self.modals, []
            data = json.dumps(reply, default=str).encode()
            self.connection.setblocking(True)
            self.connection.sendall(struct.pack("!I", len(data)) + data)
            self.connection.setblocking(False)
