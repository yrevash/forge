"""The client for structures: the base FreeCADClient, started on our worker.

    with MultiClient() as fc:
        fc.command("new_document")
        fc.command("new_body")
        ...
        fc.command("move_body", x=0.0, y=0.0, z=450.0)

Everything the base client does (the sandbox, the time limit, restart and replay after
a crash, swapping the worker every so many documents) is inherited. The differences:

  * it starts forge/freecad_multi/inside/worker.py, which serves the 48 commands;
  * the valid-command list it computes itself (only after a double crash) uses the
    structure rules;
  * `label_bodies` and a `measure_file` that measures every body.

`WORKER` is a module global of the base client, read when a worker starts. Rather than
change that global (other code in this process may be using the base client), `start`
swaps it only for the moment of starting, under a lock.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from forge.freecad import client as base
from forge.freecad.client import FreeCADClient, FreeCADUnavailable, WorkerLost
from forge.freecad_multi.multi_valid import valid_commands

__all__ = ["FreeCADUnavailable", "MultiClient", "WorkerLost"]

WORKER = Path(__file__).resolve().parent / "inside" / "worker.py"
_starting = threading.Lock()


class MultiClient(FreeCADClient):
    """One warm FreeCAD worker that can build structures. One client per thread."""

    def start(self) -> None:
        with _starting:
            kept, base.WORKER = base.WORKER, WORKER
            try:
                super().start()
            finally:
                base.WORKER = kept

    def _service(self, payload: dict, with_valid: bool = False) -> dict:
        reply = super()._service(payload)
        if with_valid:
            return {"snapshot": reply["snapshot"], "valid": valid_commands(reply["snapshot"])}
        return reply

    def label_bodies(self, labels: list[str]) -> None:
        """Name the bodies, in the order they were made (shown in FreeCAD's model tree)."""
        self._service({"op": "label_bodies", "labels": labels})

    def measure_file(self, path: str | Path) -> list[dict]:
        """Open a saved .FCStd in the worker, recompute it and measure every body."""
        inside = Path(self._work_dir) / "reopened.FCStd"
        shutil.copyfile(path, inside)
        return self._service({"op": "measure_file", "path": str(inside)})["bodies"]

    def against_files(self, paths: list[Path | None]) -> list[dict | None]:
        """Each body (in the order made) against a reference BREP file: the volume they share."""
        inside: list[str | None] = []
        for number, path in enumerate(paths):
            if path is None:
                inside.append(None)
                continue
            copy = Path(self._work_dir) / f"reference_{number}.brep"
            shutil.copyfile(path, copy)
            inside.append(str(copy))
        return self._service({"op": "against_files", "paths": inside})["bodies"]
