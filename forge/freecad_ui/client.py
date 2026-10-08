"""The client: talk to one hidden FreeCAD of our own, one interface action at a time.

    with UIClient() as ui:
        ui.new_part()                                   # housekeeping: close what is open
        reply = ui.act("button:Std_New")
        reply = ui.act("button:PartDesign_Body")
        reply["status"], reply["elements"], reply["context"]

Every `act` reply carries the new interface elements and context, so a caller
(later: the model) always decides from what the interface shows NOW.

Recovery. FreeCAD can crash or stop answering. The client remembers every action
that succeeded since `new_part()`. If the instance dies or is silent for longer
than the time limit, the client kills it, starts a fresh one, replays those
actions, checks that the interface is in the same state as before (same context,
same element ids), and tries the failed action once more.

Planned restarts. FreeCAD's main window slowly grows (it leaks menus on every
workbench switch, and entering a sketch is a workbench switch), so the instance
is swapped for a fresh one every `restart_after` parts, at a `new_part()`.
"""

from __future__ import annotations

import json
import socket
import struct
import time
from typing import Self

from forge.freecad_ui.launch import Instance, freecad_gui_available

DEFAULT_TIMEOUT = 30.0      # seconds for one request
RESTART_AFTER = 60          # parts per instance: it grows about 11 MB per part (README, "Measured")


class UIUnavailable(RuntimeError):
    """FreeCAD's application is not installed, or the hidden instance did not start."""


class InstanceLost(RuntimeError):
    """The instance died and replaying the session did not bring the same state back."""


class UIClient:
    """One hidden FreeCAD instance. Not thread-safe: one client per thread."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, restart_after: int | None = RESTART_AFTER,
                 lifetime_s: float = 6 * 3600.0, memory_gb: float = 3.0,
                 debug: bool = False) -> None:
        if not freecad_gui_available():
            raise UIUnavailable("FreeCAD.app was not found (see forge/freecad_ui/launch.py)")
        self.timeout = timeout
        self.restart_after = restart_after
        self._lifetime_s, self._memory_gb, self._debug = lifetime_s, memory_gb, debug
        self.instance: Instance | None = None
        self._socket: socket.socket | None = None
        self.pid: int | None = None
        self.version: str | None = None
        self.restarts = 0                   # unplanned: after a crash or a hang
        self.planned_restarts = 0
        self.failures: list[str] = []       # "crash" or "timeout", one per failed request
        self.failed_on: list[tuple] = []    # (kind, element id, value) of each action that failed
        self._parts = 0                     # parts this instance has started
        self._log: list[tuple[str, object]] = []    # actions to replay after a crash
        self._last: tuple | None = None             # (context, element ids) after the last action

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- starting and stopping -----------------------------------------------------------------

    def start(self) -> None:
        self.instance = Instance(self._lifetime_s, self._memory_gb, self._debug)
        try:
            self.instance.start()
        except RuntimeError as error:
            raise UIUnavailable(str(error)) from error
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.connect(self.instance.socket_path)
        hello = self._exchange({"op": "hello"}, 60.0)
        if not isinstance(hello, dict):
            self._stop()
            raise UIUnavailable("the hidden FreeCAD did not answer")
        self.pid, self.version = hello["pid"], hello["version"]
        self._parts = 0

    def _stop(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self.instance is not None:
            self.instance.kill()
            self.instance = None

    def close(self) -> None:
        """Kill the instance and forget the session."""
        self._stop()
        self._log, self._last = [], None

    # --- one request -----------------------------------------------------------------------------

    def _receive(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self._socket.recv(size - len(data))
            if not chunk:
                raise ConnectionError("FreeCAD closed the connection")
            data += chunk
        return data

    def _exchange(self, payload: dict, timeout: float | None = None) -> dict | str:
        """Send one request. Returns the reply, or "timeout" / "crash"."""
        if self._socket is None:
            self.start()
        data = json.dumps(payload).encode()
        try:
            self._socket.settimeout(self.timeout if timeout is None else timeout)
            self._socket.sendall(struct.pack("!I", len(data)) + data)
            (size,) = struct.unpack("!I", self._receive(4))
            return json.loads(self._receive(size))
        except TimeoutError:
            return "timeout"
        except OSError:                     # includes ConnectionError: the process is gone
            return "crash"

    @staticmethod
    def _state_of(reply: dict) -> tuple | None:
        if "elements" not in reply:
            return None
        # What must come back the same after a replay: where the session is (dialog, sketch,
        # tool, tip, undo steps) and every control, tree item and pick that is showing.
        # Toolbar buttons and the selection are left out: FreeCAD refreshes both on timers,
        # so two identical runs can be caught a moment apart.
        context = {key: value for key, value in reply["context"].items() if key != "selection"}
        return (json.dumps(context, sort_keys=True),
                tuple(element["id"] for element in reply["elements"]
                      if element["kind"] != "button"))

    def _recover(self) -> None:
        """Replace the instance and replay the part up to the last good action."""
        self.restarts += 1
        self._stop()
        self.start()
        state = None
        for element_id, value in self._log:
            reply = self._exchange({"op": "act", "id": element_id, "value": value})
            if not isinstance(reply, dict) or reply["status"] != "ok":
                raise InstanceLost(f"replaying {element_id} failed: {reply}")
            state = self._state_of(reply)
        if self._log and self._last is not None and state != self._last:
            raise InstanceLost("the replayed session is not in the state it was in before: "
                               f"{state} instead of {self._last}")

    def _service(self, payload: dict) -> dict:
        """A request that changes nothing: on failure recover and ask again."""
        reply = self._exchange(payload)
        if not isinstance(reply, dict):
            self.failures.append(reply)
            self._recover()
            reply = self._exchange(payload)
            if not isinstance(reply, dict):
                raise InstanceLost(f"{payload['op']} failed twice: {reply}")
        return reply

    # --- the public calls --------------------------------------------------------------------------

    def new_part(self) -> None:
        """Housekeeping before a new part: close every document; swap an old instance."""
        if self.restart_after and self._parts >= self.restart_after:
            self._stop()
            self.start()
            self.planned_restarts += 1
        else:
            self._service({"op": "reset"})
        self._parts += 1
        self._log, self._last = [], None

    def act(self, element_id: str, value: object = None, observe: bool = True,
            document: bool = False) -> dict:
        """Act on one element. Returns {"status", maybe "reason", "elements", "context", "modals"}
        (and "document" when asked for)."""
        payload = {"op": "act", "id": element_id, "value": value, "observe": observe,
                   "document": document}
        reply = self._exchange(payload)
        if not isinstance(reply, dict):
            self.failures.append(reply)
            self.failed_on.append((reply, element_id, value))
            self._recover()
            retry = self._exchange(payload)
            if not isinstance(retry, dict):         # the same action killed it twice
                self.failures.append(retry)
                self._recover()
                return {"status": retry, "reason": f"FreeCAD: {retry} on {element_id}",
                        **{k: v for k, v in self.elements(document).items() if k != "status"}}
            reply = retry
        if reply["status"] == "ok":
            self._log.append((element_id, value))
            self._last = self._state_of(reply)
        return reply

    def elements(self, document: bool = False, include_disabled: bool = False) -> dict:
        """The interface elements and the context (and the document, if asked), changing nothing."""
        return self._service({"op": "elements", "document": document,
                              "include_disabled": include_disabled})

    def document(self) -> dict:
        """What has been built: {"items", "solid", "context"}."""
        return self._service({"op": "document"})

    def probe(self, points: list[list[float]]) -> list[bool]:
        return self._service({"op": "probe", "points": points})["inside"]

    def stats(self) -> dict:
        """Process id, peak memory, widget and menu counts of the instance."""
        reply = self._service({"op": "stats"})
        reply["rss_mb"] = round(self.instance.memory_mb(), 1)
        return reply

    def debug_exec(self, code: str) -> dict:
        """Development only (needs debug=True): run a Python snippet inside FreeCAD."""
        return self._service({"op": "exec", "code": code})

    def kill_instance(self) -> None:
        """For tests and benchmarks: kill the FreeCAD process, as a crash would leave things."""
        for pid in self.instance.pids():
            import os
            import signal
            os.kill(pid, signal.SIGKILL)
        end = time.time() + 5
        while self.instance.pids() and time.time() < end:
            time.sleep(0.05)
