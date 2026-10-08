"""The script that runs on Kaggle. forge/s1/kaggle_pack.py uploads it as a private kernel.

It does three things: finds the two private datasets (the prepared arrays and the code),
runs forge/s1/train.py on the GPU, and leaves the run folder in /kaggle/working/run, which
`kaggle kernels output` downloads. The settings below are filled in by kaggle_pack.py.

With MORE_CONFIGS (a comparison run) every config trains at the same time on a
GPU of its own: a Kaggle "T4 x2" session has two, and one model uses one. Their run folders
are /kaggle/working/run, run2, ...; each writes its own train.log. With fewer GPUs than
configs they run one after the other.

If /kaggle/working/run/last.pt is absent and a dataset holds an earlier run's last.pt, the
run is resumed from it (that is how a run longer than one session would be carried on).
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

CONFIG = "configs/s1_first.yaml"    # {CONFIG}
MAX_STEPS = None                    # {MAX_STEPS}: optimiser steps, None = what the config says
MAX_MINUTES = 480                   # {MAX_MINUTES}: save and stop after this long
COMMIT = "unknown"                  # {COMMIT}: the git commit of the uploaded code
MORE_CONFIGS = []                   # {MORE_CONFIGS}: configs trained beside CONFIG
OUT = Path("/kaggle/working")       # what is left here is the kernel's output
POLL_SECONDS = 300


def find(file_name: str) -> Path:
    """The folder under /kaggle/input that holds this file (dataset layouts vary)."""
    found = sorted(Path("/kaggle/input").rglob(file_name), key=lambda path: len(path.parts))
    if not found:
        raise SystemExit(f"{file_name} was not found under /kaggle/input")
    return found[0].parent


def main() -> None:
    subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv"], check=False)
    for path in sorted(Path("/kaggle/input").glob("*/*"))[:12]:     # how the datasets arrived
        print(path)
    data = find("manifest.json")
    package = find("train.py").parent                       # the folder .../forge
    work = Path("/kaggle/working/code")
    shutil.copytree(package, work / "forge", dirs_exist_ok=True)
    shutil.copytree(find(Path(CONFIG).name), work / "configs", dirs_exist_ok=True)
    print(f"data: {data}\ncode: {package}\ncommit: {COMMIT}", flush=True)

    if MORE_CONFIGS:
        several(data, work)
        shutil.rmtree(work)
        return
    command = [sys.executable, "-m", "forge.s1.train", "--data", str(data),
               "--run-dir", "/kaggle/working/run", "--max-minutes", str(MAX_MINUTES)]
    earlier = sorted(Path("/kaggle/input").rglob("last.pt"))
    if earlier:
        shutil.copytree(earlier[0].parent, "/kaggle/working/run", dirs_exist_ok=True)
        command += ["--resume", "/kaggle/working/run/last.pt"]
    else:
        command += ["--config", CONFIG]
        if MAX_STEPS:
            command += ["--max-steps", str(MAX_STEPS)]
    # One GPU only: the second T4 of a "T4 x2" session stays idle.
    env = {**os.environ, "FORGE_COMMIT": COMMIT, "CUDA_VISIBLE_DEVICES": "0"}
    subprocess.run(command, cwd=work, env=env, check=True)
    shutil.rmtree(work)                                     # only the run folder is output


def gpu_count() -> int:
    found = subprocess.run(["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
                           capture_output=True, text=True, check=False)
    return len(found.stdout.split()) if found.returncode == 0 else 0


def several(data: Path, work: Path) -> None:
    """CONFIG and MORE_CONFIGS, each a fresh run on one GPU; side by side when there are
    enough GPUs. Every run gets MAX_MINUTES. Fails at the end if any of them failed."""
    configs = [CONFIG, *MORE_CONFIGS]
    gpus = max(1, gpu_count())
    running: list[tuple[str, subprocess.Popen, Path]] = []
    failed = []

    def wait_for(batch: list) -> None:
        while any(process.poll() is None for _, process, _ in batch):
            time.sleep(POLL_SECONDS)
            for config, _, log in batch:                    # one line each, every five minutes
                lines = log.read_text().splitlines() if log.exists() else []
                print(f"[{config}] {lines[-1] if lines else '(nothing yet)'}", flush=True)
        failed.extend(config for config, process, _ in batch if process.returncode != 0)

    for number, config in enumerate(configs):
        run_dir = OUT / ("run" if number == 0 else f"run{number + 1}")
        run_dir.mkdir(parents=True, exist_ok=True)
        log = run_dir / "train.log"
        command = [sys.executable, "-m", "forge.s1.train", "--data", str(data), "--config",
                   config, "--run-dir", str(run_dir), "--max-minutes", str(MAX_MINUTES)]
        if MAX_STEPS:
            command += ["--max-steps", str(MAX_STEPS)]
        env = {**os.environ, "FORGE_COMMIT": COMMIT, "CUDA_VISIBLE_DEVICES": str(number % gpus)}
        print(f"starting {config} on GPU {number % gpus} -> {run_dir}", flush=True)
        with log.open("w") as out:
            running.append((config, subprocess.Popen(command, cwd=work, env=env, stdout=out,
                                                     stderr=subprocess.STDOUT), log))
        # Reading the arrays takes twice their size in memory for a moment. Let this run
        # finish reading (its first line names the device) before the next one starts.
        waited = 0
        while running[-1][1].poll() is None and "device " not in log.read_text() \
                and waited < 1800:
            time.sleep(min(POLL_SECONDS, 10))
            waited += min(POLL_SECONDS, 10)
        if len(running) == gpus or number == len(configs) - 1:
            wait_for(running)
            running = []
    for number in range(len(configs)):                      # the end of every log, for the record
        log = OUT / ("run" if number == 0 else f"run{number + 1}") / "train.log"
        print(f"--- {configs[number]} ---\n" + "\n".join(log.read_text().splitlines()[-25:]),
              flush=True)
    if failed:
        raise SystemExit(f"these runs failed: {failed}")


if __name__ == "__main__":
    main()
