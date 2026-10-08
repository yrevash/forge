"""Run CadQuery programs we did not write, safely.

Programs from datasets and from models are arbitrary Python. They never run in
the main process. This module starts a helper process inside
the operating system's sandbox and talks to it over a pipe:

- no network at all;
- no file writes outside one temp directory;
- no reading the user's home directory, except this project;
- a time limit per program, enforced by killing the process.

Use `Sandbox` for many programs (the helper stays warm, so each program costs
milliseconds of overhead) or `run_code` for a single one.
"""

from __future__ import annotations

import json
import os
import platform
import select
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Self

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TIMEOUT = 30.0
STARTUP_TIMEOUT = 120.0
# Extra time the outer process waits beyond the program's own limit before it
# decides the helper itself is stuck and replaces it.
GRACE_SECONDS = 10.0

# macOS sandbox profile. Later rules win, so: allow by default, then take away
# the network, all writes, and reads of the home directory, then give back only
# what the helper needs.
_PROFILE = """
(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{work_dir}"))
(allow file-write* (literal "/dev/null") (literal "/dev/dtracehelper"))
(deny file-read* (subpath "{home}"))
(allow file-read* (subpath "{project}") (subpath "{python_home}") (subpath "{work_dir}"))
(allow file-read-metadata (subpath "{home}"))
"""


class SandboxUnavailable(RuntimeError):
    """This machine has no supported OS sandbox, so untrusted code cannot run."""


class Sandbox:
    """A warm, sandboxed helper process that runs one program at a time."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        if platform.system() != "Darwin" or shutil.which("sandbox-exec") is None:
            # Linux needs its own isolation.
            raise SandboxUnavailable("only the macOS sandbox is implemented so far")
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._work_dir: str | None = None

    def __enter__(self) -> Self:
        self._start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _start(self) -> None:
        self._work_dir = os.path.realpath(tempfile.mkdtemp(prefix="forge-sandbox-"))
        profile = _PROFILE.format(
            work_dir=self._work_dir,
            home=os.path.realpath(Path.home()),
            project=PROJECT_ROOT,
            python_home=os.path.realpath(sys.base_prefix),
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": self._work_dir,
            "TMPDIR": self._work_dir,
            "PYTHONPATH": str(PROJECT_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self._proc = subprocess.Popen(
            ["sandbox-exec", "-p", profile, sys.executable, "-m", "forge._sandbox_server",
             self._work_dir],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=self._work_dir,
            text=True,
        )
        ready = self._read_reply(STARTUP_TIMEOUT)
        if ready is None or ready.get("status") != "ready":
            self.close()
            raise SandboxUnavailable("sandbox helper failed to start")

    def _read_reply(self, wait: float) -> dict | None:
        assert self._proc is not None and self._proc.stdout is not None
        ready, _, _ = select.select([self._proc.stdout], [], [], wait)
        if not ready:
            return None
        line = self._proc.stdout.readline()
        return json.loads(line) if line else None

    def run(self, code: str, timeout: float | None = None, check_export: bool = False,
            svg: bool = False, structure: bool = False) -> dict:
        """Run one program. Always returns a dict with a `status`:

        ok        ran and left a solid; `measure` holds the measurements
        error     raised an exception (syntax errors included)
        no_result ran but left no solid in `result`
        timeout   exceeded the time limit
        crash     the kernel or interpreter died

        With `check_export`, an ok reply also carries `step_export_ok`; with `svg`,
        a line drawing of the part in `svg`; with `structure`, `measure` also holds
        the per-part measurements of a multi-part result (forge/assembly_geometry.py).
        """
        limit = self.timeout if timeout is None else timeout
        if self._proc is None or self._proc.poll() is not None:
            self.close()
            self._start()
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps({"code": code, "timeout": limit, "check_export": check_export,
                                               "svg": svg, "structure": structure}) + "\n")
            self._proc.stdin.flush()
            reply = self._read_reply(limit + GRACE_SECONDS)
        except (BrokenPipeError, ValueError):
            reply = None
        if reply is None:
            # The helper itself hung or died. Replace it so the next job is clean.
            self.close()
            return {"status": "crash", "error": "sandbox helper stopped responding"}
        return reply

    def close(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
            self._proc = None
        if self._work_dir is not None:
            shutil.rmtree(self._work_dir, ignore_errors=True)
            self._work_dir = None


def run_code(code: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Run a single program in a fresh sandbox. Slow to start; fine for one-offs."""
    with Sandbox(timeout=timeout) as sandbox:
        return sandbox.run(code)
