"""DAgger: let the model drive TRAINING parts, and label every state it reaches.

    uv run python -m forge.s1.third.dagger --checkpoint checkpoints/s1/s1-third-r0-best.pt \
        --round 1 --workers 6

WHY. A recorded session shows the states the RECORDER's mistakes
lead to. A model that drives alone reaches other states: the ones its own wrong choices
lead to, and it has never been shown what to do there. DAgger (Ross, Gordon and Bagnell,
2011) closes that gap in the simplest way there is:

    1. the current model drives FreeCAD on parts of the TRAINING sets (teacher-free: its
       own commands and its own arguments, exactly as in the closed-loop test);
    2. at every state it visits, the scripted teacher is asked what is right THERE. The
       teacher is a function of (plan, state) and nothing else, so its label is exact
       whatever road led to the state;
    3. those (state, label) pairs are added to the training data and the model is trained
       again. Then the next round, with the new model.

EXPLORATION. A good model makes few mistakes, so plain rollouts add few new states. On a
share `--explore` of the early steps the command, the plan item and the argument kinds are
DRAWN from the model's own probabilities at a temperature: the mistakes it is most inclined
to make, including wrong bindings (a real wrong number of its own, carried out).

WHICH PARTS. Parts of train_v2, train_long_v2 and train_wide_v3 only, taken from a few
recorded shards chosen by the round number, never from the held-out TRAIN shards (those
pick the checkpoint) and never from a test slice (refused below; tested). Every second part
starts messy with injected mistakes, as in the closed-loop test's second condition.

OUTPUT. data/s1/dagger/dagger_r<round>/shardNNN.jsonl.gz in the recorder's session format
(header, step records, end record), so load.py, prepare.py and the leak checks read it as
they read any slice. A session is kept whether or not the model finished the part: the
labels are right either way.
"""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing
import time
from pathlib import Path

from forge.freecad import wide_sessions
from forge.freecad.client import FreeCADClient
from forge.freecad.shards import OUT_DIR, complete_shards, read_sessions, sha256
from forge.freecad.teacher import plan_json
from forge.runs import log_metrics, start_run
from forge.s1.third import drive
from forge.s1.third.prepare import DAGGER_DIR

# slice -> (held-out shard numbers: never used here, shards taken per round)
SOURCES = {"train_v2": ((193, 194, 195), 3), "train_long_v2": ((53, 54, 55), 2),
           "train_wide_v3": ((143, 144, 145), 5)}
SESSIONS_PER_SHARD = 400


def refuse_tests(names: list[str]) -> None:
    """DAgger states become training data: only training slices may be rolled out on."""
    for name in names:
        if name not in SOURCES or not drive.is_training_slice(name):
            raise SystemExit(f"{name}: DAgger rolls out on {sorted(SOURCES)} only")


def shards_of_round(name: str, round_number: int) -> list[int]:
    """Which recorded shards a round takes its parts from: a block of its own per round,
    never a held-out shard, never beyond the audited wide shards."""
    held_out, count = SOURCES[name]
    first = (round_number - 1) * count
    chosen = list(range(first, first + count))
    if set(chosen) & set(held_out) or chosen[-1] >= min(held_out):
        raise SystemExit(f"round {round_number} would reach the held-out shards of {name}")
    return chosen


def part_ids(name: str, numbers: list[int]) -> list[str]:
    folder = wide_sessions.OUT_DIR if drive.is_wide(name) else OUT_DIR
    wanted = {f"shard{number:03d}.jsonl.gz" for number in numbers}
    return list(dict.fromkeys(
        header["part_id"] for _, path in complete_shards(folder, (name,)) if path.name in wanted
        for header, _, _ in read_sessions(path)))


def _run(task: dict) -> dict:
    """One recorded rollout in this worker's FreeCAD: (header, records, end, summary)."""
    part, records = task["part"], []
    try:
        result = drive.episode(drive._client, drive._model, True, part, task["seed"],
                               task["mistakes"], "model", task["slice"], task["other"],
                               explore=task["explore"], record=records)
    except Exception as error:      # noqa: BLE001 - FreeCAD lost: this rollout is dropped
        drive._client.close()
        drive._client = FreeCADClient(exact_undo=True)
        drive._client.start()
        return {"dropped": f"{type(error).__name__}: {error}"}
    sid = f"dg{task['round']}-{part['id']}"[:16].ljust(16, "0")
    header = {"session": sid, "part_id": part["id"], "slice": task["out"],
              "from_slice": task["slice"], "seed": task["seed"], "mistakes": task["mistakes"],
              "explore": task["explore"], "checkpoint": task["checkpoint"],
              "plan": plan_json(drive.plan_of(part)),
              "source": "dagger rollout of " + str(part.get("source")),
              "license": part.get("license"), "generator_version": part.get("generator_version")}
    end = {"session": sid, "end": "done" if result["said_done"] else "budget",
           "steps": len(records), "snapshot": result.pop("end_snapshot"),
           "problems": result["problems"]}
    return {"header": header, "records": [{"session": sid, **record} for record in records],
            "end": end, "summary": {**result, "slice": task["slice"]}}


def write_shard(folder: Path, number: int, name: str, sessions: list[dict]) -> None:
    path = folder / f"shard{number:03d}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for session in sessions:
            for line in (session["header"], *session["records"], session["end"]):
                f.write(json.dumps(line) + "\n")
    stats = {"slice": name, "file": f"{name}/{path.name}", "sha256": sha256(path),
             "sessions": len(sessions), "steps": sum(len(s["records"]) for s in sessions)}
    path.with_name(path.name.replace(".jsonl.gz", ".stats.json")).write_text(json.dumps(stats))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--slices", nargs="+", default=list(SOURCES))
    parser.add_argument("--explore", type=float, default=0.05)
    parser.add_argument("--mistakes", type=float, default=0.1, help="for every second part")
    parser.add_argument("--max-parts", type=int, default=None, help="per slice (a trial)")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    refuse_tests(args.slices)
    name = f"dagger_r{args.round}"
    folder = DAGGER_DIR / name
    if folder.exists() and any(folder.iterdir()):
        raise SystemExit(f"{folder} exists: a round is recorded once")
    folder.mkdir(parents=True, exist_ok=True)
    run_dir = start_run(f"s1-{name}", vars(args))
    tasks = []
    for slice_name in args.slices:
        by_id = drive.parts_by_id(slice_name)
        ids = [i for i in part_ids(slice_name, shards_of_round(slice_name, args.round))
               if i in by_id][:args.max_parts]
        tasks += [{"part": by_id[i], "other": by_id[ids[(place + 1) % len(ids)]],
                   "seed": 1000 + args.round, "mistakes": args.mistakes if place % 2 else 0.0,
                   "slice": slice_name, "explore": args.explore, "round": args.round,
                   "out": name, "checkpoint": Path(args.checkpoint).name}
                  for place, i in enumerate(ids)]
    print(f"{len(tasks)} rollouts", flush=True)
    sessions, summaries, shard, dropped, started = [], [], 0, 0, time.time()
    with multiprocessing.Pool(args.workers, initializer=drive._start_worker,
                              initargs=(args.checkpoint,)) as pool:
        for count, made in enumerate(pool.imap_unordered(_run, tasks), start=1):
            if "dropped" in made:
                dropped += 1
                continue
            summaries.append(made.pop("summary"))
            sessions.append(made)
            if len(sessions) == SESSIONS_PER_SHARD:
                write_shard(folder, shard, name, sessions)
                sessions, shard = [], shard + 1
            if count % 200 == 0:
                print(f"{count}/{len(tasks)} rollouts, {time.time() - started:.0f} s", flush=True)
    if sessions:
        write_shard(folder, shard, name, sessions)
    with (run_dir / "episodes.jsonl").open("w") as f:
        for summary in summaries:
            f.write(json.dumps(summary) + "\n")
    for slice_name in args.slices:
        lot = [s for s in summaries if s["slice"] == slice_name]
        erred = [s for s in lot if s["model_mistakes"] > 0]
        log_metrics(run_dir, slice=slice_name, rollouts=len(lot),
                    states=sum(s["steps"] for s in lot), built_right=sum(s["success"] for s in lot),
                    with_own_mistake=len(erred),
                    built_right_after_own_mistake=sum(s["success"] for s in erred),
                    explored_steps=sum(s["explored"] for s in lot))
    print(f"{len(summaries)} rollouts kept, {dropped} dropped, in {folder}; run folder {run_dir}")


if __name__ == "__main__":
    main()
