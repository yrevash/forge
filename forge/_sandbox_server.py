"""The process that actually runs untrusted CadQuery code. Started by forge.sandbox.

It is launched under the operating system's sandbox (no network, no file writes
outside one temp dir). It imports CadQuery once, which is slow, then for every
job forks a child that runs one program. The fork is what makes a hang, a crash
or a memory blow-up in one program harmless to the next: the parent just kills
the child and reports what happened.

Protocol: one JSON object per line on stdin ({"code": str, "timeout": float,
"check_export": bool, "svg": bool, "structure": bool}),
one JSON object per line on stdout (the result).
"""

from __future__ import annotations

import json
import os
import resource
import select
import signal
import sys
import time
import traceback

MAX_REPLY_BYTES = 4_000_000


def _export_step_ok(result: object) -> bool:
    """Can the result be written as a STEP file? The file is deleted straight away."""
    import cadquery as cq

    from forge.geometry import to_shape

    path = f"export-{os.getpid()}.step"
    try:
        cq.exporters.export(to_shape(result), path)
        return os.path.getsize(path) > 0
    except Exception:  # noqa: BLE001 - any export failure means "no"
        return False
    finally:
        if os.path.exists(path):
            os.remove(path)


SVG_OPTIONS = {"width": 320, "height": 320, "marginLeft": 12, "marginTop": 12,
               "showAxes": False, "projectionDir": (1.0, -1.2, 0.9), "showHidden": False,
               "strokeWidth": -1.0}


def _svg(result: object) -> str | None:
    """A line drawing of the result, for contact sheets. None if it cannot be drawn."""
    import cadquery as cq

    from forge.geometry import to_shape

    try:
        return cq.exporters.getSVG(to_shape(result), SVG_OPTIONS)
    except Exception:  # noqa: BLE001 - a picture is optional
        return None


def _run_in_child(code: str, timeout: float, write_fd: int, check_export: bool,
                  want_svg: bool, want_structure: bool = False) -> None:
    """Runs inside the forked child. Never returns."""
    reply: dict
    try:
        # A hard CPU cap as a second line of defence behind the parent's kill.
        # The kernel runs some operations on several threads, so CPU seconds can
        # pile up faster than wall-clock seconds; the cap is set well above the
        # wall-clock limit so that only the parent's timer decides a timeout.
        cpu = int(timeout) * 8 + 2
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(devnull, fd)

        from forge.geometry import NotASolid, measure

        namespace: dict = {"__name__": "__main__"}
        try:
            exec(compile(code, "<program>", "exec"), namespace)  # noqa: S102 - sandboxed
        except BaseException as exc:  # noqa: BLE001 - report every failure kind
            last = traceback.extract_tb(exc.__traceback__)[-1]
            reply = {
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "line": last.lineno if last.filename == "<program>" else None,
            }
        else:
            if "result" not in namespace:
                reply = {"status": "no_result", "error": "program did not set `result`"}
            else:
                try:
                    reply = {"status": "ok", "measure": measure(namespace["result"],
                                                               structure=want_structure)}
                    if check_export:
                        reply["step_export_ok"] = _export_step_ok(namespace["result"])
                    if want_svg:
                        reply["svg"] = _svg(namespace["result"])
                except NotASolid as exc:
                    reply = {"status": "no_result", "error": str(exc)}
                except Exception as exc:  # noqa: BLE001 - kernel errors while measuring
                    reply = {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error": f"while measuring: {str(exc)[:480]}",
                        "line": None,
                    }
    except BaseException as exc:  # noqa: BLE001
        reply = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)[:500]}
    try:
        os.write(write_fd, json.dumps(reply).encode()[:MAX_REPLY_BYTES])
    finally:
        os._exit(0)


def _run_job(code: str, timeout: float, check_export: bool, want_svg: bool,
             want_structure: bool = False) -> dict:
    read_fd, write_fd = os.pipe()
    started = time.monotonic()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        _run_in_child(code, timeout, write_fd, check_export, want_svg, want_structure)
    os.close(write_fd)

    chunks: list[bytes] = []
    timed_out = False
    while True:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            timed_out = True
            break
        ready, _, _ = select.select([read_fd], [], [], remaining)
        if not ready:
            timed_out = True
            break
        chunk = os.read(read_fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)

    if timed_out:
        os.kill(pid, signal.SIGKILL)
    _, wait_status = os.waitpid(pid, 0)
    seconds = round(time.monotonic() - started, 3)

    if timed_out:
        return {"status": "timeout", "error": f"exceeded {timeout}s", "seconds": seconds}
    if os.WIFSIGNALED(wait_status):
        sig = os.WTERMSIG(wait_status)
        status = "timeout" if sig == signal.SIGXCPU else "crash"
        return {"status": status, "error": f"killed by signal {sig}", "seconds": seconds}
    try:
        reply = json.loads(b"".join(chunks))
    except ValueError:
        return {"status": "crash", "error": "child exited without a result", "seconds": seconds}
    reply["seconds"] = seconds
    return reply


def main() -> None:
    work_dir = sys.argv[1]
    os.chdir(work_dir)
    import cadquery  # noqa: F401 - paid once here, inherited by every forked child

    import forge.geometry  # noqa: F401

    sys.stdout.write(json.dumps({"status": "ready"}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        job = json.loads(line)
        reply = _run_job(job["code"], float(job["timeout"]), bool(job.get("check_export")),
                         bool(job.get("svg")), bool(job.get("structure")))
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
