"""Counterfactual plans: the same recorded state, read against a plan that was changed.

Why. Swap two items of the plan on recorded states and ask the teacher again: where the right answer changed, the model was
right only 0.57 to 0.70 of the time and still answered for the ORIGINAL plan in 0.29 to
0.42: it saw that a feature of the plan was built, not that it was built in the wrong
place. Recorded sessions never contain that situation (the recorder's mistakes are wrong
commands and wrong numbers, never a later feature built first), so the model had nothing
to learn it from.

What is honest about relabelling. `forge.freecad.teacher.teacher(plan, snapshot)` is a
pure function of its two arguments: it reads nothing else (the audit's check 1 calls it
again on every recorded step and gets the recorded label). So a recorded state can be
labelled for ANOTHER plan without FreeCAD. Two things are then still to be shown, and
forge/s1/augment_proof.py shows them in real FreeCAD on a sample: that the changed plan is
a plan of the same part (the features do not overlap, so their order does not change the
solid), and that following the teacher from that state under the changed plan ends at the
stored solid.

Two changes, both made on the plan only:
    swap_two_features    two FEATURE items change places (never the start, never the edge
                         treatment, whose place is not free). Used for training and scoring.
    change_one_number    one number of one item is moved by 1.5, 3.5 or 7.5 (the
                         one-number test). Used for scoring only.

A third change keeps the answer and changes the numbers (added on 7 Oct 2026,
after every hexagon with a non-whole size failed on parts from outside the generator):
    scaled               every length of the plan AND of the state is multiplied by one
                         factor s (drawn per copy, uniform in 0.75 to 1.35), the volume by
                         s cubed; angles and counts are left alone. It is the same session
                         on a part s times as large, so every "equals" and "half of"
                         relation holds and the teacher's commands are the same. A copy is
                         written only if the teacher, asked again on the scaled plan and
                         state, names exactly the recorded commands (counted; expected: all).
                         Why: in the generated parts most sizes are whole numbers (48 of 65
                         plan slots always, the rest on a 0.5 grid) while a wrong number
                         often is not, so a model can learn "not whole means wrong".
                         `--kind scale` writes these to `<slice>_sc`.

Training data (this file's command). For a stated SHARE of the steps of a TRAINING slice,
chosen by a hash of (session, step), one counterfactual copy is written: the plan with two
features swapped, the same snapshot and valid commands, the teacher's label for the
swapped plan. They go to a slice of their own, `<slice>_cf`, beside the prepared arrays,
in the same array format, so training can take them or leave them. A test slice is refused.

    uv run python -m forge.s1.augment --slices train_v2 train_long_v2 --share 0.10 --workers 5

Run it AFTER forge.s1.prepare: prepare rewrites the manifest's `group_bits`, and this file
adds one group to them ("label changed by the swap") for scoring the held-out copies.
"""

from __future__ import annotations

import argparse
import copy
import json
import multiprocessing
import random
import time
from pathlib import Path

import numpy as np

from forge.freecad.load import label, usable, view
from forge.freecad.shards import OUT_DIR, SLICES, TRAIN_SPLITS, complete_shards, read_sessions
from forge.freecad.teacher import Advice, plan_from_json, script_of, teacher
from forge.s1.encode import encode, target_bits
from forge.s1.prepare import FACT_ARRAYS, GROUP_BITS, PREPARED_DIR, STEP_ARRAYS, content_hash
from forge.system1.splits import unit_hash
from forge.system1.steps import FEATURES

SEED = 0
CHANGED_GROUP = "label changed by the swap"
CHANGED_BIT = 32                    # beside prepare.GROUP_BITS (1 to 16); label side only
NUMBER_MOVES = (1.5, 3.5, 7.5)
SCALE_RANGE = (0.75, 1.35)          # the factor of a scaled copy is uniform in this range
SUFFIX = {"swap": "_cf", "scale": "_sc"}


def swap_two_features(plan: list[dict], rng: random.Random) -> list[dict] | None:
    """The plan with two of its feature items exchanged, or None when it has fewer than two."""
    places = [at for at, item in enumerate(plan) if item["kind"] in FEATURES]
    if len(places) < 2:
        return None
    first, second = rng.sample(places, 2)
    changed = copy.deepcopy(plan)
    changed[first], changed[second] = changed[second], changed[first]
    return changed


def change_one_number(plan: list[dict], rng: random.Random) -> list[dict] | None:
    """The plan with one number of one item moved (not a count, which must stay whole)."""
    changed = copy.deepcopy(plan)
    item = changed[rng.randrange(len(changed))]
    slots = [name for name, value in item["slots"].items()
             if isinstance(value, (int, float)) and name not in ("count", "sides")]
    if not slots:
        return None
    name = rng.choice(slots)
    item["slots"][name] = float(item["slots"][name]) + rng.choice(NUMBER_MOVES)
    return changed


def scaled(value: object, factor: float, key: str | None = None) -> object:
    """A plan or a lean snapshot with every length multiplied by `factor`.

    In the session files every float is a length in millimetres, except the values named
    `angle` (degrees) and `volume` (cubic millimetres); every count is an int (checked on the
    files, 7 Oct 2026: forge/s1/README.md). Plan slots follow the same rule, but a length
    there may be stored as an int, so only `count`, `sides` and `angle` are left alone."""
    if isinstance(value, dict):
        return {name: scaled(inner, factor, name) for name, inner in value.items()}
    if isinstance(value, list):
        return [scaled(inner, factor, key) for inner in value]
    if isinstance(value, bool) or not isinstance(value, float) or key == "angle":
        return value
    # FreeCAD reports some sizes of round solids a ten-millionth off (60.0000001). The
    # encoder calls two numbers equal when they agree to a millionth; scaling would push
    # such a pair apart. So a length is first rounded to the millionth it is compared at.
    return value * factor ** 3 if key == "volume" else round(value, 6) * factor


def scaled_plan(plan: list[dict], factor: float) -> list[dict]:
    return [{"kind": item["kind"],
             "slots": {name: value if name in ("count", "sides", "angle")
                       or isinstance(value, bool) else float(value) * factor
                       for name, value in item["slots"].items()}} for item in plan]


def relabel(plan: list[dict], snapshot: dict, valid: list[str]) -> Advice | None:
    """The teacher's answer for this plan in this state. None when the teacher has no answer
    that can be carried out (it raises, the part is complete, or a target is not valid)."""
    try:
        steps = plan_from_json(plan)
        advice = teacher(steps, snapshot, script_of(steps))
    except Exception:       # noqa: BLE001 - a changed plan the recipes cannot read: left out
        return None
    if not advice.targets or not set(advice.names()) <= set(valid):
        return None
    return advice


def chosen(session: str, t: int, share: float, seed: int = SEED) -> bool:
    return unit_hash(f"cf:{seed}:{session}:{t}") < share


def counterfactual_shard(task: tuple[dict, str, str, float, str]) -> dict:
    """One source shard of a training slice -> one .npz of counterfactual steps."""
    stats, source, out, share, kind = task
    out_path, note_path = Path(out), Path(out).with_suffix(".json")
    steps: dict[str, list] = {name: [] for name in STEP_ARRAYS}
    facts: dict[str, list] = {name: [] for name in FACT_ARRAYS}
    session_ids: list[str] = []
    counts = {"picked": 0, "no_two_features": 0, "no_answer": 0, "label_changed": 0,
              "now_off_plan": 0, "kind": kind}
    for header, records, end in read_sessions(Path(source)):
        if not usable(end):
            continue
        session_ids.append(header["session"])
        for record in records:
            salt = "cf" if kind == "swap" else "sc"
            if unit_hash(f"{salt}:{SEED}:{header['session']}:{record['t']}") >= share:
                continue
            counts["picked"] += 1
            rng = random.Random(f"{salt}:{SEED}:{header['session']}:{record['t']}")
            snapshot = record["snapshot"]
            if kind == "swap":
                plan = swap_two_features(header["plan"], rng)
            else:
                factor = rng.uniform(*SCALE_RANGE)
                plan, snapshot = scaled_plan(header["plan"], factor), scaled(snapshot, factor)
            if plan is None:
                counts["no_two_features"] += 1
                continue
            advice = relabel(plan, snapshot, record["valid"])
            if advice is None:
                counts["no_answer"] += 1
                continue
            new = label([target.to_json() for target in advice.targets])
            old = {target["command"] for target in record["target"]}
            changed = {target["command"] for target in new} != old
            counts["label_changed"] += changed
            if kind == "scale" and changed:     # must not happen; such a copy is never written
                continue
            counts["now_off_plan"] += not advice.on_plan
            enc = encode(view(plan, snapshot, record["valid"]))             # the model's side
            group = (GROUP_BITS["other on-plan steps"] if advice.on_plan
                     else GROUP_BITS["undo decisions"]) | (CHANGED_BIT if changed else 0)
            steps["n_rows"].append(enc.n_rows)
            steps["n_cand"].append(len(enc.candidates))
            steps["target"].append(target_bits(enc.candidates, new))
            steps["group"].append(group)
            steps["session"].append(len(session_ids) - 1)
            steps["t"].append(record["t"])
            steps["n_word"].append(len(enc.word_id))
            steps["n_num"].append(len(enc.num_value))
            steps["n_link"].append(len(enc.link_role))
            for name in FACT_ARRAYS:
                facts[name] += getattr(enc, name)
    arrays = {name: np.asarray(steps[name], dtype=kind) for name, kind in STEP_ARRAYS.items()}
    arrays.update({name: np.asarray(facts[name], dtype=kind) for name, kind in FACT_ARRAYS.items()})
    arrays["session_ids"] = np.asarray(session_ids, dtype="S16")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, **arrays)
    groups = {name: int(((arrays["group"] & bit) > 0).sum())
              for name, bit in {**GROUP_BITS, CHANGED_GROUP: CHANGED_BIT}.items()}
    note = {"file": f"{out_path.parent.name}/{out_path.name}", "source": stats["file"],
            "source_sha256": stats["sha256"], "share": share, "seed": SEED,
            "sha256_of_arrays": content_hash(arrays),
            "sessions": len(session_ids), "steps": len(arrays["n_rows"]),
            "words": len(arrays["word_id"]), "numbers": len(arrays["num_value"]),
            "links": len(arrays["link_role"]),
            "max_rows": int(arrays["n_rows"].max()) if len(arrays["n_rows"]) else 0,
            "multi_answer_steps": int((arrays["target"] & (arrays["target"] - 1) > 0).sum()),
            "groups": groups, "counterfactual": counts}
    note_path.write_text(json.dumps(note, indent=1) + "\n")
    return note


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--slices", nargs="+", default=["train_v2", "train_long_v2"])
    parser.add_argument("--share", type=float, default=0.10,
                        help="the share of a slice's steps that get a counterfactual copy")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--kind", choices=list(SUFFIX), default="swap",
                        help="swap: two plan features change places; scale: every length x s")
    parser.add_argument("--source", default=str(OUT_DIR))
    parser.add_argument("--out", default=str(PREPARED_DIR))
    args = parser.parse_args()
    for name in args.slices:            # a test state must never be relabelled into training
        if SLICES[name][0] not in TRAIN_SPLITS:
            raise SystemExit(f"{name} is not a training slice: no counterfactuals are made of it")
    source, out = Path(args.source), Path(args.out)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    started = time.time()
    with multiprocessing.Pool(args.workers) as pool:
        for name in args.slices:
            made = name + SUFFIX[args.kind]
            tasks = [(stats, str(path),
                      str(out / made / path.name.replace(".jsonl.gz", ".npz")), args.share,
                      args.kind)
                     for stats, path in complete_shards(source, (name,))]
            notes = sorted(pool.imap_unordered(counterfactual_shard, tasks),
                           key=lambda note: note["file"])
            totals = {key: sum(note[key] for note in notes)
                      for key in ("sessions", "steps", "words", "numbers", "links",
                                  "multi_answer_steps")}
            totals["groups"] = {group: sum(note["groups"][group] for note in notes)
                                for group in notes[0]["groups"]}
            totals["counterfactual"] = {key: sum(note["counterfactual"][key] for note in notes)
                                        for key in notes[0]["counterfactual"] if key != "kind"}
            what = "two plan features swapped, relabelled by the teacher" if args.kind == "swap" \
                else f"every length of plan and state x one factor in {SCALE_RANGE}, same label"
            manifest["slices"][made] = {
                **totals, "what": f"copies of {name}: {what} (forge/s1/augment.py)",
                "kind": args.kind,
                "of": name, "share": args.share, "seed": SEED,
                "max_rows": max(note["max_rows"] for note in notes),
                "n_shards": len(notes), "shards": notes}
            print(f"{made}: {len(notes)} shards, {totals['steps']} steps "
                  f"{totals['counterfactual']}, {time.time() - started:.0f} s so far", flush=True)
    manifest["group_bits"][CHANGED_GROUP] = CHANGED_BIT
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
