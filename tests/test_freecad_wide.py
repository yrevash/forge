"""Tests of the wide data: the sampler, the reference, the third mix, the starts.

The tests in the first half need only the CAD kernel. The ones at the end drive a real
headless FreeCAD and are skipped when it is not installed; none crashes FreeCAD on purpose.
The tests under "nothing old moved" pin the frozen recordings: the recorder's code hash,
the teacher's labels and the loader's views of stored sessions must be what they were
before the wide modules were added.
"""

from __future__ import annotations

import hashlib
import json
import random

import pytest

from forge.freecad import audit, load, sessions, shards
from forge.freecad.locate import freecad_available
from forge.freecad.noise import Moment
from forge.freecad.parts import sample_rows
from forge.freecad.recipes import context_of
from forge.freecad.teacher import plan_from_json, script_of, teacher
from forge.freecad.wide_noise import ORDER_KINDS, decimal_slips, order_mistake
from forge.freecad.wide_parts import plan_fingerprint, plan_json
from forge.freecad.wide_reference import Builder, Rejected
from forge.freecad.wide_sample import (
    HELD_OUT_ORDERS,
    HELD_OUT_SCALES,
    NUMBERS,
    TRAIN,
    decimals_of,
    held_out_orders_in,
    sample_plan,
    scale_of,
)
from forge.freecad.wide_starts import HELD_OUT_STARTS, MAKERS, TEST_STARTS, TRAIN_STARTS, make_start
from forge.system1.splits import HELD_OUT_PAIRS, held_out_pairs_in
from forge.system1.steps import Step, steps_of

BLOCK = Step("block", {"length": 41.5, "width": 30.25, "height": 12.0})


def draw(seed: str, base: str, items: int, profile=TRAIN, order: bool = False):
    rng = random.Random(seed)
    for _ in range(40):
        made = sample_plan(rng, base, items, profile, order)
        if made is not None:
            return made
    raise AssertionError("no plan in 40 draws")


# --- the reference -----------------------------------------------------------------------------

def test_the_reference_gives_a_composed_part_its_stored_volume():
    # The wide reference builds item by item; for a composed part (features that do not
    # touch) that must be the solid the composed generator's own program made.
    for row in sample_rows(per_base=2, long_per_base=1, seed=3):
        plan = steps_of(row["family"], row["params"])
        built = Builder.of_plan(plan).measures[-1]
        assert built["volume"] == pytest.approx(row["measured"]["volume"], rel=1e-6)
        assert built["bbox"] == pytest.approx(row["measured"]["bbox"], abs=1e-3)


def test_an_item_that_changes_nothing_is_rejected_and_leaves_the_builder_as_it_was():
    builder = Builder(BLOCK)
    builder.add(Step("hole", {"diameter": 4.5, "x": 3.0, "y": 0.0}))
    before = (list(builder.volumes), builder.program())
    with pytest.raises(Rejected):       # a hole beside the block cuts nothing
        builder.add(Step("hole", {"diameter": 2.0, "x": 300.0, "y": 0.0}))
    with pytest.raises(Rejected):       # a boss in thin air is a second solid
        builder.add(Step("boss", {"diameter": 2.0, "height": 3.0, "x": 300.0, "y": 0.0}))
    assert (list(builder.volumes), builder.program()) == before


def test_the_order_of_items_matters_when_they_touch():
    hole = Step("hole", {"diameter": 6.0, "x": 0.0, "y": 0.0})
    chamfer = Step("top_chamfer", {"size": 1.0})
    first = Builder.of_plan([BLOCK, hole, chamfer]).volumes[-1]     # the hole's rim is bevelled too
    second = Builder.of_plan([BLOCK, chamfer, hole]).volumes[-1]
    assert first < second - 1.0


def test_the_stored_program_is_the_solid_that_was_built():
    builder, _ = draw("program", "cylinder", 6)
    space: dict = {}
    exec(builder.program(), space)  # noqa: S102 - our own generator text
    from forge.geometry import measure

    assert measure(space["result"])["volume"] == pytest.approx(builder.volumes[-1], rel=1e-9)


# --- the sampler -------------------------------------------------------------------------------

def test_the_same_seed_gives_the_same_plan():
    one, two = draw("same", "hex", 7)[0], draw("same", "hex", 7)[0]
    assert plan_json(one.steps) == plan_json(two.steps)
    assert len(one.steps) == 7 and one.steps[0].kind == "hex"


def test_a_base_alone_is_a_plan():
    builder, _ = draw("alone", "ring", 1)
    assert [step.kind for step in builder.steps] == ["ring"]


@pytest.mark.parametrize("base", ["block", "cylinder", "hex", "ring"])
def test_training_plans_keep_out_what_the_tests_hold(base):
    for items in (2, 5, 9):
        builder, _ = draw(f"train:{base}:{items}", base, items)
        kinds = [step.kind for step in builder.steps]
        numbers = [v for step in builder.steps for slot, v in step.slots.items() if slot != "count"]
        assert not held_out_pairs_in(kinds) and not held_out_orders_in(kinds)
        assert not any(low <= scale_of(builder.steps[0]) < high for low, high in HELD_OUT_SCALES)
        assert max(decimals_of(v) for v in numbers) <= 2


def test_the_numbers_test_is_outside_training_and_the_order_test_holds_an_order():
    builder, _ = draw("numbers", "block", 4, NUMBERS)
    numbers = [v for step in builder.steps for slot, v in step.slots.items() if slot != "count"]
    assert any(low <= scale_of(builder.steps[0]) < high for low, high in HELD_OUT_SCALES)
    assert max(decimals_of(v) for v in numbers) >= 3
    builder, _ = draw("order", "cylinder", 6, TRAIN, order=True)
    kinds = [step.kind for step in builder.steps]
    assert held_out_orders_in(kinds) and not held_out_pairs_in(kinds)


def test_no_held_out_order_is_a_held_out_pairing():
    pairs = {frozenset(pair) for pair in HELD_OUT_PAIRS}
    assert not any(frozenset(order) in pairs for order in HELD_OUT_ORDERS)


def test_decimals_are_counted_as_written():
    assert [decimals_of(v) for v in (12, 41.5, 7.25, 0.125, 3.0001)] == [0, 1, 2, 3, 4]


def test_the_baseline_tool_knows_the_wide_slices():
    from forge.freecad.baselines import WIDE_SLICES
    from forge.freecad.wide_sessions import SLICES

    assert tuple(SLICES) == WIDE_SLICES and not set(SLICES) & set(shards.SLICES)


def test_the_plan_fingerprint_is_the_audits():
    plan = [{"kind": "block", "slots": dict(BLOCK.slots)}]
    assert plan_fingerprint(plan) == audit.plan_fingerprint(plan)


# --- the third mix -----------------------------------------------------------------------------

def test_a_decimal_slip_is_near_and_never_the_right_number():
    rng = random.Random(0)
    slips = decimal_slips(rng, 41.5)
    assert 41.0 in slips and 42.0 in slips and 41.05 in slips and 415.0 in slips
    assert 7.52 in decimal_slips(rng, 7.25)
    assert all(abs(slip - 41.5) > 1e-6 for slip in slips)


def test_an_order_mistake_is_the_recipe_of_a_later_item():
    plan = [BLOCK, Step("hole", {"diameter": 4.5, "x": 3.0, "y": 0.0}),
            Step("boss", {"diameter": 6.25, "height": 2.5, "x": -8.0, "y": 1.0})]
    script = script_of(plan)
    start_of_hole = next(at for at, (index, _) in enumerate(script) if index == 1)

    class Advice:       # the teacher at the start of item 1, as far as order_mistake reads it
        on_plan, active, position = True, 1, start_of_hole

    moment = Moment(plan, context_of(plan), Advice(), [], None, item_start=True)
    seen = set()
    for seed in range(20):
        kind, commands = order_mistake(random.Random(seed), moment, script)
        seen.add(kind)
        assert kind in ORDER_KINDS
        assert ("pad", {"length": 2.5}) in commands                 # the boss was built
        assert (("pocket_through_all", {}) in commands) == (kind == "order_swapped")
    assert seen == set(ORDER_KINDS)
    Advice.active = 2                                               # the last item: no later one
    assert order_mistake(random.Random(0), moment, script) is None


# --- the starts --------------------------------------------------------------------------------

def test_held_out_starts_are_in_no_training_table_and_alone_in_the_test_table():
    assert not set(HELD_OUT_STARTS) & set(TRAIN_STARTS)
    assert set(TEST_STARTS) - {"opened"} == set(HELD_OUT_STARTS)
    assert set(HELD_OUT_STARTS) <= set(MAKERS)
    assert sum(TRAIN_STARTS.values()) == pytest.approx(1.0)
    assert sum(TEST_STARTS.values()) == pytest.approx(1.0)


def test_every_start_kind_makes_commands_of_the_catalogue():
    from forge.freecad.catalogue import COMMANDS, check_args

    plan = [BLOCK, Step("hole", {"diameter": 4.5, "x": 3.0, "y": 0.0})]
    other = [Step("cylinder", {"diameter": 20.0, "height": 5.0}),
             Step("boss", {"diameter": 6.25, "height": 2.5, "x": 1.0, "y": 1.0})]
    for kind in MAKERS:
        for seed in range(5):
            commands = make_start(random.Random(seed), kind, plan, other)
            assert commands, kind
            for name, args in commands:
                assert name in COMMANDS and check_args(name, args) is None, (kind, name, args)
    complete = make_start(random.Random(1), "complete", plan, other)
    assert ("pocket_through_all", {}) in complete and ("done", {}) not in complete


# --- nothing old moved ---------------------------------------------------------------------------

RECORDER_CODE_HASH = "c7008cecd5d6"     # the hash in the stats files of train_long_v2
# sha256 (first 24 hex digits) over (view, label) of the first 25 sessions of shard 0, as the
# loader gave them on 7 Oct 2026 before any wide module existed.
VIEW_DIGESTS = {"train_v2": "ed5fcd10cb4e319e2d5fab84", "iid_v2": "e2ae03bf1c3e7ffeb214fc5d",
                "xlong_v2": "5fd506f64a2faf53e93befcb", "train": "2ac649474203ce9051e7b3f3"}


def test_the_recorder_of_the_frozen_slices_is_unchanged():
    assert sessions.code_hash() == RECORDER_CODE_HASH
    assert list(shards.SLICES) == ["iid", "pairing", "long", "train", "train_heavy", "train_s1",
                                   "train_s2", "iid_v2", "pairing_v2", "long_v2", "train_v2",
                                   "train_long_v2", "xlong_v2"]
    vocabulary = hashlib.sha256(json.dumps(sorted(load.VOCABULARY)).encode()).hexdigest()[:16]
    assert vocabulary == "bc6316dabac66ef2"


@pytest.mark.parametrize("name", sorted(VIEW_DIGESTS))
def test_stored_labels_and_loader_views_are_what_they_were(name):
    path = shards.OUT_DIR / name / "shard000.jsonl.gz"
    if not path.exists():
        pytest.skip("the frozen session files are not on this machine")
    digest = hashlib.sha256()
    for count, (header, records, end) in enumerate(shards.read_sessions(path)):
        if count == 25:
            break
        plan = plan_from_json(header["plan"])
        for record in records:      # the teacher of today gives the stored label
            advice = teacher(plan, record["snapshot"])
            assert record["target"] == [target.to_json() for target in advice.targets]
        if load.usable(end):
            for example in load.examples_of(header, records):
                digest.update(json.dumps([example.view, example.label], sort_keys=True).encode())
    assert digest.hexdigest()[:24] == VIEW_DIGESTS[name]


# --- real FreeCAD --------------------------------------------------------------------------------

needs_freecad = pytest.mark.skipif(not freecad_available(), reason="FreeCAD is not installed")


@pytest.fixture(scope="module")
def fc():
    from forge.freecad.client import FreeCADClient

    client = FreeCADClient(exact_undo=True)
    client.start()
    yield client
    client.close()


def as_part(builder: Builder, name: str = "wide_iid") -> dict:
    measure = builder.measures[-1]
    plan = plan_json(builder.steps)
    return {"id": plan_fingerprint(plan), "family": f"wide_{plan[0]['kind']}", "set": name,
            "plan": plan, "measured": {"volume": measure["volume"], "bbox": measure["bbox"]},
            "step_volumes": builder.volumes, "source": "test", "license": "forge",
            "generator_version": "test"}


@needs_freecad
def test_freecad_builds_a_late_treatment_and_a_decimal_hexagon_like_the_reference(fc):
    from forge.freecad.wide_build import build_plan

    plan = [Step("hex", {"across_flats": 41.5, "height": 12.25}),
            Step("hole", {"diameter": 6.05, "x": 0.0, "y": 0.0}),
            Step("boss", {"diameter": 9.5, "height": 4.75, "x": 11.2, "y": -3.3}),
            Step("hole", {"diameter": 6.05, "x": -9.0, "y": 4.4}),     # the same diameter again
            Step("top_chamfer", {"size": 0.6})]                        # last: on the boss's top
    builder = Builder.of_plan(plan)
    part = as_part(builder)
    result = build_plan(fc, plan, part["step_volumes"], part["measured"], random.Random(0))
    assert result.problems == []
    assert [ok for _, ok in result.steps] == [True] * 5


@needs_freecad
@pytest.mark.parametrize("kind", sorted(MAKERS))
def test_the_teacher_finishes_from_every_start_kind(fc, kind):
    from forge.freecad.wide_play import play

    builder, _ = draw("starts", "block", 4)
    other, _ = draw("other", "cylinder", 3)
    part = as_part(builder)
    for forget in (False, True):
        commands = make_start(random.Random(f"{kind}:{forget}"), kind, builder.steps, other.steps)
        header, records, end = play(fc, part, 5, "test", noise_levels=(0.0,),
                                    start=(kind, commands, forget))
        assert end["end"] == "done" and end["problems"] == [], (kind, forget, end)
        assert header["start"]["kind"] == kind and header["mix"] == "v3"
        if kind == "complete" and not commands[-1][0].startswith("select"):
            assert [target["command"] for target in records[0]["target"]] == ["done"]


@needs_freecad
def test_a_noisy_session_of_the_third_mix_passes_the_wide_audit_and_the_loader(fc):
    from forge.freecad.wide_audit import audit_session, replay_session
    from forge.freecad.wide_play import play
    from forge.freecad.wide_sessions import SEED

    builder, _ = draw("noisy", "block", 6)
    other, _ = draw("other", "ring", 4)
    part = as_part(builder)
    kinds = set()
    for seed in range(4):
        part["id"] = f"{seed:016d}"
        header, records, end = play(fc, part, SEED, "wide_iid_v3", other_plan=other.steps,
                                    noise_levels=(0.3,))
        assert end["end"] == "done" and end["problems"] == []
        kinds |= {record["executed"]["noise"] for record in records}
        found = audit_session(header, records, end, part)
        assert {name: problems for name, problems in found.items() if problems} == {}
        assert replay_session(fc, header, records, end, part) == []
        for example in load.examples_of(header, records):       # the loader reads it unchanged
            assert example.view["plan"] == header["plan"] and example.label
    assert len(kinds - {None}) >= 3
