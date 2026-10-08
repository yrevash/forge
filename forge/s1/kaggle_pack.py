"""Build the three folders that go to Kaggle, and print the commands that upload them.

    uv run python -m forge.s1.kaggle_pack --max-steps 3000 --max-minutes 25 --title-suffix speed
    uv run python -m forge.s1.kaggle_pack --max-minutes 480

    data/s1/kaggle/arrays/   PRIVATE dataset: manifest.json and every prepared shard of the
                             TRAINING slices named with --train-slices. No shard of a test
                             slice is ever packed: the first model's upload
                             carried one iid_v2 shard as a logged monitor sample; since
                             7 Oct 2026 the held-out TRAIN shards do that job.
    data/s1/kaggle/code/     PRIVATE dataset: the five files train.py needs, forge/runs.py,
                             the configs. No data, no token.
    data/s1/kaggle/kernel/   the PRIVATE script kernel: forge/s1/kaggle_kernel.py with its
                             settings filled in, GPU on, internet off

This file never talks to Kaggle and never reads the API token. The commands it prints are
run by hand with `uvx kaggle ...`.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from forge.freecad.shards import SLICES, TRAIN_SPLITS
from forge.runs import PROJECT_ROOT, git_commit
from forge.s1.data import read_manifest
from forge.s1.prepare import PREPARED_DIR

USER = "yashtiwari9182"
ARRAYS, CODE = "forge-s1-arrays-v1", "forge-s1-code"
CODE_FILES = ("forge/__init__.py", "forge/runs.py", "forge/s1/__init__.py", "forge/s1/data.py",
              "forge/s1/loss.py", "forge/s1/model.py", "forge/s1/train.py",
              "forge/s1/kaggle_kernel.py")
OUT = PROJECT_ROOT / "data" / "s1" / "kaggle"


def pack_arrays(prepared: Path, out: Path, train_slices: list[str]) -> int:
    for name in train_slices:           # a test slice must never be uploaded with training data
        if SLICES[name.removesuffix("_cf").removesuffix("_sc")][0] not in TRAIN_SPLITS:    # _cf: augment.py
            raise SystemExit(f"{name} is not a training slice: it is not packed")
    manifest = read_manifest(prepared)
    files = [note["file"] for name in train_slices for note in manifest["slices"][name]["shards"]]
    shutil.rmtree(out, ignore_errors=True)
    for file in ("manifest.json", *files):
        (out / file).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(prepared / file, out / file)
    (out / "dataset-metadata.json").write_text(json.dumps({
        "title": "Forge-S1 prepared arrays v1", "id": f"{USER}/{ARRAYS}",
        "licenses": [{"name": "other"}], "isPrivate": True,
        "subtitle": f"Private. Training arrays of {' and '.join(train_slices)}"[:80]},
        indent=1))
    return len(files)


def pack_code(out: Path) -> None:
    shutil.rmtree(out, ignore_errors=True)
    for file in (*CODE_FILES, *(str(path.relative_to(PROJECT_ROOT))
                                for path in sorted((PROJECT_ROOT / "configs").glob("s1_*.yaml")))):
        (out / file).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / file, out / file)
    (out / "dataset-metadata.json").write_text(json.dumps({
        "title": "Forge-S1 code", "id": f"{USER}/{CODE}", "licenses": [{"name": "other"}],
        "isPrivate": True, "subtitle": "Private. The files forge/s1/train.py needs"}, indent=1))


def pack_kernel(out: Path, slug: str, config: str, max_steps: int | None, max_minutes: int,
                extra_datasets: list[str], more_configs: list[str] | None = None) -> None:
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    script = (PROJECT_ROOT / "forge/s1/kaggle_kernel.py").read_text()
    for line, value in (('CONFIG = "configs/s1_first.yaml"', f'CONFIG = "{config}"'),
                        ("MAX_STEPS = None", f"MAX_STEPS = {max_steps}"),
                        ("MAX_MINUTES = 480", f"MAX_MINUTES = {max_minutes}"),
                        ('COMMIT = "unknown"', f'COMMIT = "{git_commit()}"'),
                        ("MORE_CONFIGS = []", f"MORE_CONFIGS = {list(more_configs or [])!r}")):
        assert line in script, line
        script = script.replace(line, value, 1)
    (out / "kernel.py").write_text(script)
    (out / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{USER}/{slug}", "title": slug, "code_file": "kernel.py", "language": "python",
        "kernel_type": "script", "is_private": True, "enable_gpu": True,
        "enable_internet": False,
        "dataset_sources": [f"{USER}/{ARRAYS}", f"{USER}/{CODE}", *extra_datasets],
        "competition_sources": [], "kernel_sources": [], "model_sources": []}, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default="configs/s1_first.yaml")
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--max-minutes", type=int, default=480)
    parser.add_argument("--title-suffix", default="main", help="kernel name: forge-s1-<suffix>")
    parser.add_argument("--resume-dataset", default=None,
                        help="a dataset holding an earlier run folder with last.pt")
    parser.add_argument("--skip-arrays", action="store_true", help="the arrays are already packed")
    parser.add_argument("--train-slices", nargs="+", default=["train_v2"])
    parser.add_argument("--more-configs", nargs="*", default=[],
                        help="configs trained beside --config, each on a GPU of its own")
    args = parser.parse_args()
    if not args.skip_arrays:
        count = pack_arrays(PREPARED_DIR, OUT / "arrays", args.train_slices)
        print(f"arrays: {count} shards in {OUT / 'arrays'}")
    pack_code(OUT / "code")
    slug = f"forge-s1-{args.title_suffix}"
    pack_kernel(OUT / "kernel", slug, args.config, args.max_steps, args.max_minutes,
                [args.resume_dataset] if args.resume_dataset else [], args.more_configs)
    print(f"""
Upload (first time `create`, later `version`):
  uvx kaggle datasets create -p {OUT / 'arrays'} --dir-mode zip
  uvx kaggle datasets version -p {OUT / 'arrays'} --dir-mode zip -d -m "training slices only"
      (-d deletes the older versions, so that a shard removed here is gone there too)
  uvx kaggle datasets create -p {OUT / 'code'} --dir-mode zip
  uvx kaggle datasets version -p {OUT / 'code'} --dir-mode zip -m "code at {git_commit()[:7]}"
Run:
  uvx kaggle kernels push -p {OUT / 'kernel'}
  uvx kaggle kernels status {USER}/{slug}
  uvx kaggle kernels output {USER}/{slug} -p runs/<run>/kaggle_output
""")


if __name__ == "__main__":
    main()
