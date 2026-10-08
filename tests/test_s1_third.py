"""The third Forge-S1 model: bindings, the label side, the loss, the features.

No FreeCAD is needed. The tests that read recorded sessions are skipped when the data
folder is not there (the session files are not in git).
"""

from __future__ import annotations

import copy
import json
import math

import numpy as np
import pytest
import torch

from forge.freecad import load, shards
from forge.s1 import vocab
from forge.s1.encode import encode, facts_by_row
from forge.s1.third import bindings
from forge.s1.third.loss import binding_is_right, binding_loss, step_is_right
from forge.s1.third.model import SIZE_CLASSES, build_model, number_features

PLAN = [{"kind": "block", "slots": {"length": 85.0, "width": 60.0, "height": 16.0}},
        {"kind": "shell", "slots": {"wall_thickness": 2.0}},
        {"kind": "pad", "slots": {"length": 15.0, "width": 26.0, "height": 11.0,
                                  "x": 0.0, "y": -24.0}},
        {"kind": "row", "slots": {"count": 3, "hole_diameter": 4.0, "spacing": 10.0, "y": 5.0}}]
SHARD = shards.OUT_DIR / "train_v2" / "shard000.jsonl.gz"
needs_data = pytest.mark.skipif(not SHARD.exists(), reason="the session files are not here")


# --- bindings: arguments from the plan alone ---------------------------------------------------

def test_a_binding_resolves_from_the_plan_alone_and_can_be_wrong():
    right = bindings.resolve(PLAN, "constrain_length", 2, ["slot:length"])
    wrong_slot = bindings.resolve(PLAN, "constrain_length", 2, ["slot:width"])
    wrong_item = bindings.resolve(PLAN, "constrain_length", 0, ["slot:length"])
    assert right == {"value": 15.0}
    assert wrong_slot == {"value": 26.0} and wrong_item == {"value": 85.0}  # executable, wrong


def test_the_rules_are_the_recipes_arithmetic():
    assert bindings.resolve(PLAN, "new_sketch", 2, ["floor"]) == {"offset": 2.0}    # shelled
    assert bindings.resolve(PLAN, "new_sketch", 3, ["base_height"]) == {"offset": 16.0}
    assert bindings.resolve(PLAN[:1] + PLAN[2:], "new_sketch", 1, ["floor"]) == {"offset": 16.0}
    assert bindings.resolve(PLAN, "constrain_x", 3, ["row_first"]) == {"value": -10.0}
    assert bindings.resolve(PLAN, "constrain_x", 3, ["const:0"]) == {"value": 0.0}
    assert bindings.resolve(PLAN, "select_plane", 0, ["const:XY"]) == {"plane": "XY"}
    assert bindings.resolve(PLAN, "sketch_polygon", 0, ["const:6"]) == {"sides": 6}


def test_a_kind_that_cannot_be_read_is_none_and_masked():
    assert bindings.resolve(PLAN, "constrain_length", 1, ["slot:length"]) is None   # no such slot
    assert bindings.resolve(PLAN, "select_plane", 0, ["slot:length"]) is None       # not a word
    mask = bindings.allowed(PLAN, "constrain_length", 2)[0]
    may = {kind for kind, ok in zip(bindings.KINDS, mask, strict=True) if ok}
    assert {"slot:length", "slot:width", "slot:height", "const:0", "floor"} <= may
    assert "none" not in may and "const:XY" not in may and "slot:diameter" not in may
    words = bindings.allowed(PLAN, "select_plane", 0)[0]
    assert {kind for kind, ok in zip(bindings.KINDS, words, strict=True) if ok} \
        == {"const:XY", "const:XZ", "const:YZ"}


def test_a_teachers_target_becomes_kinds_and_resolves_back():
    target = {"command": "hole_counterbore",
              "args": {"diameter": 6.0, "counterbore_diameter": 11.0, "counterbore_depth": 4.0}}
    source = {"item": 1, "sources": {"diameter": "slot:hole_diameter",
                                     "counterbore_diameter": "slot:diameter",
                                     "counterbore_depth": "slot:depth"}}
    plan = [PLAN[0], {"kind": "counterbore", "slots": {"hole_diameter": 6.0, "diameter": 11.0,
                                                       "depth": 4.0, "x": 1.0, "y": 2.0}}]
    assert bindings.kinds_of_target(target, source) \
        == (1, ["slot:hole_diameter", "slot:diameter", "slot:depth"])
    assert bindings.expressible(plan, target, source)
    plan[1]["slots"]["depth"] = 5.0         # the recording and the plan disagree: not expressible
    assert not bindings.expressible(plan, target, source)


# --- the label side -----------------------------------------------------------------------------

def _first_session() -> tuple[dict, list[dict]]:
    header, records, _ = next(iter(shards.read_sessions(SHARD)))
    return header, records


@needs_data
def test_views_without_history_are_what_they_were_and_sources_never_enter_a_view():
    header, records = _first_session()
    plain = [json.dumps(e.view, sort_keys=True) for e in load.examples_of(header, records)]
    assert all("history" not in json.loads(text) for text in plain)
    poisoned = copy.deepcopy(records)
    for record in poisoned:                 # another item, other (listed) sources
        for target in record["target"]:
            target["item"] = 7
            target["sources"] = {name: "floor" for name in target["sources"]}
    again = list(load.examples_of(header, poisoned))
    assert [json.dumps(e.view, sort_keys=True) for e in again] == plain
    assert all(entry["item"] == 7 for e in again for entry in e.sources)
    with_history = list(load.examples_of(header, poisoned, history=2))
    for before, after in zip(again, with_history, strict=True):
        assert {k: v for k, v in after.view.items() if k != "history"} == before.view
        assert "7" not in json.dumps(after.view["history"]) and "floor" not in json.dumps(after.view)


@needs_data
def test_a_source_that_is_not_listed_is_an_error():
    header, records = _first_session()
    records = copy.deepcopy(records)
    target = next(t for r in records for t in r["target"] if t["sources"])
    target["sources"] = {name: "the teacher's secret" for name in target["sources"]}
    with pytest.raises(ValueError, match="argument sources"):
        list(load.examples_of(header, records))


@needs_data
def test_history_is_what_happened_before_the_step_never_the_steps_own_outcome():
    header, records = _first_session()
    before = [e.view for e in load.examples_of(header, records, history=2)]
    assert before[0]["history"] == []
    assert before[2]["history"] == [
        {"command": records[k]["executed"]["command"], "status": records[k]["reply"]["status"]}
        for k in (0, 1)]
    changed = copy.deepcopy(records)
    changed[5]["executed"]["command"], changed[5]["reply"]["status"] = "undo", "rejected"
    after = [e.view for e in load.examples_of(header, changed, history=2)]
    assert after[:6] == before[:6] and after[6] != before[6]     # step 5 itself is unchanged


def test_history_must_be_commands_and_statuses():
    snapshot = {"items": [], "session": {"selection": None, "undo_depth": 0}, "solid": None}
    seen = load.view(PLAN, snapshot, ["undo"], [("pad", "ok")])
    assert seen["history"] == [{"command": "pad", "status": "ok"}]
    with pytest.raises(ValueError):
        load.view(PLAN, snapshot, ["undo"], [("pad", "it worked, the answer is undo")])
    with pytest.raises(ValueError):
        load.view(PLAN, snapshot, ["undo"], [("left_bracket_hole", "ok")])


# --- encoding -----------------------------------------------------------------------------------

@needs_data
def test_the_third_encoding_adds_facts_and_changes_none_of_the_first():
    header, records = _first_session()
    for example in list(load.examples_of(header, records, history=2))[:40]:
        plain = {k: v for k, v in example.view.items() if k != "history"}
        first, third = encode(plain), encode(example.view, third=True)
        assert first.candidates == third.candidates and first.n_rows == third.n_rows
        assert first.num_value == third.num_value and first.num_field == third.num_field
        old = len(vocab.WORDS)
        kept = [(r, w) for r, w in zip(third.word_row, third.word_id, strict=True) if w < old]
        base = list(zip(first.word_row, first.word_id, strict=True))
        # a "nomatch" word may go when the new relation explains the number; nothing else
        assert set(kept) <= set(base)
        assert all(vocab.WORDS[w].startswith("nomatch:") for _, w in set(base) - set(kept))
        session_row = first.n_rows - len(first.candidates) - 2
        added = [vocab.ALL_WORDS[w] for r, w in zip(third.word_row, third.word_id, strict=True)
                 if w >= old]
        assert all(r == session_row for r, w in zip(third.word_row, third.word_id, strict=True)
                   if w >= old)
        assert sum(word.startswith("prev1") for word in added) in (1, 2)


def test_the_first_models_vocabulary_is_untouched_by_the_additions():
    base = vocab.as_json()
    assert base["words"] == list(vocab.ALL_WORDS[:len(base["words"])])
    assert [name for name, _ in base["roles"]] == [n for n, _ in vocab.ALL_ROLES[:len(base["roles"])]]
    assert not any(word.startswith("prev") for word in base["words"])
    third = vocab.third_json()
    assert third["kinds"][0] == "none" and len(third["kinds"]) == len(set(third["kinds"]))
    assert third["history_words"] == [len(base["words"]), len(vocab.ALL_WORDS)]


def test_the_first_hole_of_a_row_is_a_relation_not_a_stray_number():
    sketch = {"type": "sketch", "name": "#1", "body": "#0", "valid": True, "plane": "XY",
              "offset": 16.0, "dof": 0, "closed": True, "used_by": None, "n_geometry": 1,
              "n_constraints": 3,
              "shapes": [{"shape": "circle", "diameter": 4.0, "x": -10.0, "y": 5.0,
                          "fixed": ["diameter", "x", "y"]}]}
    body = {"type": "body", "name": "#0", "body": None, "valid": True, "tip": None,
            "active": True}
    seen = {"plan": PLAN, "valid": ["undo"],
            "snapshot": {"items": [body, sketch], "solid": None,
                         "session": {"document": True, "active_body": "#0", "tip": None,
                                     "open_sketch": "#1", "finished": False, "selection": None,
                                     "can_undo": True}}}
    shape = next(row for row in facts_by_row(encode(seen, third=True))
                 if "row:shape" in row["words"])
    assert ("first:shape.x", 3) in shape["links"] and "nomatch:shape.x" not in shape["words"]
    plain = next(row for row in facts_by_row(encode(seen)) if "row:shape" in row["words"])
    assert "nomatch:shape.x" in plain["words"]          # the first models saw a stray number


# --- number features (the model's side of "relations first") -------------------------------------

def test_a_size_shows_its_class_only_and_a_count_keeps_its_value():
    values = torch.tensor([35.0, 42.0, 420.0, 0.0, -35.0, 41.5])
    size = number_features(values, torch.zeros(6, dtype=torch.bool))
    assert size.shape == (6, 5 + SIZE_CLASSES)
    assert torch.equal(size[0], size[1]) and torch.equal(size[0], size[5])   # 35, 42, 41.5
    assert not torch.equal(size[0], size[2])                                # 420 is another class
    assert size[3, 1] == 1 and size[3, 5:].sum() == 0                       # zero: no class
    assert size[4, 2] == 1 and torch.equal(size[4, 5:], size[0, 5:])        # the sign is a flag
    assert (size[:, 3:5] == 0).all()                    # no raw magnitude for a size
    count = number_features(torch.tensor([1.0, 2.0]), torch.ones(2, dtype=torch.bool))
    assert count[0, 3] != count[1, 3] and (count[:, 5:] == 0).all()


def test_a_broken_solids_huge_numbers_stay_finite():
    huge = torch.tensor([1e100, float("inf"), -float("inf"), float("nan")]).float()
    for is_count in (torch.zeros(4, dtype=torch.bool), torch.ones(4, dtype=torch.bool)):
        assert torch.isfinite(number_features(huge, is_count)).all()
    assert int(number_features(huge[:1], torch.zeros(1, dtype=torch.bool))[0, 5:].argmax()) \
        == SIZE_CLASSES - 1


def test_the_size_class_is_the_formula():
    for value, expected in ((0.05, 1), (1.0, 4), (9.9, 5), (10.0, 6), (31.7, 7), (1000.0, 10)):
        features = number_features(torch.tensor([value]), torch.zeros(1, dtype=torch.bool))
        assert int(features[0, 5:].argmax()) == expected
        assert expected == min(SIZE_CLASSES - 1, max(0, math.floor(2 * math.log10(value)) + 4))


# --- the loss, by hand ------------------------------------------------------------

def test_the_binding_loss_is_the_hand_computed_value():
    # Two queries in step 0, none in step 1, one in step 2.
    item_scores = torch.tensor([[2.0, 0.0], [0.0, 0.0], [1.0, 3.0]])
    kind_logits = torch.zeros(3, 2, 3)              # A = 2 arguments, K = 3 kinds
    kind_logits[0, 0] = torch.tensor([0.0, 1.0, 0.0])
    kind_logits[2, 1] = torch.tensor([0.0, 0.0, 2.0])
    item = torch.tensor([0, 1, 0])
    kinds = torch.tensor([[1, 0], [0, 0], [1, 2]])  # 0 = no such argument
    step = torch.tensor([0, 0, 2])
    got = binding_loss(item_scores, kind_logits, item, kinds, step, n_steps=3)

    def nll(scores: list[float], k: int) -> float:
        return math.log(sum(math.exp(s) for s in scores)) - scores[k]
    q0 = nll([2, 0], 0) + nll([0, 1, 0], 1)                     # one argument
    q1 = nll([0, 0], 1) + 0.0                                   # no argument
    q2 = nll([1, 3], 0) + (nll([0, 0, 0], 1) + nll([0, 0, 2], 2)) / 2
    assert got.tolist() == pytest.approx([(q0 + q1) / 2, 0.0, q2], abs=1e-6)


def test_a_step_is_right_only_with_the_command_and_its_binding():
    needs = torch.tensor([False, True, False])      # kind 1 reads the plan item
    item_scores = torch.tensor([[2.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    logits = torch.zeros(3, 1, 3)
    logits[0, 0, 1] = logits[1, 0, 2] = logits[2, 0, 1] = 5.0
    item, kinds = torch.tensor([0, 0, 0]), torch.tensor([[1], [2], [1]])
    right = binding_is_right(item_scores, logits, item, kinds, needs)
    # query 0: all right. query 1: wrong item but a constant, so the item does not matter.
    # query 2: the right kind, read from the wrong item.
    assert right.tolist() == [True, True, False]
    scores = torch.tensor([[0.0, 3.0, 1.0], [0.0, 1.0, 3.0], [0.0, 3.0, 1.0]])
    is_candidate = torch.tensor([[False, True, True]] * 3)
    is_target = torch.tensor([[False, True, False], [False, True, False], [False, True, False]])
    command, both = step_is_right(scores, is_candidate, is_target, right,
                                  step=torch.tensor([0, 1, 2]), row=torch.tensor([1, 1, 1]))
    assert command.tolist() == [True, False, True] and both.tolist() == [True, False, False]


# --- the model reads no label --------------------------------------------------------------------

@needs_data
def test_scores_do_not_depend_on_the_bindings_and_history_can_be_switched_off(tmp_path):
    from forge.s1.third.data import BindData
    from forge.s1.third.prepare import Shard
    header, records = _first_session()
    shard = Shard()
    shard.session_ids.append(header["session"])
    for record in records[:30]:
        shard.add(header["plan"], record["snapshot"], record["valid"],
                  load.history_before(records, record["t"], 2), record["target"], 0, record["t"])
    assert shard.counts["targets"] == shard.counts["targets_expressible"]
    arrays = shard.arrays()
    roles = [table == "doc" for _, table in vocab.third_json()["roles"]]
    torch.manual_seed(0)
    config = {"width": 32, "layers": 1, "heads": 2, "mlp_width": 64}
    model = build_model(config, vocab.third_json()).eval()
    steps = torch.arange(30)
    scores, _ = model(BindData(arrays, "cpu", roles).batch(steps))
    scrambled = dict(arrays)
    rng = np.random.default_rng(0)
    for name in ("bind_item", "bind_k0", "bind_k1", "bind_k2", "target", "group"):
        scrambled[name] = rng.permutation(arrays[name])
    again, _ = model(BindData(scrambled, "cpu", roles).batch(steps))
    assert torch.equal(scores, again)

    blind = build_model({**config, "use_history": False}, vocab.third_json()).eval()
    one = blind(BindData(arrays, "cpu", roles).batch(steps))[0]
    first = vocab.third_json()["history_words"][0]
    other = dict(arrays)
    words = arrays["word_id"].copy()
    words[words >= first] = first           # every history word becomes "prev1=None"
    other["word_id"] = words
    assert torch.equal(one, blind(BindData(other, "cpu", roles).batch(steps))[0])
    assert not torch.equal(scores, model(BindData(other, "cpu", roles).batch(steps))[0])


# --- training: it learns the binding, and a resumed run is the unbroken run ----------------------

def _prepared(folder, sessions: int = 6) -> None:
    """A small prepared folder made of real recorded sessions (train_v2, shard 0)."""
    from forge.s1.third.prepare import ALL_GROUPS, Shard
    shard = Shard()
    for count, (header, records, _) in enumerate(shards.read_sessions(SHARD)):
        if count == sessions:
            break
        shard.session_ids.append(header["session"])
        for record in records:
            shard.add(header["plan"], record["snapshot"], record["valid"],
                      load.history_before(records, record["t"], 2), record["target"], 8,
                      record["t"])
    (folder / "toy").mkdir(parents=True)
    np.savez_compressed(folder / "toy" / "shard000.npz", **shard.arrays())
    manifest = {"vocab": vocab.third_json(), "vocab_hash": vocab.third_hash(), "code_hash": "toy",
                "group_bits": ALL_GROUPS, "slices": {"toy": {"shards": [{"file": "toy/shard000.npz"}]}}}
    (folder / "manifest.json").write_text(json.dumps(manifest))


def _config(folder, max_steps: int) -> dict:
    return {"name": "toy3", "seed": 3,
            "data": {"folder": str(folder), "data_device": "cpu", "train_slices": ["toy"],
                     "train_shards": None, "monitor_slice": "toy", "monitor_steps": 200},
            "model": {"width": 48, "layers": 2, "heads": 2, "mlp_width": 96},
            "train": {"batch_size": 32, "passes": 1.0, "max_steps": max_steps, "lr": 3e-3,
                      "min_lr": 0.0, "warmup_steps": 4, "weight_decay": 0.01, "grad_clip": 1.0,
                      "random_ids": False, "mixed_precision": True},
            "log_every": 10, "eval_every": 1000, "checkpoint_minutes": 1000}


@needs_data
def test_the_third_model_learns_commands_and_bindings_and_resumes_exactly(tmp_path):
    from forge.s1.third.train import ThirdKit
    from forge.s1.train import train
    _prepared(tmp_path / "data")
    cpu = torch.device("cpu")
    whole = train(_config(tmp_path / "data", 300), tmp_path / "whole", device=cpu, kit=ThirdKit)
    table = whole["monitor"]
    assert table["command_only"]["all steps"] > 0.8         # 200 steps it was trained on
    assert table["accuracy"]["all steps"] > 0.6             # command AND binding
    assert table["accuracy"]["all steps"] <= table["command_only"]["all steps"]
    train(_config(tmp_path / "data", 40), tmp_path / "unbroken", device=cpu, kit=ThirdKit)
    train(_config(tmp_path / "data", 40), tmp_path / "broken", device=cpu, kit=ThirdKit,
          stop_at_step=25)
    train(_config(tmp_path / "data", 40), tmp_path / "broken", tmp_path / "broken" / "last.pt",
          device=cpu, kit=ThirdKit)
    a = torch.load(tmp_path / "unbroken" / "final.pt", weights_only=True)["model"]
    b = torch.load(tmp_path / "broken" / "final.pt", weights_only=True)["model"]
    assert a.keys() == b.keys() and all(torch.equal(a[name], b[name]) for name in a)


# --- what may be trained on, rolled out on, and uploaded -----------------------------------------

def test_dagger_and_the_upload_refuse_every_test_slice():
    from forge.s1.third import dagger, kaggle_pack
    from forge.s1.third.prepare import TEST_SLICES
    assert set(TEST_SLICES) >= {"iid_v2", "pairing_v2", "long_v2", "xlong_v2", "wide_iid_v3",
                                "wide_numbers_v3", "wide_order_v3", "abnormal_starts_v3"}
    for name in TEST_SLICES:
        with pytest.raises(SystemExit):
            dagger.refuse_tests([name])
        assert not kaggle_pack.is_train_name(name) and not kaggle_pack.is_train_name(name + "_sc")
    dagger.refuse_tests(["train_v2", "train_long_v2", "train_wide_v3"])
    assert kaggle_pack.is_train_name("train_v2_cf") and kaggle_pack.is_train_name("dagger_r1")
    for name, (held_out, _) in dagger.SOURCES.items():      # no round touches a held-out shard
        for round_number in (1, 2, 3):
            assert not set(dagger.shards_of_round(name, round_number)) & set(held_out)
    with pytest.raises(SystemExit):
        dagger.shards_of_round("train_long_v2", 27)


@needs_data
def test_a_live_decision_is_a_command_with_arguments_read_from_the_plan():
    import random

    from forge.freecad.catalogue import COMMANDS
    from forge.s1.third.live import decide
    header, records = _first_session()
    torch.manual_seed(1)
    model = build_model({"width": 32, "layers": 1, "heads": 2, "mlp_width": 64},
                        vocab.third_json()).eval()
    plan_numbers = {float(v) for item in header["plan"] for v in item["slots"].values()}
    with_args = 0
    for example in list(load.examples_of(header, records, history=2))[:25]:
        for explore in (None, (random.Random(0), 2.0)):
            decision = decide(model, example.view, explore=explore)
            assert decision.command in example.view["valid"]
            assert sorted(decision.ranked) == sorted(example.view["valid"])
            assert decision.args is not None            # the mask leaves only readable kinds
            assert set(decision.args) == set(COMMANDS[decision.command]["args"])
            for kind, value in zip(decision.kinds, decision.args.values(), strict=True):
                if kind.startswith("slot:"):            # an untrained model: any slot, but a slot
                    assert float(value) in plan_numbers
            with_args += bool(decision.args)
    assert with_args > 0


@needs_data
def test_the_no_repeat_rule_takes_the_next_best_command_and_only_when_asked():
    """The driver-side extra: `avoid` moves the decision to the
    next command of the model's own ranking; without it, or with nothing left, nothing moves."""
    from forge.s1.third.live import decide
    header, records = _first_session()
    torch.manual_seed(1)
    model = build_model({"width": 32, "layers": 1, "heads": 2, "mlp_width": 64},
                        vocab.third_json()).eval()
    moved = 0
    for example in list(load.examples_of(header, records, history=2))[:25]:
        plain = decide(model, example.view)
        assert plain.command == plain.ranked[0]
        assert decide(model, example.view, avoid=lambda command, args: False) == plain
        assert decide(model, example.view, avoid=lambda command, args: True) == plain
        banned = (plain.command, json.dumps(plain.args, sort_keys=True))
        other = decide(model, example.view, avoid=lambda command, args, banned=banned:
                       (command, json.dumps(args, sort_keys=True)) == banned)
        if len(plain.ranked) > 1:
            assert other.command == plain.ranked[1] and other.ranked == plain.ranked
            moved += 1
    assert moved > 0


@needs_data
def test_patience_stops_a_run_whose_held_out_score_no_longer_improves(tmp_path):
    from forge.s1.third.train import ThirdKit
    from forge.s1.train import train
    _prepared(tmp_path / "data")
    config = _config(tmp_path / "data", 400)
    config["train"].update(lr=0.0, patience=2)      # nothing is learned, so nothing improves
    config["eval_every"] = 5
    train(config, tmp_path / "run", device=torch.device("cpu"), kit=ThirdKit)
    rows = [json.loads(line) for line in (tmp_path / "run" / "metrics.jsonl").open()]
    end = rows[-1]
    assert end["event"] == "end" and end["step"] == 15 and "no improvement" in end["stopped"]
    assert (tmp_path / "run" / "best.pt").exists() and (tmp_path / "run" / "final.pt").exists()


@needs_data
def test_a_checkpoint_is_plain_data_and_opens_without_running_anything(tmp_path):
    from forge.s1.third.train import ThirdKit
    from forge.s1.train import load_checkpoint, plain, train
    _prepared(tmp_path / "data")
    config = _config(tmp_path / "data", 6)
    config["data"]["folder"] = tmp_path / "data"            # a Path: must be stored as text
    train(config, tmp_path / "run", device=torch.device("cpu"), kit=ThirdKit)

    def check(value: object) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                assert isinstance(key, (str, int))
                check(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                check(inner)
        else:
            assert value is None or isinstance(value, (bool, int, float, str, torch.Tensor)), \
                type(value)
    for name in ("last.pt", "best.pt", "final.pt"):
        state = load_checkpoint(tmp_path / "run" / name)    # weights_only=True inside
        check(state)
        assert state["config"]["data"]["folder"] == str(tmp_path / "data")
    with pytest.raises(TypeError):
        plain({"model": object()})


@needs_data
def test_steps_can_be_selected_by_plan_length_with_exactly_their_facts(tmp_path):
    from forge.s1.third.data import BindData
    _prepared(tmp_path / "data", sessions=12)
    everything = BindData.load(tmp_path / "data", ["toy"], "cpu")
    lengths = sorted(set(everything.t["n_plan"].tolist()))
    assert len(lengths) > 1
    cut = lengths[len(lengths) // 2]
    short = BindData.load(tmp_path / "data", ["toy"], "cpu", plan_items=[1, cut - 1])
    long = BindData.load(tmp_path / "data", ["toy"], "cpu", plan_items=[cut, 99])
    assert len(short) + len(long) == len(everything) and len(short) and len(long)
    assert int(short.t["n_plan"].max()) < cut <= int(long.t["n_plan"].min())
    where = (everything.t["n_plan"] >= cut).nonzero()[:, 0]
    a, b = everything.batch(where[:40]), long.batch(torch.arange(40))
    for name in ("word_id", "num_value", "link_target", "bind_kinds", "bind_item", "is_target"):
        assert torch.equal(a[name], b[name]), name
