"""The session files on disk: where they are, which slices exist, how to read them.

Plain data and two small readers, so that the recorder (sessions.py) and the tools
that only read (manifest.py, audit.py, load.py, baselines.py) share one definition
and none of them has to import another.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

from forge.freecad.noise import HEAVY_NOISE_LEVELS, NOISE_LEVELS
from forge.runs import PROJECT_ROOT

OUT_DIR = PROJECT_ROOT / "data" / "freecad" / "sessions"
# name -> (split, seed, noise levels, how many parts at most), in the order they are made.
SLICES: dict[str, tuple[str, int, tuple[float, ...], int | None]] = {
    "iid": ("iid", 0, NOISE_LEVELS, None),
    "pairing": ("pairing", 0, NOISE_LEVELS, None),
    "long": ("long", 0, NOISE_LEVELS, None),
    "train": ("train", 0, NOISE_LEVELS, None),
    "train_heavy": ("train", 3, HEAVY_NOISE_LEVELS, 3000),      # many mistakes, kept apart
    "train_s1": ("train", 1, NOISE_LEVELS, None),
    "train_s2": ("train", 2, NOISE_LEVELS, None),
    # The second mix. The test slices come first: they are small and must be complete.
    "iid_v2": ("iid", 10, NOISE_LEVELS, None),
    "pairing_v2": ("pairing", 10, NOISE_LEVELS, None),
    "long_v2": ("long", 10, NOISE_LEVELS, None),
    "train_v2": ("train", 10, NOISE_LEVELS, None),
    # Added 7 Oct 2026: long parts for training, and a test beyond every length
    # in training. New parts in splits of their own; nothing above is touched.
    "train_long_v2": ("train_long", 10, NOISE_LEVELS, None),
    "xlong_v2": ("xlong", 10, NOISE_LEVELS, None),
}
# Recorded with the first noise mix and the old undo. Never recorded again: a shard made
# now would look like its neighbours and be something else.
FROZEN = ("iid", "pairing", "long", "train", "train_heavy", "train_s1", "train_s2")
RECORDED_NOW = tuple(name for name in SLICES if name not in FROZEN)
PARTS_PER_SHARD = {"iid": 500, "pairing": 500, "long": 200, "train": 500,
                   "train_long": 200, "xlong": 200}

# Slices whose parts are the train split, and the three test splits with their slices.
TRAIN_SLICES = tuple(name for name, value in SLICES.items() if value[0] == "train")
TRAIN_SPLITS = ("train", "train_long")
TEST_SPLITS = ("iid", "pairing", "long", "xlong")


def read_sessions(path: Path) -> Iterator[tuple[dict, list[dict], dict]]:
    """Every session in one shard: (header, its step records in order, its end record)."""
    header: dict | None = None
    records: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            if "plan" in item:
                header, records = item, []
            elif "end" in item:
                yield header, records, item
            else:
                records.append(item)


def changed(record: dict, after: dict) -> bool:
    """Did this step's command change the session? `after` is the next record, or the END.

    When the undo history was lost between the two (`before: forget_undo`, play.py), the
    count of undoable commands differs for that reason alone and is left out."""
    if after.get("before") != "forget_undo":
        return after["snapshot"] != record["snapshot"]
    first, second = record["snapshot"], after["snapshot"]
    return first["items"] != second["items"] or first["solid"] != second["solid"] or \
        {**first["session"], "undo_depth": 0} != {**second["session"], "undo_depth": 0}


def complete_shards(out_dir: Path, slices: tuple[str, ...] | list[str] | None = None,
                    ) -> list[tuple[dict, Path]]:
    """(stats, shard file) of every shard whose stats file exists, in a fixed order.
    The recorder writes the stats file last, so these shards are whole."""
    found = []
    for path in sorted(Path(out_dir).glob("*/shard*.stats.json")):
        stats = json.loads(path.read_text())
        if slices is None or stats["slice"] in slices:
            found.append((stats, Path(out_dir) / stats["file"]))
    return found


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
