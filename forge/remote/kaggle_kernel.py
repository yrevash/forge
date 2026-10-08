"""The script a Kaggle kernel runs: install FreeCAD, then record shards with our recorders.

This file is pushed as the kernel's code (forge/remote/kernel_dir.py writes the folder).
It runs with plain Python and nothing of ours until it has unpacked `bundle.bin` (made by
forge/remote/bundle.py and attached as a private dataset). What it does is decided by the
one CONFIG line below, which kernel_dir.py rewrites:

    {"jobs": [{"tasks": "tasks_wide.json", "out": "wide", "workers": 4,
               "only": [0, 1], "max_minutes": 600}],          shards to record, in order
     "prove": 300,          also build this many single parts and compare with the stored solids
     "burn": 20}            also time a fixed pure-Python loop on 1 and on every core

Everything it finds out goes to /kaggle/working/report.json (rewritten after every stage,
so a kernel that dies late still leaves its early answers); shards go to
/kaggle/working/out/<job's out>/. FreeCAD itself is unpacked under /tmp, which Kaggle
does not keep.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
from pathlib import Path

CONFIG = {"jobs": [], "prove": 0, "burn": 20}

# The one thing this kernel downloads and executes: the official FreeCAD release the Mac
# records with, pinned by tag, asset name, size and SHA-256. The checksum was read on
# 8 Oct 2026 from two places that agreed: the release's own
# FreeCAD_1.1.4-Linux-x86_64-py311.AppImage-SHA256.txt, and the `digest` GitHub's API
# reports for the asset (api.github.com/repos/FreeCAD/FreeCAD/releases/tags/1.1.4, asset
# uploaded 2026-09-28). It is a constant here so that a file changed at the source is
# refused instead of run. A new release means a new constant, on purpose.
APPIMAGE_URL = ("https://github.com/FreeCAD/FreeCAD/releases/download/1.1.4/"
                "FreeCAD_1.1.4-Linux-x86_64-py311.AppImage")
APPIMAGE_SHA256 = "f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434"
APPIMAGE_BYTES = 820812280
# The CadQuery kernel the Mac uses (uv pip list, 8 Oct 2026). No fallback to another version.
PIP_PINS = ("cadquery==2.8.0", "cadquery-ocp==7.9.3.1.1")
WORK = Path("/kaggle/working") if Path("/kaggle/working").exists() else Path.cwd() / "working"
TEMP = Path("/tmp/forge_remote")
REPO = TEMP / "repo"
REPORT: dict = {"config": CONFIG, "started": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
BEGAN = time.time()


def note(key: str, value: object) -> None:
    """Keep one finding, and write the whole report again."""
    REPORT[key] = value
    REPORT["minutes_so_far"] = round((time.time() - BEGAN) / 60, 2)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "report.json").write_text(json.dumps(REPORT, indent=1, default=str) + "\n")
    print(f"== {key}: {json.dumps(value, default=str)[:1500]}", flush=True)


def run(command: list[str], timeout: int = 1800, cwd: Path | None = None,
        tail: int | None = 1500) -> tuple[int, str]:
    """Run one program with its arguments as a list (no shell reads them). Returns its exit
    code and the last `tail` characters it printed (None: all of it)."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              check=False, cwd=cwd)
    except FileNotFoundError as error:
        return 127, str(error)
    printed = done.stdout + done.stderr
    return done.returncode, printed if tail is None else printed[-tail:]


class ChecksumMismatch(RuntimeError):
    """A downloaded file is not the file we pinned. It has been deleted."""


def verified(path: Path, sha256: str, size: int | None = None) -> Path:
    """Return `path` only if it is the pinned file. Otherwise delete it and raise, so that
    nothing unverified is left on disk to be run by a later step or a later person.
    Callers make a file executable only AFTER this returned."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            digest.update(chunk)
    found, found_size = digest.hexdigest(), path.stat().st_size
    if found != sha256 or (size is not None and found_size != size):
        path.unlink()
        raise ChecksumMismatch(f"{path.name}: sha256 {found} ({found_size} bytes), "
                               f"pinned {sha256} ({size} bytes); the file was deleted")
    return path


def stage(name: str):
    """Run one stage; a failure is written down and the next stage still runs."""
    def wrap(function):
        started = time.time()
        try:
            note(name, function())
        except Exception:      # noqa: BLE001 - the report must say what broke
            note(name, {"failed": traceback.format_exc()[-2500:]})
        REPORT.setdefault("stage_seconds", {})[name] = round(time.time() - started, 1)
        return function
    return wrap


# --- what the machine is ---------------------------------------------------------------------

def _lines(path: str, wanted: tuple[str, ...]) -> list[str]:
    file = Path(path)
    return [line for line in file.read_text().splitlines()
            if line.startswith(wanted)] if file.exists() else []


def machine() -> dict:
    memory = {line.split(":")[0]: int(line.split()[1]) // 1024
              for line in Path("/proc/meminfo").read_text().splitlines()[:3]}
    cpu = _lines("/proc/cpuinfo", ("model name",))
    topology = [line for line in run(["lscpu"])[1].splitlines()
                if line.startswith(("Thread", "Core", "Socket", "CPU(s)"))]
    disk = {str(folder): f"{shutil.disk_usage(folder).free / 1e9:.0f} GB free"
            for folder in (WORK, Path("/tmp")) if folder.exists()}
    return {"nproc": os.cpu_count(), "usable_cores": len(os.sched_getaffinity(0)),
            "cpu_model": cpu[0].split(":", 1)[1].strip() if cpu else "?", "lscpu": topology,
            "cgroup_cpu_max": "".join(_lines("/sys/fs/cgroup/cpu.max", ("",))),
            "memory_mb": memory, "disk": disk, "python": sys.version.split()[0],
            "os": "".join(_lines("/etc/os-release", ("PRETTY_NAME",))),
            "gpu": run(["nvidia-smi", "-L"])[1].strip()[:200],
            "kernel_type": os.environ.get("KAGGLE_KERNEL_RUN_TYPE")}


def _loop(_: int) -> float:
    started = time.perf_counter()
    total = 0
    for number in range(6_000_000):
        total += number * number % 7
    return time.perf_counter() - started


def burn(seconds_wanted: float) -> dict:
    """The same loop on one core, then on every core at once, repeated for a while. If a
    neighbour kernel takes cores from us, the all-core time per loop goes up."""
    from concurrent.futures import ProcessPoolExecutor
    cores = len(os.sched_getaffinity(0))
    alone = _loop(0)
    together, started = [], time.time()
    with ProcessPoolExecutor(cores) as pool:
        while time.time() - started < seconds_wanted:
            together.append(max(pool.map(_loop, range(cores))))
    return {"one_core_seconds": round(alone, 2), "rounds": len(together),
            "all_cores_seconds_first": round(together[0], 2),
            "all_cores_seconds_last": round(together[-1], 2),
            "all_cores_seconds_worst": round(max(together), 2),
            "at": time.strftime("%H:%M:%S")}


# --- our code and FreeCAD ----------------------------------------------------------------------

def unpack_bundle() -> dict:
    found = glob.glob("/kaggle/input/**/bundle.bin", recursive=True)
    if not found:
        raise FileNotFoundError(f"no bundle.bin under /kaggle/input: {os.listdir('/kaggle/input')}")
    REPO.mkdir(parents=True, exist_ok=True)
    with tarfile.open(found[0], "r:gz") as tar:
        tar.extractall(REPO, filter="data")
    sys.path.insert(0, str(REPO))
    os.environ["PYTHONPATH"] = str(REPO)        # for the worker processes of the recorders
    return json.loads((REPO / "MANIFEST.json").read_text()) | {"bundle": found[0]}


def install_appimage() -> dict:
    """The official release, unpacked: the same version and the same bundled Python as the
    Mac. Download to a file, verify against the pinned checksum, and only then run it."""
    folder = TEMP / "appimage"
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / "FreeCAD.AppImage"
    started = time.time()
    code, out = run(["curl", "-L", "--fail", "-sS", "--proto", "=https", "-o", str(image),
                     APPIMAGE_URL])
    if code != 0:
        raise RuntimeError(f"download failed: {out}")
    downloaded = time.time() - started
    verified(image, APPIMAGE_SHA256, APPIMAGE_BYTES)        # raises and deletes on a mismatch
    image.chmod(0o700)
    started = time.time()
    code, out = run([str(image), "--appimage-extract"], cwd=folder)
    if code != 0:
        raise RuntimeError(f"--appimage-extract failed: {out[-600:]}")
    image.unlink()
    return {"root": str(folder / "squashfs-root"), "url": APPIMAGE_URL,
            "sha256_verified": APPIMAGE_SHA256, "bytes": APPIMAGE_BYTES,
            "download_seconds": round(downloaded, 1),
            "unpack_seconds": round(time.time() - started, 1)}


def freecad() -> dict:
    """Install, find the interpreter, and prove that it imports FreeCAD the way a worker
    will be started: bare first, then with a launcher that adds the library folder.

    There is no second way to install. The probe of 8 Oct 2026 found the AppImage to work
    bare; a conda-forge fallback was written, never needed, and removed rather than kept as
    an unpinned download nobody had run."""
    from forge.remote import linux
    installed = install_appimage()
    found = linux.find_freecad(Path(installed["root"]))
    if found is None:
        raise RuntimeError(f"no interpreter with FreeCAD.so under {installed['root']}: "
                           f"{sorted(os.listdir(installed['root']))[:30]}")
    python, lib = found
    versions = linux.freecad_versions(python, lib)
    how = "bare"
    if "error" in versions:
        installed["bare_error"] = versions["error"][-400:]
        python = linux.write_launcher(python, lib, TEMP / "freecad_python.sh")
        versions = linux.freecad_versions(python, lib)
        how = "launcher (LD_LIBRARY_PATH)"
    if "error" in versions:
        raise RuntimeError(json.dumps(installed | {"versions": versions})[:2400])
    linux.use_freecad(python, lib)
    return installed | {"python": str(python), "lib": str(lib), "started": how,
                        "versions": versions}


def worker() -> dict:
    """Start one worker, build a pad, and ask for its memory."""
    from forge.freecad.client import FreeCADClient
    started = time.time()
    fc = FreeCADClient()
    fc.start()
    ready = time.time() - started
    replies = [fc.command("new_document")["status"], fc.command("new_body")["status"],
               fc.command("select_plane", plane="XY")["status"]]
    stats = fc.stats()
    sandboxed = fc.sandboxed
    fc.close()
    return {"start_seconds": round(ready, 2), "first_commands": replies,
            "peak_rss_mb": round(stats["peak_rss_mb"]), "macos_sandbox": sandboxed}


def cadquery() -> dict:
    """The CadQuery kernel, for the structure recorder's judge: the Mac's exact versions or
    nothing. (`--require-hashes` is not used: it would need the hash of every wheel of every
    dependency for this platform, about thirty files that Kaggle's own image partly supplies;
    the versions that were resolved are written into the report instead.)"""
    from forge.remote import linux
    started = time.time()
    code, out = run([sys.executable, "-m", "pip", "install", "-q", *PIP_PINS])
    if code != 0:
        raise RuntimeError(f"pip could not install {PIP_PINS}: {out[-600:]}")
    listed = run([sys.executable, "-m", "pip", "list", "--format", "json",
                  "--disable-pip-version-check"], tail=None)[1]
    try:
        resolved = {row["name"]: row["version"]
                    for row in json.loads(listed[listed.index("["):listed.rindex("]") + 1])
                    if row["name"].lower().startswith(("cadquery", "numpy", "scipy", "ezdxf",
                                                       "nlopt", "casadi", "typish",
                                                       "multimethod", "vtk"))}
    except ValueError:
        resolved = {"unreadable": listed[-300:]}
    result = {"pins": list(PIP_PINS), "resolved": resolved,
              "install_seconds": round(time.time() - started, 1),
              "unshare_gives_no_network": linux._unshare_works()}
    box = linux.LinuxSandbox(timeout=60)
    reply = box.run("import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 30)")
    box.close()
    result["box_10x20x30"] = {key: reply.get(key) for key in ("status", "error")} | {
        "volume": (reply.get("measure") or {}).get("volume")}
    return result


def prove(count: int) -> dict:
    """forge.freecad.prove on the bundle's single parts: FreeCAD here against the solids the
    Mac's CadQuery kernel stored (bounding box 0.001 mm, volume 1e-6)."""
    from concurrent.futures import ThreadPoolExecutor

    from forge.freecad import prove as proof
    tasks = json.loads((REPO / "tasks_single.json").read_text())["tasks"]
    rows = [part for task in tasks for part in task[2]][:count]
    started = time.time()
    with ThreadPoolExecutor(max_workers=len(os.sched_getaffinity(0))) as pool:
        results = list(pool.map(proof.prove_one, rows))
    seconds = time.time() - started
    for client in proof._clients:
        client.close()
    failures = [result for result in results if result["problems"]]
    steps = [step for result in results for step in result["steps"]]
    return {"parts": len(results), "same_solid": len(results) - len(failures),
            "different": len(failures), "steps": len(steps),
            "steps_matching_the_reference": sum(ok for _, ok in steps),
            "commands": sum(result["commands"] for result in results),
            "seconds": round(seconds, 1), "parts_per_second": round(len(results) / seconds, 2),
            "worker_restarts": sum(client.restarts for client in proof._clients),
            "examples": [[r["id"], r["family"], r["problems"][:3]] for r in failures[:10]]}


def main() -> None:
    note("machine", machine())
    if CONFIG.get("burn"):
        stage("burn_before")(lambda: burn(CONFIG["burn"]))
    if CONFIG.get("jobs") or CONFIG.get("prove"):
        note("bundle", unpack_bundle())
        note("freecad", freecad())
        stage("worker")(worker)
        if any("multi" in job["tasks"] for job in CONFIG["jobs"]):
            stage("cadquery")(cadquery)
    if CONFIG.get("prove"):
        stage("prove")(lambda: prove(CONFIG["prove"]))
    for at, job in enumerate(CONFIG.get("jobs", [])):
        def run(job: dict = job) -> dict:
            from forge.remote.record import record
            return record(REPO / job["tasks"], WORK / "out" / job["out"], job["workers"],
                          job.get("max_minutes"), job.get("only"))
        stage(f"job{at}:{job['out']}:{job['workers']}w")(run)
    if CONFIG.get("burn"):
        stage("burn_after")(lambda: burn(CONFIG["burn"]))
    note("finished", time.strftime("%Y-%m-%d %H:%M:%S %Z"))


if __name__ == "__main__":
    main()
