"""The worker: a loop that runs inside FreeCAD's own Python and obeys one request per line.

Started by forge/freecad/client.py as

    <FreeCAD>/bin/python worker.py

with FreeCAD's `lib` folder on PYTHONPATH, so `import FreeCAD` works and no
window is ever opened. Requests arrive on stdin and replies leave on stdout, one
JSON object per line:

    {"op": "command", "command": "pad", "args": {"length": 10}}
        -> {"status": "ok", "snapshot": {...}, "valid": [...]}
    {"op": "snapshot"}                    the current snapshot, changing nothing
    {"op": "reset"}                       close the document: back to "no document at all"
    {"op": "forget_undo"}                 as if the document was just opened: nothing to undo
    {"op": "configure", "exact_undo": true}  switch exact undo on or off (inside/session.py)
    {"op": "rebuild"}                     build the document again from the commands in effect
    {"op": "tolerances"}                  the largest tolerance in every object's shape
    {"op": "save", "path": "..."}         write the document as a .FCStd file
    {"op": "probe", "points": [[x,y,z]]}  is each point inside the solid?
    {"op": "objects"}                     every object in the document, nothing left out
    {"op": "measure_file", "path": "..."} open a saved .FCStd and measure its body
    {"op": "stats"}                       process id and memory, for the stability report
    {"op": "_crash"} / {"op": "_hang"}    die / stop answering, so tests can check recovery

Nothing here runs code it is sent. A request names a command from the fixed
catalogue and carries numbers; that is all.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
from pathlib import Path

# FreeCAD and its libraries print to stdout whenever they like, which would corrupt
# our line protocol. So keep a private copy of the real stdout for replies, and point
# file descriptor 1 (what C++ code writes to) at stderr instead.
_replies = os.fdopen(os.dup(1), "w")
os.dup2(2, 1)
sys.stdout = sys.stderr

HERE = Path(__file__).resolve().parent
# The worker's own modules, then the folder above for catalogue.py and valid.py.
sys.path[:0] = [str(HERE), str(HERE.parent)]

from session import Session  # must come after the path set-up above


def _send(reply: dict) -> None:
    _replies.write(json.dumps(reply) + "\n")
    _replies.flush()


def _handle(session: Session, request: dict) -> dict:
    op = request.get("op")
    if op == "command":
        return session.run(request["command"], request.get("args", {}))
    if op == "snapshot":
        return {"status": "ok", "snapshot": session.snapshot}
    if op == "reset":
        return session.reset()
    if op == "forget_undo":
        return session.forget_undo()
    if op == "configure":
        session.exact_undo = bool(request["exact_undo"])
        return {"status": "ok", "exact_undo": session.exact_undo}
    if op == "rebuild":
        return session.rebuild()
    if op == "tolerances":
        return {"status": "ok", "tolerances": session.tolerances(),
                "refreshes": session.refreshes}
    if op == "save":
        session.save(request["path"])
        return {"status": "ok"}
    if op == "objects":
        return {"status": "ok", "objects": session.objects()}
    if op == "measure_file":
        return {"status": "ok", "solid": session.measure_file(request["path"])}
    if op == "probe":
        return {"status": "ok", "inside": session.probe(request["points"])}
    if op == "stats":
        # ru_maxrss is the largest amount of memory the process has held: bytes on macOS,
        # kilobytes on Linux.
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"status": "ok", "pid": os.getpid(),
                "peak_rss_mb": peak / (1e6 if sys.platform == "darwin" else 1e3)}
    if op == "_crash":
        os._exit(13)
    if op == "_hang":
        time.sleep(3600)
    return {"status": "error", "reason": f"unknown op {op!r}"}


def main() -> None:
    session = Session()
    _send({"status": "ready", "pid": os.getpid()})
    for line in sys.stdin:
        started = time.perf_counter()
        try:
            reply = _handle(session, json.loads(line))
        except Exception as error:      # noqa: BLE001 - a broken request must not kill the loop
            reply = {"status": "error", "reason": f"{type(error).__name__}: {error}"}
        reply["ms"] = round((time.perf_counter() - started) * 1000, 3)
        _send(reply)
    os._exit(0)     # skip FreeCAD's slow shutdown; there is nothing to save


main()
