"""The long training parts and the extra-long test: the splits that were there
stay exactly as they were, and a row cannot talk its way into training."""

import hashlib
import json
import random
from collections import Counter
from pathlib import Path

import pytest

from forge.freecad import shards
from forge.freecad.parts import session_parts
from forge.generators import FAMILIES
from forge.system1.parts import ADDED_DIRS, GENERATED_DIR, LONG_DIR
from forge.system1.splits import ADDED_SPLITS, held_out_pairs_in, split_of
from forge.system1.steps import steps_of

# Computed on 7 Oct 2026 at commit 16f1236, BEFORE train_long and xlong were added: the
# sha256 of the sorted (part id, split) pairs of the 124,000 stored parts, and of every
# shard's (slice, number, part ids) as `sessions.plan_shards` lays them out.
ASSIGNMENT_SHA256 = "5264ec22d1bc877ccfe93d64b8ba91e9614a87f76a6f20c9ba4425a5f820e157"
SHARD_PLAN_SHA256 = "62e04759a3d6aeb0cecbf299a3c9f7e37888b7710fec669aeda68f06d6696937"
SIZES = {"train": 97971, "pairing": 16896, "iid": 5133, "long": 4000}
OLD_SLICES = ("iid", "pairing", "long", "train", "train_heavy", "train_s1", "train_s2",
              "iid_v2", "pairing_v2", "long_v2", "train_v2")

stored = pytest.mark.skipif(
    not (GENERATED_DIR / "composed_block.jsonl").exists()
    or not (LONG_DIR / "composed_block.jsonl").exists(),
    reason="the stored parts (data/generated, data/system1/long_parts) are not on this machine")
added = pytest.mark.skipif(
    not (ADDED_DIRS["train_long"] / "composed_block.jsonl").exists(),
    reason="the added long parts are not on this machine")


def _part(features: int, pairing: bool, seed: int = 0) -> dict:
    """A freshly sampled block part with this many features, with or without a held-out pairing."""
    rng = random.Random(f"test:{seed}:{features}:{pairing}")
    while True:
        part = FAMILIES["composed_block"].sample(rng, features=(features, features))
        steps = steps_of(part.family, part.params)
        if len(steps) - 1 == features \
                and bool(held_out_pairs_in([step.kind for step in steps])) == pairing:
            return {"id": part.id, "family": part.family, "params": part.params,
                    "geom_fingerprint": part.id, "long": False}


@stored
def test_the_split_of_every_stored_part_is_what_it_was():
    pairs = sorted((part["id"], split_of(part)) for part in session_parts())
    assert dict(Counter(split for _, split in pairs)) == SIZES
    assert hashlib.sha256(json.dumps(pairs).encode()).hexdigest() == ASSIGNMENT_SHA256


@stored
def test_the_shards_of_every_older_slice_hold_the_parts_they_held():
    from forge.freecad.sessions import plan_shards
    tasks = [(name, number, [part["id"] for part in parts])
             for name, number, parts, _ in plan_shards(Path("unused")) if name in OLD_SLICES]
    assert len(tasks) == 920
    assert hashlib.sha256(json.dumps(tasks).encode()).hexdigest() == SHARD_PLAN_SHA256


def test_a_part_without_the_set_field_never_reaches_an_added_split():
    for features in (3, 8):
        for pairing in (False, True):
            assert split_of(_part(features, pairing)) in ("train", "iid", "pairing", "long")
    assert split_of(_part(8, False)) == "long"          # a long part is a test part by default
    assert split_of({**_part(8, False), "long": True}) == "long"


def test_train_long_refuses_a_held_out_pairing_and_a_wrong_length():
    assert split_of({**_part(8, False), "set": "train_long"}) == "train_long"
    assert split_of({**_part(14, False), "set": "xlong"}) == "xlong"
    with pytest.raises(ValueError, match="held-out pairing"):
        split_of({**_part(8, True), "set": "train_long"})
    with pytest.raises(ValueError, match="outside"):
        split_of({**_part(3, False), "set": "train_long"})
    with pytest.raises(ValueError, match="outside"):
        split_of({**_part(8, False), "set": "xlong"})
    with pytest.raises(KeyError):
        split_of({**_part(8, False), "set": "train"})    # only the two added names exist


def test_the_added_slices_have_splits_of_their_own():
    assert shards.SLICES["train_long_v2"][0] == "train_long"
    assert shards.SLICES["xlong_v2"][0] == "xlong"
    assert "train_long" in shards.TRAIN_SPLITS and "xlong" in shards.TEST_SPLITS
    assert not set(shards.TRAIN_SPLITS) & set(shards.TEST_SPLITS)
    assert set(ADDED_SPLITS) == {"train_long", "xlong"}
    # The long test and the long training parts overlap in length, on purpose; the
    # extra-long test starts where training ends.
    assert ADDED_SPLITS["xlong"][0] == ADDED_SPLITS["train_long"][1] + 1
    for name in ("train_long_v2", "xlong_v2"):
        assert name not in shards.FROZEN and name not in shards.TRAIN_SLICES


def test_the_audit_compares_train_long_with_every_test_split():
    from forge.freecad.audit import shared_content
    content = {"train_long": {("plan-a", "shape-a"), ("plan-b", "shape-b")},
               "long": {("plan-c", "shape-c")}, "xlong": {("plan-d", "shape-d")}}
    assert shared_content(content)[0] == []
    content["long"].add(("plan-a", "shape-z"))
    content["xlong"].add(("plan-y", "shape-b"))
    problems = shared_content(content)[0]
    assert any("plans are in both train_long and long" in line for line in problems)
    assert any("geometry fingerprints are in both train_long and xlong" in line
               for line in problems)


@stored
@added
def test_the_added_parts_share_nothing_with_any_other_part():
    from forge.freecad.long_train_parts import leak_check
    report = leak_check()
    assert report["passed"], report
    for entry in report["sets"].values():
        assert entry["parts_with_a_held_out_pairing"] == 0
        assert entry["parts"] == entry["distinct_plans"] == entry["distinct_shapes"]
        for split in ("train", "iid", "pairing", "long"):
            assert entry["shared_with"][split] == {
                "ids": 0, "plans": 0, "shapes": 0, "parts_there": SIZES[split]}
