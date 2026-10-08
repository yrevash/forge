"""Every constant of the plans data set in one place: paths, versions, grids, minimums, splits.

All values are OUR CHOICES (5-6 Oct 2026). Nothing here is measured; the measurements are in
the manifest the build writes (data/plans/manifest.json).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from forge.runs import PROJECT_ROOT

# FORGE_PLANS_OUT points the whole package at another folder (the tests use a temp folder).
OUT_DIR = Path(os.environ.get("FORGE_PLANS_OUT", PROJECT_ROOT / "data" / "plans"))
LANGUAGE = "docs/PLAN_LANGUAGE.md Draft 2"
LICENSE = "forge"
DATA_VERSION = "plans-v1"
SHARD_ROWS = 2000                      # plans per shard file

SOURCES = ("random", "negatives", "structures", "parts")
SPLITS = ("train", "iid", "combo", "long", "kinds")

# --- sizes: millimetres on plain grids ------------------------------------------------------
BASE_LONG = tuple(range(120, 601, 20))        # the long sides of a first slab
BASE_THICK = (10, 15, 20, 25, 30, 40, 50, 60)
BLOCK = tuple(range(40, 241, 10))             # a chunky part
SMALL = (5, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60)
HEIGHTS = tuple(range(100, 601, 50))          # `top at height H` for a raised slab
AMOUNTS = (5, 10, 15, 20, 25, 30, 40, 50)     # insets, gaps, offsets

# --- plan lengths (lines of text, `done` included) ------------------------------------------
MAX_TRAIN_LINES = 25          # no random plan outside `long` is longer than this
LONG_LINES = (30, 45)         # a `long` random plan has between these many lines

# --- coverage: targets and floors -----------------------------------------------------------
# A cell is "arithmetic" when plans that hit it can be resolved without the CAD kernel
# (plain boxes and cylinders, no features), else "kernel": one sandbox call per line.
# TARGET_*: proposed before anything was generated.
TARGET_ARITHMETIC = 3000
TARGET_KERNEL = 300
TARGET_PAIR_ARITHMETIC = 200
TARGET_PAIR_KERNEL = 40
TARGET_REPLY = 1000           # each rejection reply, in the negative slice
# MIN_*: the floor the audit enforces, in the TRAIN split. Set on 6 Oct 2026 from the
# measured throughput of that night (2 kernel workers on a machine shared with FreeCAD
# jobs, load average 30 to 60): see forge/plans_data/README.md, "Results".
MIN_ARITHMETIC = 2000
MIN_KERNEL = 30
MIN_PAIR_ARITHMETIC = 200
MIN_PAIR_KERNEL = 5
MIN_REPLY = 500
# Plans built on the CAD kernel (forge.resolve.verify), across the sources.
TARGET_KERNEL_BUILT = 5000
MIN_KERNEL_BUILT = 5000

# --- splits (fixed before anything is written) ----------------------------------------------
IID_FRACTION = 0.05
SPLIT_SEED = 0
# Held-out COMBINATIONS: a plan with a built line that hits both cells of a pair goes to
# `combo`. Each cell on its own stays in training. The first six are pairs of cells on ONE
# line; the feature pairs are two feature kinds on one PART (the pairs forge.system1.splits
# already holds out, so single parts keep their split).
HELD_OUT_LINE_PAIRS: tuple[tuple[str, str], ...] = (
    ("place:behind", "target:prism"),              # a placement with the shape it is put against
    ("place:under", "target:tube"),
    ("place:left of", "target:cone"),
    ("rep:grid", "place:in front of"),             # a repetition with a placement
    ("rep:spread", "place:right of"),
    ("align:offset", "place:between"),             # an alignment with a placement
)
# Structure kinds held out whole: two furniture kinds and one mechanical kind. Chosen on
# 6 Oct 2026 from the registry as it stood at 01:10 (18 kinds), before any plan was written.
# A kind added to the registry later is trained on.
HELD_OUT_KINDS = ("stool", "shelf", "standoffs")


def held_out_kinds(all_kinds: list[str]) -> tuple[str, ...]:
    return tuple(kind for kind in HELD_OUT_KINDS if kind in all_kinds)


def unit_hash(text: str) -> float:
    """A stable number in [0, 1) from a string (the same rule forge.system1.splits uses)."""
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16) / 16**12


def code_version() -> str:
    """A hash of this package, the reader and the resolver: everything a record depends on."""
    digest = hashlib.sha256()
    for package in ("plans_data", "plan", "resolve"):
        for path in sorted((PROJECT_ROOT / "forge" / package).glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def package_version(package: str) -> str:
    digest = hashlib.sha256()
    for path in sorted((Path(PROJECT_ROOT) / "forge" / package).rglob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]
