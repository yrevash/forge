"""Pure-model evaluation on Kaggle's CPU: step tables and counterfactual tables, three models.

    uv run python -m forge.s1.third.kaggle_eval --checkpoint first=checkpoints/s1/s1-first-best.pt \
        --checkpoint second-noids=checkpoints/s1/s1-second-noids-best.pt \
        --checkpoint third=checkpoints/s1/s1-third-r0-best.pt

Why on Kaggle: these two tables are model inference on recorded steps (no FreeCAD), and the
laptop is not to be loaded (8 Oct 2026). The kernel has NO GPU, so it uses none of the GPU
budget, and no internet.

    data/s1/kaggle3/eval/         PRIVATE dataset forge-s1-eval-tests. EVALUATION ONLY: the first
                                  SHARDS shards of the eight TEST slices (session files and the
                                  third model's arrays), the checkpoints, the forge source.
                                  It is a different dataset from the training arrays
                                  (forge-s1-arrays-v3) and is never attached to a training
                                  kernel: third/kaggle_pack.py lists its datasets by name.
    data/s1/kaggle3/eval_kernel/  the PRIVATE script kernel forge-s1-third-eval

The kernel lays the dataset out as a small copy of the repository and runs, unchanged,
`forge.s1.third.evaluate` and `forge.s1.counterfactual`; their run folders are its output.
This file never talks to Kaggle and never reads the API token.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from forge.freecad import wide_sessions
from forge.freecad.shards import OUT_DIR
from forge.runs import PROJECT_ROOT, git_commit
from forge.s1.third.prepare import PREPARED_DIR, TEST_SLICES

USER, DATASET, KERNEL = "yashtiwari9182", "forge-s1-eval-tests", "forge-s1-third-eval"
OUT = PROJECT_ROOT / "data" / "s1" / "kaggle3"
SHARDS = 4

KERNEL_SCRIPT = '''"""Runs on Kaggle (CPU only). Written by forge/s1/third/kaggle_eval.py; see there."""
import gzip, os, shutil, subprocess, sys
from pathlib import Path

CHECKPOINTS = {checkpoints!r}
SLICES = {slices!r}
root = sorted(Path("/kaggle/input").rglob("EVAL_ONLY.txt"))[0].parent
repo = Path("/kaggle/working/repo")
shutil.copytree(root, repo)
# Kaggle unpacks every .gz it is given; the readers expect shardNNN.jsonl.gz: pack them again.
for path in list(repo.rglob("shard*.jsonl")):
    with open(path, "rb") as plain, gzip.open(f"{{path}}.gz", "wb", compresslevel=1) as packed:
        shutil.copyfileobj(plain, packed)
    path.unlink()
env = {{**os.environ, "FORGE_COMMIT": "{commit}", "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "2"}}
marks = [part for label, file in CHECKPOINTS for part in ("--checkpoint", f"{{label}}={{file}}")]
jobs = [[sys.executable, "-m", "forge.s1.third.evaluate", *marks, "--max-shards", "{shards}",
         "--device", "cpu"],
        [sys.executable, "-m", "forge.s1.counterfactual", *marks, "--slices", *SLICES,
         "--steps", "20000", "--shards", "{shards}", "--device", "cpu"]]
running = [subprocess.Popen(job, cwd=repo, env=env, stdout=open(f"/kaggle/working/job{{n}}.log", "w"),
                            stderr=subprocess.STDOUT) for n, job in enumerate(jobs)]
codes = [process.wait() for process in running]
shutil.copytree(repo / "runs", "/kaggle/working/runs", dirs_exist_ok=True)
shutil.rmtree(repo)
for n in range(len(jobs)):
    print(open(f"/kaggle/working/job{{n}}.log").read()[-6000:])
sys.exit(max(codes))
'''


def pack(checkpoints: list[tuple[str, str]]) -> None:
    out = OUT / "eval"
    shutil.rmtree(out, ignore_errors=True)
    (out / "EVAL_ONLY.txt").parent.mkdir(parents=True)
    (out / "EVAL_ONLY.txt").write_text(
        "EVALUATION ONLY. Test slices of Forge-S1. Never attach this dataset to a training "
        "kernel and never train on anything in it.\n")
    shutil.copytree(PROJECT_ROOT / "forge", out / "forge",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    manifest = json.loads((PREPARED_DIR / "manifest.json").read_text())
    manifest["slices"] = {name: {**manifest["slices"][name],
                                 "shards": manifest["slices"][name]["shards"][:SHARDS]}
                          for name in TEST_SLICES}
    for name in TEST_SLICES:
        folder = wide_sessions.OUT_DIR if name in wide_sessions.SLICES else OUT_DIR
        for note in manifest["slices"][name]["shards"]:
            stem = Path(note["file"]).stem                      # shardNNN
            for file in (f"{stem}.jsonl.gz", f"{stem}.stats.json"):
                target = out / folder.relative_to(PROJECT_ROOT) / name / file
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(folder / name / file, target)
            target = out / "data" / "s1" / "v3" / note["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PREPARED_DIR / note["file"], target)
    (out / "data" / "s1" / "v3" / "manifest.json").write_text(json.dumps(manifest) + "\n")
    shutil.copy2(OUT_DIR / "long_pure.json", out / "data/freecad/sessions/long_pure.json")
    for _, file in checkpoints:
        (out / file).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / file, out / file)
    (out / "dataset-metadata.json").write_text(json.dumps({
        "title": "Forge-S1 EVALUATION ONLY test slices", "id": f"{USER}/{DATASET}",
        "licenses": [{"name": "other"}], "isPrivate": True,
        "subtitle": "Private. EVALUATION ONLY. Never attach to a training kernel"}, indent=1))
    kernel = OUT / "eval_kernel"
    shutil.rmtree(kernel, ignore_errors=True)
    kernel.mkdir(parents=True)
    (kernel / "kernel.py").write_text(KERNEL_SCRIPT.format(
        checkpoints=checkpoints, slices=list(TEST_SLICES), commit=git_commit(), shards=SHARDS))
    (kernel / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{USER}/{KERNEL}", "title": KERNEL, "code_file": "kernel.py",
        "language": "python", "kernel_type": "script", "is_private": True,
        "enable_gpu": False, "enable_internet": False,
        "dataset_sources": [f"{USER}/{DATASET}"], "competition_sources": [],
        "kernel_sources": [], "model_sources": []}, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", action="append", required=True, help="label=file")
    args = parser.parse_args()
    pack([tuple(text.split("=", 1)) for text in args.checkpoint])
    print(f"dataset: {OUT / 'eval'}\nkernel:  {OUT / 'eval_kernel'}")


if __name__ == "__main__":
    main()
