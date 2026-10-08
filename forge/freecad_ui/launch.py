"""Start one hidden FreeCAD window of our own, and make sure it can always be stopped.

    instance = Instance()
    instance.start()            # a second FreeCAD, hidden, with its own settings folder
    instance.socket_path        # where its in-app server listens
    instance.kill()

Why each flag of macOS `open` is there
  -n   a NEW instance, never the FreeCAD the user may have open
  -g   do not bring it to the front
  -j   start it hidden (no window appears, no icon jumps to the front)
  --env FREECAD_USER_HOME=<temp folder>   its settings, recovery files and temp files live
       in a folder we make and delete, so the user's own FreeCAD settings are never touched.
We write that folder's `user.cfg` before FreeCAD starts: English interface, the
PartDesign workbench, no start page, no animations, no auto-save.

Finding OUR process: the instance is started with a stub macro whose path contains
the instance's unique temp folder. Only processes whose command line contains that
path are ever measured or killed.

Stopping it, three ways (so a stuck or orphaned FreeCAD cannot outlive us):
  1. `kill()` here;
  2. the in-app self guard (inside/server.py): deadline, memory cap, launcher gone;
  3. a watchdog process (watchdog.py) that does the same from outside, in case the
     interface thread itself is stuck.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

APP = Path(os.environ.get("FORGE_FREECAD_APP", "/Applications/FreeCAD.app"))
INSIDE = Path(__file__).resolve().parent / "inside"
WATCHDOG = Path(__file__).resolve().parent / "watchdog.py"
STARTUP_TIMEOUT = 90.0
SOCKET_NAME = "s.sock"
MACRO_NAME = "forge_ui_boot.FCMacro"

# Preferences written before FreeCAD starts. (group path under BaseApp/Preferences, type, name, value)
PREFERENCES = [
    ("General", "Text", "Language", "English"),
    ("General", "Text", "AutoloadModule", "PartDesignWorkbench"),
    ("General", "Bool", "ConfirmAll", "0"),
    ("View", "Bool", "UseNavigationAnimations", "0"),     # the camera jumps; clicks never land mid-move
    ("Document", "Bool", "AutoSaveEnabled", "0"),
    ("Document", "Bool", "RecoveryEnabled", "0"),
    ("Document", "Bool", "CreateBackupFiles", "0"),
    ("Document", "Int", "MaxUndoSize", "200"),
    ("Mod/Start", "Bool", "ShowOnStartup", "0"),
    # Sketcher on-view fields: 2 = show position fields (x, y) as well as size fields.
    ("Mod/Sketcher/Tools", "Int", "OnViewParameterVisibility", "2"),
]


def freecad_gui_available() -> bool:
    return sys.platform == "darwin" and (APP / "Contents").exists() and shutil.which("open") is not None


def _user_cfg() -> str:
    """The preferences above as FreeCAD's parameter XML (nested groups)."""
    tree: dict = {}
    for path, kind, name, value in PREFERENCES:
        node = tree
        for part in path.split("/"):
            node = node.setdefault(("group", part), {})
        node[("value", name)] = (kind, value)

    def render(node: dict, indent: str) -> str:
        out = ""
        for (what, name), child in node.items():
            if what == "group":
                out += f'{indent}<FCParamGroup Name="{name}">\n{render(child, indent + "  ")}{indent}</FCParamGroup>\n'
            elif child[0] == "Text":
                out += f'{indent}<FCText Name="{name}">{child[1]}</FCText>\n'
            else:
                out += f'{indent}<FC{child[0]} Name="{name}" Value="{child[1]}"/>\n'
        return out

    body = render({("group", "Preferences"): tree}, "      ")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n<FCParameters>\n'
            '  <FCParamGroup Name="Root">\n    <FCParamGroup Name="BaseApp">\n'
            f'{body}    </FCParamGroup>\n  </FCParamGroup>\n</FCParameters>\n')


def _pids_with(marker: str) -> list[int]:
    """Process ids of FreeCAD instances whose command line contains `marker`."""
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True,
                           check=False).stdout.split()
    mine = []
    for pid in found:
        command = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True,
                                 text=True, check=False).stdout
        if "FreeCAD.app" in command and marker in command:
            mine.append(int(pid))
    return mine


def rss_mb(pid: int) -> float:
    """Resident memory of a process in megabytes (0 if it is gone)."""
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True,
                         text=True, check=False).stdout.strip()
    return int(out) / 1024 if out.isdigit() else 0.0


class Instance:
    """One hidden FreeCAD of our own."""

    def __init__(self, lifetime_s: float = 3600.0, memory_gb: float = 3.0,
                 debug: bool = False, paint: bool = False) -> None:
        self.lifetime_s = lifetime_s
        self.memory_gb = memory_gb
        self.debug = debug              # allows the "exec" op (development only)
        self.paint = paint              # let the hidden window draw itself (slower)
        self.folder: Path | None = None
        self._open: subprocess.Popen | None = None
        self._watchdog: subprocess.Popen | None = None

    @property
    def socket_path(self) -> str:
        return str(self.folder / SOCKET_NAME)

    @property
    def log_path(self) -> Path:
        return self.folder / "freecad.log"

    def start(self) -> None:
        if not freecad_gui_available():
            raise RuntimeError("FreeCAD.app was not found (set FORGE_FREECAD_APP)")
        # A short path: a Unix socket's path may be at most 104 characters on macOS.
        self.folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="fui-", dir="/tmp")))
        home = self.folder / "home"
        home.mkdir()
        (home / "user.cfg").write_text(_user_cfg())
        macro = self.folder / MACRO_NAME
        macro.write_text(f"import sys\nsys.path.insert(0, {str(INSIDE)!r})\nimport boot\n")
        env = {"FREECAD_USER_HOME": str(home), "FORGE_UI_SOCK": self.socket_path,
               "FORGE_UI_TIMEOUT": str(self.lifetime_s), "FORGE_UI_MEM_GB": str(self.memory_gb),
               "FORGE_UI_PARENT": str(os.getpid()), "FORGE_UI_DEBUG": "1" if self.debug else "0",
               "FORGE_UI_PAINT": "1" if self.paint else "0",
               "PYTHONDONTWRITEBYTECODE": "1"}
        command = ["open", "-n", "-g", "-j", "-W", "--stdout", str(self.log_path),
                   "--stderr", str(self.log_path)]
        for key, value in env.items():
            command += ["--env", f"{key}={value}"]
        command += ["-a", str(APP), "--args", str(macro)]
        self._open = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        self._watchdog = subprocess.Popen(
            [sys.executable, str(WATCHDOG), str(os.getpid()), str(macro), str(self.lifetime_s),
             str(self.memory_gb)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        end = time.time() + STARTUP_TIMEOUT
        while not os.path.exists(self.socket_path):
            if time.time() > end:
                self.kill()
                raise RuntimeError("the hidden FreeCAD did not start its server in time")
            time.sleep(0.1)

    def pids(self) -> list[int]:
        return _pids_with(str(self.folder / MACRO_NAME)) if self.folder else []

    def memory_mb(self) -> float:
        return sum(rss_mb(pid) for pid in self.pids())

    def alive(self) -> bool:
        return bool(self.pids())

    def kill(self) -> None:
        """Stop the instance (SIGKILL: no crash report, no dialog) and delete its folder."""
        if self.folder is None:
            return
        for _ in range(20):
            pids = self.pids()
            if not pids:
                break
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.1)
        for process in (self._open, self._watchdog):
            if process is not None and process.poll() is None:
                process.kill()
            if process is not None:
                process.wait()
        self._open = self._watchdog = None
        shutil.rmtree(self.folder, ignore_errors=True)
        self.folder = None
