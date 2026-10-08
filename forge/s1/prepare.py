"""Turn session files into arrays a training loop can read fast.

    uv run python -m forge.s1.prepare                      # train_v2 and the three v2 tests
    uv run python -m forge.s1.prepare --slices iid_v2 --max-shards 2 --out data/s1/trial

Reading the gzip JSON and building the facts takes about a tenth of a millisecond per
step; a training loop cannot afford that on every pass. So this is done once: every
source shard data/freecad/sessions/<slice>/shardNNN.jsonl.gz becomes
data/s1/v1/<slice>/shardNNN.npz, and data/s1/v1/manifest.json says what is in them.

Everything goes through forge/freecad/load.py (`usable`, `examples_of`): the model's side
of a step is `encode(example.view)` and nothing else.

Arrays of one shard (N steps; facts of all steps laid end to end):
    n_rows[N] n_cand[N]         rows of the step, and how many of them (the last) are candidates
    target[N]                   bit k set = candidate k is accepted by the teacher
    group[N]                    evaluation group bits (GROUP_BITS). LABEL SIDE: never a model input
    session[N] t[N]             which session (index into session_ids) and which step
    n_word[N] word_row word_id                  the word facts of step i are the next n_word[i]
    n_num[N]  num_row num_field num_value
    n_link[N] link_row link_role link_target
    session_ids                 the 16-character session ids of this shard

The group of a step is found exactly as forge/freecad/baselines.py finds it (`groups_of`),
from the recording. It is stored beside the label for scoring by group.

The work is deterministic: the same source files and code give the same arrays (their
sha256 is in the manifest).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import time
from pathlib import Path

import numpy as np

from forge.freecad.baselines import groups_of
from forge.freecad.load import examples_of, usable
from forge.freecad.shards import OUT_DIR, complete_shards, read_sessions
from forge.freecad.shards import changed as step_changed
from forge.runs import PROJECT_ROOT, git_commit
from forge.s1 import vocab
from forge.s1.encode import encode, target_bits

PREPARED_DIR = PROJECT_ROOT / "data" / "s1" / "v1"
DEFAULT_SLICES = ("train_v2", "iid_v2", "pairing_v2", "long_v2")
# Never prepared for training by this file: test slices are only ever read for scoring, and
# train_heavy is kept out of the first model.
GROUP_BITS = {"undo decisions": 1, "right after a mistake": 2, "wrong-number mistakes": 4,
              "other on-plan steps": 8, "long_pure": 16}
CODE_FILES = ("forge/s1/vocab.py", "forge/s1/encode.py", "forge/s1/prepare.py",
              "forge/freecad/load.py", "forge/freecad/baselines.py")
# name -> (numpy type, which Encoded list it comes from)
FACT_ARRAYS = {"word_row": np.uint8, "word_id": np.int16,
               "num_row": np.uint8, "num_field": np.uint8, "num_value": np.float32,
               "link_row": np.uint8, "link_role": np.int16, "link_target": np.uint8}
STEP_ARRAYS = {"n_rows": np.uint8, "n_cand": np.uint8, "target": np.uint32, "group": np.uint8,
               "session": np.int32, "t": np.int16,
               "n_word": np.uint16, "n_num": np.uint16, "n_link": np.uint16}


def code_hash() -> str:
    digest = hashlib.sha256()
    for name in CODE_FILES:
        digest.update((PROJECT_ROOT / name).read_bytes())
    return digest.hexdigest()[:12]


def arrays_of_shard(path: Path, pure: frozenset[str] = frozenset()) -> dict[str, np.ndarray]:
    """Every step of a source shard's usable sessions, as the arrays described above."""
    steps: dict[str, list] = {name: [] for name in STEP_ARRAYS}
    facts: dict[str, list] = {name: [] for name in FACT_ARRAYS}
    session_ids: list[str] = []
    for header, records, end in read_sessions(path):
        if not usable(end):
            continue
        session_ids.append(header["session"])
        before, before_changed = None, False
        for example, record in zip(examples_of(header, records), records, strict=True):
            enc = encode(example.view)                      # the model's side: the view only
            group = sum(GROUP_BITS[name] for name in groups_of(record, before, before_changed)
                        if name in GROUP_BITS)
            if header["session"] in pure:
                group |= GROUP_BITS["long_pure"]
            steps["n_rows"].append(enc.n_rows)
            steps["n_cand"].append(len(enc.candidates))
            steps["target"].append(target_bits(enc.candidates, example.label))
            steps["group"].append(group)
            steps["session"].append(len(session_ids) - 1)
            steps["t"].append(example.t)
            steps["n_word"].append(len(enc.word_id))
            steps["n_num"].append(len(enc.num_value))
            steps["n_link"].append(len(enc.link_role))
            for name in FACT_ARRAYS:
                facts[name] += getattr(enc, name)
            # The same bookkeeping as baselines.steps_of_shard: did this step change the session?
            after = records[record["t"] + 1] if record["t"] + 1 < len(records) else end
            before, before_changed = record, step_changed(record, after)
    made = {name: np.asarray(steps[name], dtype=kind) for name, kind in STEP_ARRAYS.items()}
    made.update({name: np.asarray(facts[name], dtype=kind) for name, kind in FACT_ARRAYS.items()})
    made["session_ids"] = np.asarray(session_ids, dtype="S16")
    return made


def content_hash(arrays: dict[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for name in sorted(arrays):
        digest.update(name.encode())
        digest.update(np.ascontiguousarray(arrays[name]).tobytes())
    return digest.hexdigest()


def prepare_shard(task: tuple[dict, str, str, frozenset[str]]) -> dict:
    """One source shard -> one .npz. Returns its manifest entry. Skips work already done."""
    stats, source, out, pure = task
    out_path, note_path = Path(out), Path(out).with_suffix(".json")
    if out_path.exists() and note_path.exists():
        note = json.loads(note_path.read_text())
        if note["source_sha256"] == stats["sha256"] and note["code_hash"] == code_hash():
            return note
    arrays = arrays_of_shard(Path(source), pure)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, **arrays)
    groups = {name: int(((arrays["group"] & bit) > 0).sum()) for name, bit in GROUP_BITS.items()}
    note = {"file": f"{stats['slice']}/{out_path.name}", "source": stats["file"],
            "source_sha256": stats["sha256"], "code_hash": code_hash(),
            "sha256_of_arrays": content_hash(arrays),
            "sessions": len(arrays["session_ids"]), "steps": len(arrays["n_rows"]),
            "words": len(arrays["word_id"]), "numbers": len(arrays["num_value"]),
            "links": len(arrays["link_role"]), "max_rows": int(arrays["n_rows"].max()),
            "multi_answer_steps": int((arrays["target"] & (arrays["target"] - 1) > 0).sum()),
            "groups": groups}
    note_path.write_text(json.dumps(note, indent=1) + "\n")     # written last: the shard is whole
    return note


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--slices", nargs="+", default=list(DEFAULT_SLICES))
    parser.add_argument("--max-shards", type=int, default=None,
                        help="only the first so many shards of each slice")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--source", default=str(OUT_DIR))
    parser.add_argument("--out", default=str(PREPARED_DIR))
    args = parser.parse_args()
    if "train_heavy" in args.slices:
        raise SystemExit("train_heavy is kept out of the first model")
    source, out = Path(args.source), Path(args.out)
    pure_file = source / "long_pure.json"
    listing = json.loads(pure_file.read_text()) if pure_file.exists() else {}

    started = time.time()
    manifest = {"what": "Forge-S1 prepared arrays (forge/s1/prepare.py)", "commit": git_commit(),
                "code_hash": code_hash(), "vocab_hash": vocab.vocab_hash(),
                "vocab": vocab.as_json(), "group_bits": GROUP_BITS, "slices": {}}
    with multiprocessing.Pool(args.workers) as pool:
        for name in args.slices:
            pure = frozenset(entry["session"] for entry in listing.get(name, {}).get("pure", []))
            shards = complete_shards(source, (name,))[:args.max_shards]
            tasks = [(stats, str(path), str(out / name / path.name.replace(".jsonl.gz", ".npz")),
                      pure) for stats, path in shards]
            notes = sorted(pool.imap_unordered(prepare_shard, tasks), key=lambda n: n["file"])
            totals = {key: sum(note[key] for note in notes)
                      for key in ("sessions", "steps", "words", "numbers", "links",
                                  "multi_answer_steps")}
            totals["groups"] = {group: sum(note["groups"][group] for note in notes)
                                for group in GROUP_BITS}
            manifest["slices"][name] = {**totals, "max_rows": max(n["max_rows"] for n in notes),
                                        "n_shards": len(notes), "shards": notes}
            print(f"{name}: {len(notes)} shards, {totals['sessions']} sessions, "
                  f"{totals['steps']} steps, {time.time() - started:.0f} s so far", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    # Slices prepared by an earlier call stay in the manifest when the code is the same.
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old.get("code_hash") == manifest["code_hash"]:
            manifest["slices"] = {**old["slices"], **manifest["slices"]}
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
