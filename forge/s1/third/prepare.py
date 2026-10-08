"""Session files -> the third model's arrays (data/s1/v3/), with the bindings beside the label.

    uv run python -m forge.s1.third.prepare --slices train_v2 train_long_v2 train_wide_v3
    uv run python -m forge.s1.third.prepare --slices train_v2_cf train_v2_sc      # copies
    uv run python -m forge.s1.third.prepare --census                              # read the manifest

It does what forge/s1/prepare.py does for the first two models, in a folder of its own
(their arrays in data/s1/v1 are not touched), and adds four things:

    history      the view carries the two commands carried out before the step (load.py)
    n_plan[N]    how many plan rows the step has (the binding head points at them)
    bindings     one entry per accepted command that works on a plan item: which candidate
                 it is, the plan item, and the kind of each argument (bindings.KINDS).
                 LABEL SIDE, like `target`: the model is trained to produce it.
    expressible  per shard, how many accepted commands resolve to the recorded arguments
                 from the plan alone (bindings.expressible). One that does not gets no
                 binding entry and is counted; it is never filled in silently.

A slice name ending in `_cf` or `_sc` is a set of COPIES of a training slice (forge/s1/
augment.py says what they are and why relabelling by the teacher is honest): `_cf` two plan
features swapped, `_sc` every length of plan and state multiplied by one factor.

Which wide shards: only the first WIDE_AUDITED shards of train_wide_v3, the ones the audit
of 8 Oct 05:46 covers. Raise the number only after a new audit.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import random
import time
from pathlib import Path

import numpy as np

from forge.freecad import wide_sessions
from forge.freecad.baselines import groups_of
from forge.freecad.load import history_before, label, sources, usable, view
from forge.freecad.shards import OUT_DIR, SLICES, TRAIN_SPLITS, complete_shards, read_sessions
from forge.freecad.shards import changed as step_changed
from forge.runs import PROJECT_ROOT, git_commit
from forge.s1 import vocab
from forge.s1.augment import (
    CHANGED_BIT,
    CHANGED_GROUP,
    SCALE_RANGE,
    SEED,
    relabel,
    scaled,
    scaled_plan,
    swap_two_features,
)
from forge.s1.encode import encode, target_bits
from forge.s1.prepare import FACT_ARRAYS, GROUP_BITS, STEP_ARRAYS, content_hash
from forge.s1.third.bindings import KIND_ID, MAX_ARGS, expressible, kinds_of_target
from forge.system1.splits import unit_hash

PREPARED_DIR = PROJECT_ROOT / "data" / "s1" / "v3"
DAGGER_DIR = PROJECT_ROOT / "data" / "s1" / "dagger"     # sessions recorded by dagger.py
WIDE_AUDITED = 146
COPY_SHARE = {"_cf": 0.10, "_sc": 0.15}                 # as for the second models
STEP_ARRAYS_3 = {**STEP_ARRAYS, "n_plan": np.uint8, "n_bind": np.uint8}
BIND_ARRAYS = {"bind_cand": np.uint8, "bind_item": np.uint8,
               **{f"bind_k{a}": np.uint8 for a in range(MAX_ARGS)}}
ALL_GROUPS = {**GROUP_BITS, CHANGED_GROUP: CHANGED_BIT}
TEST_SLICES = ("iid_v2", "pairing_v2", "long_v2", "xlong_v2", *wide_sessions.TEST_SLICES)


def source_of(name: str) -> tuple[Path, str, str]:
    """A slice name -> (folder of its session files, the recorded slice, copy kind or "")."""
    kind = name[-3:] if name.endswith(("_cf", "_sc")) else ""
    base = name[:-3] if kind else name
    if base.startswith("dagger"):
        folder = DAGGER_DIR
    elif base in wide_sessions.SLICES:
        folder = wide_sessions.OUT_DIR
    else:
        folder = OUT_DIR
    if kind and (base not in SLICES or SLICES[base][0] not in TRAIN_SPLITS):
        raise SystemExit(f"{name}: copies are made of the v2 training slices only")
    return folder, base, kind


class Shard:
    """Collects the steps of one output shard, one `add` per step."""

    def __init__(self) -> None:
        self.steps: dict[str, list] = {name: [] for name in STEP_ARRAYS_3}
        self.facts: dict[str, list] = {name: [] for name in (*FACT_ARRAYS, *BIND_ARRAYS)}
        self.session_ids: list[str] = []
        self.counts = {"targets": 0, "targets_expressible": 0, "steps_fully_expressible": 0}

    def add(self, plan: list[dict], snapshot: dict, valid: list[str],
            history: list[tuple[str, str]], targets: list[dict], group: int, t: int) -> None:
        enc = encode(view(plan, snapshot, valid, history), third=True)      # the model's side
        accepted, where = label(targets), sources(targets)                  # the label side
        n_bind, whole = 0, True
        for target, source in zip(accepted, where, strict=True):
            self.counts["targets"] += 1
            if not expressible(plan, target, source):
                whole = False
                continue
            self.counts["targets_expressible"] += 1
            item, kinds = kinds_of_target(target, source)
            if item is None:            # undo, done, ...: nothing to point at, no arguments
                continue
            n_bind += 1
            self.facts["bind_cand"].append(enc.candidates.index(target["command"]))
            self.facts["bind_item"].append(item)
            for a in range(MAX_ARGS):
                self.facts[f"bind_k{a}"].append(KIND_ID[kinds[a]] if a < len(kinds) else 0)
        self.counts["steps_fully_expressible"] += whole
        row = {"n_rows": enc.n_rows, "n_cand": len(enc.candidates), "group": group,
               "target": target_bits(enc.candidates, accepted),
               "session": len(self.session_ids) - 1, "t": t, "n_word": len(enc.word_id),
               "n_num": len(enc.num_value), "n_link": len(enc.link_role),
               "n_plan": enc.n_plan, "n_bind": n_bind}
        for name, value in row.items():
            self.steps[name].append(value)
        for name in FACT_ARRAYS:
            self.facts[name] += getattr(enc, name)

    def arrays(self) -> dict[str, np.ndarray]:
        kinds = {**STEP_ARRAYS_3, **FACT_ARRAYS, **BIND_ARRAYS}
        made = {name: np.asarray(values, dtype=kinds[name])
                for name, values in {**self.steps, **self.facts}.items()}
        made["session_ids"] = np.asarray(self.session_ids, dtype="S16")
        return made


def plain_shard(path: Path, pure: frozenset[str], every_session: bool = False) -> Shard:
    """Every step of a recorded shard's usable sessions, as recorded. `every_session` (DAgger
    rollouts only): also sessions the model did not finish; their labels are the teacher's
    answer for each visited state all the same."""
    shard = Shard()
    for header, records, end in read_sessions(path):
        if not (usable(end) or every_session):
            continue
        shard.session_ids.append(header["session"])
        before, before_changed = None, False
        for record in records:
            group = sum(GROUP_BITS[name] for name in groups_of(record, before, before_changed)
                        if name in GROUP_BITS)
            if header["session"] in pure:
                group |= GROUP_BITS["long_pure"]
            shard.add(header["plan"], record["snapshot"], record["valid"],
                      history_before(records, record["t"], vocab.HISTORY), record["target"],
                      group, record["t"])
            after = records[record["t"] + 1] if record["t"] + 1 < len(records) else end
            before, before_changed = record, step_changed(record, after)
    return shard


def copy_shard(path: Path, kind: str) -> Shard:
    """Copies of a share of a TRAINING shard's steps: plan features swapped (`_cf`) or every
    length scaled (`_sc`). The same steps and factors as forge/s1/augment.py picks."""
    shard, salt = Shard(), kind[1:]
    for header, records, end in read_sessions(path):
        if not usable(end):
            continue
        shard.session_ids.append(header["session"])
        for record in records:
            key = f"{salt}:{SEED}:{header['session']}:{record['t']}"
            if unit_hash(key) >= COPY_SHARE[kind]:
                continue
            rng, snapshot = random.Random(key), record["snapshot"]
            if kind == "_cf":
                plan = swap_two_features(header["plan"], rng)
            else:
                factor = rng.uniform(*SCALE_RANGE)
                plan, snapshot = scaled_plan(header["plan"], factor), scaled(snapshot, factor)
            advice = None if plan is None else relabel(plan, snapshot, record["valid"])
            if advice is None:
                continue
            targets = [target.to_json() for target in advice.targets]
            changed = {target["command"] for target in targets} \
                != {target["command"] for target in record["target"]}
            if kind == "_sc" and changed:       # must not happen; such a copy is never written
                continue
            group = (GROUP_BITS["other on-plan steps"] if advice.on_plan
                     else GROUP_BITS["undo decisions"]) | (CHANGED_BIT if changed else 0)
            shard.add(plan, snapshot, record["valid"],
                      history_before(records, record["t"], vocab.HISTORY), targets, group,
                      record["t"])
    return shard


def prepare_shard(task: tuple[dict, str, str, str, frozenset[str]]) -> dict:
    """One source shard -> one .npz and its note. Work already done is kept."""
    stats, source, out, kind, pure = task
    out_path, note_path = Path(out), Path(out).with_suffix(".json")
    if out_path.exists() and note_path.exists():
        note = json.loads(note_path.read_text())
        if note["source_sha256"] == stats["sha256"] and note["vocab_hash"] == vocab.third_hash():
            return note
    shard = copy_shard(Path(source), kind) if kind \
        else plain_shard(Path(source), pure, every_session="dagger" in Path(source).parent.name)
    arrays = shard.arrays()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, **arrays)
    note = {"file": f"{out_path.parent.name}/{out_path.name}", "source": stats["file"],
            "source_sha256": stats["sha256"], "vocab_hash": vocab.third_hash(),
            "sha256_of_arrays": content_hash(arrays), "sessions": len(shard.session_ids),
            "steps": len(arrays["n_rows"]), "bindings": len(arrays["bind_cand"]),
            "max_rows": int(arrays["n_rows"].max()) if len(arrays["n_rows"]) else 0,
            "groups": {name: int(((arrays["group"] & bit) > 0).sum())
                       for name, bit in ALL_GROUPS.items()}, **shard.counts}
    note_path.write_text(json.dumps(note, indent=1) + "\n")     # written last: the shard is whole
    return note


def census(folder: Path) -> str:
    """The share of accepted commands and of steps that are expressible, per slice."""
    manifest = json.loads((folder / "manifest.json").read_text())
    lines = ["| Slice | Steps | Steps fully expressible | Accepted commands | Expressible |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for name, entry in manifest["slices"].items():
        lines.append(f"| {name} | {entry['steps']} | {entry['steps_fully_expressible']} "
                     f"({entry['steps_fully_expressible'] / max(1, entry['steps']):.6f}) | "
                     f"{entry['targets']} | {entry['targets_expressible']} "
                     f"({entry['targets_expressible'] / max(1, entry['targets']):.6f}) |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--slices", nargs="+", default=[])
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--out", default=str(PREPARED_DIR))
    parser.add_argument("--census", action="store_true", help="print the expressible shares")
    args = parser.parse_args()
    out = Path(args.out)
    if args.census:
        print(census(out))
        return
    manifest_path = out / "manifest.json"
    manifest = {"what": "Forge-S1 third model, prepared arrays (forge/s1/third/prepare.py)",
                "vocab_hash": vocab.third_hash(), "vocab": vocab.third_json(),
                "group_bits": ALL_GROUPS, "slices": {}}
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old["vocab_hash"] == manifest["vocab_hash"]:
            manifest["slices"] = old["slices"]
    manifest.update(commit=git_commit(), code_hash=vocab.third_hash())
    started = time.time()
    with multiprocessing.Pool(args.workers) as pool:
        for name in args.slices:
            folder, base, kind = source_of(name)
            pure_file = folder / "long_pure.json"
            listing = json.loads(pure_file.read_text()) if pure_file.exists() else {}
            pure = frozenset(entry["session"] for entry in listing.get(base, {}).get("pure", []))
            shards = complete_shards(folder, (base,))
            if base == "train_wide_v3":
                shards = shards[:WIDE_AUDITED]
            tasks = [(stats, str(path), str(out / name / path.name.replace(".jsonl.gz", ".npz")),
                      kind, pure) for stats, path in shards]
            notes = sorted(pool.imap_unordered(prepare_shard, tasks), key=lambda n: n["file"])
            totals = {key: sum(note[key] for note in notes)
                      for key in ("sessions", "steps", "bindings", "targets",
                                  "targets_expressible", "steps_fully_expressible")}
            totals["groups"] = {group: sum(note["groups"][group] for note in notes)
                                for group in ALL_GROUPS}
            manifest["slices"][name] = {**totals, "test": base in TEST_SLICES,
                                        "max_rows": max(n["max_rows"] for n in notes),
                                        "n_shards": len(notes), "shards": notes}
            out.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
            print(f"{name}: {len(notes)} shards, {totals['steps']} steps, "
                  f"{totals['steps_fully_expressible']} fully expressible, "
                  f"{time.time() - started:.0f} s so far", flush=True)
    print(census(out))


if __name__ == "__main__":
    main()
