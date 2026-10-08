"""Audit the FreeCAD session files. Exit code 0 only if every check passes.

The six checks:
  1. teacher   every record's target is exactly what the teacher returns for that record's
               (plan, snapshot); every target is in the record's valid list; a command the
               teacher chose was never refused; a noise command is never a target;
  2. oracle    the same labels, judged by code that shares nothing with the teacher or the
               recipes (oracle.py): every record's on-plan flag and target set must be what
               the session's FINAL document implies. Check 1 asks the teacher whether the
               teacher was right; this one does not;
  3. replay    a random sample of sessions is played again in a fresh FreeCAD: the start
               commands, then every executed command. The snapshot before every step, the
               valid list and FreeCAD's reply must be the recorded ones, and the session must
               end at the part's stored solid with a clean document. A session is replayed
               on the runtime it was recorded with (exact undo or not, from its header);
  4. splits    every session is in its part's split (`forge.system1.splits`), no held-out
               pairing is in `train` or `train_long`, no long part is in `train`, only parts
               generated for it are in `train_long`, and no (part, seed) has two sessions;
  5. content   the splits are also compared by what is IN them, not only by the function
               that assigned them: no plan (kinds and numbers) and no geometry fingerprint
               occurs in two different splits (train, train_long, iid, pairing, long, xlong);
  6. endings   every session ends with the teacher's `done` on a correct, clean document.
               A session that ended any other way (`budget`, `stuck`, `error`) FAILS the
               audit. With `--allow-excluded` such sessions are listed as excluded instead;
               load.py never reads them either way.

The audit trusts nothing in the session files about the part: the plan and the
stored measurement are read again from data/generated/ and data/system1/ (long_parts,
long_train_parts, xlong_parts).
It audits the shards that are complete, so it can run while generation is going on.

Run:    uv run python -m forge.freecad.audit [--replay-sample 2000] [--workers 3]
        uv run python -m forge.freecad.audit --allow-excluded
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean, same_lean
from forge.freecad.oracle import agrees, oracle
from forge.freecad.parts import session_parts
from forge.freecad.play import end_check, run_start, session_id
from forge.freecad.shards import (
    OUT_DIR,
    SLICES,
    TEST_SPLITS,
    TRAIN_SPLITS,
    complete_shards,
    read_sessions,
    sha256,
)
from forge.freecad.teacher import plan_json, script_of, teacher
from forge.runs import log_metrics, start_run
from forge.system1.splits import MAX_TRAIN_ITEMS, held_out_pairs_in, split_of, unit_hash
from forge.system1.steps import steps_of

CHECKS = ("teacher", "oracle", "replay", "splits", "content", "endings")
_parts: dict[str, dict] = {}            # part id -> row; filled once in every worker process
_client: FreeCADClient | None = None


def _load_parts(parts: dict[str, dict]) -> None:
    _parts.update(parts)


def plan_fingerprint(plan: list[dict]) -> str:
    """A hash of a stored plan: its kinds and numbers, in order."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]


# --- checks 1, 2, 4 and 6: plain Python, every session -------------------------------------------

def audit_session(header: dict, records: list[dict], end: dict, part: dict) -> dict[str, list[str]]:
    problems: dict[str, list[str]] = {name: [] for name in CHECKS}
    plan = steps_of(part["family"], part["params"])         # from the part, not from the header
    if header["plan"] != plan_json(plan):
        problems["teacher"].append("the stored plan is not the part's plan")
    script = script_of(plan)

    for record in records:
        t, done = record["t"], record["executed"]
        # Check 1.
        advice = teacher(plan, record["snapshot"], script)
        if record["target"] != [target.to_json() for target in advice.targets] \
                or record["progress"] != {"on_plan": advice.on_plan, "built": advice.built,
                                          "active": advice.active}:
            problems["teacher"].append(f"t={t}: the target is not the teacher's answer")
        missing = [target["command"] for target in record["target"]
                   if target["command"] not in record["valid"]]
        if missing:
            problems["teacher"].append(f"t={t}: target {missing} is not in the valid list")
        executed_is_target = any(done["command"] == target["command"]
                                 and done["args"] == target["args"]
                                 for target in record["target"])
        if done["noise"] and executed_is_target:
            problems["teacher"].append(f"t={t}: a noise command equals a target")
        if not done["noise"] and (not executed_is_target or record["reply"]["status"] != "ok"):
            problems["teacher"].append(f"t={t}: the teacher's command was not carried out "
                                       f"({record['reply']})")
        # Check 2: the final document's opinion.
        on_plan, commands = oracle(record["snapshot"], end["snapshot"])
        if on_plan != record["progress"]["on_plan"]:
            problems["oracle"].append(f"t={t}: recorded on_plan {record['progress']['on_plan']}, "
                                      f"the final document says {on_plan}")
        elif not agrees(commands, record["target"]):
            problems["oracle"].append(f"t={t}: the final document asks for {commands}")

    # Check 4.
    if header["split"] != split_of(part):
        problems["splits"].append(f"in {header['split']}, its part belongs in {split_of(part)}")
    if header["split"] in TRAIN_SPLITS \
            and held_out_pairs_in([item["kind"] for item in header["plan"]]):
        problems["splits"].append(f"a held-out pairing is in {header['split']}")
    if header["split"] == "train_long" and part.get("set") != "train_long":
        problems["splits"].append("a part of another set is in train_long")
    if header["split"] == "train" \
            and (part["long"] or len(header["plan"]) - 1 > MAX_TRAIN_ITEMS):
        problems["splits"].append("a long part is in train")

    # Check 6 (the recorded side; check 3 confirms a sample of it in FreeCAD).
    last = records[-1] if records else None
    if last is None or last["executed"] != {"command": "done", "args": {}, "noise": None} \
            or [target["command"] for target in last["target"]] != ["done"]:
        problems["endings"].append("the session does not end with the teacher's done")
    elif end["problems"]:
        problems["endings"].append(f"ended on a wrong document: {end['problems'][0]}")
    else:
        final = teacher(plan, end["snapshot"], script)
        if final.targets or not final.on_plan or not end["snapshot"]["session"]["finished"]:
            problems["endings"].append(f"the final document is not clean: {final.why}")
    return problems


def audit_shard(task: tuple[str, dict[str, float], int]) -> dict:
    """Every check but the replay on one shard, and the candidates for the replay."""
    path, fractions, seed = task
    counts: Counter = Counter()
    examples: dict[str, list[str]] = {name: [] for name in CHECKS}
    sessions_of_part: Counter = Counter()
    part_splits: dict[str, str] = {}
    content: dict[str, set] = {}        # split -> {(plan fingerprint, geometry fingerprint)}
    excluded = []
    candidates = []
    for header, records, end in read_sessions(Path(path)):
        counts["sessions"] += 1
        counts[f"end:{end['end']}"] += 1
        sessions_of_part[header["session"]] += 1       # one id per (part, seed)
        if header["session"] != session_id(header["part_id"], header["seed"]) \
                or header["slice"] != Path(path).parent.name:
            counts["bad:splits"] += 1
        part_splits[header["part_id"]] = header["split"]
        if end["end"] != "done":
            excluded.append(f"{header['slice']} {header['session']} (part {header['part_id']}): "
                            f"ended {end['end']} after {end['steps']} steps")
            continue                    # never training data; the audit fails or lists it
        counts["steps"] += len(records)
        part = _parts.get(header["part_id"])
        if part is None:
            counts["bad:teacher"] += 1
            examples["teacher"].append(f"{header['session']}: part not found")
            continue
        content.setdefault(header["split"], set()).add(
            (plan_fingerprint(header["plan"]), part["geom_fingerprint"]))
        for name, found in audit_session(header, records, end, part).items():
            if found:
                counts[f"bad:{name}"] += 1
                if len(examples[name]) < 5:
                    examples[name].append(f"{header['session']}: {found[0]}")
        point = unit_hash(f"audit:{seed}:{header['session']}")
        if point < fractions[header["slice"]]:
            candidates.append((point, header["slice"], header["session"]))
    return {"path": path, "counts": counts, "examples": examples, "candidates": candidates,
            "sessions_of_part": sessions_of_part, "part_splits": part_splits,
            "content": content, "excluded": excluded}


def shared_content(content: dict[str, set]) -> tuple[list[str], dict[str, int]]:
    """Check 5: plans and geometry that two splits share. Returns (problems, the counts)."""
    problems, sizes = [], {}
    names = [name for name in (*TRAIN_SPLITS, *TEST_SPLITS) if name in content]
    for at, first in enumerate(names):
        plans = {plan for plan, _ in content[first]}
        shapes = {shape for _, shape in content[first]}
        sizes[first] = len(plans)
        for second in names[at + 1:]:
            same_plans = plans & {plan for plan, _ in content[second]}
            same_shapes = shapes & {shape for _, shape in content[second]}
            if same_plans:
                problems.append(f"{len(same_plans)} plans are in both {first} and {second}")
            if same_shapes:
                problems.append(f"{len(same_shapes)} geometry fingerprints are in both {first} "
                                f"and {second}")
    return problems, sizes


# --- check 3: play sampled sessions again in FreeCAD ---------------------------------------------

def replay_session(fc: FreeCADClient, header: dict, records: list[dict], end: dict,
                   part: dict) -> list[str]:
    """Play one recorded session again. Returns what differs (nothing, we hope)."""
    # The runtime as it was when the session was recorded: the first recording had no
    # exact undo (inside/session.py), and its sessions only replay without it.
    exact = header.get("runtime", {}).get("exact_undo", False)
    if fc.exact is not exact:
        fc.set_exact_undo(exact)
    start = header["start"]
    reply, carried_out = run_start(fc, [tuple(command) for command in start["commands"]],
                                   start["forget_undo"])
    if carried_out != start["commands"]:
        return ["the start commands were not all carried out again"]
    for record in records:
        if record.get("before") == "forget_undo":      # play.py, "reopen"
            reply = fc.forget_undo()
        if not same_lean(lean(reply["snapshot"]), record["snapshot"]):
            return [f"t={record['t']}: the snapshot is not the recorded one"]
        if reply["valid"] != record["valid"]:
            return [f"t={record['t']}: the valid commands are not the recorded ones"]
        done = record["executed"]
        reply = fc.command(done["command"], **done["args"])
        if reply["status"] != record["reply"]["status"]:
            return [(f"t={record['t']}: {done['command']} gave {reply['status']}, recorded "
                     f"{record['reply']['status']}")]
    if not same_lean(lean(reply["snapshot"]), end["snapshot"]):
        return ["the final snapshot is not the recorded one"]
    return end_check(steps_of(part["family"], part["params"]), reply["snapshot"],
                     part["measured"])


def replay_shard(task: tuple[str, list[str]]) -> dict:
    """Replay the chosen sessions of one shard (runs in a worker process with its own FreeCAD)."""
    global _client
    path, wanted = task
    if _client is None:
        _client = FreeCADClient()
        _client.start()
    bad, steps, by_split = [], 0, Counter()
    restarts = -_client.restarts
    for header, records, end in read_sessions(Path(path)):
        if header["session"] not in wanted:
            continue
        try:
            found = replay_session(_client, header, records, end, _parts[header["part_id"]])
        except Exception as error:      # noqa: BLE001 - a lost worker must not stop the audit
            found = [f"{type(error).__name__}: {error}"]
            restarts += _client.restarts + 1
            _client.close()
            _client = FreeCADClient()
            _client.start()
        steps += len(records)
        by_split[header["slice"]] += 1
        if found:
            bad.append(f"{header['session']}: {found[0]}")
    return {"bad": bad, "steps": steps, "by_split": by_split,
            "restarts": restarts + _client.restarts}


# --- the command -------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", default=str(OUT_DIR))
    parser.add_argument("--replay-sample", type=int, default=2000,
                        help="sessions to play again in FreeCAD, shared between the slices")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--replay-workers", type=int, default=None,
                        help="FreeCAD workers for the replay (default: --workers)")
    parser.add_argument("--allow-excluded", action="store_true",
                        help="list sessions that did not end with done instead of failing")
    parser.add_argument("--slices", nargs="*", default=None, choices=list(SLICES),
                        help="audit only these slices (default: all that are on disk)")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    out_dir = Path(args.dir)
    run_dir = start_run("freecad-audit", vars(args))
    started = time.time()

    parts = {part["id"]: part for part in session_parts(added=True)}
    # A shard is audited once its stats file exists (the generator writes that last).
    found = complete_shards(out_dir, args.slices)
    stats = [shard for shard, _ in found]
    shards = [path for _, path in found]
    sessions_in = Counter()
    for shard in stats:
        sessions_in[shard["slice"]] += shard["counts"].get("end:done", 0)
    live = [name for name in SLICES if sessions_in[name]]
    quota = -(-args.replay_sample // max(len(live), 1))
    fractions = {name: min(1.0, 1.5 * quota / max(sessions_in[name], 1)) for name in SLICES}

    counts: Counter = Counter()
    examples: dict[str, list[str]] = {name: [] for name in CHECKS}
    sessions_of_part: Counter = Counter()
    part_splits: dict[str, set[str]] = {}
    content: dict[str, set] = {}
    excluded: list[str] = []
    candidates: dict[str, list] = {name: [] for name in SLICES}
    where: dict[str, str] = {}
    with multiprocessing.Pool(args.workers, initializer=_load_parts, initargs=(parts,)) as pool:
        tasks = [(str(path), fractions, args.seed) for path in shards]
        for result in pool.imap_unordered(audit_shard, tasks):
            counts.update(result["counts"])
            sessions_of_part.update(result["sessions_of_part"])
            excluded += result["excluded"]
            for name in CHECKS:
                examples[name] += result["examples"][name]
            for part_id, split in result["part_splits"].items():
                part_splits.setdefault(part_id, set()).add(split)
            for split, items in result["content"].items():
                content.setdefault(split, set()).update(items)
            for point, split, session in result["candidates"]:
                candidates[split].append((point, session))
                where[session] = result["path"]

    twice = [part_id for part_id, n in sessions_of_part.items() if n > 1]
    torn = [part_id for part_id, found in part_splits.items() if len(found) > 1]
    counts["bad:splits"] += len(twice) + len(torn)
    examples["splits"] += [f"session {sid} is there {sessions_of_part[sid]} times"
                           for sid in twice[:5]]
    shared, plans_in = shared_content(content)
    counts["bad:content"] += len(shared)
    examples["content"] += shared
    # The files must be the ones their stats describe.
    recorded = sum(shard["counts"]["sessions"] for shard in stats)
    changed = [shard["file"] for shard in stats if sha256(out_dir / shard["file"]) != shard["sha256"]]
    if changed or recorded != counts["sessions"]:
        counts["bad:endings"] += 1
        examples["endings"].append(f"files differ from their stats: {changed[:3]}, "
                                   f"{recorded} sessions recorded, {counts['sessions']} read")
    if excluded and not args.allow_excluded:
        counts["bad:endings"] += len(excluded)
        examples["endings"] = excluded[:5] + examples["endings"]

    # Check 3. A slice with fewer sessions than its share gives what it has to the others.
    chosen: list[str] = []
    spare = sorted((point, session) for split in SLICES
                   for point, session in sorted(candidates[split])[quota:])
    for split in SLICES:
        chosen += [session for _, session in sorted(candidates[split])[:quota]]
    chosen += [session for _, session in spare[:max(0, args.replay_sample - len(chosen))]]
    by_shard: dict[str, list[str]] = {}
    for session in chosen:
        by_shard.setdefault(where[session], []).append(session)
    replayed = Counter()
    restarts = 0
    with ProcessPoolExecutor(max_workers=args.replay_workers or args.workers,
                             initializer=_load_parts,
                             initargs=(parts,)) as pool:
        for result in pool.map(replay_shard, sorted(by_shard.items())):
            replayed.update(result["by_split"])
            counts["replayed_steps"] += result["steps"]
            counts["bad:replay"] += len(result["bad"])
            examples["replay"] += result["bad"]
            restarts += result["restarts"]
    counts["replayed"] = sum(replayed.values())
    if counts["replayed"] < min(args.replay_sample, counts["end:done"]):
        counts["bad:replay"] += 1
        examples["replay"].append(f"only {counts['replayed']} sessions were replayed")

    unfinished = {key[4:]: value for key, value in counts.items()
                  if key.startswith("end:") and key != "end:done"}
    print(f"audited {counts['sessions']} sessions ({counts['end:done']} finished, "
          f"{counts['steps']} steps) of {len(part_splits)} parts in {len(shards)} shards "
          f"({time.time() - started:.0f}s)")
    print(f"sessions that did not end with done: {unfinished or 0}"
          + ("  (listed as excluded: --allow-excluded)" if excluded and args.allow_excluded
             else ""))
    for line in excluded[:10] if args.allow_excluded else []:
        print(f"        excluded: {line}")
    wording = {
        "teacher": "every target is the teacher's answer for its (plan, snapshot), is valid, "
                   "and was carried out when chosen",
        "oracle": "every on-plan flag and target set is what the session's final document "
                  "implies (oracle.py, no teacher or recipe code)",
        "replay": f"{counts['replayed']} sessions ({counts['replayed_steps']} steps) replayed in "
                  f"FreeCAD reproduce every snapshot and end at the stored solid "
                  f"{dict(replayed)}",
        "splits": "every session is in its part's split; no held-out pairing in train or "
                  "train_long; no long part in train; one session per (part, seed)",
        "content": f"no plan and no geometry fingerprint is shared between any two splits "
                   f"(distinct plans: {plans_in})",
        "endings": "every session ends with done on a correct, clean document"
                   + (f", except {len(excluded)} excluded" if excluded and args.allow_excluded
                      else ""),
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
    print(f"FreeCAD worker restarts during the replay: {restarts}")
    report = {"passed": not failed, "failed_checks": failed, "counts": dict(counts),
              "replayed_by_split": dict(replayed), "unfinished": unfinished,
              "excluded": excluded, "allow_excluded": args.allow_excluded,
              "distinct_plans_by_split": plans_in,
              "examples": {name: found[:20] for name, found in examples.items()}}
    (out_dir / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    log_metrics(run_dir, final=True, passed=not failed, **dict(counts))
    print("AUDIT PASSED" if not failed else f"AUDIT FAILED: {', '.join(failed)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
