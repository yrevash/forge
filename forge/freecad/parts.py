"""Pick verified composed parts to build in FreeCAD. Reads data/, never writes it.

    data/generated/composed_<base>.jsonl            parts with 1 to 5 features
    data/system1/long_parts/composed_<base>.jsonl   parts with 6 to 12 features (test only)
Added 7 Oct 2026 (long_train_parts.py); read only when `added=True` is asked for:
    data/system1/long_train_parts/composed_<base>.jsonl   6 to 12 features, for TRAINING
    data/system1/xlong_parts/composed_<base>.jsonl        13 to 15 features, test only
Each row has `id`, `family`, `params` and `measured` (what the CadQuery kernel built).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from forge.system1.parts import ADDED_DIRS, BASES, GENERATED_DIR, LONG_DIR

FIELDS = ("id", "family", "params", "measured")


def _files() -> list[tuple[Path, bool]]:
    return ([(GENERATED_DIR / f"composed_{base}.jsonl", False) for base in BASES]
            + [(LONG_DIR / f"composed_{base}.jsonl", True) for base in BASES])


def _row(line: str, long: bool) -> dict:
    row = json.loads(line)
    return {**{key: row[key] for key in FIELDS}, "long": long}


def sample_rows(per_base: int, long_per_base: int, seed: int = 0) -> list[dict]:
    """A random sample: `per_base` normal and `long_per_base` long parts of each base."""
    rows = []
    for path, long in _files():
        wanted = long_per_base if long else per_base
        if wanted == 0 or not path.exists():
            continue
        lines = path.read_text().splitlines()
        rng = random.Random(f"{seed}:{path.name}:{long}")
        rows += [_row(line, long) for line in rng.sample(lines, min(wanted, len(lines)))]
    return rows


def find_row(part_id: str) -> dict | None:
    """The stored row with this id, or None."""
    for path, long in _files():
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                if part_id in line[:40]:        # the id is the first field of every row
                    row = _row(line, long)
                    if row["id"] == part_id:
                        return row
    return None


SESSION_FIELDS = ("id", "family", "params", "measured", "geom_fingerprint", "source", "license",
                  "generator_version")


def session_parts(limit_per_file: int | None = None, added: bool = False) -> list[dict]:
    """Every composed part with what a session needs: its parameters, its stored measurement,
    what its split is decided from, and its provenance. Normal parts first, then long ones.

    `added=True` appends the sets of 7 Oct 2026 (train_long, xlong). Their rows carry `set`;
    the rows of the older parts are exactly what they were."""
    parts = []
    for path, long in _files():
        if not path.exists():
            continue
        with path.open() as f:
            for count, line in enumerate(f):
                if limit_per_file is not None and count >= limit_per_file:
                    break
                row = json.loads(line)
                parts.append({**{key: row[key] for key in SESSION_FIELDS}, "long": long})
    for name, folder in ADDED_DIRS.items() if added else ():
        for base in BASES:
            path = folder / f"composed_{base}.jsonl"
            if not path.exists():
                continue
            with path.open() as f:
                for count, line in enumerate(f):
                    if limit_per_file is not None and count >= limit_per_file:
                        break
                    row = json.loads(line)
                    parts.append({**{key: row[key] for key in SESSION_FIELDS}, "long": False,
                                  "set": name})
    return parts
