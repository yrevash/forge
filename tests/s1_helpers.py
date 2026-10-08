"""Small hand-made views for the Forge-S1 tests (a toy plan). Test fixtures
only: nothing here is training data."""

from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

import pytest

from forge.freecad.shards import OUT_DIR

PLAN = [{"kind": "block", "slots": {"length": 24.0, "width": 30.0, "height": 10.0}},
        {"kind": "hole", "slots": {"diameter": 6.0, "x": 5.0, "y": -4.0}}]
SKETCH_VALID = ["new_document", "sketch_rectangle", "sketch_circle", "sketch_polygon",
                "sketch_slot", "leave_sketch", "undo"]


def sketch_view(length: float = 24.0, plan: list[dict] | None = None) -> dict:
    """Toy B: the block's sketch with all four dimensions given. With
    length=42 it is toy C (a wrong number); with length=30 the width was given as the length."""
    return {
        "plan": copy.deepcopy(plan or PLAN),
        "snapshot": {
            "items": [
                {"type": "body", "name": "#0", "valid": True, "tip": None, "active": True},
                {"type": "sketch", "name": "#1", "body": "#0", "valid": True, "plane": "XY",
                 "offset": 0.0, "dof": 0, "closed": True, "used_by": None, "n_geometry": 5,
                 "n_constraints": 13,
                 "shapes": [{"shape": "rectangle", "first": 0, "length": length, "width": 30.0,
                             "x": 0.0, "y": 0.0, "fixed": ["length", "width", "x", "y"]}]}],
            "session": {"document": True, "active_body": "#0", "tip": None, "open_sketch": "#1",
                        "finished": False, "selection": None, "can_undo": True},
            "solid": None},
        "valid": list(SKETCH_VALID)}


def solid_view() -> dict:
    """Toy D, built right: the block exists, and a second sketch is started."""
    return {
        "plan": copy.deepcopy(PLAN),
        "snapshot": {
            "items": [
                {"type": "body", "name": "#0", "valid": True, "tip": "#2", "active": True},
                {"type": "sketch", "name": "#1", "body": "#0", "valid": True, "plane": "XY",
                 "offset": 0.0, "dof": 0, "closed": True, "used_by": "#2", "n_geometry": 5,
                 "n_constraints": 13,
                 "shapes": [{"shape": "rectangle", "first": 0, "length": 24.0, "width": 30.0,
                             "x": 0.0, "y": 0.0, "fixed": ["length", "width", "x", "y"]}]},
                {"type": "pad", "name": "#2", "body": "#0", "valid": True, "sketch": "#1",
                 "length": 10.0},
                {"type": "sketch", "name": "#3", "body": "#0", "valid": True, "plane": "XY",
                 "offset": 10.0, "dof": 3, "closed": True, "used_by": None, "n_geometry": 1,
                 "n_constraints": 0,
                 "shapes": [{"shape": "circle", "first": 0, "diameter": 10.0, "x": 0.0, "y": 0.0,
                             "fixed": []}]}],
            "session": {"document": True, "active_body": "#0", "tip": "#2", "open_sketch": "#3",
                        "finished": False,
                        "selection": {"type": "plane", "name": "XY"}, "can_undo": True},
            "solid": {"volume": 7200.0, "bbox": [-12.0, -15.0, 0.0, 12.0, 15.0, 10.0],
                      "solids": 1, "valid": True}},
        "valid": ["new_document", "sketch_circle", "constrain_diameter", "constrain_x",
                  "constrain_y", "leave_sketch", "undo"]}


def real_shard(slice_name: str = "train_v2", number: int = 0) -> Path:
    """A recorded shard, or skip the test: the data is not in git."""
    path = OUT_DIR / slice_name / f"shard{number:03d}.jsonl.gz"
    if not path.exists():
        pytest.skip(f"{path} is not on this machine")
    return path


def first_sessions(path: Path, count: int) -> list[dict]:
    """The lines (header, records, end) of the first `count` sessions of a shard."""
    lines, ends = [], 0
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            lines.append(json.loads(line))
            ends += "end" in lines[-1]
            if ends == count:
                break
    return lines


def write_shard(path: Path, lines: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
