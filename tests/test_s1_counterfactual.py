"""Counterfactual plans (forge/s1/augment.py, counterfactual.py) and the driver's part
samples and its separate no-repeat-undo rule (forge/s1/drive.py)."""

import random
import sys

import pytest

from forge.freecad.shards import OUT_DIR, complete_shards, read_sessions
from forge.s1 import augment, drive, kaggle_pack
from forge.s1.augment import change_one_number, relabel, swap_two_features
from forge.system1.steps import FEATURES

PLAN = [{"kind": "block", "slots": {"length": 40.0, "width": 30.0, "height": 10.0}},
        {"kind": "hole", "slots": {"diameter": 6.0, "x": 1.0, "y": 2.0}},
        {"kind": "boss", "slots": {"diameter": 8.0, "height": 4.0, "x": -9.0, "y": 0.0}},
        {"kind": "pocket", "slots": {"length": 5.0, "width": 4.0, "depth": 2.0, "x": 9.0, "y": 7.0}},
        {"kind": "top_chamfer", "slots": {"size": 1.0}}]

recorded = pytest.mark.skipif(not (OUT_DIR / "train_v2" / "shard000.stats.json").exists(),
                              reason="the recorded sessions are not on this machine")


def test_a_swap_moves_two_features_and_nothing_else():
    seen = set()
    for seed in range(50):
        changed = swap_two_features(PLAN, random.Random(seed))
        assert changed[0] == PLAN[0] and changed[-1] == PLAN[-1]    # start and treatment stay
        moved = [at for at in range(len(PLAN)) if changed[at] != PLAN[at]]
        assert len(moved) == 2 and all(PLAN[at]["kind"] in FEATURES for at in moved)
        assert sorted(map(str, changed)) == sorted(map(str, PLAN))  # the same items
        seen.add(tuple(moved))
    assert seen == {(1, 2), (1, 3), (2, 3)}
    assert PLAN[1]["kind"] == "hole"                                # the input is not touched
    assert swap_two_features(PLAN[:2], random.Random(0)) is None    # one feature: no swap
    assert swap_two_features([PLAN[0], PLAN[1], PLAN[4]], random.Random(0)) is None


def test_one_number_changes_and_it_is_never_a_count():
    plan = [*PLAN, {"kind": "polar", "slots": {"count": 6, "hole_diameter": 3.0,
                                                "circle_diameter": 20.0}}]
    for seed in range(80):
        changed = change_one_number(plan, random.Random(seed))
        different = [(old, new) for old, new in zip(plan, changed, strict=True) if old != new]
        assert len(different) == 1
        old, new = different[0]
        slots = [name for name in old["slots"] if old["slots"][name] != new["slots"][name]]
        assert len(slots) == 1 and slots[0] != "count"
        assert new["slots"][slots[0]] - old["slots"][slots[0]] in augment.NUMBER_MOVES


@recorded
def test_the_teacher_is_a_function_of_plan_and_state_only():
    """What relabelling rests on: asked again for the recorded plan it gives the recorded
    label; asked twice, or after other questions, it gives the same answer."""
    path = complete_shards(OUT_DIR, ("train_v2",))[0][1]
    checked = changed = 0
    for number, (header, records, _) in enumerate(read_sessions(path)):
        if number == 12:
            break
        rng = random.Random(number)
        swapped = swap_two_features(header["plan"], rng)
        for record in records:
            again = relabel(header["plan"], record["snapshot"], record["valid"])
            assert [t.to_json() for t in again.targets] == record["target"]
            if swapped is None:
                continue
            first = relabel(swapped, record["snapshot"], record["valid"])
            relabel(header["plan"], record["snapshot"], record["valid"])    # something in between
            second = relabel(swapped, record["snapshot"], record["valid"])
            assert first == second
            if first is not None:
                assert set(first.names()) <= set(record["valid"])
                checked += 1
                changed += set(first.names()) != {t["command"] for t in record["target"]}
    assert checked > 200 and 0 < changed < checked


def test_no_counterfactual_is_ever_made_of_a_test_slice(monkeypatch):
    for name in ("iid_v2", "pairing_v2", "long_v2", "xlong_v2", "long"):
        monkeypatch.setattr(sys, "argv", ["augment", "--slices", name])
        with pytest.raises(SystemExit, match="not a training slice"):
            augment.main()


def test_no_test_slice_is_packed_for_kaggle(tmp_path):
    for name in ("iid_v2", "long_v2", "xlong_v2"):
        with pytest.raises(SystemExit, match="not a training slice"):
            kaggle_pack.pack_arrays(tmp_path, tmp_path / "out", ["train_v2", name])


def test_closed_loop_refuses_a_training_slice_and_samples_parts_at_random(monkeypatch):
    ids = [f"part{number:03d}" for number in range(400)]
    by_id = {part_id: {"id": part_id} for part_id in ids}
    monkeypatch.setattr(drive, "usable_parts", lambda name: list(ids))
    seeds = [[part["id"] for part in drive.parts_of_slice("long_v2", 100, by_id, seed * 100)]
             for seed in range(3)]
    assert all(len(lot) == 100 for lot in seeds)
    assert len({part_id for lot in seeds for part_id in lot}) == 300        # no part twice
    assert seeds[0] != ids[:100]                                            # not the first 100
    assert seeds[0] == [p["id"] for p in drive.parts_of_slice("long_v2", 100, by_id, 0)]
    other = [p["id"] for p in drive.parts_of_slice("iid_v2", 100, by_id, 0)]
    assert other != seeds[0]                                                # an order per slice
    first = drive.parts_of_slice("long_v2", 100, by_id, 0, first_sessions=True)
    assert [part["id"] for part in first] == ids[:100]
    assert drive.shards_split("train_long_v2") == "train_long"


def test_the_no_repeat_undo_rule_only_acts_on_an_identical_view():
    ranked = ["undo", "leave_sketch", "done"]
    view_a, view_b = {"snapshot": 1}, {"snapshot": 2}
    # Off: always the model's first choice.
    assert drive.first_choice(ranked, view_a, view_a, False) == ("undo", view_a)
    # On: the first undo in a view is the model's; the same view again gets the second choice.
    assert drive.first_choice(ranked, view_a, None, True) == ("undo", view_a)
    assert drive.first_choice(ranked, view_a, view_a, True) == ("leave_sketch", view_a)
    assert drive.first_choice(ranked, view_b, view_a, True) == ("undo", view_b)
    # A choice that is not undo is never touched, and does not forget the view.
    assert drive.first_choice(["done", "undo"], view_a, view_a, True) == ("done", view_a)
    assert drive.first_choice(["undo"], view_a, view_a, True) == ("undo", view_a)
