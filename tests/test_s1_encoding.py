"""The Forge-S1 encoding: nothing lost, wrong numbers visible, nothing of the label inside."""

import copy
import itertools

import numpy as np
import pytest
import torch

from forge.freecad.catalogue import COMMANDS
from forge.freecad.load import examples_of, usable
from forge.freecad.shards import read_sessions
from forge.s1 import vocab
from forge.s1.data import DOC_IDS, PLAN_IDS, row_ids
from forge.s1.encode import decode, encode, facts_by_row, ordinals, target_bits
from forge.s1.live import steps_data
from forge.s1.prepare import FACT_ARRAYS, STEP_ARRAYS, arrays_of_shard
from tests.s1_helpers import (
    first_sessions,
    real_shard,
    sketch_view,
    solid_view,
    write_shard,
)


def shape_row(view: dict) -> dict:
    return next(row for row in facts_by_row(encode(view)) if "row:shape" in row["words"])


# --- round trip ----------------------------------------------------------------------------------

def test_round_trip_on_toy_views():
    for view in (sketch_view(), sketch_view(42.0), solid_view()):
        assert decode(encode(view)) == view


def test_round_trip_on_real_steps():
    """Every value of the view can be read back from the facts: 3,000 real steps of the
    training slice and 3,000 of the long test slice (the encoder reads only the view)."""
    for slice_name in ("train_v2", "long_v2"):
        sessions = read_sessions(real_shard(slice_name))
        steps = (example for header, records, end in sessions if usable(end)
                 for example in examples_of(header, records))
        for example in itertools.islice(steps, 3000):
            assert decode(encode(example.view)) == example.view


# --- match flags ---------------------------------------------------------------------------------

def test_a_right_number_points_at_its_plan_slot():
    row = shape_row(sketch_view(24.0))
    assert ("eq:shape.length:length", 0) in row["links"]
    assert ("eq:shape.width:width", 0) in row["links"]
    assert "nomatch:shape.length" not in row["words"]


def test_a_wrong_number_equals_nothing():
    # 42 where the plan says 24: toy example C.
    row = shape_row(sketch_view(42.0))
    assert "nomatch:shape.length" in row["words"]
    assert not [link for link in row["links"] if link[0].startswith("eq:shape.length")]
    # 24.5 is not 24 either: equality is exact, there are no buckets.
    assert "nomatch:shape.length" in shape_row(sketch_view(24.5))["words"]


def test_a_swapped_number_points_at_the_wrong_slot():
    # The width's value (30) given as the length: it matches, but the `width` slot.
    links = shape_row(sketch_view(30.0))["links"]
    assert ("eq:shape.length:width", 0) in links
    assert ("eq:shape.length:length", 0) not in links


def test_a_number_from_another_item_points_at_that_item():
    links = shape_row(sketch_view(6.0))["links"]           # the hole's diameter
    assert ("eq:shape.length:diameter", 1) in links


def test_two_slots_with_the_same_value_are_both_named():
    square = [{"kind": "block", "slots": {"length": 30.0, "width": 30.0, "height": 10.0}}]
    links = shape_row(sketch_view(30.0, plan=square))["links"]
    assert ("eq:shape.length:length", 0) in links and ("eq:shape.length:width", 0) in links


def test_half_of_a_plan_number_and_used_slots():
    rows = facts_by_row(encode(solid_view()))
    solid = next(row for row in rows if "row:solid" in row["words"])
    assert ("half:solid.xmin:length", 0) in solid["links"]      # the block runs from -12 to +12
    assert ("eq:solid.dx:length", 0) in solid["links"]
    assert ("eq:solid.zmax:height", 0) in solid["links"]
    block, hole = rows[0], rows[1]
    assert {"plan.used:length", "plan.used:width", "plan.used:height"} <= set(block["words"])
    assert not [word for word in hole["words"] if word.startswith("plan.used")]


def test_a_dimension_that_is_not_fixed_is_not_compared():
    # The circle of solid_view was drawn at 10 mm, which happens to be the block's height.
    # It is not given yet, so that says nothing.
    circle = [row for row in facts_by_row(encode(solid_view())) if "row:shape" in row["words"]][1]
    assert "shape.free:diameter" in circle["words"]
    assert not [link for link in circle["links"] if link[0].startswith("eq:shape.diameter")]


def test_ordinals_line_the_document_up_with_the_plan():
    items = solid_view()["snapshot"]["items"]
    # the pad is the first primary feature; its sketch shares its number; the sketch that
    # nothing uses yet is for the next plan item; the body has none.
    assert ordinals(items) == {"#2": 0, "#1": 0, "#3": 1}


def test_an_unknown_word_is_an_error_not_a_silent_drop():
    view = sketch_view()
    view["snapshot"]["items"][1]["plane"] = "left_bracket"
    try:
        encode(view)
    except KeyError:
        return
    raise AssertionError("an unknown word got through")


def test_target_bits():
    candidates = ["new_document", "constrain_x", "constrain_y", "undo"]
    label = [{"command": "constrain_y", "args": {"value": 0.0}},
             {"command": "constrain_x", "args": {"value": 0.0}}]
    assert target_bits(candidates, label) == 0b0110


# --- no label-side leak ----------------------------------------------------------------------------

def test_the_label_side_cannot_reach_the_model_arrays(tmp_path):
    """Scramble everything a model must not see in real sessions (the teacher's set, whether
    the state is on plan, what was executed, the reply, how the session was made). The
    arrays the model reads must not change by one bit; only the label arrays may."""
    lines = first_sessions(real_shard("train_v2"), 25)
    scrambled = copy.deepcopy(lines)
    for line in scrambled:
        if "plan" in line:                                  # the session's header
            line.update(noise_level=9.9, clean_length=1, seed=123,
                        start={"kind": "something_else", "commands": [], "forget_undo": True})
        elif "end" not in line:                             # a step record
            plain = next(name for name in line["valid"] if not COMMANDS[name]["args"])
            line["target"] = [{"command": plain, "args": {}, "item": 7, "sources": {"a": "b"}}]
            line["progress"] = {"on_plan": not line["progress"]["on_plan"], "built": 99,
                                "active": 99}
            line["executed"] = {"command": "undo", "args": {}, "noise": "wrong_argument",
                                "flavour": "swapped"}
            line["reply"] = {"status": "refused", "reason": "scrambled"}
    write_shard(tmp_path / "real.jsonl.gz", lines)
    write_shard(tmp_path / "scrambled.jsonl.gz", scrambled)
    real = arrays_of_shard(tmp_path / "real.jsonl.gz")
    other = arrays_of_shard(tmp_path / "scrambled.jsonl.gz")
    model_side = (*FACT_ARRAYS, "n_rows", "n_cand", "n_word", "n_num", "n_link")
    for name in model_side:
        assert np.array_equal(real[name], other[name]), name
    assert len(real["n_rows"]) > 500
    assert not np.array_equal(real["target"], other["target"])     # the label did change
    assert set(STEP_ARRAYS) - set(model_side) == {"target", "group", "session", "t"}


def test_the_model_input_holds_no_group_and_no_target():
    """What model.py reads from a batch is listed here; the label keys are not among them."""
    import inspect

    from forge.s1 import model
    source = inspect.getsource(model)
    for key in ("is_target", '"group"', '"target"'):
        assert key not in source


# --- row ids -----------------------------------------------------------------------

def test_random_row_ids():
    generator = torch.Generator().manual_seed(0)
    n_plan, n_doc = torch.full((16,), 6), torch.full((16,), 11)
    plan_ids, doc_ids = row_ids(16, generator, torch.device("cpu"), n_plan=n_plan, n_doc=n_doc)
    again, _ = row_ids(16, generator, torch.device("cpu"), n_plan=n_plan, n_doc=n_doc)
    assert plan_ids.shape == (16, vocab.MAX_PLAN_ITEMS) and doc_ids.shape == (16, vocab.MAX_DOC_ITEMS)
    # The columns a step uses (its first n) are strictly increasing; all are inside the table.
    assert (plan_ids[:, 1:6] > plan_ids[:, :5]).all() and (doc_ids[:, 1:11] > doc_ids[:, :10]).all()
    assert plan_ids.min() >= 0 and plan_ids.max() < PLAN_IDS and doc_ids.max() < DOC_IDS
    assert not torch.equal(plan_ids, again)                         # a fresh draw differs
    assert not torch.equal(plan_ids[0], plan_ids[1])                # and so do two steps
    with pytest.raises(ValueError):                                 # the lengths are required
        row_ids(16, generator, torch.device("cpu"))


@pytest.mark.parametrize("n", [2, 6, 13, 32])
def test_random_row_ids_use_the_whole_table_whatever_the_plan_length(n):
    """A step that uses n ids gets n of ALL 64, so every id is trained equally
    often. The first version drew 32 of 64 and used the first n: with n = 6 the ids above
    about 20 were used in under 1% of steps."""
    steps = 6000
    generator = torch.Generator().manual_seed(1)
    plan_ids, doc_ids = row_ids(steps, generator, torch.device("cpu"),
                                n_plan=torch.full((steps,), n), n_doc=torch.full((steps,), 2 * n))
    for ids, used, table in ((plan_ids, n, PLAN_IDS), (doc_ids, 2 * n, DOC_IDS)):
        counts = torch.bincount(ids[:, :used].flatten(), minlength=table).float()
        expected = steps * used / table
        spread = 5 * expected ** 0.5                        # five standard deviations
        assert counts.min() > expected - spread and counts.max() < expected + spread
        assert (ids[:, 1:used] > ids[:, :used - 1]).all()
    # The LAST item of the plan gets a high id as often as a low one is given to the first:
    # the id of place k is the (k+1)-th smallest of n, not of 32.
    assert abs(float(plan_ids[:, n - 1].float().mean()) - n * (PLAN_IDS + 1) / (n + 1) + 1) < 1.0
    assert abs(float(plan_ids[:, 0].float().mean()) - (PLAN_IDS + 1) / (n + 1) + 1) < 1.0


def test_random_row_ids_follow_each_step_s_own_length():
    generator = torch.Generator().manual_seed(2)
    n_plan = torch.tensor([1, 3, 32] * 500)
    plan_ids, _ = row_ids(1500, generator, torch.device("cpu"), n_plan=n_plan,
                          n_doc=torch.ones(1500, dtype=torch.long))
    assert plan_ids[0::3, 0].float().mean() > 25            # one id of 64: about 31.5
    assert plan_ids[2::3, 0].float().mean() < 2             # 32 of 64: the smallest is about 1
    assert plan_ids[2::3, 31].float().mean() > 61


def test_evaluation_row_ids_are_the_identity():
    plan_ids, doc_ids = row_ids(3, None, torch.device("cpu"))
    assert torch.equal(plan_ids[2], torch.arange(vocab.MAX_PLAN_ITEMS))
    assert torch.equal(doc_ids[0], torch.arange(vocab.MAX_DOC_ITEMS))


# --- the batch builder -------------------------------------------------------------------------------

def test_a_batch_holds_each_step_s_own_facts():
    views = [sketch_view(), solid_view(), sketch_view(42.0)]
    encoded = [encode(view) for view in views]
    targets = [target_bits(enc.candidates, [{"command": "undo"}]) for enc in encoded]
    data = steps_data(encoded, "cpu", targets)
    batch = data.batch(torch.tensor([2, 1]))                        # two steps, out of order
    most = batch["n_rows_max"]
    assert most == encoded[1].n_rows
    for place, step in enumerate((2, 1)):
        enc = encoded[step]
        mine = (batch["word_at"] // most) == place
        assert sorted(zip((batch["word_at"][mine] % most).tolist(),
                          batch["word_id"][mine].tolist(), strict=True)) \
            == sorted(zip(enc.word_row, enc.word_id, strict=True))
        mine = (batch["num_at"] // most) == place
        assert batch["num_value"][mine].tolist() == [np.float32(v) for v in enc.num_value]
        assert batch["is_row"][place].sum() == enc.n_rows
        rows = batch["is_candidate"][place].nonzero()[:, 0].tolist()
        assert rows == list(range(enc.n_rows - len(enc.candidates), enc.n_rows))
        chosen = batch["is_target"][place].nonzero()[:, 0].tolist()
        assert [enc.candidates[row - rows[0]] for row in chosen] == ["undo"]


def test_a_document_larger_than_the_plan_id_list_is_scored():
    """A long part has more objects (up to 64) than the plan id list has columns (32). A link
    to object 40 must read the object ids, not run off the end of the plan ids."""
    from forge.s1.model import build_model
    view = solid_view()
    body = view["snapshot"]["items"][0]
    for place in range(4, 45):                          # 41 more sketches: objects #4 .. #44
        view["snapshot"]["items"].append(
            {"type": "sketch", "name": f"#{place}", "body": "#0", "valid": True, "plane": "XY",
             "offset": 10.0, "dof": 0, "closed": True, "used_by": None, "n_geometry": 0,
             "n_constraints": 0, "shapes": []})
    body["tip"] = "#44"
    encoded = encode(view)
    assert decode(encoded) == view and max(encoded.link_target) == 44
    model = build_model({"width": 16, "layers": 1, "heads": 2, "mlp_width": 32, "dropout": 0.0},
                        vocab.as_json())
    data = steps_data([encoded], "cpu")
    for generator in (None, torch.Generator().manual_seed(0)):
        scores = model(data.batch(torch.arange(1), generator))
        assert scores.shape == (1, encoded.n_rows) and torch.isfinite(scores).all()
