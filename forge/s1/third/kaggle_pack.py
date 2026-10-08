"""Build the folders that go to Kaggle for the third model, and print the upload commands.

    uv run python -m forge.s1.third.kaggle_pack --title-suffix third-a --max-minutes 160 \
        --job configs/s1_third.yaml:cuda:0 --job configs/s1_third_big.yaml:cuda:1

    data/s1/kaggle3/arrays/   PRIVATE dataset forge-s1-arrays-v3: manifest.json and every
                              prepared shard of the TRAINING slices named. A slice the
                              manifest marks `test` is refused, and after copying, every
                              folder in the staging area is checked against the list again.
    data/s1/kaggle3/code/     PRIVATE dataset forge-s1-code: the files training needs.
    data/s1/kaggle3/kernel/   the PRIVATE script kernel, GPU on, internet off.

This file never talks to Kaggle and never reads the API token.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from forge.runs import PROJECT_ROOT, git_commit
from forge.s1.data import read_manifest
from forge.s1.third.drive import is_training_slice
from forge.s1.third.prepare import PREPARED_DIR, TEST_SLICES

USER = "yashtiwari9182"
ARRAYS, CODE = "forge-s1-arrays-v3", "forge-s1-code"
CODE_FILES = ("forge/__init__.py", "forge/runs.py", "forge/s1/__init__.py", "forge/s1/data.py",
              "forge/s1/loss.py", "forge/s1/model.py", "forge/s1/train.py",
              "forge/s1/third/__init__.py", "forge/s1/third/data.py", "forge/s1/third/loss.py",
              "forge/s1/third/model.py", "forge/s1/third/train.py",
              "forge/s1/third/train_many.py")
OUT = PROJECT_ROOT / "data" / "s1" / "kaggle3"


def is_train_name(name: str) -> bool:
    """A recorded training slice, a copy of one (_cf, _sc), or DAgger states of TRAIN parts."""
    base = name[:-3] if name.endswith(("_cf", "_sc")) else name
    return base not in TEST_SLICES and (is_training_slice(base) or base.startswith("dagger"))


def pack_arrays(prepared: Path, out: Path, train_slices: list[str]) -> int:
    manifest = read_manifest(prepared)
    for name in train_slices:
        if not is_train_name(name) or manifest["slices"][name].get("test"):
            raise SystemExit(f"{name} is not a training slice: it is not packed")
    shutil.rmtree(out, ignore_errors=True)
    # The uploaded manifest lists the packed slices only: no test slice is even named.
    manifest["slices"] = {name: manifest["slices"][name] for name in train_slices}
    files = [note["file"] for name in train_slices for note in manifest["slices"][name]["shards"]]
    for file in files:
        (out / file).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prepared / file, out / file)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (out / "dataset-metadata.json").write_text(json.dumps({
        "title": "Forge-S1 prepared arrays v3", "id": f"{USER}/{ARRAYS}",
        "licenses": [{"name": "other"}], "isPrivate": True,
        "subtitle": "Private. Training arrays of the third Forge-S1 model"}, indent=1))
    folders = sorted(path.name for path in out.iterdir() if path.is_dir())
    if folders != sorted(train_slices) or any(not is_train_name(name) for name in folders):
        raise SystemExit(f"the staging area holds something else than {train_slices}: {folders}")
    return len(files)


def pack_code(out: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    configs = sorted((PROJECT_ROOT / "configs").glob("s1_third*.yaml"))
    for file in (*CODE_FILES, *(str(path.relative_to(PROJECT_ROOT)) for path in configs)):
        (out / file).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / file, out / file)
    (out / "dataset-metadata.json").write_text(json.dumps({
        "title": "Forge-S1 code", "id": f"{USER}/{CODE}", "licenses": [{"name": "other"}],
        "isPrivate": True, "subtitle": "Private. The files forge/s1 training needs"}, indent=1))


def pack_kernel(out: Path, slug: str, jobs: list[list[str]], max_steps: int | None,
                max_minutes: int, extra_datasets: list[str],
                only_slices: list[str] | None = None, kernel_sources: list[str] | None = None,
                resume: bool = False) -> None:
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    script = (PROJECT_ROOT / "forge/s1/third/kaggle_kernel.py").read_text()
    for line, value in (("JOBS = []", f"JOBS = {jobs!r}"),
                        ("MAX_STEPS = None", f"MAX_STEPS = {max_steps}"),
                        ("MAX_MINUTES = 160", f"MAX_MINUTES = {max_minutes}"),
                        ("ONLY_SLICES = []", f"ONLY_SLICES = {list(only_slices or [])!r}"),
                        ("RESUME = False", f"RESUME = {resume}"),
                        ('COMMIT = "unknown"', f'COMMIT = "{git_commit()}"')):
        assert line in script, line
        script = script.replace(line, value, 1)
    (out / "kernel.py").write_text(script)
    (out / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{USER}/{slug}", "title": slug, "code_file": "kernel.py", "language": "python",
        "kernel_type": "script", "is_private": True, "enable_gpu": True,
        "enable_internet": False,
        "dataset_sources": [f"{USER}/{ARRAYS}", f"{USER}/{CODE}", *extra_datasets],
        "competition_sources": [], "kernel_sources": list(kernel_sources or []),
        "model_sources": []}, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--job", action="append", required=True, help="<config>:<device>")
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--max-minutes", type=int, default=160)
    parser.add_argument("--title-suffix", default="third-a", help="kernel: forge-s1-<suffix>")
    parser.add_argument("--extra-dataset", action="append", default=[],
                        help="e.g. a dataset holding the checkpoint a round starts from")
    parser.add_argument("--kernel-source", action="append", default=[],
                        help="an earlier kernel whose output is attached (init_from, resume)")
    parser.add_argument("--resume", action="store_true",
                        help="carry on from last.pt in an attached earlier output")
    parser.add_argument("--only-slices", nargs="*", default=None,
                        help="a trial: the kernel trains on these slices only")
    parser.add_argument("--train-slices", nargs="+", default=None,
                        help="pack these slices' arrays (leave out: the arrays are not repacked)")
    args = parser.parse_args()
    if args.train_slices:
        count = pack_arrays(PREPARED_DIR, OUT / "arrays", args.train_slices)
        print(f"arrays: {count} shards in {OUT / 'arrays'}")
    pack_code(OUT / "code")
    slug = f"forge-s1-{args.title_suffix}"
    pack_kernel(OUT / "kernel", slug, [job.split(":", 1) for job in args.job], args.max_steps,
                args.max_minutes, args.extra_dataset, args.only_slices, args.kernel_source,
                args.resume)
    print(f"kernel {slug}: {OUT / 'kernel'}")


if __name__ == "__main__":
    main()
