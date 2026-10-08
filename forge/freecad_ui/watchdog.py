"""Watch one hidden FreeCAD from outside and kill it when it must not go on.

    python watchdog.py <launcher pid> <marker in FreeCAD's command line> <seconds> <memory GB>

Kills (SIGKILL) every FreeCAD whose command line contains the marker when the
launcher is gone, the time is up, or its memory is over the cap. Exits when that
FreeCAD is gone. Standard library only; started by launch.py.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def pids_with(marker: str) -> list[int]:
    found = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True,
                           check=False).stdout.split()
    mine = []
    for pid in found:
        command = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True,
                                 text=True, check=False).stdout
        if "FreeCAD.app" in command and marker in command:
            mine.append(int(pid))
    return mine


def rss_bytes(pid: int) -> int:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True,
                         text=True, check=False).stdout.strip()
    return int(out) * 1024 if out.isdigit() else 0


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> None:
    parent, marker = int(sys.argv[1]), sys.argv[2]
    deadline = time.time() + float(sys.argv[3])
    cap = float(sys.argv[4]) * (1 << 30)
    seen = False
    started = time.time()
    while True:
        pids = pids_with(marker)
        seen = seen or bool(pids)
        if seen and not pids:
            return                      # it is gone: nothing left to watch
        if not seen and time.time() - started > 120:
            return                      # it never appeared
        stop = (not alive(parent) or time.time() > deadline
                or any(rss_bytes(pid) > cap for pid in pids))
        if stop:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        time.sleep(1.0)


if __name__ == "__main__":
    main()
