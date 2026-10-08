"""Sessions, wrong steps, shards and the audit."""

import copy
import random
from collections import Counter

import pytest

from forge.system1 import engine
from forge.system1.audit import CHECKS, audit_session
from forge.system1.mentions import prompt_and_plan
from forge.system1.noise import WRONG_KINDS, same_step, wrong_step
from forge.system1.sessions import (
    BUDGET_FACTOR,
    NOISE_LEVELS,
    full_record,
    make_session,
    plan_of,
    play,
    read_sessions,
    step_json,
    write_shard,
)
from forge.system1.splits import split_of
from forge.system1.steps import DONE, UNDO
from forge.system1.teacher import on_plan, teacher
from tests.system1_helpers import as_row, sample_parts


def rows(per_family: int, seed: str, **kw) -> list[dict]:
    return [as_row(part) for part in sample_parts(per_family, seed=seed, **kw)]


def test_noise_levels_are_the_five_fixed_values():
    assert NOISE_LEVELS == (0.0, 0.0, 0.1, 0.2, 0.3)


def test_a_clean_session_is_the_plan_then_done():
    for row in rows(40, "clean"):
        _, mentions, plan = prompt_and_plan(row["family"], row["params"], row["id"], "request-0")
        records = play(plan, [m["value"] for m in mentions], 0.0, random.Random(0))
        assert [r["target"] for r in records] == [*(step_json(s) for s in plan), step_json(DONE)]
        assert [r["t"] for r in records] == list(range(len(plan) + 1))
        for record in records:
            executed = record["executed"]
            assert not executed["was_noise"] and executed["outcome"] == "ok"
            assert {k: executed[k] for k in ("kind", "slots")} == record["target"]
        assert records[0]["state"] == {"built": [], "used": [0] * len(mentions), "last": "none",
                                       "steps": 0}
        assert records[-1]["state"]["used"] == [1] * len(mentions)


def test_a_session_is_fixed_by_part_variant_and_seed():
    row = rows(1, "fixed")[0]
    first = make_session(row, "compact-0", 7, "train")
    assert first == make_session(copy.deepcopy(row), "compact-0", 7, "train")
    others = [make_session(row, "compact-0", seed, "train") for seed in range(8, 28)]
    assert any(other[1] != first[1] for other in others)        # the seed matters
    assert first[0]["session"] != make_session(row, "request-0", 7, "train")[0]["session"]


def test_noisy_sessions_end_at_the_target_and_store_only_the_teachers_answer():
    """Replay every session by hand: the target is always the teacher's answer for the state
    shown, wrong steps never become targets, and the end state is the plan."""
    seen: Counter = Counter()
    for row in rows(60, "noisy") + rows(15, "noisy", features=(6, 12)):
        for variant in ("request-0", "compact-0", "spec"):
            header, records = make_session(row, variant, 3, "train")
            plan = plan_of(header)
            values = [m["value"] for m in header["mentions"]]
            state = engine.State(values)
            for record in records:
                assert record["state"] == state.to_json()
                assert record["target"] == step_json(teacher(plan, state))
                executed = record["executed"]
                if executed["was_noise"]:
                    seen[(executed["noise_kind"], executed["outcome"])] += 1
                    assert executed["kind"] != "done"
                    assert (executed["kind"], executed["slots"]) != \
                        (record["target"]["kind"], record["target"]["slots"])
                    assert header["noise_level"] > 0
                    assert record["t"] < BUDGET_FACTOR * (len(plan) + 1)
                else:
                    assert {k: executed[k] for k in ("kind", "slots")} == record["target"]
                if executed["kind"] == "undo":
                    step = UNDO
                elif executed["kind"] == "done":
                    step = DONE
                else:
                    step = engine.resolve(executed["kind"], {
                        k: int(v.removeprefix("mention:")) for k, v in executed["slots"].items()},
                        values)
                assert engine.execute(state, step) == executed["outcome"]
            assert records[-1]["target"]["kind"] == "done"
            assert on_plan(plan, state) and len(state.built) == len(plan)
            seen[f"level {header['noise_level']}"] += 1
    # Every kind of wrong step happened, some accepted and some rejected, and undo was needed.
    assert {kind for kind, _ in (k for k in seen if isinstance(k, tuple))} == set(WRONG_KINDS)
    assert seen[("wrong_kind", "ok")] and seen[("wrong_mention", "ok")]
    assert seen[("out_of_order", "ok")] and seen[("repeat", "rejected")]
    assert seen[("random", "undone")]
    assert all(seen[f"level {level}"] for level in (0.0, 0.1, 0.2, 0.3))


def test_a_wrong_step_is_never_the_teachers_step():
    rng = random.Random(5)
    for row in rows(25, "wrong"):
        _, mentions, plan = prompt_and_plan(row["family"], row["params"], row["id"], "request-0")
        state = engine.State([m["value"] for m in mentions])
        for _ in range(len(plan) + 1):
            target = teacher(plan, state)
            for _ in range(20):
                step, name = wrong_step(rng, plan, state, target)
                assert name in WRONG_KINDS and step.kind != "done"
                assert not same_step(step, target)
            engine.execute(state, target)


def test_even_at_full_noise_a_session_ends():
    row = rows(1, "storm")[0]
    _, mentions, plan = prompt_and_plan(row["family"], row["params"], row["id"], "request-0")
    for seed in range(30):
        records = play(plan, [m["value"] for m in mentions], 1.0, random.Random(seed))
        budget = BUDGET_FACTOR * (len(plan) + 1)
        assert records[-1]["target"]["kind"] == "done"
        assert all(r["executed"]["was_noise"] == (r["t"] < budget) for r in records)
        assert len(records) <= budget + budget + len(plan) + 1


def test_a_shard_reads_back_and_passes_the_audit_and_tampering_is_caught(tmp_path):
    parts = rows(8, "shard")
    stats = write_shard(("train", 0, parts, ["request-0", "compact-0"], 0, str(tmp_path)))
    path = tmp_path / "train" / "shard000.jsonl.gz"
    again = tmp_path / "copy"
    write_shard(("train", 0, parts, ["request-0", "compact-0"], 0, str(again)))
    assert path.read_bytes() == (again / "train" / "shard000.jsonl.gz").read_bytes()

    sessions = list(read_sessions(path))
    assert len(sessions) == 64 == stats["train|sessions"]
    assert sum(len(records) for _, records in sessions) == stats["train|steps"]
    by_id = {part["id"]: part for part in parts}
    for header, records in sessions:
        assert {"source", "license", "generator_version"} <= set(header)
        record = full_record(header, records[0])
        assert set(record) == {"session", "part_id", "family", "split", "prompt", "mentions",
                               "noise_level", "t", "state", "target", "executed"}
        assert set(record["executed"]) == {"kind", "slots", "was_noise", "noise_kind", "outcome"}
        part = {**by_id[header["part_id"]]}
        header = {**header, "split": split_of(part)}
        problems, _ = audit_session(header, records, part)
        assert problems == {name: [] for name in CHECKS}

    # Now break one thing at a time; the audit must notice each.
    header, records = next((h, r) for h, r in sessions if len(r) >= 3)
    part = by_id[header["part_id"]]
    header = {**header, "split": split_of(part)}

    wrong_target = copy.deepcopy(records)
    wrong_target[1]["target"] = {"kind": "undo", "slots": {}}
    assert audit_session(header, wrong_target, part)[0]["teacher"]

    cut_short = copy.deepcopy(records[:-1])
    assert audit_session(header, cut_short, part)[0]["replay"]

    wrong_state = copy.deepcopy(records)
    wrong_state[2]["state"]["built"][0]["slots"]["height"] = 999.0
    assert audit_session(header, wrong_state, part)[0]["replay"]

    other_part = {**part, "params": {**part["params"], "base_height": 999.0}}
    found = audit_session(header, records, other_part)[0]
    assert found["mentions"] and found["replay"]

    in_wrong_split = {**header, "split": "long"}
    assert audit_session(in_wrong_split, records, part)[0]["splits"]


@pytest.mark.parametrize("variant", ["request-0", "spec"])
def test_long_parts_make_sessions_too(variant):
    for row in rows(5, "long-session", features=(12, 12)):
        header, records = make_session({**row, "long": True}, variant, 0, "long")
        assert len(header["plan"]) == 13 and records[-1]["target"]["kind"] == "done"
