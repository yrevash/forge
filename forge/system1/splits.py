"""Which split a part belongs to.

A part's split is decided from the part alone, before any session exists, and
every session of a part goes to that split.

    long     6 or more items after the start (generated only for testing, long_parts.py)
    pairing  contains one of the HELD_OUT_PAIRS of feature kinds
    iid      5% of the rest, chosen by a hash of the geometry
    train    everything else

Two splits were added on 7 Oct 2026. A part is in one of them only if its row
says so (`set`, given by the folder it is read from: forge/system1/parts.py ADDED_DIRS).
No part of the four splits above carries that field, so their assignment cannot change.
    train_long  6 to 12 items after the start, NO held-out pairing: long parts for training.
                They are new parts; none is a part of `long` (the generator and the audit
                compare ids, plans and geometry).
    xlong       13 to 15 items after the start, no held-out pairing: a test of length alone,
                beyond anything in training. Never trained on.

Run `uv run python -m forge.system1.splits` to print the co-occurrence counts
the held-out pairs were chosen from, and the size of every split.
"""

from __future__ import annotations

import hashlib
import itertools
from collections import Counter

from forge.system1.parts import read_parts
from forge.system1.steps import FEATURES, Number, steps_of

SPLITS = ("train", "iid", "pairing", "long", "train_long", "xlong")
# The added splits: name -> (fewest, most) items after the start. See the top of this file.
ADDED_SPLITS = {"train_long": (6, 12), "xlong": (13, 15)}
# Train never holds a part with more items after the start than this (edge treatment included).
MAX_TRAIN_ITEMS = 5
IID_FRACTION = 0.05
SPLIT_SEED = 0

# Pairs of feature kinds that never occur together in training. Chosen on 5 Oct 2026 from
# the counts over the 120,000 parts (print them with this module's command):
# - no kind is in two pairs, so each of the eight kinds is still seen in training many
#   thousands of times without its partner;
# - each pair mixes two different sorts of feature (something added with something cut, a
#   single with a pattern, a single with a mirrored pair), which is the kind of new
#   combination the test is about;
# - together they take about 14% of the parts, leaving more than 95,000 for training.
HELD_OUT_PAIRS: tuple[tuple[str, str], ...] = (
    ("boss", "slot"),                 # an added round feature with a milled cut
    ("counterbore", "polar"),         # a single stepped hole with a circle of holes
    ("blind_hole", "boss_pair"),      # a single cut with a mirrored pair of bosses
    ("pad", "hole_pair"),             # an added rectangle with a mirrored pair of holes
)


def unit_hash(text: str) -> float:
    """A stable number in [0, 1) from a string. Same text, same number, every run."""
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16) / 16**12


def feature_kinds(family: str, params: dict[str, Number]) -> list[str]:
    """The feature kinds of a part, in build order (start and edge treatment left out)."""
    return [step.kind for step in steps_of(family, params) if step.kind in FEATURES]


def held_out_pairs_in(kinds: list[str]) -> list[tuple[str, str]]:
    present = set(kinds)
    return [pair for pair in HELD_OUT_PAIRS if pair[0] in present and pair[1] in present]


def split_of(part: dict) -> str:
    """The split of one part row (needs family, params, geom_fingerprint and the `long` flag)."""
    steps = steps_of(part["family"], part["params"])
    added = part.get("set")
    if added is not None:
        # A row cannot simply declare itself training data: the rules are checked here.
        low, high = ADDED_SPLITS[added]
        if not low <= len(steps) - 1 <= high:
            raise ValueError(f"part {part.get('id')}: {len(steps) - 1} items is outside "
                             f"{added} ({low} to {high})")
        if held_out_pairs_in([step.kind for step in steps]):
            raise ValueError(f"part {part.get('id')}: a held-out pairing in {added}")
        return added
    if part.get("long") or len(steps) - 1 > MAX_TRAIN_ITEMS:
        return "long"
    if held_out_pairs_in([step.kind for step in steps]):
        return "pairing"
    # By geometry, not by id: two parts with the same shape always land on the same side.
    return "iid" if unit_hash(f"{SPLIT_SEED}:{part['geom_fingerprint']}") < IID_FRACTION else "train"


def main() -> None:
    together: Counter = Counter()
    alone: Counter = Counter()
    sizes: Counter = Counter()
    train_kinds: Counter = Counter()
    train_pairs: Counter = Counter()
    shapes: dict[str, set[str]] = {name: set() for name in SPLITS}
    for part in read_parts():
        kinds = sorted(set(feature_kinds(part["family"], part["params"])))
        split = split_of(part)
        sizes[split] += 1
        shapes[split].add(part["geom_fingerprint"])
        if part["long"]:
            continue
        alone.update(kinds)
        together.update(itertools.combinations(kinds, 2))
        if split == "train":
            train_kinds.update(kinds)
            train_pairs.update(itertools.combinations(kinds, 2))

    print("parts with both kinds, over the parts of 1 to 5 features (held-out pairs marked):")
    held = {tuple(sorted(pair)) for pair in HELD_OUT_PAIRS}
    for pair, count in sorted(together.items(), key=lambda item: -item[1]):
        print(f"  {pair[0]:12s} {pair[1]:12s} {count:6d}{'   HELD OUT' if pair in held else ''}")
    print("\nheld-out pairs: parts with both, and parts in train with each kind alone")
    for a, b in HELD_OUT_PAIRS:
        print(f"  {a} + {b}: {together[tuple(sorted((a, b)))]} parts held out; in train "
              f"{a} appears in {train_kinds[a]} parts, {b} in {train_kinds[b]}, "
              f"both in {train_pairs[tuple(sorted((a, b)))]}")
    print("\nparts per split:", {name: sizes[name] for name in SPLITS})
    for name in ("iid", "pairing", "long"):
        print(f"  shapes shared between train and {name}: {len(shapes['train'] & shapes[name])}")


if __name__ == "__main__":
    main()
