"""The worker for structures: FreeCAD's own Python, one request per line, several bodies.

Started by forge/freecad_multi/client.py exactly as the base worker is started
(forge/freecad/inside/worker.py), and it speaks the same protocol. It is a separate
entry script because the base one starts a single-part `Session` the moment it is
loaded. This one

  1. puts the base worker's folders on the path and loads its modules UNCHANGED
     (session, shapes, features, rules, snapshot);
  2. hands the base session the extended catalogue and rules (see below);
  3. serves a `MultiSession`, which inherits every base command and adds ours.

Step 2 in detail. The base session asks two modules it has imported under the names
`catalogue` and `valid`: "is this a command, and are its arguments right?" and "is it
available now?". We point those two names, inside this process only, at
multi_catalogue and multi_valid, which answer for all 48 commands and pass the base
questions on to the base files. No base file is edited, and a base worker running at
the same time (another process) is not affected.

Extra requests beyond the base ones
    {"op": "configure", "exact_undo": true}   exact undo, as the base worker has it
    {"op": "label_bodies", "labels": [...]}   name the bodies for a person (before saving)
    {"op": "measure_file", "path": "..."}     measure EVERY body of a saved file
    {"op": "against_files", "paths": [...]}   each body against a reference solid (BREP file)
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
from pathlib import Path

# FreeCAD prints to stdout when it likes; keep a private copy of the real stdout for
# replies and send everything else to stderr (same as the base worker).
_replies = os.fdopen(os.dup(1), "w")
os.dup2(2, 1)
sys.stdout = sys.stderr

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[1] / "freecad"
# Our worker modules, the base worker's, the base catalogue's folder, then ours. The two
# folders that also hold tools (bench.py, recipes.py ...) come last so that no name of
# theirs can hide a worker module; our own files there are called multi_*.
sys.path[:0] = [str(HERE), str(BASE / "inside"), str(BASE), str(HERE.parent)]

import multi_catalogue  # must come after the path set-up above
import multi_valid
import session as base_session

base_session.catalogue = multi_catalogue    # step 2: the base session now knows 48 commands
base_session.valid = multi_valid

from multi_session import MultiSession


def _send(reply: dict) -> None:
    _replies.write(json.dumps(reply) + "\n")
    _replies.flush()


def _handle(session: MultiSession, request: dict) -> dict:
    op = request.get("op")
    if op == "command":
        return session.run(request["command"], request.get("args", {}))
    if op == "snapshot":
        return {"status": "ok", "snapshot": session.snapshot}
    if op == "reset":
        return session.reset()
    if op == "forget_undo":
        return session.forget_undo()
    if op == "configure":       # exact undo, as in the base worker (multi_session._refresh)
        session.exact_undo = bool(request["exact_undo"])
        return {"status": "ok", "exact_undo": session.exact_undo}
    if op == "save":
        session.save(request["path"])
        return {"status": "ok"}
    if op == "label_bodies":
        session.label_bodies(request["labels"])
        return {"status": "ok"}
    if op == "objects":
        return {"status": "ok", "objects": session.objects()}
    if op == "measure_file":
        return {"status": "ok", "bodies": session.measure_file(request["path"])}
    if op == "against_files":
        return {"status": "ok", "bodies": session.against_files(request["paths"])}
    if op == "stats":
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss      # bytes on macOS
        return {"status": "ok", "pid": os.getpid(),
                "peak_rss_mb": peak / (1e6 if sys.platform == "darwin" else 1e3)}
    if op == "_crash":
        os._exit(13)        # a simulated crash: no macOS crash dialog
    if op == "_hang":
        time.sleep(3600)
    return {"status": "error", "reason": f"unknown op {op!r}"}


def main() -> None:
    session = MultiSession()
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
