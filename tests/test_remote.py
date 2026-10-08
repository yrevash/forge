"""forge/remote: the parts that need no FreeCAD and no Kaggle (planning, comparing, the shim)."""

from __future__ import annotations

import ast
import gzip
import json
import sys
from pathlib import Path

import pytest

from forge.remote import compare, kernel_dir, linux, record
from forge.remote.bundle import parse_choice


def test_a_choice_names_a_slice_its_shards_and_an_optional_cut() -> None:
    assert parse_choice("train_v2:0-6:200") == ("train_v2", range(7), 200)
    assert parse_choice("train:3") == ("train", range(3, 4), None)


def test_a_task_becomes_the_recorders_own_tuple() -> None:
    out = Path("/out")
    assert record.full_task("single", ["train_v2", 3, [{"id": "a"}]], out) == (
        "train_v2", 3, [{"id": "a"}], "/out")
    assert record.full_task("wide", ["w", 1, [1], [2]], out) == ("w", 1, [1], [2], "/out")
    name, number, plans, where, chosen = record.full_task("multi", ["train", 7, "data/p.gz", "h"],
                                                          out)
    assert (name, number, where, chosen) == ("train", 7, "/out", "h")
    assert plans == str(record.PROJECT_ROOT / "data/p.gz")
    # The file that marks a shard as done has the recorder's own name: 3 digits, or 4.
    assert record.stats_file("wide", ["w", 1], out).name == "shard001.stats.json"
    assert record.stats_file("multi", ["train", 7], out).name == "shard0007.stats.json"


def test_nothing_is_recorded_with_other_code_than_was_planned(tmp_path: Path) -> None:
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({"recorder": "single", "code_hash": "not-this-code",
                                 "tasks": []}))
    with pytest.raises(RuntimeError, match="hashes to"):
        record.record(tasks, tmp_path / "out", workers=1)


def test_differences_says_where_and_noise_is_told_from_content() -> None:
    far = {"solid": {"volume": 1000.0000001, "faces": 6}, "items": [{"name": "Pad"}]}
    mac = {"solid": {"volume": 1000.0, "faces": 6}, "items": [{"name": "Pad"}]}
    found = compare.differences(far, mac)
    assert found == [(".solid.volume", 1000.0000001, 1000.0)]
    assert compare.is_noise(found[0][1], found[0][2])
    assert not compare.is_noise(6, 7) and not compare.is_noise("Pad", "Pad001")
    assert compare.differences({"a": [1, 2]}, {"a": [1]}) == [(".a[len]", 2, 1)]
    assert compare.differences({"a": 1}, {"b": 1})[0] == (".a", 1, "<absent>")


def _session(volume: float = 8.0, name: str = "Pad", low: float = -1.0,
             ) -> tuple[dict, list[dict], dict]:
    def snapshot(v: float) -> dict:
        return {"items": [{"name": name}], "session": {},
                "solid": {"volume": v, "bbox": [low, 0, 0, 1, 1, 1], "solids": 1, "valid": True}}
    header = {"session": "s1", "part_id": "p1", "plan": [], "code_hash": "x"}
    records = [{"session": "s1", "t": 0, "snapshot": snapshot(4.0), "valid": ["pad"]}]
    return header, records, {"session": "s1", "end": "done", "snapshot": snapshot(volume)}


def test_a_session_is_the_same_only_if_every_record_is() -> None:
    from forge.freecad.lean import same_lean
    assert compare.compare_session(_session(), _session())["kind"] == "same"
    # The last digit of a volume: the audit's own comparison lets it pass.
    volume = compare.compare_session(_session(8.000001), _session(), same_lean=same_lean)
    assert (volume["same"], volume["kind"], volume["first"]) == (False, "audit", "end.snapshot.solid.volume")
    # A bounding box off by 1e-7: the same part, and the audit's replay would reject it.
    box = compare.compare_session(_session(low=-1.0000001), _session(), same_lean=same_lean)
    assert box["kind"] == "noise" and box["fields"] == ["snapshot.solid.bbox"]
    assert box["largest_number_gap"] == pytest.approx(1e-7)
    renamed = compare.compare_session(_session(name="Pad001"), _session(), same_lean=same_lean)
    assert renamed["kind"] == "content" and renamed["first"].startswith("t=0")
    other_hash = ({**_session()[0], "code_hash": "y"}, *_session()[1:])
    assert compare.compare_session(other_hash, _session())["kind"] == "content"
    assert compare.compare_session(other_hash, _session(), ignore=("code_hash",))["same"]


def _write_shard(folder: Path, sessions: list[tuple], sha: str) -> None:
    (folder / "train").mkdir(parents=True)
    with gzip.open(folder / "train" / "shard000.jsonl.gz", "wt") as f:
        for header, records, end in sessions:
            for line in (header, *records, end):
                f.write(json.dumps(line) + "\n")
    (folder / "train" / "shard000.stats.json").write_text(json.dumps(
        {"file": "train/shard000.jsonl.gz", "sha256": sha, "code_hash": "x",
         "counts": {"sessions": len(sessions)}}))


def test_a_far_shard_is_compared_with_the_macs_shard_of_the_same_name(tmp_path: Path) -> None:
    second = ({**_session()[0], "session": "s2"}, [], {**_session()[2], "session": "s2"})
    _write_shard(tmp_path / "mac", [_session(), second], "a")
    _write_shard(tmp_path / "far", [_session()], "b")      # a probe: the first sessions only
    report = compare.compare_dirs("single", tmp_path / "far", tmp_path / "mac")
    assert (report["sessions"], report["sessions_same"], report["shards_same_bytes"]) == (1, 1, 0)
    _write_shard(tmp_path / "far2", [_session(name="Pad001")], "b")
    report = compare.compare_dirs("single", tmp_path / "far2", tmp_path / "mac")
    assert report["sessions_differ_content"] == 1 and report["examples"][0][2] == "content"


def test_the_kernel_is_private_cpu_only_and_its_script_still_parses() -> None:
    meta = kernel_dir.metadata("someone", "probe", "someone/bundle")
    assert meta["is_private"] is True and meta["enable_gpu"] is False
    assert meta["kernel_type"] == "script" and meta["dataset_sources"] == ["someone/bundle"]
    assert kernel_dir.metadata("someone", "probe", None)["dataset_sources"] == []
    config = {"jobs": [{"tasks": "tasks_wide.json", "out": "wide", "workers": 4}], "prove": 5}
    script = kernel_dir.script_with(config)
    tree = ast.parse(script)
    assigned = [node for node in tree.body if isinstance(node, ast.Assign)
                and node.targets[0].id == "CONFIG"]
    assert json.loads(assigned[0].value.args[0].value) == config


def test_freecad_is_found_in_an_unpacked_appimage_and_in_a_conda_folder(tmp_path: Path) -> None:
    assert linux.find_freecad(tmp_path) is None
    for python, lib in linux.LAYOUTS:
        root = tmp_path / python.split("/")[0]
        (root / python).parent.mkdir(parents=True)
        (root / python).touch()
        (root / lib).mkdir(parents=True, exist_ok=True)
        (root / lib / "FreeCAD.so").touch()
        assert linux.find_freecad(root) == (root / python, root / lib)


def test_the_launcher_adds_the_library_folder_and_nothing_else(tmp_path: Path) -> None:
    launcher = linux.write_launcher(Path("/fc/usr/bin/python"), Path("/fc/usr/lib"),
                                    tmp_path / "python.sh")
    assert launcher.read_text().splitlines() == [
        "#!/bin/sh", 'export LD_LIBRARY_PATH="/fc/usr/lib"', 'exec "/fc/usr/bin/python" "$@"']
    assert launcher.stat().st_mode & 0o100


def test_use_freecad_is_what_locate_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from forge.freecad.locate import freecad_python
    (tmp_path / "lib").mkdir()
    (tmp_path / "python").touch()
    monkeypatch.setenv("FORGE_FREECAD_PYTHON", "")      # restored after the test
    monkeypatch.setenv("FORGE_FREECAD_LIB", "")
    linux.use_freecad(tmp_path / "python", tmp_path / "lib")
    assert freecad_python() == (tmp_path / "python", tmp_path / "lib")


@pytest.mark.skipif(sys.platform == "linux", reason="on Linux it is allowed to start")
def test_the_bare_cadquery_helper_exists_on_linux_only() -> None:
    from forge.sandbox import SandboxUnavailable
    with pytest.raises(SandboxUnavailable):
        linux.LinuxSandbox()


def test_a_download_that_is_not_the_pinned_file_is_deleted_before_anything_runs(
        tmp_path: Path) -> None:
    import hashlib

    from forge.remote import kaggle_kernel as kernel
    good = tmp_path / "FreeCAD.AppImage"
    good.write_bytes(b"the release")
    pinned = hashlib.sha256(b"the release").hexdigest()
    assert kernel.verified(good, pinned, size=11) == good

    tampered = tmp_path / "tampered.AppImage"
    tampered.write_bytes(b"something else")
    with pytest.raises(kernel.ChecksumMismatch, match="deleted"):
        kernel.verified(tampered, pinned)
    assert not tampered.exists()            # nothing is left that could be run
    # The right bytes with the wrong length (a truncated download) are refused too.
    with pytest.raises(kernel.ChecksumMismatch):
        kernel.verified(good, pinned, size=12)
    assert not good.exists()


def test_the_kernel_pins_what_it_downloads_and_never_uses_a_shell() -> None:
    from forge.remote import kaggle_kernel as kernel
    assert "/download/1.1.4/" in kernel.APPIMAGE_URL and "latest" not in kernel.APPIMAGE_URL
    assert len(kernel.APPIMAGE_SHA256) == 64
    assert all("==" in pin for pin in kernel.PIP_PINS)
    source = Path(kernel.__file__).read_text()
    assert "shell=True" not in source
    # The file is made executable only after `verified` returned.
    assert source.index("verified(image, APPIMAGE_SHA256") < source.index("image.chmod(")
