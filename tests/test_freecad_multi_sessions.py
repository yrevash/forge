"""The recorder's bookkeeping, the mistake mix and the audit's split checks. No FreeCAD.

Uses the same real sessions as test_freecad_multi_load.py.
"""

from collections import Counter
from pathlib import Path

import pytest

from forge.freecad.teacher import Advice, Target
from forge.freecad_multi import audit, noise, selection, sessions
from forge.freecad_multi.manifest import summary
from forge.freecad_multi.recipes import structure_items
from forge.freecad_multi.shards import SLICES, TEST_SLICES, TRAIN_SLICES, read_sessions
from forge.resolve import space as sp
from forge.resolve.bodies import Body

SAMPLE = Path(__file__).parent / "data" / "freecad_multi_sessions.jsonl.gz"
pytestmark = pytest.mark.skipif(not SAMPLE.exists(), reason="the sample sessions are missing")


@pytest.fixture(scope="module")
def recorded() -> list[tuple[dict, list[dict], dict]]:
    return list(read_sessions(SAMPLE))


# --- the code hash and the edit guard ------------------------------------------------------------

def test_the_code_hash_covers_what_decides_a_session_and_not_the_readers():
    here = Path(sessions.__file__).parent
    assert all((here / name).exists() for name in sessions.CODE_FILES)
    for decides in ("teacher.py", "noise.py", "play.py", "recipes.py", "selection.py",
                    "inside/multi_session.py", "../resolve/resolver.py", "../freecad/noise.py"):
        assert decides in sessions.CODE_FILES
    for reader in ("audit.py", "oracle.py", "load.py", "manifest.py", "show_session.py"):
        assert reader not in sessions.CODE_FILES
    assert sessions.code_hash() == sessions.code_hash() and len(sessions.code_hash()) == 12


def test_a_shard_is_thrown_away_when_the_code_changes_under_it(tmp_path, monkeypatch):
    """The edit guard: the hash is taken when a shard starts and again when it ends."""
    hashes = iter(["aaaaaaaaaaaa", "bbbbbbbbbbbb", "bbbbbbbbbbbb"])
    monkeypatch.setattr(sessions, "code_hash", lambda: next(hashes))
    monkeypatch.setattr(sessions, "KernelJudge", lambda: None)
    empty = tmp_path / "part0000.jsonl.gz"
    import gzip
    with gzip.open(empty, "wt") as f:
        f.write("")
    with pytest.raises(RuntimeError, match="the code changed while it was recorded"):
        sessions.write_shard(("train", 0, str(empty), str(tmp_path), "sel"))
    assert not list((tmp_path / "train").glob("shard*"))        # nothing was left behind


def test_slices_tests_first_then_train():
    names = list(SLICES)
    assert names[:4] == list(TEST_SLICES)
    assert set(names[4:]) == set(TRAIN_SLICES) and names[-1] == "train"
    seeds = {(SLICES[name][0], SLICES[name][2]) for name in names}
    assert len(seeds) == len(names)             # no (plans, seed) is recorded twice


# --- the mistake mix -----------------------------------------------------------------------------

def test_every_kind_of_mistake_has_a_maker_and_a_weight():
    assert set(noise._MAKERS) == set(noise.NOISE_KINDS)
    assert noise.NOISE_KINDS["wrong_number"] == max(noise.NOISE_KINDS.values())
    assert noise.kind_of("move_body", "body:centre") == "misplaced_body"
    assert noise.kind_of("turn_body", "body:matrix") == "misturned_body"
    assert noise.kind_of("select_side", "cut:face") == "wrong_side"
    assert noise.kind_of("pocket", "cut:depth") == "wrong_feature"
    assert noise.kind_of("constrain_length", "size:length") == "wrong_size"
    assert noise.kind_of("pad_symmetric", "size:height") == "wrong_size"


def test_a_wrong_command_is_never_the_teachers_and_never_new_document():
    import random
    target = Target("move_body", {"x": 10.0, "y": 0.0, "z": 5.0}, 0,
                    {"x": "body:centre", "y": "body:centre", "z": "body:centre"})
    advice = Advice((target,), True, 0, 0, "put the body in its place", 9)
    box = Body("a", "a", 1, "box", None, {"length": 40.0, "depth": 20.0, "height": 10.0},
               sp.IDENTITY, (10.0, 0.0, 5.0))
    moment = noise.Moment(structure_items([box]), [10.0, 5.0, 40.0, 20.0], {1: ((10.0, 0.0, 5.0), None),
                                                        2: ((40.0, 0.0, 5.0), None)},
                          advice, ["move_body", "turn_body", "undo", "new_body", "done",
                                   "new_document", "select_plane"], None, 2)
    kinds = Counter()
    for seed in range(600):
        made = noise.wrong_command(random.Random(seed), moment)
        assert made is not None
        name, args, kind, _ = made
        assert name != "new_document"
        assert not (name == "move_body" and args == target.args)
        assert kind in noise.NOISE_KINDS or kind in noise.WRONG_NUMBERS
        kinds[kind] += 1
    assert kinds["misplaced_body"] > 300 and kinds["early_done"] > 0 and kinds["extra_body"] > 0


def test_the_chance_is_raised_at_the_rare_moments():
    def advice(*names: str) -> Advice:
        return Advice(tuple(Target(name, {}, 0, {}) for name in names), True, 0, 0, "", 3)
    assert noise.chance_at(0.0, advice("done")) == 0.0          # a clean session stays clean
    assert noise.chance_at(0.1, advice("done")) == noise.CHANCE_BEFORE_DONE
    assert noise.chance_at(0.1, advice("select_side")) == noise.CHANCE_AT_FEATURE
    assert noise.chance_at(0.1, advice("activate_body")) == noise.CHANCE_AT_FEATURE
    assert noise.chance_at(0.1, advice("move_body", "turn_body")) == noise.CHANCE_AT_PLACING
    assert noise.chance_at(0.1, advice("constrain_x")) == 0.1
    assert noise.chance_at(0.3, advice("move_body")) == 0.3


def test_the_sample_holds_the_structure_mistakes_and_only_teacher_targets(recorded):
    kinds, flavours = Counter(), Counter()
    for _, records, _ in recorded:
        for record in records:
            done = record["executed"]
            if done["noise"]:
                kinds[done["noise"]] += 1
                flavours[done.get("flavour")] += 1
                assert all((done["command"], done["args"]) != (t["command"], t["args"])
                           for t in record["target"])
    for kind in ("wrong_size", "misplaced_body", "wrong_feature", "wrong_side", "wrong_body",
                 "extra_body", "early_done", "failing_feature", "carry_on", "extra_undo"):
        assert kinds[kind] > 0, kind
    for flavour in ("swapped", "other_item", "plan_number", "a_little_off"):
        assert flavours[flavour] > 0, flavour


# --- counting ------------------------------------------------------------------------------------

def test_the_counts_of_a_shard_add_up(recorded):
    stats: Counter = Counter()
    for header, records, end in recorded:
        sessions.count_session(stats, header, records, end)
    made = summary(stats)
    assert made["sessions"] == len(recorded) and made["sessions_ended"] == {"done": len(recorded)}
    assert made["steps"] == sum(len(records) for _, records, _ in recorded)
    assert sum(made["steps_by_number_of_targets"].values()) == made["steps"]
    assert made["sessions_with_a_feature"] >= 3
    assert made["sessions_with_another_shape_than_box_or_cylinder"] >= 2
    assert made["sessions_with_activate_body_as_target"] >= 2
    assert made["undo_history_lost"] >= 1 and made["steps_finished_too_early"] >= 1
    assert 0.15 < made["wrong_number_share_of_noisy_steps"] < 0.6
    assert abs(sum(made["noisy_steps_share_by_kind"].values()) - 1.0) < 0.01
    # A session that did not finish is counted and nothing else of it is.
    header, records, end = recorded[0]
    before = stats["steps"]
    sessions.count_session(stats, header, records, {**end, "end": "stuck"})
    assert stats["end:stuck"] == 1 and stats["steps"] == before


# --- the audit's content check (check 5) ---------------------------------------------------------

HELD = selection.HELD_OUT_CELLS[0]


def _content(*rows) -> list[tuple]:
    return [(name, key, sorted(cells), bodies, kind, f"plan{n}")
            for n, (name, key, cells, bodies, kind) in enumerate(rows)]


def _other_halves() -> list[tuple]:
    """Train sessions that hold both halves of every held-out cell, each in another cell."""
    rows = []
    for n, cell in enumerate(selection.HELD_OUT_CELLS):
        family, pair = cell.split(":")
        first, second = pair.split("|")
        rows.append(("train", f"t{n}", {f"{family}:{first}|elsewhere", f"{family}:other|{second}"},
                     5, "table"))
        rows.append(("combo", f"c{n}", {cell}, 5, None))
    return rows


def test_a_clean_split_passes_the_content_check():
    rows = [*_other_halves(), ("kinds", "k1", {"st:box|0,0,0"}, 6, "stool"),
            ("kinds", "k2", {"st:box|0,0,0"}, 6, "shelf"),
            ("kinds", "k3", {"st:box|0,0,0"}, 6, "standoffs"),
            ("iid", "i1", {"st:box|0,0,0"}, 30, "chair"), ("long", "l1", {"st:box|0,0,0"}, 31, None),
            ("train_rare_s1", "t0", {"st:box|0,0,0"}, 4, None)]      # train twice: allowed
    problems, proof = audit.check_content(_content(*rows))
    assert problems == []
    assert proof["held_out_cells"][HELD]["train"] == 0 and proof["held_out_cells"][HELD]["combo"] == 1
    assert proof["sets_of_parts_shared"]["train & kinds"] == 0


@pytest.mark.parametrize("leak, says", [
    (("train", "t99", {HELD}, 5, "table"), "held-out cell"),            # the cell in train
    (("iid", "t0", {"st:box|0,0,0"}, 5, "table"), "in both train and iid"),     # same parts
    (("train", "t98", {"st:box|0,0,0"}, 5, "stool"), "stool sessions are in train"),
    (("train", "t97", {"st:box|0,0,0"}, 31, "table"), "has 31 bodies"),
    (("long", "l2", {"st:box|0,0,0"}, 30, None), "only 30 bodies"),
    (("combo", "c99", {"st:box|0,0,0"}, 5, None), "holds no held-out cell"),
    (("kinds", "k9", {"st:box|0,0,0"}, 5, "table"), "kinds slice"),
])
def test_the_content_check_finds_each_kind_of_leak(leak, says):
    rows = [*_other_halves(), ("kinds", "k1", {"st:box|0,0,0"}, 6, "stool"),
            ("kinds", "k2", {"st:box|0,0,0"}, 6, "shelf"),
            ("kinds", "k3", {"st:box|0,0,0"}, 6, "standoffs"),
            ("long", "l1", {"st:box|0,0,0"}, 31, None), leak]
    problems, _ = audit.check_content(_content(*rows))
    assert any(says in problem for problem in problems), problems


def test_a_held_out_cell_whose_half_is_nowhere_else_in_train_is_reported():
    rows = [row for row in _other_halves() if row[1] != "t0"]
    problems, _ = audit.check_content(_content(*rows))
    assert any("is not in train on its own" in problem for problem in problems)


def test_the_header_alone_gives_the_cells_and_a_name_free_hash(recorded):
    for header, _, _ in recorded:
        key, cells = audit.header_content(header)
        renamed = {**header, "plan": [{**item, "name": "x", "line": 0} for item in header["plan"]]}
        assert audit.header_content(renamed) == (key, cells)
        shapes = {item["shape"] for item in header["plan"] if item["kind"] == "part"}
        assert {cell[3:].split("|")[0] for cell in cells if cell.startswith("st:")} == shapes
        features = {f"{f['feature']}|{f['side']}" for item in header["plan"]
                    for f in item["features"]}
        assert {cell[3:] for cell in cells if cell.startswith("fs:")} == features
        moved = {**header, "plan": [{**item, "centre": [v + 1 for v in item["centre"]]}
                                    for item in header["plan"]]}
        assert audit.header_content(moved)[0] != key
