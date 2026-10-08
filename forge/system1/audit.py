"""Audit the session files. Exit code 0 only if every check passes.

The five checks:
  1. replay    every session, replayed through the engine from an empty state, shows
               the recorded state at every step and ends at its target part;
  2. teacher   every stored target is what the teacher gives for that state;
  3. kernel    a random sample of final states is built on the CAD kernel (through
               the sandbox) and measures the same as the part's verified one-go solid;
  4. splits    no held-out pairing and no long part is in `train`, and every
               session of a part is in that part's split;
  5. mentions  every mention index in a target points at a mention whose value
               equals the slot's value in the part's own parameters.

The audit trusts nothing in the session files about the part itself: the target
parameters are read again from data/generated/ and data/system1/long_parts/.

Run:    uv run python -m forge.system1.audit [--kernel-sample 2000] [--workers 7]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from forge.generators.base import BBOX_TOLERANCE_MM, VOLUME_RELATIVE_TOLERANCE
from forge.runs import log_metrics, start_run
from forge.sandbox import Sandbox
from forge.system1 import engine
from forge.system1.parts import read_parts
from forge.system1.sessions import OUT_DIR, plan_of, read_sessions, step_json
from forge.system1.splits import (
    MAX_TRAIN_ITEMS,
    SPLITS,
    held_out_pairs_in,
    split_of,
    unit_hash,
)
from forge.system1.steps import DONE, UNDO, params_of, steps_of
from forge.system1.teacher import teacher

CHECKS = ("replay", "teacher", "kernel", "splits", "mentions")
_parts: dict[str, dict] = {}        # part id -> row; filled once in every worker process
_local = threading.local()


def _load_parts(parts: dict[str, dict]) -> None:
    _parts.update(parts)


def _step_from(stored: dict, mentions: list[float]) -> engine.Step:
    """A stored step (kind plus a source per slot) as a step with values."""
    if stored["kind"] == "undo":
        return UNDO
    if stored["kind"] == "done":
        return DONE
    sources = {slot: int(source.removeprefix("mention:"))
               for slot, source in stored["slots"].items()}
    return engine.resolve(stored["kind"], sources, mentions)


def audit_session(header: dict, records: list[dict], part: dict) -> tuple[dict[str, list[str]],
                                                                         engine.State]:
    """Run checks 1, 2, 4 and 5 on one session. Returns the problems per check and the
    final state reached by replaying it."""
    problems: dict[str, list[str]] = {name: [] for name in CHECKS}
    truth = steps_of(part["family"], part["params"])      # from the part, not from the header
    plan = plan_of(header)
    mentions = header["mentions"]
    values = [m["value"] for m in mentions]
    for m in mentions:
        if header["prompt"][m["start"]:m["end"]] != m["text"]:
            problems["mentions"].append(f"mention {m} is not at its place in the prompt")

    state = engine.State(values)
    for record in records:
        if record["state"] != state.to_json() or record["t"] != state.steps:
            problems["replay"].append(f"t={record['t']}: recorded state is not the replayed state")
            break
        # Check 2, from the RECORDED state, so it does not lean on the replay.
        recorded = engine.State.from_json(record["state"], values)
        if record["target"] != step_json(teacher(plan, recorded)):
            problems["teacher"].append(f"t={record['t']}: target is not the teacher's answer")
        # Check 5: a target that builds something must name the true numbers of the part.
        target = record["target"]
        if target["kind"] not in ("undo", "done"):
            position = len(state.built)
            wanted = truth[position] if position < len(truth) else None
            if wanted is None or wanted.kind != target["kind"] \
                    or set(wanted.slots) != set(target["slots"]):
                problems["mentions"].append(f"t={record['t']}: target is not the part's step")
            else:
                for slot, source in target["slots"].items():
                    index = int(source.removeprefix("mention:"))
                    if values[index] != float(wanted.slots[slot]):
                        problems["mentions"].append(
                            f"t={record['t']}: {target['kind']}.{slot} points at {values[index]}, "
                            f"the part has {wanted.slots[slot]}")
        executed = record["executed"]
        outcome = engine.execute(state, _step_from(executed, values))
        if outcome != executed["outcome"]:
            problems["replay"].append(f"t={record['t']}: outcome {outcome}, recorded "
                                      f"{executed['outcome']}")
        if executed["was_noise"] and executed["kind"] == record["target"]["kind"] \
                and executed["slots"] == record["target"]["slots"]:
            problems["teacher"].append(f"t={record['t']}: a noise step equals the target")

    # Check 1, the end: the last step is `done`, and what is built is the part.
    if not records or records[-1]["executed"]["kind"] != "done" \
            or records[-1]["target"]["kind"] != "done":
        problems["replay"].append("the session does not end with done")
    elif not state.built or params_of([engine.Step(b.kind, b.slots) for b in state.built]) \
            != (part["family"], part["params"]):
        problems["replay"].append("the final state is not the target part")

    # Check 4.
    if header["split"] != split_of(part):
        problems["splits"].append(f"session is in {header['split']}, its part belongs in "
                                  f"{split_of(part)}")
    if header["split"] == "train":
        kinds = [step.kind for step in truth]
        if held_out_pairs_in(kinds):
            problems["splits"].append(f"held-out pairing in train: {held_out_pairs_in(kinds)}")
        if part["long"] or len(truth) - 1 > MAX_TRAIN_ITEMS:
            problems["splits"].append("a long part is in train")
    return problems, state


def audit_shard(task: tuple[str, dict[str, float], int]) -> dict:
    """Audit one shard file (runs in a worker process)."""
    path, fractions, seed = task
    counts: Counter = Counter()
    examples: dict[str, list[str]] = {name: [] for name in CHECKS}
    part_splits: dict[str, str] = {}
    sample = []
    for header, records in read_sessions(Path(path)):
        counts["sessions"] += 1
        counts["steps"] += len(records)
        part = _parts.get(header["part_id"])
        if part is None:
            counts["bad:replay"] += 1
            examples["replay"].append(f"{header['session']}: part {header['part_id']} not found")
            continue
        problems, state = audit_session(header, records, part)
        for name, found in problems.items():
            if found:
                counts[f"bad:{name}"] += 1
                if len(examples[name]) < 5:
                    examples[name].append(f"{header['session']}: {found[0]}")
        part_splits[header["part_id"]] = header["split"]
        # Check 3 picks its sample here; the building happens in the main process.
        point = unit_hash(f"audit:{seed}:{header['session']}")
        if point < fractions[header["split"]] and state.built:
            sample.append((point, header["split"], header["session"], header["part_id"],
                           engine.build(state)))
    return {"counts": counts, "examples": examples, "part_splits": part_splits, "sample": sample}


def _measure(item: tuple) -> tuple[tuple, dict]:
    if not hasattr(_local, "sandbox"):
        _local.sandbox = Sandbox(timeout=30)
    return item, _local.sandbox.run(item[4])


def kernel_problems(reply: dict, part: dict) -> list[str]:
    """How a built final state differs from the part's verified solid."""
    if reply["status"] != "ok":
        return [f"{reply['status']}: {reply.get('error')}"]
    got, want = reply["measure"], part["measured"]
    problems = [f"bbox {axis}: {a:.5f} against {b:.5f}"
                for axis, a, b in zip("XYZ", got["bbox"], want["bbox"], strict=True)
                if abs(a - b) > BBOX_TOLERANCE_MM]
    if abs(got["volume"] - want["volume"]) > VOLUME_RELATIVE_TOLERANCE * want["volume"]:
        problems.append(f"volume {got['volume']:.6f} against {want['volume']:.6f}")
    if got["fingerprint"] != part["geom_fingerprint"]:
        problems.append(f"fingerprint {got['fingerprint']} against {part['geom_fingerprint']}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", default=str(OUT_DIR))
    parser.add_argument("--kernel-sample", type=int, default=2000,
                        help="final states to build on the kernel, shared between the splits")
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    out_dir = Path(args.dir)
    manifest = json.loads((out_dir / "manifest.json").read_text())
    run_dir = start_run("system1-audit", vars(args))
    started = time.time()

    # The truth about every part comes from the part files, with measurements.
    parts = {part["id"]: {key: part[key] for key in ("id", "family", "params", "measured",
                                                      "geom_fingerprint", "long")}
             for part in read_parts(full=True)}
    # Ask each split for a bit more than its share of the kernel sample, then trim.
    live = [s for s in SPLITS if manifest["splits"][s]["sessions"]]
    quota = -(-args.kernel_sample // len(live))
    fractions = {s: min(1.0, 1.5 * quota / max(manifest["splits"][s]["sessions"], 1))
                 for s in SPLITS}
    shards = sorted(out_dir.glob("*/shard*.jsonl.gz"))
    missing = sorted(set(manifest["files"]) ^ {str(p.relative_to(out_dir)) for p in shards})

    counts: Counter = Counter()
    examples: dict[str, list[str]] = {name: [] for name in CHECKS}
    part_splits: dict[str, set[str]] = {}
    candidates: dict[str, list] = {s: [] for s in SPLITS}
    with multiprocessing.Pool(args.workers, initializer=_load_parts, initargs=(parts,)) as pool:
        tasks = [(str(path), fractions, args.seed) for path in shards]
        for result in pool.imap_unordered(audit_shard, tasks):
            counts.update(result["counts"])
            for name in CHECKS:
                examples[name] += result["examples"][name]
            for part_id, split in result["part_splits"].items():
                part_splits.setdefault(part_id, set()).add(split)
            for item in result["sample"]:
                candidates[item[1]].append(item)
    torn = [part_id for part_id, found in part_splits.items() if len(found) > 1]
    counts["bad:splits"] += len(torn)
    examples["splits"] += [f"part {part_id} has sessions in {sorted(part_splits[part_id])}"
                           for part_id in torn[:5]]
    if missing or counts["sessions"] != manifest["totals"]["sessions"] \
            or counts["steps"] != manifest["totals"]["steps"]:
        counts["bad:replay"] += 1
        examples["replay"].append(f"files or totals differ from the manifest: {missing[:3]}")

    # Check 3: build the sampled final states on the kernel.
    sample = [item for s in SPLITS for item in sorted(candidates[s])[:quota]]
    kernel_by_split: Counter = Counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for item, reply in pool.map(_measure, sample):
            found = kernel_problems(reply, parts[item[3]])
            kernel_by_split[item[1]] += 1
            if found:
                counts["bad:kernel"] += 1
                examples["kernel"].append(f"{item[2]}: {found[0]}")
    counts["kernel_built"] = len(sample)
    if len(sample) < args.kernel_sample:
        counts["bad:kernel"] += 1
        examples["kernel"].append(f"only {len(sample)} final states were sampled")

    print(f"audited {counts['sessions']} sessions, {counts['steps']} steps, "
          f"{len(part_splits)} parts, {len(shards)} shards ({time.time() - started:.0f}s)")
    wording = {
        "replay": "every session replays to its recorded states and ends at its target part",
        "teacher": "every target is the teacher's answer for its state",
        "kernel": f"{len(sample)} final states built on the kernel match the verified solid "
                  f"{dict(kernel_by_split)}",
        "splits": "no held-out pairing or long part in train; one split per part",
        "mentions": "every target mention carries the slot's value",
    }
    failed = []
    for number, name in enumerate(CHECKS, start=1):
        bad = counts[f"bad:{name}"]
        print(f"  {number}. {'PASS' if not bad else 'FAIL'}  {wording[name]}"
              + (f"  ({bad} bad)" if bad else ""))
        for example in examples[name][:5]:
            print(f"        {example}")
        if bad:
            failed.append(name)
    report = {"passed": not failed, "failed_checks": failed, "counts": dict(counts),
              "kernel_sample_by_split": dict(kernel_by_split),
              "examples": {name: found[:20] for name, found in examples.items()}}
    (out_dir / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    log_metrics(run_dir, final=True, passed=not failed, **dict(counts))
    print("AUDIT PASSED" if not failed else f"AUDIT FAILED: {', '.join(failed)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
