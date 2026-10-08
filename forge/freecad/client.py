"""The client: start a headless FreeCAD worker and send it one command at a time.

    with FreeCADClient() as fc:
        fc.command("new_document")
        fc.command("new_body")
        reply = fc.command("select_plane", plane="XY")
        reply["status"], reply["snapshot"], reply["valid"]

The worker is a separate process running FreeCAD's own Python (see
inside/worker.py). This process never imports FreeCAD, so a FreeCAD crash
cannot take our program (later: the training loop) down with it.

Isolation. The worker runs inside the macOS sandbox, like forge/sandbox.py: no
network, and it can write only into one temporary folder. FreeCAD is told that
folder is its home, so it never reads or writes the user's FreeCAD settings and
cannot disturb a FreeCAD window the user has open.

Recovery. FreeCAD can crash or hang. The client remembers every command that
succeeded since the last `new_document`. If the worker dies, or does not answer
within the time limit, the client starts a new worker, replays those commands,
checks that the snapshot is the same as before, and tries the failed command
once more. If it fails again the reply says "crash" or "timeout", and the
session is back in the state before that command.

Exact undo. By default this client tells the single-part worker to switch exact undo
on (inside/session.py, "Exact undo"). `exact_undo=False` gives the behaviour before
6 Oct 2026, which the audit needs to replay sessions recorded then. A client started
on another worker script (forge/freecad_multi swaps WORKER) is left alone unless it
asks for it.

Memory. A FreeCAD process grows a little with every document it has built
(measured: about 33 MB per 1000 parts). So after `recycle_after` documents the
client quietly swaps the worker for a fresh one at the next `new_document`,
which costs about 0.4 s.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Self

from forge.freecad.locate import freecad_python
from forge.freecad.valid import valid_commands

WORKER = Path(__file__).resolve().parent / "inside" / "worker.py"
SINGLE_PART_WORKER = WORKER     # WORKER may be swapped by a subclass; this name never is
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIMEOUT = 30.0      # seconds for one request
FORGET_UNDO = "!forget_undo"    # how `forget_undo` is written in the replay log (not a command)
STARTUP_TIMEOUT = 60.0

# Later rules win: allow by default, then take away the network, all writes and reads of
# the home folder, then give back only the temp folder and this project (read-only).
_PROFILE = """
(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{work_dir}"))
(allow file-write* (literal "/dev/null") (literal "/dev/dtracehelper"))
(deny file-read* (subpath "{home}"))
(allow file-read* (subpath "{project}") (subpath "{work_dir}"))
(allow file-read-metadata (subpath "{home}"))
"""


class FreeCADUnavailable(RuntimeError):
    """FreeCAD is not installed, or its worker did not start."""


class WorkerLost(RuntimeError):
    """The worker died and replaying the session did not bring the same state back."""


class FreeCADClient:
    """One warm FreeCAD worker. Not thread-safe: use one client per thread."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, sandboxed: bool = True,
                 recycle_after: int | None = 1000, exact_undo: bool | None = None) -> None:
        if freecad_python() is None:
            raise FreeCADUnavailable("FreeCAD was not found (see forge/freecad/locate.py)")
        self.timeout = timeout
        self.sandboxed = sandboxed and shutil.which("sandbox-exec") is not None
        self.recycle_after = recycle_after  # documents per worker; None = never swap
        # None = "on for the single-part worker, untouched for any other worker script".
        self.exact_undo = exact_undo
        self.exact = False                  # what the running worker was told (see start)
        self.recycles = 0                   # planned swaps (not failures)
        self._documents = 0                 # documents this worker has started
        self.restarts = 0                   # how many times a worker had to be replaced
        self.failures: list[str] = []       # "crash" or "timeout", one per failed request
        self._proc: subprocess.Popen | None = None
        self._work_dir: str | None = None
        self._log: list[tuple[str, dict]] = []      # commands to replay after a crash
        self._last: dict | None = None              # the snapshot after the last command

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- starting and stopping -----------------------------------------------------------------

    def start(self) -> None:
        python, lib = freecad_python()
        self._work_dir = os.path.realpath(tempfile.mkdtemp(prefix="forge-freecad-"))
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": self._work_dir,
            "FREECAD_USER_HOME": self._work_dir,    # FreeCAD keeps its settings here
            "TMPDIR": self._work_dir,
            "PYTHONPATH": str(lib),                 # where FreeCAD.so lives
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        command = [str(python), str(WORKER)]
        if self.sandboxed:
            profile = _PROFILE.format(work_dir=self._work_dir, project=PROJECT_ROOT,
                                      home=os.path.realpath(Path.home()))
            command = ["sandbox-exec", "-p", profile, *command]
        self._proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, env=env, cwd=self._work_dir,
                                      text=True)
        ready = self._read(STARTUP_TIMEOUT)
        if not isinstance(ready, dict) or ready.get("status") != "ready":
            self._stop()
            raise FreeCADUnavailable("the FreeCAD worker did not start")
        self.pid = ready["pid"]
        self._documents = 0
        self.exact = (self.exact_undo if self.exact_undo is not None
                      else WORKER == SINGLE_PART_WORKER)
        if self.exact:  # every new worker is told again, so a restart keeps the setting
            answer = self._read_after({"op": "configure", "exact_undo": True})
            if not isinstance(answer, dict) or answer.get("exact_undo") is not True:
                self._stop()
                raise FreeCADUnavailable("the FreeCAD worker did not switch exact undo on")

    def _stop(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
            for pipe in (self._proc.stdin, self._proc.stdout):
                pipe.close()
            self._proc = None
        if self._work_dir is not None:
            shutil.rmtree(self._work_dir, ignore_errors=True)
            self._work_dir = None

    def close(self) -> None:
        """Stop the worker and forget the session."""
        self._stop()
        self._log, self._last = [], None

    # --- one request -----------------------------------------------------------------------------

    def _read(self, wait: float) -> dict | str:
        """The next reply, or "timeout" / "crash" if none came."""
        ready, _, _ = select.select([self._proc.stdout], [], [], wait)
        if not ready:
            return "timeout"
        line = self._proc.stdout.readline()
        return json.loads(line) if line else "crash"

    def _read_after(self, payload: dict) -> dict | str:
        """Send one request to a worker that is known to be running, and read its reply."""
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()
        return self._read(STARTUP_TIMEOUT)

    def _request(self, payload: dict, timeout: float | None = None) -> dict | str:
        if self._proc is None:
            self.start()                # first use without `with`, or after close()
        if self._proc.poll() is not None:
            return "crash"
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            return "crash"
        return self._read(self.timeout if timeout is None else timeout)

    def _recover(self) -> None:
        """Replace the worker and replay the session up to the last good command."""
        self.restarts += 1
        self._stop()
        self.start()
        snapshot = None
        for name, args in self._log:
            payload = ({"op": "forget_undo"} if name == FORGET_UNDO
                       else {"op": "command", "command": name, "args": args})
            reply = self._request(payload)
            if not isinstance(reply, dict) or reply["status"] != "ok":
                raise WorkerLost(f"replaying {name} failed: {reply}")
            snapshot = reply["snapshot"]
        if self._log and snapshot != self._last:
            raise WorkerLost("the replayed session is not in the state it was in before")

    # --- the public calls --------------------------------------------------------------------------

    def command(self, name: str, **args: object) -> dict:
        """Issue one command. Returns {"status", "snapshot", "valid", maybe "reason"}."""
        payload = {"op": "command", "command": name, "args": args}
        if name == "new_document":
            if self.recycle_after and self._documents >= self.recycle_after:
                self._stop()            # a new document needs nothing from the old worker
                self.start()
                self.recycles += 1
            self._documents += 1
        reply = self._request(payload)
        if not isinstance(reply, dict):
            self.failures.append(reply)
            self._recover()
            retry = self._request(payload)
            if not isinstance(retry, dict):         # the same command killed it twice
                self.failures.append(retry)
                self._recover()
                return {"status": retry, "reason": f"FreeCAD: {retry} on {name}",
                        **self._service({"op": "snapshot"}, with_valid=True)}
            reply = retry
        if reply["status"] == "ok":
            if name == "new_document":
                self._log = []                      # nothing before it matters any more
            self._log.append((name, dict(args)))
            self._last = reply["snapshot"]
        return reply

    def _service(self, payload: dict, with_valid: bool = False) -> dict:
        reply = self._request(payload)
        if not isinstance(reply, dict):
            self.failures.append(reply)
            self._recover()
            reply = self._request(payload)
            if not isinstance(reply, dict):
                raise WorkerLost(f"{payload['op']} failed twice: {reply}")
        if with_valid:
            return {"snapshot": reply["snapshot"], "valid": valid_commands(reply["snapshot"])}
        return reply

    def reset(self) -> dict:
        """Close the document: the session is as a fresh worker's, with no document at all."""
        reply = self._service({"op": "reset"})
        self._log, self._last = [], None
        return reply

    def forget_undo(self) -> dict:
        """Empty the undo history, as if the document had just been opened from a file."""
        reply = self._service({"op": "forget_undo"})
        self._log.append((FORGET_UNDO, {}))
        self._last = reply["snapshot"]
        return reply

    def set_exact_undo(self, on: bool) -> None:
        """Switch exact undo on or off, now and for every worker this client starts later."""
        self.exact_undo = self.exact = on
        if self._proc is not None:
            self._service({"op": "configure", "exact_undo": on})

    def rebuild(self) -> dict:
        """Build the document again from the commands in effect. The snapshot stays the same;
        whatever it does not show is gone. Returns {"status", "snapshot", "valid"}."""
        reply = self._service({"op": "rebuild"})
        self._last = reply["snapshot"]
        return reply

    def tolerances(self) -> dict[str, float]:
        """Object name -> the largest tolerance in its shape. 1e-7 is healthy."""
        return self._service({"op": "tolerances"})["tolerances"]

    def snapshot(self) -> dict:
        """The current snapshot, without changing anything."""
        return self._service({"op": "snapshot"})["snapshot"]

    def probe(self, points: list[list[float]]) -> list[bool]:
        """For each (x, y, z): is that point inside the solid?"""
        return self._service({"op": "probe", "points": points})["inside"]

    def objects(self) -> list[list[str]]:
        """[name, FreeCAD type] of every object in the document, origins included."""
        return self._service({"op": "objects"})["objects"]

    def measure_file(self, path: str | Path) -> dict | None:
        """Open a saved .FCStd in the worker, recompute it and measure its body."""
        # The worker may read only its temp folder and this project, so hand it a copy.
        inside = Path(self._work_dir) / "reopened.FCStd"
        shutil.copyfile(path, inside)
        return self._service({"op": "measure_file", "path": str(inside)})["solid"]

    def stats(self) -> dict:
        """The worker's process id and peak memory in megabytes."""
        return self._service({"op": "stats"})

    def save(self, path: str | Path) -> Path:
        """Save the document as an .FCStd file that FreeCAD can open and edit."""
        # The sandbox lets the worker write only into its temp folder, so it saves there
        # and this process copies the file to where it was asked for.
        inside = Path(self._work_dir) / "document.FCStd"
        reply = self._service({"op": "save", "path": str(inside)})
        if reply["status"] != "ok":
            raise RuntimeError(reply.get("reason", "save failed"))
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inside, target)
        return target

    def break_worker(self, how: str) -> None:
        """For tests: make the NEXT request find a dead ("crash") or silent ("hang") worker."""
        self._proc.stdin.write(json.dumps({"op": f"_{how}"}) + "\n")
        self._proc.stdin.flush()
        if how == "crash":
            self._proc.wait()
