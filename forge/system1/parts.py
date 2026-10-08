"""Read the verified composed parts that sessions are cut from.

Two places hold them, and neither is ever written by this package's session code:
    data/generated/composed_<base>.jsonl          the 120,000 parts with 1 to 5 features
    data/system1/long_parts/composed_<base>.jsonl the longer test parts (long_parts.py)

Two sets were added on 7 Oct 2026 (forge/freecad/long_train_parts.py). Their rows
carry `set`, and that is what `splits.split_of` reads. No older part has that field.
    data/system1/long_train_parts/composed_<base>.jsonl   long parts for TRAINING ("train_long")
    data/system1/xlong_parts/composed_<base>.jsonl        13 to 15 features, test only ("xlong")
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from forge.runs import PROJECT_ROOT

BASES = ("block", "cylinder", "hex", "ring")
GENERATED_DIR = PROJECT_ROOT / "data" / "generated"
LONG_DIR = PROJECT_ROOT / "data" / "system1" / "long_parts"
# set name -> its folder. A part of these folders gets `set`; a part of the two above never does.
ADDED_DIRS = {"train_long": PROJECT_ROOT / "data" / "system1" / "long_train_parts",
              "xlong": PROJECT_ROOT / "data" / "system1" / "xlong_parts"}
# What a session needs from a row. The program text is big and is left out unless asked for.
LIGHT_FIELDS = ("id", "family", "params", "geom_fingerprint", "source", "license",
                "generator_version")


def _read(path: Path, long: bool, full: bool, limit: int | None,
          added: str | None = None) -> Iterator[dict]:
    with path.open() as f:
        for count, line in enumerate(f):
            if limit is not None and count >= limit:
                return
            row = json.loads(line)
            part = row if full else {key: row[key] for key in LIGHT_FIELDS}
            part["long"] = long
            if added is not None:
                part["set"] = added
            yield part


def read_parts(full: bool = False, limit_per_file: int | None = None,
               include_long: bool = True, include_added: bool = False) -> Iterator[dict]:
    """Every composed part, base by base. `full` keeps the program and the measurements.
    `include_added` also reads the sets added on 7 Oct 2026 (ADDED_DIRS), after everything else."""
    for base in BASES:
        yield from _read(GENERATED_DIR / f"composed_{base}.jsonl", False, full, limit_per_file)
    if include_long:
        for base in BASES:
            path = LONG_DIR / f"composed_{base}.jsonl"
            if path.exists():
                yield from _read(path, True, full, limit_per_file)
    if include_added:
        for added, folder in ADDED_DIRS.items():
            for base in BASES:
                path = folder / f"composed_{base}.jsonl"
                if path.exists():
                    yield from _read(path, False, full, limit_per_file, added)
