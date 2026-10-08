"""The build, end to end, on tiny shards in a temp folder (arithmetic only: no kernel)."""

import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def run(out: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "FORGE_PLANS_OUT": str(out), "FORGE_PLANS_PER": "12"}
    return subprocess.run([sys.executable, "-m", *args], cwd=ROOT, env=env, text=True,
                          capture_output=True, check=check)


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("plans")
    run(out, "forge.plans_data.build", "random", "--shards", "0-1")
    run(out, "forge.plans_data.build", "single", "--shards", "0-0")
    run(out, "forge.plans_data.build", "negatives", "--shards", "0-0")
    run(out, "forge.plans_data.build", "structures", "--shards", "0-0", "--kinds", "table", "stool")
    run(out, "forge.plans_data.finalize")
    return out


def records(out: Path, source: str) -> list[dict]:
    found = []
    for path in sorted((out / source).glob("*/shard*.jsonl.gz")):
        with gzip.open(path, "rt") as f:
            found += [json.loads(line) for line in f]
    return found


def test_manifest_counts_match_the_files(built: Path) -> None:
    manifest = json.loads((built / "manifest.json").read_text())
    for source in ("random", "negatives", "structures"):
        assert sum(manifest["plans"][source].values()) == len(records(built, source)) > 0
    assert manifest["plans_total"] == sum(row["rows"] for row in manifest["files"].values())
    assert manifest["coverage"]["cells_required"] > 400


def test_every_record_has_its_provenance_and_both_halves(built: Path) -> None:
    for source in ("random", "negatives", "structures"):
        for record in records(built, source):
            for key in ("id", "source", "license", "generator_version", "caption_style",
                        "caption_model", "geom_fingerprint", "split", "lines", "plan", "replies",
                        "parts", "cells"):
                assert record[key] not in (None, ""), key
            assert record["license"] == "forge"
            assert "Draft 2" in record["provenance"]["language"]
            assert len(record["lines"]) == len(record["plan"]) == len(record["replies"])
            assert record["negative"] == (source == "negatives")


def test_held_out_kinds_and_combinations_stay_out_of_train(built: Path) -> None:
    for record in records(built, "structures"):
        assert (record["split"] == "kinds") == (record["kind"] == "stool")
    for source in ("random", "negatives"):
        for record in records(built, source):
            if record["split"] in ("train", "iid"):
                assert record["held_out"] == [] and len(record["lines"]) <= 25


def test_a_shard_that_exists_is_skipped_and_a_rebuild_is_identical(built: Path, tmp_path: Path) -> None:
    again = run(built, "forge.plans_data.build", "random", "--shards", "0-1")
    assert again.stdout.count("'skipped': True") == 2
    run(tmp_path, "forge.plans_data.build", "random", "--shards", "0-1")

    def raw(out: Path) -> list[str]:
        lines = []
        for path in sorted((out / "_raw").glob("random-arithmetic-*.jsonl.gz")):
            with gzip.open(path, "rt") as f:
                lines += [json.dumps({k: v for k, v in json.loads(line).items()}) for line in f]
        return lines

    assert raw(tmp_path) == raw(built)


def test_the_audit_passes_every_check_but_the_two_that_need_scale(built: Path) -> None:
    """Twelve plans per shard cannot reach the coverage floors or the kernel-built
    floor; every other check must pass, and the command must then exit with 1."""
    result = run(built, "forge.plans_data.audit", "--workers", "1", "--kernel-plans", "0",
                 check=False)
    assert result.returncode == 1
    for check in ("1 reader", "2 resolver", "5 splits", "6 records"):
        assert f"PASS  {check}" in result.stdout, result.stdout
    assert "FAIL  3 kernel" in result.stdout and "FAIL  4 coverage" in result.stdout


def test_show_prints_a_plan_in_plain_words(built: Path) -> None:
    result = run(built, "forge.plans_data.show", "--source", "structures", "--split", "train",
                 "--n", "1")
    assert "=== structures / train" in result.stdout
    assert "touches" in result.stdout and "made by line" in result.stdout
