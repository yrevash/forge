"""Repair pairs in the dataset build and in the audit.

A small world is made once: a few families of real generated parts, their repair
rows, all in a temp folder. Each test then builds a dataset from it (fast: the
build runs no programs) and, where needed, audits it.
"""

import hashlib
import json
import shutil
import sys
from collections import Counter

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import yaml

from forge.data import audit, build_dataset
from forge.data.build_dataset import (
    SPLITS,
    TEST_SPLITS,
    FamilyCaps,
    cap_for,
    cap_point,
    repair_rows,
)
from forge.data.generate import parts_for, row
from forge.data.mistakes import REPAIR_PAIR_FIELDS, repairs_for_part, sample_parts
from forge.generators.base import check
from forge.sandbox import Sandbox

FAMILIES_USED = {"spacer": 24, "cup": 24, "hex_nut": 16, "square_nut": 6}
CONFIG = {
    "version": "t", "seed": 0,
    "prompts": {"standard": {"designation": 4, "compact": 2, "request": 2},
                "free": {"compact": 1, "request": 2}},
    "holdout_families": ["square_nut"], "holdout_sizes": ["M12", "M5"],
    "holdout_band": [0.45, 0.55],
    "fractions": {"train": 0.6, "val": 0.2, "test_id": 0.2},
    "repairs": {"prompts_per_repair": 2},
}
BASE_FILES = ("parts.parquet", *(f"{s}.parquet" for s in SPLITS))


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Generated parts and their repair rows, written the way the real jobs write them."""
    root = tmp_path_factory.mktemp("world")
    generated, mistakes_dir = root / "data" / "generated", root / "data" / "mistakes"
    generated.mkdir(parents=True)
    mistakes_dir.mkdir()
    # The build records a hash of the prompt code, which it looks up under the root.
    (root / "forge" / "generators").mkdir(parents=True)
    for name in ("captions.py", "prompts.py"):
        shutil.copy(build_dataset.PROJECT_ROOT / "forge" / "generators" / name,
                    root / "forge" / "generators" / name)
    with Sandbox(timeout=30) as sandbox:
        for family, count in FAMILIES_USED.items():
            rows, repairs = [], []
            for part in parts_for(family, count, seed=0)[:count]:
                measure = sandbox.run(part.code)["measure"]
                assert check(part, measure) == []
                rows.append(row(part, measure, "genv"))
                repairs += repairs_for_part(rows[-1], sandbox, per_part=2, seed=0,
                                            version="mv")[0]
            (generated / f"{family}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows))
            (mistakes_dir / f"{family}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in repairs))
            (mistakes_dir / f"{family}.done").write_text("done")
    return root


def build(world, monkeypatch, mistakes_dir=None, config=CONFIG, version="t"):
    """Run the real build on the small world. Returns (dataset folder, manifest)."""
    config = {**config, "version": version}
    (world / "configs").mkdir(exist_ok=True)
    (world / "configs" / f"{version}.yaml").write_text(yaml.safe_dump(config))
    (world / "runs").mkdir(exist_ok=True)
    for module in (build_dataset, audit):
        monkeypatch.setattr(module, "PROJECT_ROOT", world)
        monkeypatch.setattr(module, "start_run", lambda name, config: world / "runs")
    monkeypatch.setattr(build_dataset, "IN_DIR", world / "data" / "generated")
    monkeypatch.setattr(build_dataset, "MISTAKES_DIR",
                        mistakes_dir if mistakes_dir is not None else world / "no-such-folder")
    monkeypatch.setattr(sys, "argv", ["build", "--config", f"configs/{version}.yaml"])
    build_dataset.main()
    out = world / "data" / "dataset" / version
    return out, json.loads((out / "manifest.json").read_text())


def run_audit(monkeypatch, version="t") -> int:
    monkeypatch.setattr(sys, "argv", ["audit", "--version", version, "--reexecute", "5",
                                      "--reexecute-repairs", "25"])
    try:
        audit.main()
    except SystemExit as stop:
        return int(stop.code)
    return 0


def read(path) -> list[dict]:
    return pq.read_table(path).to_pylist()


def rewrite(out, name: str, rows: list[dict]) -> None:
    """Replace a repair file and correct its hash in the manifest, so only the
    check under test can notice the change."""
    schema = pa.schema([(field, pa.string()) for field in REPAIR_PAIR_FIELDS])
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), out / name)
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["files"][name] = hashlib.sha256((out / name).read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest))


def copy_mistakes(world, name: str, change) -> object:
    """A copy of the repair data with `change(row)` applied to every row."""
    target = world / "data" / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(world / "data" / "mistakes", target)
    for path in target.glob("*.jsonl"):
        rows = [change(json.loads(line)) for line in path.open()]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return target


# --- the build ----------------------------------------------------------------

def test_build_without_mistakes_data_is_unchanged(world, monkeypatch, capsys):
    out, manifest = build(world, monkeypatch)
    assert not list(out.glob("*_repair.parquet"))
    assert sorted(manifest["files"]) == sorted(BASE_FILES)
    assert "repairs" not in manifest
    assert not any("repair" in key for key in manifest["stats"])
    assert "repair" not in capsys.readouterr().out
    # The same build with repair data present writes byte-identical base files.
    plain = dict(manifest["files"])
    plain_stats = manifest["stats"]
    _, with_repairs = build(world, monkeypatch, world / "data" / "mistakes")
    assert {name: with_repairs["files"][name] for name in BASE_FILES} == plain
    assert with_repairs["stats"] == plain_stats
    assert with_repairs["pairs_by_style"] == manifest["pairs_by_style"]


def test_an_empty_mistakes_folder_counts_as_no_repair_data(world, monkeypatch):
    empty = world / "data" / "empty-mistakes"
    empty.mkdir(exist_ok=True)
    out, manifest = build(world, monkeypatch, empty)
    assert "repairs" not in manifest and not list(out.glob("*_repair.parquet"))


def test_a_family_without_a_done_marker_is_not_read(world, monkeypatch):
    partial = copy_mistakes(world, "partial-mistakes", lambda r: r)
    (partial / "cup.done").unlink()
    _, manifest = build(world, monkeypatch, partial)
    assert "cup" not in manifest["repairs"]["families_with_repair_data"]


def test_old_repair_files_do_not_survive_a_build_without_repair_data(world, monkeypatch):
    out, _ = build(world, monkeypatch, world / "data" / "mistakes")
    assert (out / "train_repair.parquet").exists()
    out, manifest = build(world, monkeypatch)
    assert not list(out.glob("*_repair.parquet")) and "repairs" not in manifest


def test_repairs_inherit_the_split_of_their_part(world, monkeypatch, capsys):
    out, manifest = build(world, monkeypatch, world / "data" / "mistakes")
    split_of = {part["id"]: part["split"] for part in read(out / "parts.parquet")}
    code_of = {part["id"]: part["code"] for part in read(out / "parts.parquet")}
    test_codes = {code_of[i] for i, split in split_of.items() if split in TEST_SPLITS}
    total = 0
    for split in SPLITS:
        rows = read(out / f"{split}_repair.parquet")
        total += len(rows)
        for r in rows:
            assert set(r) == set(REPAIR_PAIR_FIELDS) and all(r.values())
            assert r["split"] == split == split_of[r["part_id"]]
            assert r["code"] == code_of[r["part_id"]] and r["wrong_code"] != r["code"]
            assert r["mistake_version"] == "mv" and r["generator_version"] == "genv"
            if split in ("train", "val"):
                assert r["wrong_code"] not in test_codes and r["code"] not in test_codes
        per_repair = Counter(r["repair_id"] for r in rows)
        assert not per_repair or max(per_repair.values()) <= 2
    stats = manifest["repairs"]["stats"]
    assert total == sum(stats[f"repair_pairs_{s}"] for s in SPLITS) > 50
    # Held-out families and sizes have repairs too, and they stay in the test files.
    assert stats["repair_pairs_test_ood_family"] > 0 and stats["repair_pairs_test_ood_params"] > 0
    assert sum(manifest["repairs"]["pairs_by_mistake_group"].values()) == total
    assert manifest["repairs"]["mistake_versions"] == ["mv"]
    assert f"{total} repair pairs written" in capsys.readouterr().out


def test_the_cap_comes_from_the_config(world, monkeypatch):
    out, _ = build(world, monkeypatch, world / "data" / "mistakes",
                   {**CONFIG, "repairs": {"prompts_per_repair": 1}})
    counts = Counter(r["repair_id"] for s in SPLITS for r in read(out / f"{s}_repair.parquet"))
    assert set(counts.values()) == {1}
    no_section = {k: v for k, v in CONFIG.items() if k != "repairs"}
    out, manifest = build(world, monkeypatch, world / "data" / "mistakes", no_section)
    assert manifest["repairs"]["prompts_per_repair"] == 2  # the default


def test_stale_repairs_are_dropped(world, monkeypatch):
    def age(repair):
        if repair["family"] == "cup":
            repair["generator_version"] = "older"
        if repair["family"] == "spacer":
            repair["correct_code"] += "# edited\n"
        return repair

    out, manifest = build(world, monkeypatch, copy_mistakes(world, "stale-mistakes", age))
    families = {r["family"] for s in SPLITS for r in read(out / f"{s}_repair.parquet")}
    assert families == {"hex_nut", "square_nut"}
    assert manifest["repairs"]["stats"]["repairs_dropped_stale"] > 0


def test_a_wrong_program_that_is_a_test_program_is_dropped(world, monkeypatch):
    out, _ = build(world, monkeypatch)
    parts = read(out / "parts.parquet")
    test_code = next(p["code"] for p in parts if p["split"] == "test_id")
    train_ids = {p["id"] for p in parts if p["split"] == "train"}

    def leak(repair):
        if repair["part_id"] in train_ids:
            repair["wrong_code"] = test_code
        return repair

    out, manifest = build(world, monkeypatch, copy_mistakes(world, "leaky-mistakes", leak))
    assert read(out / "train_repair.parquet") == []
    assert manifest["repairs"]["stats"]["repairs_dropped_for_leakage"] > 0
    assert len(read(out / "val_repair.parquet")) > 0


def test_repair_rows_alone():
    """The safeguards, without any files."""
    part = {"id": "p", "family": "demo", "code": "CODE", "generator_version": "g"}
    error = {"status": "error", "error_type": "NameError", "line": 2, "error": "name 'x'"}
    repair = {"id": "r", "part_id": "p", "family": "demo", "mistake": "undefined_name",
              "mistake_group": "does_not_run", "wrong_code": "WRONG", "correct_code": "CODE",
              "wrong_outcome": error,
              "correct_measure": {"bbox": [1.0, 1.0, 1.0], "cylinders": {}},
              "params": {"side": 1.0}, "designation": None, "source": "gen:demo",
              "license": "forge", "generator_version": "g", "mistake_version": "m",
              "feedback_model": "template", "geom_fingerprint": "f"}
    prompts = [{"text": f"cube {i}", "style": "request", "variant": f"request-{i}",
                "model": "template"} for i in range(5)]
    config = {"seed": 0, "repairs": {"prompts_per_repair": 3}}
    stats = Counter()
    rows = repair_rows(part, [repair], prompts, "val", config, set(), stats)
    assert len(rows) == 3 and {r["split"] for r in rows} == {"val"}
    assert rows == repair_rows(part, [repair], prompts, "val", config, set(), Counter())
    assert stats == {"repairs_in": 1, "repairs_val": 1, "repair_pairs_val": 3}
    wrong_key = build_dataset.code_key("WRONG")
    assert repair_rows(part, [repair], prompts, "train", config, {wrong_key}, Counter()) == []
    # A test part keeps its own repairs: they are written to its test split.
    assert len(repair_rows(part, [repair], prompts, "test_id", config, {wrong_key},
                           Counter())) == 3
    assert repair_rows({**part, "code": "NEWER"}, [repair], prompts, "train", config, set(),
                       Counter()) == []


# --- max_parts_per_family -------------------------------------------------------

def test_cap_for_prefers_the_name_then_a_pattern_then_the_default():
    caps = {"default": 3000, "composed_*": None, "cup": 10}
    assert cap_for("spacer", caps) == 3000
    assert cap_for("composed_ring", caps) is None
    assert cap_for("cup", caps) == 10
    assert cap_for("spacer", {"cup": 10}) is None  # no default: no cap


def test_family_caps_choose_by_hash_and_never_cut_standard_parts():
    free = [{"id": f"f{i}", "family": "spacer", "designation": None} for i in range(50)]
    standard = [{"id": f"s{i}", "family": "hex_nut", "designation": {"size": "M8"}}
                for i in range(50)]
    caps = FamilyCaps(free + standard, {"default": 10}, seed=0)
    kept = [r["id"] for r in caps.filter(free + standard)]
    assert len(kept) == 60 and kept[10:] == [r["id"] for r in standard]
    by_hash = sorted(free, key=lambda r: cap_point(r, 0))[:10]
    assert set(kept[:10]) == {r["id"] for r in by_hash} != {f"f{i}" for i in range(10)}
    # File order does not matter, the seed does.
    assert set(FamilyCaps(free[::-1], {"default": 10}, 0).chosen["spacer"]) == set(kept[:10])
    assert set(FamilyCaps(free, {"default": 10}, 1).chosen["spacer"]) != set(kept[:10])
    assert len(list(FamilyCaps(free, {"default": 80}, 0).filter(free))) == 50


def test_mistakes_sample_the_parts_the_cap_keeps(world):
    """Repairs made for 8 parts per family must all be parts a cap of 10 keeps."""
    path = world / "data" / "generated" / "spacer.jsonl"
    everything = [json.loads(line) for line in path.open()]
    sampled = sample_parts(path, 8, seed=0)
    kept = set(FamilyCaps(everything, {"default": 10}, seed=0).chosen["spacer"])
    assert len(sampled) == 8 and {p["id"] for p in sampled} <= kept
    assert [p["id"] for p in sampled] != [p["id"] for p in everything[:8]]
    assert sample_parts(path, None, seed=0) == everything
    assert sample_parts(path, 100, seed=0) == everything


def test_build_with_a_cap_keeps_the_same_parts_in_every_pass(world, monkeypatch, capsys):
    uncapped_out, uncapped = build(world, monkeypatch, world / "data" / "mistakes")
    all_parts = {p["id"]: p for p in read(uncapped_out / "parts.parquet")}
    assert "max_parts_per_family" not in uncapped
    capsys.readouterr()

    capped = {**CONFIG, "max_parts_per_family": {"default": 10, "cu*": None}}
    out, manifest = build(world, monkeypatch, world / "data" / "mistakes", capped)
    parts = read(out / "parts.parquet")
    report = manifest["max_parts_per_family"]
    assert report["spacer"] == {"available": 24, "kept": 10, "cap": 10}
    assert report["cup"] == {"available": 24, "kept": 24, "cap": None}       # pattern: no cap
    assert report["hex_nut"] == {"available": 16, "kept": 16, "cap": 10}     # standard sizes
    assert report["square_nut"]["kept"] == 6
    assert Counter(p["family"] for p in parts) == {"spacer": 10, "cup": 24, "hex_nut": 16,
                                                   "square_nut": 6}
    # The ten spacers are the ten smallest by hash of id and seed, not the first ten lines.
    spacers = [p for p in all_parts.values() if p["family"] == "spacer"]
    expected = {p["id"] for p in sorted(spacers, key=lambda p: cap_point(p, 0))[:10]}
    assert {p["id"] for p in parts if p["family"] == "spacer"} == expected
    assert expected != {p["id"] for p in spacers[:10]}
    # The held-out band was worked out from the kept spacers only.
    values = sorted(json.loads(p["params"])["outer_diameter"]
                    for p in parts if p["family"] == "spacer")
    assert manifest["holdout_bands"]["spacer"] == [values[int(0.45 * 9)], values[int(0.55 * 9)]]
    # Pairs and repairs exist only for kept parts.
    kept_ids = {p["id"] for p in parts}
    for split in SPLITS:
        for name in (f"{split}.parquet", f"{split}_repair.parquet"):
            assert {r["part_id"] for r in read(out / name)} <= kept_ids
    assert "max_parts_per_family: 1 families cut down, 14 parts left out" in capsys.readouterr().out
    assert run_audit(monkeypatch) == 0


# --- the audit ----------------------------------------------------------------

SEVEN = ["1 files match manifest", "2 complete provenance", "3 ids unique, one split per part",
         "4 no shape or program shared with a test split",
         "5 held-out families only in test_ood_family",
         "6 every prompt number belongs to its part; full prompts are complete",
         "7 re-executed 5 programs"]


def test_audit_without_repair_files_runs_the_same_seven_checks(world, monkeypatch, capsys):
    out, _ = build(world, monkeypatch)
    assert run_audit(monkeypatch) == 0
    report = json.loads((out / "audit.json").read_text())
    assert list(report) == [*SEVEN, "pairs", "parts"]
    assert all(report[name] == "PASS" for name in SEVEN)
    printed = capsys.readouterr().out
    assert "repair" not in printed.split("dataset t:")[0] + printed.split("PASS  1")[1]


def test_audit_passes_a_build_with_repairs(world, monkeypatch, capsys):
    out, manifest = build(world, monkeypatch, world / "data" / "mistakes")
    assert run_audit(monkeypatch) == 0
    report = json.loads((out / "audit.json").read_text())
    assert list(report)[:7] == SEVEN and len([k for k in report if k[0].isdigit()]) == 12
    assert all(value == "PASS" for name, value in report.items() if name[0].isdigit())
    assert report["repair_pairs"] == sum(
        manifest["repairs"]["stats"][f"repair_pairs_{s}"] for s in SPLITS)
    assert "12 re-executed 25 wrong programs, feedback reproduced" in report
    assert f"{report['repair_pairs']} repair pairs" in capsys.readouterr().out


def failed(out) -> set[str]:
    report = json.loads((out / "audit.json").read_text())
    return {name.split(" ")[0] for name, value in report.items() if str(value).startswith("FAIL")}


def test_audit_catches_feedback_that_cannot_be_reproduced(world, monkeypatch):
    out, _ = build(world, monkeypatch, world / "data" / "mistakes")
    rows = read(out / "train_repair.parquet")
    rewrite(out, "train_repair.parquet",
            [{**r, "feedback": r["feedback"] + "\nlength 999 is stated."} for r in rows])
    assert run_audit(monkeypatch) == 1
    assert failed(out) == {"12"}


def test_audit_catches_a_test_repair_placed_in_train(world, monkeypatch):
    out, _ = build(world, monkeypatch, world / "data" / "mistakes")
    stray = [{**r, "split": "train", "id": f"stray-{i}"}
             for i, r in enumerate(read(out / "test_ood_family_repair.parquet")[:3])]
    rewrite(out, "train_repair.parquet", read(out / "train_repair.parquet") + stray)
    assert run_audit(monkeypatch) == 1
    assert failed(out) == {"10", "11"}


def test_audit_catches_incomplete_repair_provenance(world, monkeypatch):
    out, _ = build(world, monkeypatch, world / "data" / "mistakes")
    rows = read(out / "val_repair.parquet")
    rows[0] = {**rows[0], "mistake_version": ""}
    rows[1] = {**rows[1], "mistake": "typed_by_hand"}
    rewrite(out, "val_repair.parquet", rows)
    assert run_audit(monkeypatch) == 1
    assert failed(out) == {"9"}


def test_audit_catches_a_repair_file_the_manifest_does_not_know(world, monkeypatch):
    out, _ = build(world, monkeypatch, world / "data" / "mistakes")
    kept = (out / "train_repair.parquet").read_bytes()
    out, _ = build(world, monkeypatch)               # a build without repair data ...
    (out / "train_repair.parquet").write_bytes(kept)  # ... and a repair file slipped in
    assert run_audit(monkeypatch) == 1
    assert "8" in failed(out)
