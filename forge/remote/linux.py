"""Run our FreeCAD worker, and our CadQuery helper, on a Linux machine we rent or borrow.

Nothing in forge/freecad or forge/sandbox.py is changed for this. Two facts make it work:

  1. forge/freecad/locate.py already reads FORGE_FREECAD_PYTHON and FORGE_FREECAD_LIB, so
     `use_freecad` only has to set them.
  2. forge/freecad/client.py wraps the worker in macOS `sandbox-exec` only when that
     program exists. Linux has none, so the worker starts bare.

What is given up on Linux, and why that is acceptable HERE ONLY. The macOS sandbox takes
the network and all writes outside one temp folder away from the worker. It exists for
programs a MODEL wrote. A recorder runs only our own deterministic code: the worker obeys
a fixed catalogue of commands and runs nothing it is sent. On a throw-away Kaggle
container there is also nothing of the user's to protect. So for recording, running bare
is a fair trade. It is NOT for model output: forge/sandbox.py keeps refusing to start on
Linux, and `LinuxSandbox` below is for our own generator programs only.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from forge.sandbox import PROJECT_ROOT, STARTUP_TIMEOUT, Sandbox, SandboxUnavailable

# Where FreeCAD's interpreter and FreeCAD.so are, relative to the root of an install:
# the official AppImage unpacked with --appimage-extract, then a conda environment.
LAYOUTS = (("usr/bin/python", "usr/lib"), ("bin/python", "lib"))


def find_freecad(root: Path) -> tuple[Path, Path] | None:
    """(interpreter, folder of FreeCAD.so) under an unpacked FreeCAD, or None."""
    for python, lib in LAYOUTS:
        if (root / python).exists() and (root / lib / "FreeCAD.so").exists():
            return root / python, root / lib
    return None


def write_launcher(python: Path, lib: Path, where: Path) -> Path:
    """A two-line script that starts FreeCAD's interpreter with its libraries on the loader
    path. Only needed when the bare interpreter cannot find its shared libraries: the client
    hands the worker an almost empty environment, so this is the one place to add to it."""
    where.write_text(f'#!/bin/sh\nexport LD_LIBRARY_PATH="{lib}"\nexec "{python}" "$@"\n')
    where.chmod(where.stat().st_mode | stat.S_IXUSR)
    return where


def use_freecad(python: Path, lib: Path) -> None:
    """Point every FreeCADClient started from this process (and its children) at this FreeCAD."""
    os.environ["FORGE_FREECAD_PYTHON"] = str(python)
    os.environ["FORGE_FREECAD_LIB"] = str(lib)


def freecad_versions(python: Path, lib: Path) -> dict:
    """FreeCAD's and OpenCascade's versions, asked of the interpreter itself, in the same
    bare environment the client gives a worker. {"error": ...} if it does not import."""
    code = ("import json,sys,FreeCAD,Part;"
            "print('VERSIONS'+json.dumps({'freecad':FreeCAD.Version()[:4],"
            "'occ':Part.OCC_VERSION,'python':sys.version.split()[0]}))")
    env = {"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir(), "PYTHONPATH": str(lib)}
    done = subprocess.run([str(python), "-c", code], env=env, capture_output=True, text=True,
                          timeout=180, check=False)
    for line in done.stdout.splitlines():
        if line.startswith("VERSIONS"):
            import json
            return json.loads(line[len("VERSIONS"):])
    return {"error": (done.stderr or done.stdout)[-600:]}


class LinuxSandbox(Sandbox):
    """The CadQuery helper of forge/sandbox.py WITHOUT an operating-system sandbox.

    For our own generator programs only (the kernel judge of forge/resolve, the second build
    of forge/freecad/wide_parts.py): code a model wrote must never be run through this. The
    separate process, the time limit and the kill on a hang are all kept; the network and
    file-write bans are not, unless `unshare` can give the helper an empty network (tried
    once, used when the container allows it).
    """

    def __init__(self, timeout: float = 30.0) -> None:
        if sys.platform != "linux":
            raise SandboxUnavailable("LinuxSandbox is for Linux; use forge.sandbox.Sandbox")
        self.timeout = timeout
        self._proc = None
        self._work_dir = None
        self.no_network = _unshare_works()

    def _start(self) -> None:
        self._work_dir = os.path.realpath(tempfile.mkdtemp(prefix="forge-sandbox-"))
        env = {"PATH": "/usr/bin:/bin", "HOME": self._work_dir, "TMPDIR": self._work_dir,
               "PYTHONPATH": str(PROJECT_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
        command = [sys.executable, "-m", "forge._sandbox_server", self._work_dir]
        if self.no_network:
            command = ["unshare", "--user", "--net", *command]
        self._proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, env=env, cwd=self._work_dir,
                                      text=True)
        ready = self._read_reply(STARTUP_TIMEOUT)
        if ready is None or ready.get("status") != "ready":
            self.close()
            raise SandboxUnavailable("the CadQuery helper failed to start")


def _unshare_works() -> bool:
    """Can this container start a process with no network of its own? (Kaggle: measured.)"""
    if shutil.which("unshare") is None:
        return False
    done = subprocess.run(["unshare", "--user", "--net", "true"], capture_output=True,
                          check=False)
    return done.returncode == 0
