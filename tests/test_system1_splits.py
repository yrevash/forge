"""Splits and the longer parts they need."""

import hashlib
import json

from forge.generators.base import check
from forge.sandbox import Sandbox
from forge.system1.long_parts import LONGEST, SHORTEST, long_parts
from forge.system1.splits import (
    HELD_OUT_PAIRS,
    IID_FRACTION,
    MAX_TRAIN_ITEMS,
    feature_kinds,
    held_out_pairs_in,
    split_of,
)
from forge.system1.steps import FEATURES, steps_of
from tests.system1_helpers import as_row, sample_parts


def test_three_or_four_pairs_and_no_kind_is_in_two_of_them():
    assert 3 <= len(HELD_OUT_PAIRS) <= 4
    kinds = [kind for pair in HELD_OUT_PAIRS for kind in pair]
    assert len(kinds) == len(set(kinds)) and set(kinds) <= set(FEATURES)


def test_a_part_goes_to_exactly_one_split_by_these_rules():
    counts = {"train": 0, "iid": 0, "pairing": 0, "long": 0}
    train_kinds, train_pairs = set(), set()
    for part in sample_parts(1500, seed="split"):
        row = as_row(part)
        split = split_of(row)
        counts[split] += 1
        kinds = feature_kinds(part.family, part.params)
        assert (split == "pairing") == bool(held_out_pairs_in(kinds))
        assert len(steps_of(part.family, part.params)) - 1 <= MAX_TRAIN_ITEMS
        assert split_of({**row, "id": "another-id"}) == split       # the id plays no part
        if split == "train":
            train_kinds.update(kinds)
            train_pairs.update((a, b) for a in kinds for b in kinds)
    assert counts["long"] == 0 and counts["pairing"] > 500
    assert abs(counts["iid"] / (counts["iid"] + counts["train"]) - IID_FRACTION) < 0.015
    # Each held-out kind is still seen in training, never with its partner.
    assert train_kinds == set(FEATURES)
    assert not any(pair in train_pairs for pair in HELD_OUT_PAIRS)


def test_parts_with_the_same_shape_share_a_split():
    part = sample_parts(1, seed="shape")[0]
    while held_out_pairs_in(feature_kinds(part.family, part.params)):
        part = sample_parts(1, seed=f"shape-{part.id}")[0]
    for shape in range(200):
        row = {**as_row(part), "geom_fingerprint": f"shape-{shape}"}
        assert split_of(row) == split_of({**row, "id": "twin"}) in ("train", "iid")


def test_long_parts_are_long_and_always_in_the_long_split():
    parts = sample_parts(40, seed="long", features=(SHORTEST, LONGEST))
    lengths = set()
    for part in parts:
        items = len(steps_of(part.family, part.params)) - 1
        lengths.add(items)
        assert SHORTEST <= items <= LONGEST
        assert split_of(as_row(part)) == "long"          # by its length alone
    assert len(lengths) >= 5
    short = sample_parts(1, seed="flag")[0]
    assert split_of(as_row(short, long=True)) == "long"  # and by the flag of its file


def test_asking_for_a_length_gives_exactly_that_length():
    for length in (6, 9, 12):
        for part in sample_parts(10, seed=f"exact-{length}", features=(length, length)):
            assert len(steps_of(part.family, part.params)) - 1 == length


def test_the_long_part_sampler_is_deterministic_and_spreads_the_lengths():
    first = long_parts("ring", 28, seed=4)
    assert [p.id for p in first] == [p.id for p in long_parts("ring", 28, seed=4)]
    lengths = [len(steps_of(p.family, p.params)) - 1 for p in first]
    assert sorted(set(lengths)) == list(range(SHORTEST, LONGEST + 1))
    assert all(lengths.count(n) == 4 for n in set(lengths))


def test_long_parts_pass_the_normal_check_on_the_kernel():
    parts = [part for base in ("block", "cylinder", "hex", "ring")
             for part in long_parts(base, 2, seed=11)]
    with Sandbox(timeout=30) as sandbox:
        for part in parts:
            reply = sandbox.run(part.code)
            assert reply["status"] == "ok", (part.params, reply)
            assert check(part, reply["measure"]) == [], part.params


def test_the_default_sampler_ignores_the_new_argument():
    """`features=None` and no argument at all are the same thing, draw for draw."""
    with_default = sample_parts(100, seed="same")
    digest = hashlib.sha256(json.dumps([p.params for p in with_default]).encode()).hexdigest()
    again = hashlib.sha256(json.dumps(
        [p.params for p in sample_parts(100, seed="same", features=None)]).encode()).hexdigest()
    assert digest == again
