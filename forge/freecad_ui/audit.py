"""Audit the interface session files. Exit code 0 only if every check passes.

The four checks, the command level's (forge/freecad/audit.py) as far as the
interface allows:
  1. teacher   every record's target is exactly what the teacher returns for that record's
               (plan, snapshot); every target is an element that the snapshot shows (or
               `done`); an action the teacher chose was never refused; a noise action is
               never a target, never a hidden button, and only ever a button from the list
               noise may press;
  2. replay    a random sample of sessions is played again in a fresh hidden FreeCAD: every
               executed action in order. The snapshot before every step (context, controls,
               tree, picks, document, solid) and FreeCAD's reply must be the recorded ones,
               and the session must end at the part's stored solid on a clean document.
               Toolbar buttons are not compared one by one (FreeCAD refreshes them on its
               own timer); instead the teacher's answer on the replayed snapshot must be the
               recorded target, which covers every button a decision depends on;
  3. splits    every session is in its part's split (`forge.system1.splits`), no held-out
               pairing and no long part is in `train`, and no (part, seed) has two sessions;
  4. endings   every finished session ends with the teacher's `done` on a correct, clean
               document. Sessions cut off, stuck or lost to a FreeCAD failure are counted,
               reported and take no part in training; they do not fail the audit.

The audit trusts nothing in the session files about the part: the plan and the
stored measurement are read again from data/generated/ and data/system1/long_parts/.
It audits the shards that are complete, so it can run while generation goes on.

Run:    uv run python -m forge.freecad_ui.audit [--replay-sample 60] [--replay-workers 1]
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from forge.freecad.parts import session_parts
from forge.freecad_ui.client import UIClient
from forge.freecad_ui.noise import WRONG_BUTTONS
from forge.freecad_ui.play import end_check, observe, session_id
from forge.freecad_ui.sessions import OUT_DIR, SLICES, read_sessions, sha256
from forge.freecad_ui.snapshots import same_snapshot
from forge.freecad_ui.teacher import DONE, plan_json, segments_of, teacher
from forge.runs import log_metrics, start_run
from forge.system1.splits import MAX_TRAIN_ITEMS, held_out_pairs_in, split_of, unit_hash
from forge.system1.steps import steps_of

CHECKS = ("teacher", "replay", "splits", "endings")
NEVER = ("button:Std_Open", "button:Std_Save", "button:Std_SaveAs", "button:Std_Print",
         "button:Std_Import", "button:Std_Export", "button:Std_New", "button:PartDesign_Body")


def _answer(advice) -> tuple[list[dict], dict]:
    return ([target.to_json() for target in advice.targets],
            {"on_plan": advice.on_plan, "built": advice.built, "active": advice.active})


# --- checks 1, 3 and 4: plain Python, every session ----------------------------------------------

def audit_session(header: dict, records: list[dict], end: dict, part: dict) -> dict[str, list[str]]:
    problems: dict[str, list[str]] = {name: [] for name in CHECKS}
    plan = steps_of(part["family"], part["params"])         # from the part, not from the header
    if header["plan"] != plan_json(plan):
        problems["teacher"].append("the stored plan is not the part's plan")
    segments = segments_of(plan)

    # Check 1.
    for record in records:
        t, done, snapshot = record["t"], record["executed"], record["snapshot"]
        if (record["target"], record["progress"]) != _answer(teacher(plan, snapshot, segments)):
            problems["teacher"].append(f"t={t}: the target is not the teacher's answer")
        showing = {*snapshot["buttons"], *(e["id"] for e in snapshot["elements"]), DONE}
        missing = [target["id"] for target in record["target"] if target["id"] not in showing]
        if missing:
            problems["teacher"].append(f"t={t}: target {missing} is not showing")
        is_target = any(done["id"] == target["id"] and done["value"] == target["value"]
                        for target in record["target"])
        if done["noise"]:
            if is_target:
                problems["teacher"].append(f"t={t}: a noise action equals a target")
            if done["id"] in NEVER or (done["id"].startswith("button:")
                                       and done["id"] not in (*WRONG_BUTTONS, "button:Std_Undo")):
                problems["teacher"].append(f"t={t}: noise pressed {done['id']}")
        elif not is_target or record["reply"]["status"] != "ok":
            problems["teacher"].append(f"t={t}: the teacher's action was not carried out "
                                       f"({done['id']}: {record['reply']})")
        hidden = [name for name in snapshot["buttons"] if name in NEVER[:6]]
        if hidden:
            problems["teacher"].append(f"t={t}: {hidden} is listed as an element")

    # Check 3.
    if header["split"] != split_of(part):
        problems["splits"].append(f"in {header['split']}, its part belongs in {split_of(part)}")
    if header["split"] == "train":
        if held_out_pairs_in([step.kind for step in plan]):
            problems["splits"].append("a held-out pairing is in train")
        if part["long"] or len(plan) - 1 > MAX_TRAIN_ITEMS:
            problems["splits"].append("a long part is in train")

    # Check 4 (the recorded side; check 2 confirms a sample of it in FreeCAD).
    last = records[-1] if records else None
    if last is None or last["executed"] != {"id": DONE, "value": None, "noise": None} \
            or [target["id"] for target in last["target"]] != [DONE]:
        problems["endings"].append("the session does not end with the teacher's done")
    elif end["problems"]:
        problems["endings"].append(f"ended on a wrong document: {end['problems'][0]}")
    else:
        found = end_check(plan, end["snapshot"], part["measured"])
        if found:
            problems["endings"].append(f"the final document is not right: {found[0]}")
    return problems


# --- check 2: play sampled sessions again in FreeCAD ---------------------------------------------

def replay_session(ui: UIClient, header: dict, records: list[dict], end: dict,
                   part: dict) -> list[str]:
    """Play one recorded session again. Returns what differs (nothing, we hope)."""
    plan = steps_of(part["family"], part["params"])
    segments = segments_of(plan)
    ui.new_part()
    snapshot = observe(ui)
    for record in records:
        differs = same_snapshot(snapshot, record["snapshot"], buttons=False)
        if differs:
            return [f"t={record['t']}: the snapshot's {differs} is not the recorded one"]
        if (record["target"], record["progress"]) != _answer(teacher(plan, snapshot, segments)):
            return [f"t={record['t']}: the teacher's answer is not the recorded one"]
        done = record["executed"]
        if done["id"] == DONE:
            break
        reply = ui.act(done["id"], done["value"], document=True)
        if reply["status"] != record["reply"]["status"]:
            return [(f"t={record['t']}: {done['id']} gave {reply['status']}, recorded "
                     f"{record['reply']['status']}")]
        snapshot = observe(ui, reply)
    differs = same_snapshot(snapshot, end["snapshot"], buttons=False)
    if differs:
        return [f"the final snapshot's {differs} is not the recorded one"]
    return end_check(plan, snapshot, part["measured"])


def replay_shards(tasks: list[tuple[str, list[str]]], parts: dict[str, dict]) -> dict:
    """Replay the chosen sessions of some shards with one hidden FreeCAD of its own."""
    bad, steps, by_slice, restarts = [], 0, Counter(), 0
    ui = UIClient()
    ui.start()
    try:
        for path, wanted in tasks:
            for header, records, end in read_sessions(Path(path)):
                if header["session"] not in wanted:
                    continue
                try:
                    found = replay_session(ui, header, records, end, parts[header["part_id"]])
                except Exception as error:      # noqa: BLE001 - a lost instance must not stop it
                    found = [f"{type(error).__name__}: {str(error)[:200]}"]
                    restarts += 1
                    ui.close()
                    ui = UIClient()
                    ui.start()
                steps += len(records)
                by_slice[header["slice"]] += 1
                if found:
                    bad.append(f"{header['session']}: {found[0]}")
    finally:
        restarts += ui.restarts
        ui.close()
    return {"bad": bad, "steps": steps, "by_slice": by_slice, "restarts": restarts}


# --- the command -------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", default=str(OUT_DIR))
    parser.add_argument("--replay-sample", type=int, default=60,
                        help="sessions to play again in FreeCAD, shared between the slices")
    parser.add_argument("--replay-workers", type=int, default=1,
                        help="hidden FreeCAD windows for the replay")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    out_dir = Path(args.dir)
    run_dir = start_run("freecad-ui-audit", vars(args))
    started = time.time()

    parts = {part["id"]: part for part in session_parts()}
    stats = [json.loads(path.read_text()) for path in sorted(out_dir.glob("*/shard*.stats.json"))]
    counts: Counter = Counter()
    examples: dict[str, list[str]] = {name: [] for name in CHECKS}
    seen: Counter = Counter()
    part_splits: dict[str, set[str]] = {}
    candidates: dict[str, list[tuple[float, str, str]]] = {name: [] for name in SLICES}
    for shard in stats:
        path = out_dir / shard["file"]
        if sha256(path) != shard["sha256"]:
            counts["bad:endings"] += 1
            examples["endings"].append(f"{shard['file']} is not the file its stats describe")
        read = 0
        for header, records, end in read_sessions(path):
            read += 1
            counts["sessions"] += 1
            counts[f"end:{end['end']}"] += 1
            seen[header["session"]] += 1
            if header["session"] != session_id(header["part_id"], header["seed"]) \
                    or header["slice"] != path.parent.name:
                counts["bad:splits"] += 1
            part_splits.setdefault(header["part_id"], set()).add(header["split"])
            if end["end"] != "done":
                continue                # counted, reported, not training data
            counts["steps"] += len(records)
            part = parts.get(header["part_id"])
            if part is None:
                counts["bad:teacher"] += 1
                examples["teacher"].append(f"{header['session']}: part not found")
                continue
            for name, found in audit_session(header, records, end, part).items():
                if found:
                    counts[f"bad:{name}"] += 1
                    if len(examples[name]) < 8:
                        examples[name].append(f"{header['session']}: {found[0]}")
            candidates[header["slice"]].append(
                (unit_hash(f"audit:{args.seed}:{header['session']}"), header["session"], str(path)))
        if read != shard["counts"]["sessions"]:
            counts["bad:endings"] += 1
            examples["endings"].append(f"{shard['file']}: {read} sessions read, "
                                       f"{shard['counts']['sessions']} recorded")
    twice = [sid for sid, n in seen.items() if n > 1]
    torn = [part_id for part_id, found in part_splits.items() if len(found) > 1]
    counts["bad:splits"] += len(twice) + len(torn)
    examples["splits"] += [f"session {sid} is there {seen[sid]} times" for sid in twice[:5]]

    # Check 2: the same number from every slice that has sessions; spare places go round.
    live = [name for name in SLICES if candidates[name]]
    quota = -(-args.replay_sample // max(len(live), 1))
    chosen = [item for name in live for item in sorted(candidates[name])[:quota]]
    chosen = sorted(chosen)[:args.replay_sample] if len(chosen) > args.replay_sample else chosen
    by_shard: dict[str, list[str]] = {}
    for _, session, path in chosen:
        by_shard.setdefault(path, []).append(session)
    groups = [sorted(by_shard.items())[k::args.replay_workers] for k in range(args.replay_workers)]
    replayed: Counter = Counter()
    restarts = 0
    if chosen:
        with ThreadPoolExecutor(max_workers=args.replay_workers) as pool:
            for result in pool.map(lambda group: replay_shards(group, parts),
                                   [g for g in groups if g]):
                replayed.update(result["by_slice"])
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
          f"{counts['steps']} steps) of {len(part_splits)} parts in {len(stats)} shards "
          f"({time.time() - started:.0f}s)")
    print(f"sessions that did not finish and are kept out of training: {unfinished or 0}")
    wording = {
        "teacher": "every target is the teacher's answer for its (plan, snapshot), is showing, "
                   "and was carried out when chosen; noise never pressed a forbidden button",
        "replay": f"{counts['replayed']} sessions ({counts['replayed_steps']} steps) replayed in "
                  f"FreeCAD reproduce every snapshot and end at the stored solid "
                  f"{dict(replayed)}",
        "splits": "every session is in its part's split; no held-out pairing or long part in "
                  "train; one session per (part, seed)",
        "endings": "every finished session ends with done on a correct, clean document",
    }
    failed = []
    for number, name in enumerate(CHECKS, start=1):
        bad = counts[f"bad:{name}"]
        print(f"  {number}. {'PASS' if not bad else 'FAIL'}  {wording[name]}"
              + (f"  ({bad} bad)" if bad else ""))
        for example in examples[name][:6]:
            print(f"        {example}")
        if bad:
            failed.append(name)
    print(f"instance restarts during the replay: {restarts}")
    report = {"passed": not failed, "failed_checks": failed, "counts": dict(counts),
              "replayed_by_slice": dict(replayed), "unfinished": unfinished,
              "examples": {name: found[:20] for name, found in examples.items()}}
    (out_dir / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    log_metrics(run_dir, final=True, passed=not failed, **dict(counts))
    print("AUDIT PASSED" if not failed else f"AUDIT FAILED: {', '.join(failed)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
