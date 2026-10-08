"""Audit the structure session files. Exit code 0 only if every check passes.

The seven checks:
  1. teacher   every record's target is exactly what the teacher returns for that record's
               (plan, snapshot); every target is in the record's valid list; a command the
               teacher chose was never refused; a noise command is never a target;
  2. oracle    the same labels, judged by code that shares nothing with the teacher or the
               recipes (oracle.py): every record's on-plan flag and target set must be what
               the session's FINAL document implies;
  3. replay    a sample of sessions is played again in a fresh FreeCAD: the start commands,
               then every executed command. The snapshot before every step, the valid list
               and FreeCAD's reply must be the recorded ones, and the session must end at
               the resolver's structure (every part's frame and volume, who touches whom,
               no overlap that is not `sunk`) with a clean document;
  4. splits    every session's plan is in data/plans exactly as the recorder read it, still
               resolves to its stored parts, and sits in the slice selection.py gave it,
               with the plan's source, licence and generator version; no (plan, seed) has
               two sessions;
  5. content   the splits compared by what is IN the sessions, from their headers alone:
               no set of parts is in train and in a test slice, or in two test slices; no
               held-out cell (selection.HELD_OUT_CELLS) is in train, and both halves of
               each one are; no held-out kind outside `kinds` and all of them in it; train
               and the other slices hold at most MAX_BODIES bodies and `long` more;
  6. coverage  every structure kind and every shape of the selection's train plans has
               train sessions; features, `select_side` and `activate_body` are targets
               (the numbers per kind, shape and feature are in the result file);
  7. endings   every session ends with the teacher's `done` on a correct, clean document.
               A session that ended `budget`, `stuck` or `error` FAILS the audit, always; the
               shard files are the ones their stats files describe (hash and counts). A
               session that ended with `done` but whose final structure FreeCAD and the
               resolver disagree about (a contact one of them sees and the other does not)
               fails it too, unless `--allow-excluded` lists it instead; load.py never
               reads such a session either way.

The audit trusts nothing in the session files about the structure: the plan is resolved
again from its text. It audits the shards that are complete, so it can run while
generation is going on (then check 6 may fail for a slice that has hardly begun).

Run:    uv run python -m forge.freecad_multi.audit [--replay-sample 600] [--workers 2]
Result: <dir>/audit.json
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from forge.freecad.play import run_start
from forge.freecad_multi import sources
from forge.freecad_multi.client import MultiClient
from forge.freecad_multi.lean import lean, same_lean
from forge.freecad_multi.oracle import agrees, oracle
from forge.freecad_multi.play import end_check, plan_json, session_id
from forge.freecad_multi.recipes import script_of, structure_items
from forge.freecad_multi.selection import HELD_OUT_CELLS, MAX_BODIES
from forge.freecad_multi.shards import (
    OUT_DIR,
    SELECTION_DIR,
    SLICES,
    TEST_SLICES,
    TRAIN_SLICES,
    complete_shards,
    read_sessions,
    sha256,
)
from forge.freecad_multi.teacher import places_of, teacher
from forge.resolve.judge import KernelJudge
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.system1.splits import unit_hash

CHECKS = ("teacher", "oracle", "replay", "splits", "content", "coverage", "endings")
PROVENANCE = ("origin", "where", "kind", "source", "license", "generator_version")
COPIED = ("id", "source", "split", "lines", "parts", "license", "generator_version")
HELD_OUT_KINDS = ("stool", "shelf", "standoffs")       # data/plans' own `kinds` split
ID_FIRST = re.compile(r'\{"id": ?"([0-9a-f]{16})"')
_judge: KernelJudge | None = None
_client: MultiClient | None = None


def _json(value: object) -> object:
    """A value as it comes back from a JSON file (tuples become lists)."""
    return json.loads(json.dumps(value))


def units_of(plans_file: Path) -> dict[str, tuple[sources.Unit, dict]]:
    """plan id -> (the plan resolved again, its selection row) for one selection file."""
    global _judge
    if _judge is None:
        _judge = KernelJudge()
    units = {}
    for record in sources.plan_records(plans_file):
        try:
            unit = sources.unit_from_record(record, PROJECT_ROOT / record["where"], _judge)
        except (KeyError, ValueError):
            continue
        unit.split = record["s1_split"]
        units[record["id"]] = (unit, record)
    return units


# --- what a session holds, read from its header alone (check 5) ----------------------------------

def _rounded(value: object) -> object:
    if isinstance(value, float):
        return round(value, 3) + 0.0
    if isinstance(value, list):
        return [_rounded(inner) for inner in value]
    if isinstance(value, dict):
        return {key: _rounded(inner) for key, inner in sorted(value.items())}
    return value


def header_content(header: dict) -> tuple[str, set[str]]:
    """(a hash of the bodies the executor is asked for, the cells it sees), from the stored
    plan: shapes, sizes, places, turns and features. No names, no lines."""
    bodies: dict[int, dict] = {}
    for item in header["plan"]:
        body = bodies.setdefault(item["body"], {"features": []})
        if item["kind"] == "part":
            body.update(shape=item["shape"], sizes=item["sizes"], centre=item["centre"],
                        matrix=item["matrix"], drawn=item["drawn"], turn=item["turn"])
        body["features"] += item["features"]
    cells = set()
    for body in bodies.values():
        square = all(min(abs(v), abs(abs(v) - 1.0)) < 1e-9 for row in body["matrix"] for v in row)
        turn = ",".join(f"{angle:g}" for angle in body["turn"]) if square else "odd"
        cells.add(f"st:{body['shape']}|{turn}")
        for feature in body["features"]:
            cells.add(f"fs:{feature['feature']}|{feature['side']}")
            cells.add(f"sf:{body['shape']}|{feature['feature']}")
    listed = sorted(json.dumps(_rounded({key: value for key, value in body.items()
                                         if key != "turn"}), sort_keys=True)
                    for body in bodies.values())
    return hashlib.sha256("\n".join(listed).encode()).hexdigest()[:20], cells


# --- checks 1, 2, 4 and 7: plain Python, every session -------------------------------------------

def audit_session(header: dict, records: list[dict], end: dict, unit: sources.Unit,
                  row: dict) -> dict[str, list[str]]:
    problems: dict[str, list[str]] = {name: [] for name in CHECKS}
    items = structure_items(unit.bodies)            # from the plan's text, not from the header
    if header["plan"] != _json(plan_json(unit.bodies, items)):
        problems["teacher"].append("the stored plan is not the plan in data/plans")
    script, places = script_of(items), places_of(items)

    for record in records:
        t, done = record["t"], record["executed"]
        # Check 1.
        advice = teacher(items, record["snapshot"], script, places)
        if record["target"] != _json([target.to_json() for target in advice.targets]) \
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
    for field in PROVENANCE:
        if header.get(field) != getattr(unit, field):
            problems["splits"].append(f"{field} is {header.get(field)!r}, the plan has "
                                      f"{getattr(unit, field)!r}")
    if not (header["split"] == SLICES[header["slice"]][1] == row["s1_split"]) \
            or header.get("plans_split") != row["split"]:
        problems["splits"].append(f"split {header['split']} / slice {header['slice']}, the "
                                  f"selection has {row['s1_split']}")

    # Check 7 (the recorded side; check 3 confirms a sample of it in FreeCAD).
    last = records[-1] if records else None
    if last is None or last["executed"] != {"command": "done", "args": {}, "noise": None} \
            or [target["command"] for target in last["target"]] != ["done"]:
        problems["endings"].append("the session does not end with the teacher's done")
    elif end["problems"]:
        problems["endings"].append(f"ended on a wrong document: {end['problems'][0]}")
    else:
        final = teacher(items, end["snapshot"], script, places)
        if final.targets or not final.on_plan or not end["snapshot"]["session"]["finished"]:
            problems["endings"].append(f"the final document is not clean: {final.why}")
    return problems


def audit_shard(task: tuple[str, str, float, int]) -> dict:
    """Every check but the replay on one shard, and the candidates for the replay."""
    path, plans_file, fraction, seed = task
    counts: Counter = Counter()
    examples: dict[str, list[str]] = {name: [] for name in CHECKS}
    seen: Counter = Counter()
    content: list[tuple] = []           # (slice, geometry hash, cells, bodies, kind, plan id)
    excluded, candidates, wrong_end = [], [], []
    units = units_of(Path(plans_file))
    for header, records, end in read_sessions(Path(path)):
        name = header["slice"]
        counts["sessions"] += 1
        counts[f"end:{end['end']}"] += 1
        seen[header["session"]] += 1                    # one id per (plan, seed)
        if header["session"] != session_id(header["plan_id"], header["seed"]) \
                or name != Path(path).parent.name:
            counts["bad:splits"] += 1
        if end["end"] != "done":
            excluded.append(f"{name} {header['session']} (plan {header['plan_id']}): ended "
                            f"{end['end']} after {end['steps']} steps")
            continue                    # never training data; the audit fails
        counts["steps"] += len(records)
        counts[f"sessions:{name}"] += 1
        counts[f"steps:{name}"] += len(records)
        if header["plan_id"] not in units:
            counts["bad:splits"] += 1
            examples["splits"].append(f"{header['session']}: its plan does not resolve to its "
                                      "stored parts any more")
            continue
        unit, row = units[header["plan_id"]]
        geometry, cells = header_content(header)
        content.append((name, geometry, sorted(cells), header["parts"], row.get("kind"),
                        header["plan_id"]))
        for record in records:
            for target in record["target"]:
                counts[f"target:{name}:{target['command']}"] += 1
            kind = record["executed"]["noise"]
            if kind:
                counts[f"noise:{kind}"] += 1
        if end["problems"]:             # done, but FreeCAD and the resolver disagree at the end
            wrong_end.append(f"{name} {header['session']} (plan {header['plan_id']}): "
                             f"{end['problems'][0]}")
        for check, found in audit_session(header, records, end, unit, row).items():
            if found and not (check == "endings" and end["problems"]):
                counts[f"bad:{check}"] += 1
                if len(examples[check]) < 5:
                    examples[check].append(f"{header['session']}: {found[0]}")
        point = unit_hash(f"audit:{seed}:{header['session']}")
        if point < fraction and not end["problems"]:
            candidates.append((point, name, header["session"]))
    return {"path": path, "plans_file": plans_file, "counts": counts, "examples": examples,
            "candidates": candidates, "seen": seen, "content": content, "excluded": excluded,
            "wrong_end": wrong_end}


# --- check 4, the other half: the selection's copy of each plan is data/plans' own ----------------

def check_copies(selection_dir: Path, used: list[str]) -> list[str]:
    """Every plan a session was made from, read again from data/plans: the selection's
    copy must hold the same lines, parts and provenance. `used`: the selection files."""
    rows: dict[str, dict[str, dict]] = {}       # data/plans file -> plan id -> selection row
    for file in used:
        for row in sources.plan_records(selection_dir / file):
            rows.setdefault(row["where"], {})[row["id"]] = row
    problems = []
    for where, wanted in sorted(rows.items()):
        found = 0
        with gzip.open(PROJECT_ROOT / where, "rt", encoding="utf-8") as lines:
            for line in lines:
                first = ID_FIRST.match(line)      # fast path: the id is the first field
                if first and first.group(1) not in wanted:
                    continue
                record = json.loads(line)
                row = wanted.get(record["id"])
                if row is None:
                    continue
                found += 1
                if any(row[key] != record[key] for key in COPIED):
                    problems.append(f"plan {record['id']}: the selection's copy differs from "
                                    f"{where}")
        if found != len(wanted):
            problems.append(f"{len(wanted) - found} plans of the selection are not in {where}")
    return problems


# --- check 5 -------------------------------------------------------------------------------------

def check_content(content: list[tuple]) -> tuple[list[str], dict]:
    """Returns (problems, the numbers that prove the splits)."""
    problems: list[str] = []
    splits = ("train", *TEST_SLICES)        # every train slice counts as "train" here
    geometry: dict[str, set[str]] = {name: set() for name in splits}
    cells: dict[str, Counter] = {name: Counter() for name in splits}
    kinds: dict[str, Counter] = {name: Counter() for name in splits}
    bodies: dict[str, list[int]] = {name: [] for name in splits}
    content = [(SLICES[name][1], *rest) for name, *rest in content]
    for name, key, found, count, kind, _ in content:
        geometry[name].add(key)
        cells[name].update(found)
        kinds[name][kind or "random"] += 1
        bodies[name].append(count)
    names = [name for name in ("train", *TEST_SLICES) if geometry[name]]
    shared = {}
    for at, first in enumerate(names):
        for second in names[at + 1:]:
            both = len(geometry[first] & geometry[second])
            shared[f"{first} & {second}"] = both
            if both:
                problems.append(f"{both} sets of parts are in both {first} and {second}")
    held = {}
    for cell in HELD_OUT_CELLS:
        family, pair = cell.split(":")
        first, second = pair.split("|")
        halves = [sum(n for other, n in cells["train"].items()
                      if other.startswith(f"{family}:{first}|") and other != cell),
                  sum(n for other, n in cells["train"].items()
                      if other.startswith(f"{family}:") and other.endswith(f"|{second}")
                      and other != cell)]
        held[cell] = {"train": cells["train"][cell], "iid": cells["iid"][cell],
                      "kinds": cells["kinds"][cell], "combo": cells["combo"][cell],
                      "train_sessions_with_first_half_elsewhere": halves[0],
                      "train_sessions_with_second_half_elsewhere": halves[1]}
        for name in ("train", "iid", "kinds"):
            if cells[name][cell]:
                problems.append(f"the held-out cell {cell} is in {cells[name][cell]} {name} sessions")
        if geometry["train"] and geometry["combo"]:
            if not cells["combo"][cell]:
                problems.append(f"the held-out cell {cell} is in no combo session")
            if not all(halves):
                problems.append(f"a half of {cell} is not in train on its own")
    for name in splits:
        for kind in HELD_OUT_KINDS:
            if name != "kinds" and kinds[name][kind]:
                problems.append(f"{kinds[name][kind]} {kind} sessions are in {name}")
        if not bodies[name]:
            continue
        if name == "long" and min(bodies[name]) <= MAX_BODIES:
            problems.append(f"a long session has only {min(bodies[name])} bodies")
        if name != "long" and max(bodies[name]) > MAX_BODIES:
            problems.append(f"a {name} session has {max(bodies[name])} bodies")
        if name == "combo" and any(not set(found) & set(HELD_OUT_CELLS)
                                   for slice_name, _, found, *_ in content if slice_name == name):
            problems.append("a combo session holds no held-out cell")
    if geometry["kinds"]:
        missing = [kind for kind in HELD_OUT_KINDS if not kinds["kinds"][kind]]
        if missing or set(kinds["kinds"]) - set(HELD_OUT_KINDS):
            problems.append(f"the kinds slice: missing {missing}, others "
                            f"{sorted(set(kinds['kinds']) - set(HELD_OUT_KINDS))}")
    proof = {"sets_of_parts_per_slice": {name: len(geometry[name]) for name in splits},
             "sets_of_parts_shared": shared, "held_out_cells": held,
             "sessions_by_kind": {name: dict(sorted(kinds[name].items())) for name in splits},
             "bodies_min_max": {name: [min(found), max(found)] for name, found in bodies.items()
                                if found},
             "sessions_with_cell": {name: dict(sorted(cells[name].items())) for name in splits}}
    return problems, proof


# --- check 6 -------------------------------------------------------------------------------------

def check_coverage(proof: dict, counts: Counter, selection: dict) -> tuple[list[str], dict]:
    """Every kind and shape the selection's train plans have must have train sessions."""
    problems = []
    train_kinds = proof["sessions_by_kind"]["train"]
    wanted_kinds = sorted({key.split(": ")[1] for key in selection["kinds"]
                           if key.startswith("train: ")})
    by_shape: Counter = Counter()
    for cell, n in proof["sessions_with_cell"]["train"].items():
        if cell.startswith("sf:"):
            continue
        if cell.startswith("st:"):
            by_shape[cell[3:].split("|")[0]] += n
    wanted_shapes = sorted({cell[3:].split("|")[0] for cell in selection["cells_train_iid"]
                            if cell.startswith("st:")})
    features = Counter()
    for cell, n in proof["sessions_with_cell"]["train"].items():
        if cell.startswith("fs:"):
            features[cell[3:].split("|")[0]] += n
    missing_kinds = [kind for kind in wanted_kinds if not train_kinds.get(kind)]
    missing_shapes = [shape for shape in wanted_shapes if not by_shape[shape]]
    if train_kinds:
        if missing_kinds:
            problems.append(f"no train session of the kinds {missing_kinds}")
        if missing_shapes:
            problems.append(f"no train session with the shapes {missing_shapes}")
        for command in ("activate_body", "select_side", "select_side_edges", "move_body",
                        "turn_body", "undo", "clear_selection", "new_document"):
            if not sum(counts[f"target:{name}:{command}"] for name in TRAIN_SLICES):
                problems.append(f"{command} is never a target in train")
    report = {"train_sessions_by_kind": train_kinds,
              "train_sessions_with_shape_and_turn_cells_by_shape": dict(sorted(by_shape.items())),
              "train_sessions_with_feature_and_side_cells_by_feature": dict(sorted(features.items())),
              "targets_by_slice": {name: {key.split(":")[2]: value
                                          for key, value in sorted(counts.items())
                                          if key.startswith(f"target:{name}:")}
                                   for name in SLICES}}
    return problems, report


# --- check 3: play sampled sessions again in FreeCAD ---------------------------------------------

def replay_session(fc: MultiClient, header: dict, records: list[dict], end: dict,
                   unit: sources.Unit) -> list[str]:
    """Play one recorded session again. Returns what differs (nothing, we hope)."""
    start = header["start"]
    reply, carried_out = run_start(fc, [tuple(command) for command in start["commands"]],
                                   start["forget_undo"])
    if _json(carried_out) != start["commands"]:
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
    return end_check(unit, structure_items(unit.bodies), reply["snapshot"])


def replay_shard(task: tuple[str, str, list[str]]) -> dict:
    """Replay the chosen sessions of one shard (runs in a worker process with its own FreeCAD)."""
    global _client
    path, plans_file, wanted = task
    if _client is None:
        _client = MultiClient(exact_undo=True)
        _client.start()
    units = units_of(Path(plans_file))
    bad, steps, by_slice = [], 0, Counter()
    restarts = -_client.restarts
    for header, records, end in read_sessions(Path(path)):
        if header["session"] not in wanted:
            continue
        try:
            found = replay_session(_client, _json(header), records, end,
                                   units[header["plan_id"]][0])
        except Exception as error:      # noqa: BLE001 - a lost worker must not stop the audit
            found = [f"{type(error).__name__}: {error}"]
            restarts += _client.restarts + 1
            _client.close()
            _client = MultiClient(exact_undo=True)
            _client.start()
        steps += len(records)
        by_slice[header["slice"]] += 1
        if found:
            bad.append(f"{header['session']}: {found[0]}")
    return {"bad": bad, "steps": steps, "by_slice": by_slice,
            "restarts": restarts + _client.restarts}


# --- the command -------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", default=str(OUT_DIR))
    parser.add_argument("--selection", default=str(SELECTION_DIR))
    parser.add_argument("--replay-sample", type=int, default=600,
                        help="sessions to play again in FreeCAD, shared between the slices")
    parser.add_argument("--replay-all", action="store_true", help="replay every session")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--replay-workers", type=int, default=None,
                        help="FreeCAD workers for the replay (default: --workers)")
    parser.add_argument("--allow-excluded", action="store_true",
                        help="list sessions that ended with `done` on a document FreeCAD and the "
                             "resolver disagree about, instead of failing (load.py never reads "
                             "them). Sessions that ended budget, stuck or error always fail")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    out_dir, selection_dir = Path(args.dir), Path(args.selection)
    run_dir = start_run("freecad-multi-audit", vars(args))
    started = time.time()
    selection = json.loads((selection_dir / "selection.json").read_text())

    found = complete_shards(out_dir)
    stats = [shard for shard, _ in found]
    plans_file = {str(path): str(selection_dir / shard["plans_file"]) for shard, path in found}
    sessions_in: Counter = Counter()
    for shard in stats:
        sessions_in[shard["slice"]] += shard["counts"].get("end:done", 0)
    live = [name for name in SLICES if sessions_in[name]]
    quota = -(-args.replay_sample // max(len(live), 1))
    fractions = {name: 1.0 if args.replay_all
                 else min(1.0, 1.5 * quota / max(sessions_in[name], 1)) for name in SLICES}

    counts: Counter = Counter()
    examples: dict[str, list[str]] = {name: [] for name in CHECKS}
    seen: Counter = Counter()
    content: list[tuple] = []
    excluded: list[str] = []
    wrong_end: list[str] = []
    candidates: dict[str, list] = {name: [] for name in SLICES}
    where: dict[str, str] = {}
    tasks = [(str(path), plans_file[str(path)], fractions[shard["slice"]], args.seed)
             for shard, path in found]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(audit_shard, tasks):
            counts.update(result["counts"])
            seen.update(result["seen"])
            content += result["content"]
            excluded += result["excluded"]
            wrong_end += result["wrong_end"]
            for name in CHECKS:
                examples[name] += result["examples"][name]
            for point, name, session in result["candidates"]:
                candidates[name].append((point, session))
                where[session] = result["path"]

    # Check 4, across shards.
    twice = [session for session, n in seen.items() if n > 1]
    counts["bad:splits"] += len(twice)
    examples["splits"] += [f"session {session} is there {seen[session]} times"
                           for session in twice[:5]]
    wrong_selection = sorted({shard["selection"] for shard in stats}
                             - {selection["selection_hash"]})
    if wrong_selection:
        counts["bad:splits"] += 1
        examples["splits"].append(f"shards were made from another selection: {wrong_selection}")
    used = sorted({shard["plans_file"] for shard in stats})
    changed_files = [file for file in used if hashlib.sha256(
        (selection_dir / file).read_bytes()).hexdigest() != selection["files"].get(file)]
    copies = check_copies(selection_dir, used)
    if changed_files or copies:
        counts["bad:splits"] += len(changed_files) + len(copies)
        examples["splits"] += [f"selection file changed: {file}" for file in changed_files[:3]]
        examples["splits"] += copies[:5]
    plans_in_train = {plan for name, *_, plan in content if name in TRAIN_SLICES}
    in_both = [plan for name, *_, plan in content
               if name not in TRAIN_SLICES and plan in plans_in_train]
    if in_both:
        counts["bad:splits"] += len(in_both)
        examples["splits"].append(f"{len(in_both)} test plans also have a train session")

    # Checks 5 and 6.
    problems, proof = check_content(content)
    counts["bad:content"] += len(problems)
    examples["content"] += problems
    problems, coverage = check_coverage(proof, counts, selection)
    counts["bad:coverage"] += len(problems)
    examples["coverage"] += problems

    # Check 7, the files.
    recorded = sum(shard["counts"]["sessions"] for shard in stats)
    changed = [shard["file"] for shard in stats if sha256(out_dir / shard["file"]) != shard["sha256"]]
    if changed or recorded != counts["sessions"]:
        counts["bad:endings"] += 1
        examples["endings"].append(f"files differ from their stats: {changed[:3]}, "
                                   f"{recorded} sessions recorded, {counts['sessions']} read")
    counts["bad:endings"] += len(excluded)           # budget, stuck, error: always a failure
    examples["endings"] = excluded[:5] + examples["endings"]
    if wrong_end and not args.allow_excluded:
        counts["bad:endings"] += len(wrong_end)
        examples["endings"] += [f"ended on a wrong document: {text}" for text in wrong_end[:5]]

    # Check 3. A slice with fewer sessions than its share gives what it has to the others.
    chosen: list[str] = []
    for name in SLICES:
        chosen += [session for _, session in sorted(candidates[name])[:quota]]
    spare = sorted((point, session) for name in SLICES
                   for point, session in sorted(candidates[name])[quota:])
    want = counts["end:done"] if args.replay_all else args.replay_sample
    chosen += [session for _, session in spare[:max(0, want - len(chosen))]]
    by_shard: dict[str, list[str]] = {}
    for session in chosen:
        by_shard.setdefault(where[session], []).append(session)
    replayed: Counter = Counter()
    restarts = 0
    with ProcessPoolExecutor(max_workers=args.replay_workers or args.workers) as pool:
        replay_tasks = [(path, plans_file[path], sessions)
                        for path, sessions in sorted(by_shard.items())]
        for result in pool.map(replay_shard, replay_tasks):
            replayed.update(result["by_slice"])
            counts["replayed_steps"] += result["steps"]
            counts["bad:replay"] += len(result["bad"])
            examples["replay"] += result["bad"]
            restarts += result["restarts"]
    counts["replayed"] = sum(replayed.values())
    if counts["replayed"] < min(want, counts["end:done"]):
        counts["bad:replay"] += 1
        examples["replay"].append(f"only {counts['replayed']} sessions were replayed")

    unfinished = {key[4:]: value for key, value in counts.items()
                  if key.startswith("end:") and key != "end:done"}
    per_slice = {name: [counts[f"sessions:{name}"], counts[f"steps:{name}"]] for name in SLICES}
    print(f"audited {counts['sessions']} sessions ({counts['end:done']} finished, "
          f"{counts['steps']} steps) in {len(found)} shards ({time.time() - started:.0f}s)")
    print(f"sessions and steps per slice: {per_slice}")
    print(f"sessions that did not end with done: {unfinished or 0}")
    print(f"sessions that ended with done on a document FreeCAD and the resolver disagree "
          f"about: {len(wrong_end)}" + ("  (excluded: --allow-excluded; load.py does not read "
                                        "them)" if wrong_end and args.allow_excluded else ""))
    for text in wrong_end[:10]:
        print(f"        {text}")
    held = proof["held_out_cells"]
    wording = {
        "teacher": "every target is the teacher's answer for its (plan, snapshot), is valid, "
                   "and was carried out when chosen; no noise command is a target",
        "oracle": "every on-plan flag and target set is what the session's final document "
                  "implies (oracle.py, no teacher or recipe code)",
        "replay": f"{counts['replayed']} sessions ({counts['replayed_steps']} steps) replayed in "
                  f"FreeCAD reproduce every snapshot and end at the resolver's structure "
                  f"{dict(replayed)}",
        "splits": "every plan is data/plans' own, resolves to its stored parts, and is in the "
                  "slice the selection gave it; one session per (plan, seed)",
        "content": f"no set of parts shared between slices {proof['sets_of_parts_shared']}; "
                   f"held-out cells in train: {sum(cell['train'] for cell in held.values())}, "
                   f"in combo: {sum(cell['combo'] for cell in held.values())}; kinds and body "
                   f"counts as defined {proof['bodies_min_max']}",
        "coverage": f"every train kind ({len(coverage['train_sessions_by_kind'])}) and shape "
                    f"({len(coverage['train_sessions_with_shape_and_turn_cells_by_shape'])}) "
                    "has train sessions; side, body and placing commands are targets",
        "endings": "every session ends with done on a correct, clean document; no budget, "
                   "stuck or error ending; the files are the ones their stats describe"
                   + (f"; {len(wrong_end)} sessions with a disagreement at the end are excluded"
                      if wrong_end and args.allow_excluded else ""),
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
    report = {"passed": not failed, "failed_checks": failed,
              "counts": {key: value for key, value in counts.items()
                         if not key.startswith("target:")},
              "sessions_and_steps_per_slice": per_slice,
              "replayed_by_slice": dict(replayed), "unfinished": unfinished,
              "excluded": excluded, "ended_on_a_disputed_document": wrong_end,
              "allow_excluded": args.allow_excluded, "split_proof": proof, "coverage": coverage,
              "examples": {name: listed[:20] for name, listed in examples.items()}}
    (out_dir / "audit.json").write_text(json.dumps(report, indent=1) + "\n")
    log_metrics(run_dir, final=True, passed=not failed,
                **{key: value for key, value in counts.items() if not key.startswith("target:")})
    print("AUDIT PASSED" if not failed else f"AUDIT FAILED: {', '.join(failed)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
