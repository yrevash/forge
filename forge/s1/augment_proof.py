"""Prove in real FreeCAD that counterfactual labels (forge/s1/augment.py) are right.

    uv run python -m forge.s1.augment_proof --sessions 400 --workers 5

augment.py relabels a recorded state for a plan with two features swapped, without FreeCAD.
That is only honest if the teacher's answer for the swapped plan really leads to the part.
So, for a random sample of TRAINING sessions, one state each:

    1. the recorded session is played again in a fresh FreeCAD up to that state, and the
       snapshot must be the recorded one (as in the audit's replay);
    2. the plan is replaced by the swapped plan, the one augment.py would write;
    3. the first command is the counterfactual LABEL (the first of the teacher's set), and
       from there the teacher of the swapped plan drives to `done`;
    4. the document must then be the stored solid of the part, complete and clean under the
       swapped plan (play.end_check). The solid is the same because the generator's
       features never overlap; this check is what shows it.

It also counts how often the teacher's answer for the swapped plan differs from the
recorded one, and what it is (mostly `undo`: a feature stands where another belongs).

Only training slices are read. Nothing is written but the run folder.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import random
from collections import Counter

from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean, same_lean
from forge.freecad.load import usable
from forge.freecad.parts import session_parts
from forge.freecad.play import HARD_EXTRA, HARD_FACTOR, end_check, run_start
from forge.freecad.shards import OUT_DIR, SLICES, TRAIN_SPLITS, complete_shards, read_sessions
from forge.freecad.teacher import clean_length, plan_from_json, script_of, teacher
from forge.runs import log_metrics, start_run
from forge.s1.augment import relabel, swap_two_features
from forge.system1.splits import unit_hash

_client: FreeCADClient | None = None
_parts: dict[str, dict] = {}


def _start(parts: dict[str, dict]) -> None:
    global _client
    _parts.update(parts)
    _client = FreeCADClient(exact_undo=True)
    _client.start()


def prove(fc: FreeCADClient, header: dict, records: list[dict], seed: int) -> dict:
    rng = random.Random(f"cf-proof:{seed}:{header['session']}")
    out = {"session": header["session"], "plan_items": len(header["plan"])}
    plan = swap_two_features(header["plan"], rng)
    if plan is None:
        return {**out, "result": "no two features"}
    t = rng.randrange(len(records))
    # 1. Back to the recorded state.
    start = header["start"]
    reply, _ = run_start(fc, [tuple(command) for command in start["commands"]],
                         start["forget_undo"])
    for record in records[:t]:
        if record.get("before") == "forget_undo":
            reply = fc.forget_undo()
        reply = fc.command(record["executed"]["command"], **record["executed"]["args"])
    if records[t].get("before") == "forget_undo":
        reply = fc.forget_undo()
    if not same_lean(lean(reply["snapshot"]), records[t]["snapshot"]):
        return {**out, "result": "replay differs"}
    # 2 and 3. The swapped plan; its label first, then its teacher.
    advice = relabel(plan, records[t]["snapshot"], records[t]["valid"])
    if advice is None:
        return {**out, "result": "no answer"}
    steps = plan_from_json(plan)
    script = script_of(steps)
    out.update(t=t, on_plan=advice.on_plan, label=sorted(advice.names()),
               changed=set(advice.names()) != {x["command"] for x in records[t]["target"]})
    said_done = False
    for _ in range(HARD_FACTOR * clean_length(steps) + HARD_EXTRA):
        advice = teacher(steps, lean(reply["snapshot"]), script)
        if not advice.targets:
            break
        target = advice.targets[0]
        reply = fc.command(target.command, **target.args)
        if reply["status"] != "ok":
            return {**out, "result": f"refused: {target.command}"}
        if target.command == "done":
            said_done = True
            break
    # 4. The stored solid of the part, under the swapped plan.
    problems = end_check(steps, reply["snapshot"], _parts[header["part_id"]]["measured"])
    if not said_done:
        problems.insert(0, "the teacher did not reach `done`")
    return {**out, "result": "ok" if not problems else "wrong end", "problems": problems[:2]}


def prove_shard(task: tuple[str, list[str], int]) -> list[dict]:
    global _client
    path, wanted, seed = task
    found = []
    for header, records, end in read_sessions(path):
        if header["session"] not in wanted or not usable(end):
            continue
        try:
            found.append({**prove(_client, header, records, seed), "slice": header["slice"]})
        except Exception as error:      # noqa: BLE001 - a lost worker must not stop the proof
            found.append({"session": header["session"], "slice": header["slice"],
                          "result": f"harness: {type(error).__name__}: {error}"})
            _client.close()
            _client = FreeCADClient(exact_undo=True)
            _client.start()
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--slices", nargs="+", default=["train_v2", "train_long_v2"])
    parser.add_argument("--sessions", type=int, default=400, help="per slice")
    parser.add_argument("--shards", type=int, default=5, help="read the first so many shards")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    for name in args.slices:
        if SLICES[name][0] not in TRAIN_SPLITS:
            raise SystemExit(f"{name} is not a training slice")
    run_dir = start_run("s1-augment-proof", vars(args))
    tasks = []
    for name in args.slices:
        shards = complete_shards(OUT_DIR, (name,))[:args.shards]
        sessions = sum(stats["counts"]["sessions"] for stats, _ in shards)
        share = min(1.0, 1.1 * args.sessions / max(1, sessions))
        left = args.sessions
        for _, path in shards:
            wanted = [header["session"] for header, _, end in read_sessions(path)
                      if usable(end)
                      and unit_hash(f"cf-proof:{args.seed}:{header['session']}") < share][:left]
            left -= len(wanted)
            tasks.append((path, wanted, args.seed))
    parts = {part["id"]: part for part in session_parts(added=True)}
    results: list[dict] = []
    with multiprocessing.Pool(args.workers, initializer=_start, initargs=(parts,)) as pool, \
            (run_dir / "states.jsonl").open("w") as f:
        for found in pool.imap_unordered(prove_shard, tasks):
            results += found
            f.write("".join(json.dumps(result) + "\n" for result in found))
    for name in args.slices:
        lot = [r for r in results if r["slice"] == name]
        tried = [r for r in lot if "label" in r]
        changed = [r for r in tried if r["changed"]]
        row = {"slice": name, "sessions": len(lot),
               "results": dict(Counter(r["result"] for r in lot)),
               "states_relabelled": len(tried), "label_changed": len(changed),
               "ok_when_changed": sum(r["result"] == "ok" for r in changed),
               "ok_when_unchanged": sum(r["result"] == "ok" for r in tried if not r["changed"]),
               "changed_labels": dict(Counter(",".join(r["label"]) for r in changed))}
        log_metrics(run_dir, **row)
        print(json.dumps(row), flush=True)
    bad = [r for r in results if r["result"] not in ("ok", "no two features", "no answer")]
    for result in bad[:10]:
        print("  NOT OK:", json.dumps(result))
    print(f"{'PROOF PASSED' if not bad else 'PROOF FAILED'}: {len(results)} sessions, "
          f"{len(bad)} not ok. run folder: {run_dir}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
