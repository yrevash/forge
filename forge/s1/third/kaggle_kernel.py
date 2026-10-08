"""The script that runs on Kaggle for the third model (uploaded by third/kaggle_pack.py).

It finds the two private datasets (arrays, code), then runs forge/s1/third/train_many.py:
every job of JOBS trains at the same time in one process that holds the arrays once. The
run folders are left in /kaggle/working (run, run2, ...), which `kaggle kernels output`
downloads. Settings are filled in by kaggle_pack.py. Internet is off.

A job is (config file, device). A config with `train.init_from: <file name>` starts from
that checkpoint, found by name in an attached dataset (forge/s1/train.py: find_file).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

JOBS = []                           # {JOBS}: [[config, device], ...]
MAX_STEPS = None                    # {MAX_STEPS}: a short trial; None = what the configs say
MAX_MINUTES = 160                   # {MAX_MINUTES}: every run saves and stops after this long
ONLY_SLICES = []                    # {ONLY_SLICES}: a trial on part of the data
RESUME = False                      # {RESUME}: carry on from last.pt of an attached earlier output
COMMIT = "unknown"                  # {COMMIT}
OUT = Path("/kaggle/working")


def find(file_name: str, under: str = "/kaggle/input") -> Path:
    """The folder that holds this file (dataset layouts vary)."""
    found = sorted(Path(under).rglob(file_name), key=lambda path: len(path.parts))
    if not found:
        raise SystemExit(f"{file_name} was not found under {under}")
    return found[0].parent


def main() -> None:
    subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv"], check=False)
    subprocess.run(["free", "-g"], check=False)
    data = find("manifest.json")
    package = find("train_many.py").parent.parent              # third -> s1 -> the folder .../forge
    work = OUT / "code"
    shutil.copytree(package, work / "forge", dirs_exist_ok=True)
    shutil.copytree(find(Path(JOBS[0][0]).name), work / "configs", dirs_exist_ok=True)
    print(f"data: {data}\ncode: {package}\ncommit: {COMMIT}", flush=True)
    command = [sys.executable, "-m", "forge.s1.third.train_many", "--data", str(data),
               "--max-minutes", str(MAX_MINUTES)]
    if MAX_STEPS:
        command += ["--max-steps", str(MAX_STEPS)]
    if RESUME:
        command += ["--resume-from-input"]
    if ONLY_SLICES:
        command += ["--only-slices", *ONLY_SLICES]
    for number, (config, device) in enumerate(JOBS):
        run_dir = OUT / ("run" if number == 0 else f"run{number + 1}")
        command += ["--job", f"{config}:{device}:{run_dir}"]
    env = {**os.environ, "FORGE_COMMIT": COMMIT, "PYTHONUNBUFFERED": "1"}
    done = subprocess.run(command, cwd=work, env=env, check=False)
    shutil.rmtree(work)                                         # only the run folders are output
    sys.exit(done.returncode)


if __name__ == "__main__":
    main()
