"""The structure session files on disk: where they are, which slices exist, how to read them.

Plain data and small readers, so that the recorder (sessions.py) and the tools that only
read (manifest.py, audit.py, load.py) share one definition.

WHAT IS STORED PER STEP, AND WHY IT IS A DELTA

A structure snapshot lists every object of every body. The first recording (`boxes_v1`)
stored it whole at every step: 7.9 KB per step on average and up to 64 KB, although one
command changes one or two objects. Here a step stores

    the first step of a session    the whole lean snapshot
    every later step               {"delta": true, "n": <number of objects>,
                                    "set": [[place, object], ...],      only what changed
                                    "session": ..., "solid": ...,       always (small)
                                    "structure": ...}                   only if it changed
    "valid"                         left out when it is the previous step's list

`read_sessions` puts the whole snapshot back, exactly (the same JSON values), so the
teacher, the oracle, the audit's replay and load.py all read full snapshots and nothing
about them depends on the packing. It is lossless: `unpack(pack(x)) == x` is tested, and
the recorder checks it on every session before the shard is written.

Why not store the model's compact view instead: the view is derived (load.py) and will
change when the model's input does; the state it is derived from must not. Why not keep
the whole snapshot: gzip already hides most of the repetition on disk (about 200 bytes a
step either way), but every reader would still have to parse 8 KB of JSON per step.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path

from forge.freecad.shards import sha256
from forge.freecad_multi.noise import NOISE_LEVELS
from forge.runs import PROJECT_ROOT

__all__ = ["OUT_DIR", "SELECTION_DIR", "SLICES", "TEST_SLICES", "TRAIN_SLICES", "changed", "complete_shards",
           "pack", "read_sessions", "sha256", "unpack"]

OUT_DIR = PROJECT_ROOT / "data" / "freecad_multi" / "sessions"
SELECTION_DIR = PROJECT_ROOT / "data" / "freecad_multi" / "selection"
SEED = 20
# name -> (selection folder, split, seed, noise levels, how many plans at most), in the
# order they are recorded: the test slices first (they are small and must be complete),
# then three more passes over the rare train plans (selection.in_rare_pass) with other
# seeds, then train. Which plans a folder holds, and in which order, is selection.py's
# business.
SLICES: dict[str, tuple[str, str, int, tuple[float, ...], int | None]] = {
    "iid": ("iid", "iid", SEED, NOISE_LEVELS, 1200),
    "kinds": ("kinds", "kinds", SEED, NOISE_LEVELS, 1200),
    "combo": ("combo", "combo", SEED, NOISE_LEVELS, 1400),
    "long": ("long", "long", SEED, NOISE_LEVELS, 300),
    "train_rare_s1": ("train_rare", "train", SEED + 1, NOISE_LEVELS, None),
    "train_rare_s2": ("train_rare", "train", SEED + 2, NOISE_LEVELS, None),
    "train_rare_s3": ("train_rare", "train", SEED + 3, NOISE_LEVELS, None),
    "train": ("train", "train", SEED, NOISE_LEVELS, None),
}
TEST_SLICES = ("iid", "kinds", "combo", "long")
TRAIN_SLICES = tuple(name for name, value in SLICES.items() if value[1] == "train")


# --- packing -------------------------------------------------------------------------------------

def _pack_snapshot(previous: dict | None, snapshot: dict | None) -> dict | None:
    if previous is None or snapshot is None:
        return snapshot
    old, new = previous["items"], snapshot["items"]
    packed = {"delta": True, "n": len(new),
              "set": [[place, item] for place, item in enumerate(new)
                      if place >= len(old) or old[place] != item],
              "session": snapshot["session"], "solid": snapshot["solid"]}
    if snapshot["structure"] != previous["structure"]:
        packed["structure"] = snapshot["structure"]
    return packed


def _unpack_snapshot(previous: dict | None, packed: dict | None) -> dict | None:
    if packed is None or not packed.get("delta"):
        return packed
    items = list(previous["items"][:packed["n"]])
    items += [None] * (packed["n"] - len(items))
    for place, item in packed["set"]:
        items[place] = item
    return {"items": items, "session": packed["session"], "solid": packed["solid"],
            "structure": packed.get("structure", previous["structure"])}


def pack(records: list[dict], end: dict) -> list[dict]:
    """The step records and the end record as they are written (see the top of this file)."""
    packed, previous, valid = [], None, None
    for record in [*records, end]:
        line = {**record, "snapshot": _pack_snapshot(previous, record["snapshot"])}
        if "valid" in record:
            if record["valid"] == valid:
                del line["valid"]
            valid = record["valid"]
        previous = record["snapshot"] if record["snapshot"] is not None else previous
        packed.append(line)
    return packed


def unpack(lines: list[dict]) -> tuple[list[dict], dict]:
    """The inverse of `pack`: (step records, end record) with whole snapshots."""
    whole, previous, valid = [], None, None
    for line in lines:
        record = {**line, "snapshot": _unpack_snapshot(previous, line["snapshot"])}
        if "end" not in line:
            valid = line.get("valid", valid)
            # Keep the key order of a record as play.py makes it.
            record = {"session": line["session"], "t": line["t"], "snapshot": record["snapshot"],
                      "valid": valid, **{key: value for key, value in line.items()
                                         if key not in ("session", "t", "snapshot", "valid")}}
        previous = record["snapshot"] if record["snapshot"] is not None else previous
        whole.append(record)
    return whole[:-1], whole[-1]


# --- reading -------------------------------------------------------------------------------------

def read_sessions(path: Path) -> Iterator[tuple[dict, list[dict], dict]]:
    """Every session in one shard: (header, its step records in order, its end record),
    snapshots whole."""
    header: dict | None = None
    lines: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for text in f:
            item = json.loads(text)
            if "plan" in item:
                header, lines = item, []
            else:
                lines.append(item)
                if "end" in item:
                    records, end = unpack(lines)
                    yield header, records, end


def changed(record: dict, after: dict) -> bool:
    """Did this step's command change the session? `after` is the next record, or the END.
    When the undo history was lost between the two, the count of undoable commands
    differs for that reason alone and is left out."""
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
