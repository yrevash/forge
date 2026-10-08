"""Cut verified composed parts into training sessions for the step model.

A session is one (part, prompt) played from an empty
state until the teacher says `done`. At every step we store the state, the
teacher's answer (the TARGET) and what was actually executed. In a noisy session
the executed step is sometimes a wrong one on purpose (noise.py); the engine
applies or rejects it and the session carries on from whatever state results.
Only the teacher's answer is ever a target.

Everything is plain Python (no CAD kernel), so this is fast. A session depends
only on (part id, prompt variant, seed): run it twice and the bytes are the same.

Run:    uv run python -m forge.system1.sessions --prompts-per-part 2 --workers 7 --seed 0
Input:  data/generated/composed_*.jsonl and data/system1/long_parts/composed_*.jsonl
Output: data/system1/sessions/<split>/shardNNN.jsonl.gz
        data/system1/sessions/manifest.json

File format, one JSON object per line. A session starts with its HEADER, written once:
    {"session", "part_id", "family", "split", "variant", "prompt", "mentions",
     "noise_level", "plan", "source", "license", "generator_version", "prompt_model"}
and is followed by one line per step:
    {"session", "t", "state", "target": {kind, slots: {name: source}},
     "executed": {kind, slots, was_noise, noise_kind, outcome}}
`read_sessions` reads them back; `full_record` joins a step with its header into
one self-contained record.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import multiprocessing
import random
import shutil
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from forge.data.generate import generator_version
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.system1.engine import State, execute
from forge.system1.mentions import VARIANTS, prompt_and_plan
from forge.system1.noise import wrong_step
from forge.system1.parts import read_parts
from forge.system1.splits import HELD_OUT_PAIRS, IID_FRACTION, SPLIT_SEED, SPLITS, split_of
from forge.system1.steps import Step
from forge.system1.teacher import teacher

OUT_DIR = PROJECT_ROOT / "data" / "system1" / "sessions"
# Each session draws one of these: the chance, at every step, that a wrong step is carried
# out instead of the teacher's. Two of the five are zero, so 40% of sessions are clean.
NOISE_LEVELS = (0.0, 0.0, 0.1, 0.2, 0.3)
# Noise may strike only during the first BUDGET_FACTOR * (clean session length) steps.
# After that the teacher's step is always carried out, so every session ends, and ends at
# its target part.
BUDGET_FACTOR = 3
PARTS_PER_SHARD = 2500


def step_json(step: Step) -> dict:
    """A step as stored: its kind, and for each slot the source of its number."""
    return {"kind": step.kind,
            "slots": {slot: f"mention:{index}" for slot, index in (step.sources or {}).items()}}


def session_id(part_id: str, variant: str, seed: int) -> str:
    return hashlib.sha1(f"{part_id}:{variant}:{seed}".encode()).hexdigest()[:16]


def play(plan: list[Step], mention_values: list[float], noise: float,
         rng: random.Random) -> list[dict]:
    """Play one session; return one record per step. The last record's target is `done`."""
    state = State(mention_values)
    budget = BUDGET_FACTOR * (len(plan) + 1)
    records = []
    while True:
        target = teacher(plan, state)
        noisy = noise > 0 and state.steps < budget and rng.random() < noise
        step, mistake = wrong_step(rng, plan, state, target) if noisy else (target, None)
        record = {"t": state.steps, "state": state.to_json(), "target": step_json(target)}
        outcome = execute(state, step)     # the state moves on; the record holds the state before
        record["executed"] = {**step_json(step), "was_noise": noisy, "noise_kind": mistake,
                              "outcome": outcome}
        records.append(record)
        if step.kind == "done":
            return records
        # Cannot happen (see BUDGET_FACTOR), but a loop that could run for ever should say so.
        assert state.steps < 20 * budget, "session did not end"


def make_session(part: dict, variant: str, seed: int, split: str) -> tuple[dict, list[dict]]:
    """The header and the step records of one (part, prompt) session."""
    prompt, mentions, plan = prompt_and_plan(part["family"], part["params"], part["id"], variant)
    sid = session_id(part["id"], variant, seed)
    rng = random.Random(f"{seed}:{part['id']}:{variant}")
    noise = rng.choice(NOISE_LEVELS)
    header = {
        "session": sid, "part_id": part["id"], "family": part["family"], "split": split,
        "variant": variant, "prompt": prompt, "mentions": mentions, "noise_level": noise,
        "plan": [{"kind": s.kind, "slots": s.slots, "sources": step_json(s)["slots"]}
                 for s in plan],
        # Provenance every training row must carry.
        "source": part["source"], "license": part["license"],
        "generator_version": part["generator_version"],
        "prompt_model": "template",
    }
    records = play(plan, [m["value"] for m in mentions], noise, rng)
    return header, [{"session": sid, **record} for record in records]


# --- reading sessions back -------------------------------------------------------------------

def read_sessions(path: Path) -> Iterator[tuple[dict, list[dict]]]:
    """Every session in one shard: (header, its step records in order)."""
    header: dict | None = None
    records: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            if "plan" in item:
                if header is not None:
                    yield header, records
                header, records = item, []
            else:
                records.append(item)
    if header is not None:
        yield header, records


def full_record(header: dict, record: dict) -> dict:
    """One step as a self-contained record."""
    return {**{key: header[key] for key in ("session", "part_id", "family", "split", "prompt",
                                             "mentions", "noise_level")}, **record}


def plan_of(header: dict) -> list[Step]:
    """The plan stored in a header, as steps again."""
    return [Step(item["kind"], item["slots"],
                 {slot: int(source.removeprefix("mention:"))
                  for slot, source in item["sources"].items()}) for item in header["plan"]]


# --- writing shards --------------------------------------------------------------------------

def write_shard(task: tuple[str, int, list[dict], list[str], int, str]) -> Counter:
    """Write one shard file (runs in a worker process). Returns its counts."""
    split, number, parts, variants, seed, out_dir = task
    path = Path(out_dir) / split / f"shard{number:03d}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    stats: Counter = Counter()
    started = time.perf_counter()
    # mtime=0 keeps a timestamp out of the file, so the same run gives the same bytes.
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as f:
        for part in parts:
            for variant in variants:
                header, records = make_session(part, variant, seed, split)
                lines = [header, *records]
                f.write("".join(json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n"
                                for line in lines).encode("utf-8"))
                stats["sessions"] += 1
                stats["steps"] += len(records)
                stats[f"noise_level:{header['noise_level']}"] += 1
                stats[f"plan_length:{len(header['plan'])}"] += 1
                stats[f"variant:{variant}"] += 1
                for record in records:
                    done = record["executed"]
                    stats[f"target:{record['target']['kind']}"] += 1
                    if done["was_noise"]:
                        stats["noisy_steps"] += 1
                        stats[f"noise:{done['noise_kind']}:{done['outcome']}"] += 1
    stats["parts"] = len(parts)
    stats["seconds"] = time.perf_counter() - started
    return Counter({f"{split}|{key}": value for key, value in stats.items()})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_hash() -> str:
    """A hash of the code that decides what a session contains."""
    digest = hashlib.sha256()
    here = Path(__file__).parent
    for path in [*sorted(here.glob("*.py")), PROJECT_ROOT / "forge/generators/prompts.py",
                 PROJECT_ROOT / "forge/generators/captions.py"]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def grouped(stats: Counter, split: str, prefix: str) -> dict:
    """The counts of one split whose name starts with `prefix`, with the prefix removed."""
    start = f"{split}|{prefix}"
    return {key[len(start):]: value for key, value in sorted(stats.items())
            if key.startswith(start)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--prompts-per-part", type=int, default=2,
                        help=f"how many prompts of each part to play, taken in this order: "
                             f"{', '.join(VARIANTS)}")
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--limit-per-file", type=int, default=None,
                        help="read only this many parts from each input file (for a trial run)")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()
    if not 1 <= args.prompts_per_part <= len(VARIANTS):
        parser.error(f"--prompts-per-part must be between 1 and {len(VARIANTS)}")
    variants = list(VARIANTS[: args.prompts_per_part])
    out_dir = Path(args.out)
    config = {**vars(args), "variants": variants, "noise_levels": list(NOISE_LEVELS),
              "budget_factor": BUDGET_FACTOR, "held_out_pairs": [list(p) for p in HELD_OUT_PAIRS],
              "iid_fraction": IID_FRACTION, "split_seed": SPLIT_SEED, "code_hash": code_hash()}
    run_dir = start_run("system1-sessions", config)

    # 1. Every part gets its split first; sessions are made afterwards, split by split.
    by_split: dict[str, list[dict]] = {name: [] for name in SPLITS}
    part_versions: Counter = Counter()
    for part in read_parts(limit_per_file=args.limit_per_file):
        by_split[split_of(part)].append(part)
        part_versions[part["generator_version"]] += 1
    tasks = [(split, number, parts[at: at + PARTS_PER_SHARD], variants, args.seed, str(out_dir))
             for split, parts in by_split.items()
             for number, at in enumerate(range(0, len(parts), PARTS_PER_SHARD))]

    # 2. Old shards go first, so a smaller rerun never leaves stale files behind.
    for split in SPLITS:
        shutil.rmtree(out_dir / split, ignore_errors=True)

    # 3. One shard per task, several at a time.
    started = time.perf_counter()
    stats: Counter = Counter()
    with multiprocessing.Pool(args.workers) as pool:
        for shard_stats in pool.imap_unordered(write_shard, tasks):
            stats.update(shard_stats)
    elapsed = time.perf_counter() - started

    sessions = sum(stats[f"{s}|sessions"] for s in SPLITS)
    steps = sum(stats[f"{s}|steps"] for s in SPLITS)
    busy = sum(stats[f"{s}|seconds"] for s in SPLITS)
    manifest = {
        "config": config,
        "contract": "docs/STEPS.md version 1",
        "part_generator_versions": dict(part_versions),
        "generator_version_now": generator_version(),
        "totals": {"sessions": sessions, "steps": steps},
        "splits": {split: {
            "parts": stats[f"{split}|parts"], "sessions": stats[f"{split}|sessions"],
            "steps": stats[f"{split}|steps"], "noisy_steps": stats[f"{split}|noisy_steps"],
            "sessions_by_noise_level": grouped(stats, split, "noise_level:"),
            "sessions_by_plan_length": grouped(stats, split, "plan_length:"),
            "sessions_by_variant": grouped(stats, split, "variant:"),
            "steps_by_target_kind": grouped(stats, split, "target:"),
            "noisy_steps_by_kind_and_outcome": grouped(stats, split, "noise:"),
        } for split in SPLITS},
        "speed": {"wall_seconds": round(elapsed, 1), "workers": args.workers,
                  "sessions_per_second": round(sessions / elapsed, 1),
                  "sessions_per_second_one_process": round(sessions / busy, 1)},
        "files": {str(p.relative_to(out_dir)): sha256(p)
                  for p in sorted(out_dir.glob("*/shard*.jsonl.gz"))},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    log_metrics(run_dir, final=True, sessions=sessions, steps=steps,
                **{f"{s}_{k}": stats[f"{s}|{k}"] for s in SPLITS for k in ("sessions", "steps")},
                **manifest["speed"])
    (run_dir / "NOTES.md").write_text(
        f"# system1-sessions\n\nStep sessions for the System 1 model.\n"
        f"{sessions} sessions, {steps} labelled steps, written to {out_dir}.\n"
        f"Counts and file hashes: {out_dir / 'manifest.json'}.\n"
        f"Check it with: uv run python -m forge.system1.audit\n")

    print(f"{sessions} sessions, {steps} labelled steps in {elapsed:.1f}s "
          f"({sessions / elapsed:.0f} sessions/s with {args.workers} workers, "
          f"{sessions / busy:.0f} per second in one process)")
    for split in SPLITS:
        info = manifest["splits"][split]
        print(f"  {split:8s} {info['parts']:7d} parts {info['sessions']:8d} sessions "
              f"{info['steps']:9d} steps  noise levels {info['sessions_by_noise_level']}")
    print(f"manifest: {out_dir / 'manifest.json'}\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
