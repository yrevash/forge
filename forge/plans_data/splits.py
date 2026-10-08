"""Which split a plan belongs to. Decided from the plan alone; it never changes.

    kinds   structures only: the kind is one of config.HELD_OUT_KINDS (held out whole)
    long    longer than any plan in training (per source, see `is_long`)
    combo   holds a held-out COMBINATION: a built line that pairs two cells of
            config.HELD_OUT_LINE_PAIRS, or two feature kinds of
            forge.system1.splits.HELD_OUT_PAIRS on one part
    iid     5% of the rest, by a hash of the plan's id
    train   everything else

Single parts keep the split forge.system1.splits gave them (its `pairing` is `combo` here).
"""

from __future__ import annotations

from forge.plan.model import Line
from forge.plans_data import config, coverage
from forge.system1.splits import HELD_OUT_PAIRS
from forge.system1.splits import split_of as part_split

PART_SPLIT = {"train": "train", "iid": "iid", "pairing": "combo", "long": "long"}


def held_out_combinations(lines: list[Line], replies: list[str]) -> list[str]:
    """The held-out combinations a plan contains, as text (empty: none)."""
    found = []
    for tags in coverage.line_cell_sets(lines, replies):
        for a, b in config.HELD_OUT_LINE_PAIRS:
            if a in tags and b in tags:
                found.append(f"{a} + {b}")
    for kinds in coverage.feature_kinds_by_part(lines, replies).values():
        for a, b in HELD_OUT_PAIRS:
            if a in kinds and b in kinds:
                found.append(f"feature:{a} + feature:{b}")
    return sorted(set(found))


def is_long(n_lines: int) -> bool:
    return n_lines > config.MAX_TRAIN_LINES


def assign(source: str, record_id: str, lines: list[Line], replies: list[str],
           kind: str | None = None, part: dict | None = None,
           held_out_kinds: tuple[str, ...] = ()) -> tuple[str, list[str]]:
    """(split, the held-out combinations found)."""
    combos = held_out_combinations(lines, replies)
    if source == "parts":
        return PART_SPLIT[part_split(part)], combos
    if source == "structures" and kind in held_out_kinds:
        return "kinds", combos
    if is_long(len(lines)):
        return "long", combos
    if combos:
        return "combo", combos
    iid = config.unit_hash(f"{config.SPLIT_SEED}:{record_id}") < config.IID_FRACTION
    return ("iid" if iid else "train"), combos
