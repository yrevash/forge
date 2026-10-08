"""Audit the third-mix session files (data/freecad/sessions_wide/). Exit 0 only if all pass.

The same six checks as audit.py, on the wide slices, with what is new about them:
  1. teacher   every record's target is exactly what the teacher returns for that record's
               (plan, snapshot); every target is in the valid list; a command the teacher
               chose was never refused; a wrong command is never a target;
  2. oracle    the same labels judged by oracle.py from the session's FINAL document, which
               shares no code with the teacher or the recipes;
  3. replay    a sample of sessions is played again in a fresh FreeCAD: the start commands,
               then every executed command; every snapshot, valid list and reply must be the
               recorded one, and the end must be the part's stored solid on a clean document;
  4. splits    every session's part is in the set its slice is made of, with the plan the
               part file holds; one session per part; no held-out PAIRING anywhere; a
               held-out ORDER in every plan of wide_order_v3 and in no other; held-out scales
               and 3+ decimals in every part of wide_numbers_v3 and in no other; a held-out
               START kind in every session of abnormal_starts_v3 and in no other;
  5. content   no part id, plan or geometry fingerprint is shared between any two wide
               slices, or between a wide slice and ANY older split (train, train_long, iid,
               pairing, long, xlong: read from the part files those sessions were made from);
  6. endings   every session ends with the teacher's `done` on the stored solid and a clean
               document. Sessions that ended otherwise FAIL the audit, or are listed with
               --allow-excluded (load.py never reads them).
The plan and the stored measurement are read again from data/system1/wide_parts/, not
trusted from the session files. Shards that are complete are audited, so this can run
while recording goes on.

Run:    uv run python -m forge.freecad.wide_audit [--replay-sample 1500] [--workers 3]
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean, same_lean
from forge.freecad.oracle import agrees, oracle
from forge.freecad.play import end_check, run_start, session_id
from forge.freecad.shards import complete_shards, read_sessions, sha256
from forge.freecad.teacher import script_of, teacher
from forge.freecad.wide_parts import (
    SETS,
    facts_of,
    old_parts,
    plan_fingerprint,
    read_wide,
    steps_of_part,
)
from forge.freecad.wide_sessions import OUT_DIR, SEED, SLICES, TEST_SLICES, TRAIN_SLICES
from forge.freecad.wide_starts import HELD_OUT_STARTS
from forge.runs import log_metrics, start_run
from forge.system1.splits import unit_hash

CHECKS = ("teacher", "oracle", "replay", "splits", "content", "endings")
_parts: dict[str, dict] = {}
_client: FreeCADClient | None = None


def _load_parts(parts: dict[str, dict]) -> None:
    _parts.update(parts)


def held_out_start(kind: str) -> bool:
    return kind.removeprefix("opened_") in HELD_OUT_STARTS


def audit_session(header: dict, records: list[dict], end: dict, part: dict) -> dict[str, list[str]]:
    problems: dict[str, list[str]] = {name: [] for name in CHECKS}
    plan = steps_of_part(part)                      # from the part file, not from the header
    if header["plan"] != part["plan"]:
        problems["teacher"].append("the stored plan is not the part's plan")
    script = script_of(plan)
    for record in records:
        t, done = record["t"], record["executed"]
        advice = teacher(plan, record["snapshot"], script)
        if record["target"] != [target.to_json() for target in advice.targets] \
                or record["progress"] != {"on_plan": advice.on_plan, "built": advice.built,
                                          "active": advice.active}:
            problems["teacher"].append(f"t={t}: the target is not the teacher's answer")
        missing = [target["command"] for target in record["target"]
                   if target["command"] not in record["valid"]]
        if missing:
            problems["teacher"].append(f"t={t}: target {missing} is not in the valid list")
        is_target = any(done["command"] == target["command"] and done["args"] == target["args"]
                        for target in record["target"])
        if done["noise"] and is_target:
            problems["teacher"].append(f"t={t}: a wrong command equals a target")
        if not done["noise"] and (not is_target or record["reply"]["status"] != "ok"):
            problems["teacher"].append(f"t={t}: the teacher's command was not carried out "
                                       f"({record['reply']})")
        on_plan, commands = oracle(record["snapshot"], end["snapshot"])
        if on_plan != record["progress"]["on_plan"]:
            problems["oracle"].append(f"t={t}: recorded on_plan {record['progress']['on_plan']}, "
                                      f"the final document says {on_plan}")
        elif not agrees(commands, record["target"]):
            problems["oracle"].append(f"t={t}: the final document asks for {commands}")

    # Check 4.
    name = header["slice"]
    part_set, facts = SLICES[name][0], facts_of(part)
    profile, order = SETS[part_set][0], SETS[part_set][1]
    if header["split"] != part_set or part["set"] != part_set:
        problems["splits"].append(f"in {name}, its part is of {part['set']}")
    if facts["pairing"]:
        problems["splits"].append("a held-out pairing")
    if facts["order"] != order:
        problems["splits"].append(f"held-out order: {facts['order']}, the slice wants {order}")
    if facts["held_out_scale"] != (profile == "numbers") \
            or (facts["most_decimals"] > 2) != (profile == "numbers"):
        problems["splits"].append("its numbers are not of the slice's profile")
    if held_out_start(header["start"]["kind"]) != (name == "abnormal_starts_v3"):
        problems["splits"].append(f"start kind {header['start']['kind']} in {name}")
    if header["session"] != session_id(header["part_id"], SEED) or header["seed"] != SEED:
        problems["splits"].append("the session id is not its part's")

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
    path, fractions, seed = task
    counts: Counter = Counter()
    examples: dict[str, list[str]] = {name: [] for name in CHECKS}
    seen_sessions: Counter = Counter()
    content: dict[str, set] = {}
    excluded, candidates = [], []
    for header, records, end in read_sessions(Path(path)):
        counts["sessions"] += 1
        counts[f"end:{end['end']}"] += 1
        seen_sessions[header["part_id"]] += 1
        if header["slice"] != Path(path).parent.name:
            counts["bad:splits"] += 1
        if end["end"] != "done":
            excluded.append(f"{header['slice']} {header['session']} (part {header['part_id']}): "
                            f"ended {end['end']} after {end['steps']} steps")
            continue
        counts["steps"] += len(records)
        part = _parts.get(header["part_id"])
        if part is None:
            counts["bad:teacher"] += 1
            examples["teacher"].append(f"{header['session']}: part not found")
            continue
        content.setdefault(header["slice"], set()).add(
            (header["part_id"], plan_fingerprint(header["plan"]), part["geom_fingerprint"]))
        for name, found in audit_session(header, records, end, part).items():
            if found:
                counts[f"bad:{name}"] += 1
                if len(examples[name]) < 5:
                    examples[name].append(f"{header['session']}: {found[0]}")
        point = unit_hash(f"audit:{seed}:{header['session']}")
        if point < fractions[header["slice"]]:
            candidates.append((point, header["slice"], header["session"]))
    return {"path": path, "counts": counts, "examples": examples, "candidates": candidates,
            "seen_sessions": seen_sessions, "content": content, "excluded": excluded}


def shared_content(content: dict[str, set], old: dict[str, dict[str, set]],
                   ) -> tuple[list[str], dict[str, int], int]:
    """Check 5. Returns (problems, sessions per wide slice, how many pairs were compared)."""
    groups: dict[str, dict[str, set]] = {
        name: {"ids": {i for i, _, _ in found}, "plans": {p for _, p, _ in found},
               "shapes": {s for _, _, s in found}} for name, found in content.items()}
    sizes = {name: len(found) for name, found in content.items()}
    groups.update({f"old:{split}": value for split, value in old.items()})
    names, problems, pairs = list(groups), [], 0
    for at, first in enumerate(names):
        for second in names[at + 1:]:
            if first.startswith("old:") and second.startswith("old:"):
                continue                    # audit.py's own check 5 covers old against old
            pairs += 1
            for key in ("ids", "plans", "shapes"):
                same = groups[first][key] & groups[second][key]
                if same:
                    problems.append(f"{len(same)} {key} are in both {first} and {second}")
    return problems, sizes, pairs


def close(a: object, b: object) -> bool:
    """Are two lean snapshots the same state? Exactly equal, or equal with two allowances
    that the first audit of the wide slices showed to be needed:
      - numbers may differ by 0.000001 (the teacher's own tolerance): FreeCAD's bounding box
        of a filleted solid moved by 0.0000001 mm between two builds of the same commands;
      - the volume of a solid FreeCAD itself marks INVALID is not compared: it differed by
        0.2% between two runs of the same wrong pattern. Such a state is off plan either way.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        skip = {"volume"} if a.get("valid") is False and b.get("valid") is False \
            and "volume" in a else set()
        return all(close(a[key], b[key]) for key in a if key not in skip)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b, strict=True))
    numbers = (int, float)
    if isinstance(a, numbers) and isinstance(b, numbers) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(a - b) <= 1e-6
    return a == b


def replay_session(fc: FreeCADClient, header: dict, records: list[dict], end: dict,
                   part: dict, notes: Counter | None = None) -> list[str]:
    """Play one recorded session again. Returns what differs (nothing, we hope).

    A command whose recorded reply is `timeout` or `crash` is NOT issued again: whether
    FreeCAD answers inside its time limit depends on how busy the machine is, and after
    either reply the session is unchanged by contract (client.py). Counted in `notes`."""
    notes = Counter() if notes is None else notes

    def same(now: dict, recorded: dict) -> bool:
        if same_lean(now, recorded):
            return True
        notes["snapshots_equal_only_within_tolerance"] += 1
        return close(now, recorded)

    start = header["start"]
    reply, carried_out = run_start(fc, [tuple(command) for command in start["commands"]],
                                   start["forget_undo"])
    if carried_out != start["commands"]:
        return ["the start commands were not all carried out again"]
    for record in records:
        if record.get("before") == "forget_undo":
            reply = fc.forget_undo()
        if not same(lean(reply["snapshot"]), record["snapshot"]):
            return [f"t={record['t']}: the snapshot is not the recorded one"]
        if reply["valid"] != record["valid"]:
            return [f"t={record['t']}: the valid commands are not the recorded ones"]
        done = record["executed"]
        if record["reply"]["status"] in ("timeout", "crash"):
            notes["commands_not_issued_again_recorded_timeout_or_crash"] += 1
            continue
        reply = fc.command(done["command"], **done["args"])
        if reply["status"] != record["reply"]["status"]:
            return [(f"t={record['t']}: {done['command']} gave {reply['status']}, recorded "
                     f"{record['reply']['status']}")]
    if not same(lean(reply["snapshot"]), end["snapshot"]):
        return ["the final snapshot is not the recorded one"]
    return end_check(steps_of_part(part), reply["snapshot"], part["measured"])


def _new_client() -> FreeCADClient:
    global _client
    if _client is not None:
        _client.close()
    _client = FreeCADClient(exact_undo=True)
    _client.start()
    return _client


def replay_shard(task: tuple[str, list[str]]) -> dict:
    """Replay the chosen sessions of one shard. A session that does not replay is replayed
    ONCE more on a new FreeCAD worker: a worker that timed out or was replaced under an
    earlier session has been seen to carry something over into the next one. Both numbers
    are reported (failed, and passed only at the second try)."""
    path, wanted = task
    fc = _client or _new_client()
    bad, steps, by_slice, notes = [], 0, Counter(), Counter()
    for header, records, end in read_sessions(Path(path)):
        if header["session"] not in wanted:
            continue
        part = _parts[header["part_id"]]
        found = ["not replayed"]
        for attempt in (1, 2):
            try:
                found = replay_session(fc, header, records, end, part, notes)
            except Exception as error:      # noqa: BLE001 - a lost worker must not stop the audit
                found = [f"{type(error).__name__}: {error}"]
            if not found:
                notes["passed_only_at_the_second_try"] += attempt == 2
                break
            notes["restarts"] += 1
            fc = _new_client()
        steps += len(records)
        by_slice[header["slice"]] += 1
        if found:
            bad.append(f"{header['session']}: {found[0]}")
    return {"bad": bad, "steps": steps, "by_slice": by_slice, "notes": notes}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", default=str(OUT_DIR))
    parser.add_argument("--replay-sample", type=int, default=1500)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--replay-workers", type=int, default=None)
    parser.add_argument("--allow-excluded", action="store_true")
    parser.add_argument("--slices", nargs="*", default=None, choices=list(SLICES))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    out_dir = Path(args.dir)
    run_dir = start_run("freecad-wide-audit", vars(args))
    started = time.time()

    parts = {part["id"]: part for name in SETS for part in read_wide(name)}
    found = complete_shards(out_dir, args.slices)
    stats = [shard for shard, _ in found]
    sessions_in: Counter = Counter()
    for shard in stats:
        sessions_in[shard["slice"]] += shard["counts"].get("end:done", 0)
    live = [name for name in SLICES if sessions_in[name]]
    quota = -(-args.replay_sample // max(len(live), 1))
    fractions = {name: min(1.0, 1.5 * quota / max(sessions_in[name], 1)) for name in SLICES}

    counts: Counter = Counter()
    examples: dict[str, list[str]] = {name: [] for name in CHECKS}
    seen_sessions: Counter = Counter()
    content: dict[str, set] = {}
    excluded: list[str] = []
    candidates: dict[str, list] = {name: [] for name in SLICES}
    where: dict[str, str] = {}
    with multiprocessing.Pool(args.workers, initializer=_load_parts, initargs=(parts,)) as pool:
        tasks = [(str(path), fractions, args.seed) for _, path in found]
        for result in pool.imap_unordered(audit_shard, tasks):
            counts.update(result["counts"])
            seen_sessions.update(result["seen_sessions"])
            excluded += result["excluded"]
            for name in CHECKS:
                examples[name] += result["examples"][name]
            for name, items in result["content"].items():
                content.setdefault(name, set()).update(items)
            for point, name, session in result["candidates"]:
                candidates[name].append((point, session))
                where[session] = result["path"]

    twice = [part_id for part_id, n in seen_sessions.items() if n > 1]
    counts["bad:splits"] += len(twice)
    examples["splits"] += [f"part {part_id} has {seen_sessions[part_id]} sessions"
                           for part_id in twice[:5]]
    shared, sessions_by_slice, pairs = shared_content(content, old_parts())
    counts["bad:content"] += len(shared)
    examples["content"] += shared
    recorded = sum(shard["counts"].get("sessions", 0) for shard in stats)
    changed = [shard["file"] for shard in stats if sha256(out_dir / shard["file"]) != shard["sha256"]]
    if changed or recorded != counts["sessions"]:
        counts["bad:endings"] += 1
        examples["endings"].append(f"files differ from their stats: {changed[:3]}, "
                                   f"{recorded} sessions recorded, {counts['sessions']} read")
    if excluded and not args.allow_excluded:
        counts["bad:endings"] += len(excluded)
        examples["endings"] = excluded[:5] + examples["endings"]

    chosen: list[str] = []
    spare = sorted((point, session) for name in SLICES
                   for point, session in sorted(candidates[name])[quota:])
    for name in SLICES:
        chosen += [session for _, session in sorted(candidates[name])[:quota]]
    chosen += [session for _, session in spare[:max(0, args.replay_sample - len(chosen))]]
    by_shard: dict[str, list[str]] = {}
    for session in chosen:
        by_shard.setdefault(where[session], []).append(session)
    replayed: Counter = Counter()
    notes: Counter = Counter()
    with ProcessPoolExecutor(max_workers=args.replay_workers or args.workers,
                             initializer=_load_parts, initargs=(parts,)) as pool:
        for result in pool.map(replay_shard, sorted(by_shard.items())):
            replayed.update(result["by_slice"])
            counts["replayed_steps"] += result["steps"]
            counts["bad:replay"] += len(result["bad"])
            examples["replay"] += result["bad"]
            notes.update(result["notes"])
    counts["replayed"] = sum(replayed.values())
    if counts["replayed"] < min(args.replay_sample, counts["end:done"]):
        counts["bad:replay"] += 1
        examples["replay"].append(f"only {counts['replayed']} sessions were replayed")

    unfinished = {key[4:]: value for key, value in counts.items()
                  if key.startswith("end:") and key != "end:done"}
    rejected = sum(shard["counts"].get("parts_rejected_by_freecad", 0) for shard in stats)
    print(f"audited {counts['sessions']} sessions ({counts['end:done']} finished, "
          f"{counts['steps']} steps) in {len(found)} shards ({time.time() - started:.0f}s); "
          f"parts the recorder rejected because FreeCAD builds them differently: {rejected}")
    print(f"sessions that did not end with done: {unfinished or 0}"
          + ("  (listed as excluded: --allow-excluded)" if excluded and args.allow_excluded else ""))
    for line in excluded[:10] if args.allow_excluded else []:
        print(f"        excluded: {line}")
    wording = {
        "teacher": "every target is the teacher's answer for its (plan, snapshot), is valid, "
                   "and was carried out when chosen",
        "oracle": "every on-plan flag and target set is what the session's final document "
                  "implies (oracle.py, no teacher or recipe code)",
        "replay": f"{counts['replayed']} sessions ({counts['replayed_steps']} steps) replayed in "
                  f"FreeCAD reproduce every snapshot and end at the stored solid {dict(replayed)}",
        "splits": "every session's part is of its slice's set; no held-out pairing; held-out "
                  "orders, scales, decimals and start kinds only in their test slice, and "
                  "there in every session; one session per part",
        "content": (f"no part id, plan or geometry fingerprint is shared between any two wide "
                    f"slices, or between a wide slice and an older split ({pairs} pairs of "
                    f"sets compared; wide sessions: {sessions_by_slice})"),
        "endings": "every session ends with done on a correct, clean document"
                   + (f", except {len(excluded)} excluded" if excluded and args.allow_excluded else ""),
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
    print(f"replay notes: {dict(notes)}")
    report = {"passed": not failed, "failed_checks": failed, "counts": dict(counts),
              "replayed_by_slice": dict(replayed), "unfinished": unfinished,
              "excluded": excluded, "allow_excluded": args.allow_excluded,
              "sessions_by_slice": sessions_by_slice, "parts_rejected_by_freecad": rejected,
              "replay_notes": dict(notes),
              "train_slices": TRAIN_SLICES, "test_slices": TEST_SLICES,
              "examples": {name: found[:20] for name, found in examples.items()}}
    (out_dir / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    log_metrics(run_dir, final=True, passed=not failed, **dict(counts))
    print("AUDIT PASSED" if not failed else f"AUDIT FAILED: {', '.join(failed)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
