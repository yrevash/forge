"""Pack what a far machine needs to record chosen shards: our code and the shards' inputs.

Runs on the Mac. It reads data/ (never writes it) and starts no FreeCAD.

    uv run python -m forge.remote.bundle --out <folder> \
        --single train_v2:0-6:200 --wide train_wide_v3:0-6:30 --multi train:0-13

Each choice is `slice:first-last[:parts]`: the shards of that slice with these numbers,
and (for a probe) only the first `parts` parts of each. The shard numbers and their parts
are the recorders' own plan, so shard N made far away is shard N made here.

Written into <folder>:
    bundle.bin   a .tar.gz (named .bin so that no upload service unpacks it for us) with
        forge/**                     every source file of the package, byte for byte
        tasks_single.json, tasks_wide.json, tasks_multi.json     (forge/remote/record.py)
        data/freecad_multi/selection/...      the plan files the structure tasks name
        MANIFEST.json                the code hashes, the git commit, what was chosen
"""

from __future__ import annotations

import argparse
import io
import json
import tarfile
from pathlib import Path

from forge.runs import PROJECT_ROOT, git_commit


def parse_choice(text: str) -> tuple[str, range, int | None]:
    """'train_v2:0-6:200' -> ('train_v2', range(0, 7), 200); the last field may be absent."""
    name, numbers, *cut = text.split(":")
    first, _, last = numbers.partition("-")
    return name, range(int(first), int(last or first) + 1), int(cut[0]) if cut else None


def single_tasks(name: str, numbers: range, cut: int | None) -> dict:
    from forge.freecad import sessions
    tasks = [[task[0], task[1], task[2][:cut]] for task in sessions.plan_shards(Path("unused"))
             if task[0] == name and task[1] in numbers]
    return {"recorder": "single", "code_hash": sessions.code_hash(), "tasks": tasks}


def wide_tasks(name: str, numbers: range, cut: int | None) -> dict:
    from forge.freecad import wide_sessions as wide
    part_set, _, size = wide.SLICES[name]
    # The same three lines as wide_sessions.plan_shards, for one slice: its other slices are
    # test sets, and planning them here would read test parts for no reason.
    parts = wide.ordered_parts(part_set)
    others = parts[1:] + parts[:1]
    tasks = [[name, number, parts[at:at + size][:cut], others[at:at + size][:cut]]
             for number, at in enumerate(range(0, len(parts), size)) if number in numbers]
    return {"recorder": "wide", "code_hash": wide.code_hash(), "tasks": tasks}


def multi_tasks(name: str, numbers: range) -> dict:
    from forge.freecad_multi import sessions
    tasks = [[task[0], task[1], str(Path(task[2]).relative_to(PROJECT_ROOT)), task[4]]
             for task in sessions.plan_shards(Path("unused"))
             if task[0] == name and task[1] in numbers]
    return {"recorder": "multi", "code_hash": sessions.code_hash(), "tasks": tasks}


def source_files() -> list[Path]:
    """Every file of the forge package, as it is on disk now (compiled files left out)."""
    return sorted(path for path in (PROJECT_ROOT / "forge").rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts
                  and path.name != ".DS_Store")


def _add(tar: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    tar.addfile(info, io.BytesIO(content))


def write_bundle(out: Path, task_files: dict[str, dict]) -> dict:
    """Write <out>/bundle.bin. Returns the manifest that is also stored inside it."""
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"git_commit": git_commit(),
                "code_hash": {key: value["code_hash"] for key, value in task_files.items()},
                "shards": {key: [[task[0], task[1]] for task in value["tasks"]]
                           for key, value in task_files.items()}}
    with tarfile.open(out / "bundle.bin", "w:gz") as tar:
        for path in source_files():
            tar.add(path, arcname=str(path.relative_to(PROJECT_ROOT)))
        for key, value in task_files.items():
            _add(tar, f"tasks_{key}.json", json.dumps(value).encode())
        if "multi" in task_files:
            selection = PROJECT_ROOT / "data" / "freecad_multi" / "selection"
            tar.add(selection / "selection.json",
                    arcname=str((selection / "selection.json").relative_to(PROJECT_ROOT)))
            for task in task_files["multi"]["tasks"]:
                tar.add(PROJECT_ROOT / task[2], arcname=task[2])
        _add(tar, "MANIFEST.json", json.dumps(manifest, indent=1).encode())
    manifest["bytes"] = (out / "bundle.bin").stat().st_size
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--single", default=None, help="slice:first-last[:parts]")
    parser.add_argument("--wide", default=None, help="slice:first-last[:parts]")
    parser.add_argument("--multi", default=None, help="slice:first-last")
    args = parser.parse_args()
    task_files = {}
    if args.single:
        task_files["single"] = single_tasks(*parse_choice(args.single))
    if args.wide:
        task_files["wide"] = wide_tasks(*parse_choice(args.wide))
    if args.multi:
        task_files["multi"] = multi_tasks(*parse_choice(args.multi)[:2])
    print(json.dumps(write_bundle(Path(args.out), task_files), indent=1))


if __name__ == "__main__":
    main()
