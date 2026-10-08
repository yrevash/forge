"""Record FreeCAD sessions for Forge-S1: one session per part, in real FreeCAD.

For every verified composed part: its plan
(`forge.system1.steps.steps_of`), one session played in a headless FreeCAD
(play.py), one record per step. Only the teacher's set is ever a target.

Run:    uv run python -m forge.freecad.sessions [--workers 3] [--max-minutes 300]
        (the same command again carries on where the last run stopped)
Trial:  uv run python -m forge.freecad.sessions --limit-per-slice 250 --out data/freecad/trial_v2
Output: data/freecad/sessions/<slice>/shardNNN.jsonl.gz   the sessions
        data/freecad/sessions/<slice>/shardNNN.stats.json that shard's counts and the hash of
                                                          the code that made it (it marks the
                                                          shard as complete)
        data/freecad/sessions/manifest.json               counts over all complete shards,
                                                          rewritten after every shard (manifest.py)

Splits are `forge.system1.splits`, unchanged. A SLICE is one pass over one split
with one seed (see SLICES). Inside a split the parts are in a fixed shuffled
order, so every shard mixes the four bases.

Two generations of slices
    first mix   iid, pairing, long, train, train_heavy, train_s1, train_s2 (5-6 Oct 2026).
                FROZEN: they were recorded with the first noise mix and the runtime
                before exact undo, and this file refuses to record them again.
    second mix  train_v2, iid_v2, pairing_v2, long_v2 (noise.py MIX "v2", exact undo).
                One session per part, a new seed. This is what the command records.
                Complete and read-only since 6 Oct 2026: the command finds nothing to do.
    added       train_long_v2, xlong_v2 (7 Oct 2026): the same mix and runtime on
                new long parts (forge/freecad/long_train_parts.py), in splits of their own.

File format: one JSON object per line. A session is
    HEADER  {"session", "part_id", "family", "split", "slice", "seed", "noise_level", "clean_length",
             "start": {"kind", "commands", "forget_undo"}, "plan": [{"kind", "slots"}],
             "source", "license", "generator_version",
             "code_hash", "mix", "runtime": {"exact_undo"}}          (last three: second mix only)
    STEP    {"session", "t", "snapshot", "valid", "target": [{"command", "args", "item",
             "sources"}], "progress": {"on_plan", "built", "active"},
             "executed": {"command", "args", "noise", "flavour"?}, "reply": {"status", "reason"},
             "before"?: "forget_undo"}
    END     {"session", "end": "done" | "budget" | "stuck" | "error", "steps", "snapshot", "problems"}
`snapshot` is the LEAN snapshot (lean.py) of the session BEFORE the step's command;
the END record holds the one after the last command. `target` is the teacher's
whole set. `executed.noise` is None, or the kind of wrong command that was run;
`executed.flavour` says which kind of wrong number a `wrong_argument` was.
`before: "forget_undo"` marks a step before which the undo history was lost
(play.py, "reopen").

A model must never be handed these records as they are: `progress`, `executed`,
`reply` and half the header say what the right answer is or how the session was
made. Read them through load.py.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from forge.freecad import noise, starts
from forge.freecad import play as player
from forge.freecad.client import FreeCADClient
from forge.freecad.manifest import grouped, summary, write_manifest
from forge.freecad.parts import session_parts
from forge.freecad.play import HARD_EXTRA, HARD_FACTOR, NOISE_FACTOR, play, session_id
from forge.freecad.shards import (
    FROZEN,
    OUT_DIR,
    PARTS_PER_SHARD,
    RECORDED_NOW,
    SLICES,
    read_sessions,
    sha256,
)
from forge.freecad.shards import changed as step_changed
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.system1.splits import SPLITS, split_of, unit_hash

# Names other files import from here (forge.freecad_multi reads grouped, read_sessions, sha256).
__all__ = ["FROZEN", "OUT_DIR", "PARTS_PER_SHARD", "RECORDED_NOW", "SLICES", "grouped",
           "read_sessions", "sha256", "summary", "write_manifest"]

TRIES = 3           # how often one session is attempted when FreeCAD itself fails
# The files that decide what a session contains. Tools that only read sessions (audit.py,
# load.py, baselines.py, manifest.py, show_session.py) are left out on purpose, so that
# working on them does not change the hash of the data.
CODE_FILES = ("catalogue.py", "valid.py", "recipes.py", "teacher.py", "noise.py", "starts.py",
              "play.py", "lean.py", "client.py", "build.py", "parts.py", "sessions.py", "shards.py",
              "inside/worker.py", "inside/session.py", "inside/shapes.py", "inside/features.py",
              "inside/rules.py", "inside/snapshot.py",
              "../system1/steps.py", "../system1/splits.py", "../system1/engine.py")

_client: FreeCADClient | None = None    # one FreeCAD worker per process


# --- one shard (runs in a worker process) ------------------------------------------------------

def _fresh_client() -> FreeCADClient:
    global _client
    if _client is not None:
        _client.close()
    _client = FreeCADClient(exact_undo=True)
    _client.start()
    return _client


def _session(part: dict, slice_name: str, stats: Counter, code: str,
             ) -> tuple[dict, list[dict], dict]:
    """Play one session; if FreeCAD fails underneath it, start a new worker and play it again."""
    split, seed, noise_levels, _ = SLICES[slice_name]
    for _ in range(TRIES):
        try:
            fc = _client or _fresh_client()
            before = fc.restarts
            result = play(fc, part, seed, split, noise_levels, slice_name, code)
            stats["worker_restarts"] += fc.restarts - before
            return result
        except Exception as error:      # noqa: BLE001 - a lost worker, a broken pipe, bad JSON
            stats["sessions_played_again"] += 1
            stats["worker_restarts"] += 1
            last = f"{type(error).__name__}: {error}"
            _fresh_client()
    sid = session_id(part["id"], seed)
    header = {"session": sid, "part_id": part["id"], "family": part["family"], "split": split,
              "slice": slice_name, "seed": seed, "noise_level": None, "clean_length": 0,
              "start": {"kind": "error", "commands": [], "forget_undo": False}, "plan": [],
              "source": part["source"], "license": part["license"],
              "generator_version": part["generator_version"],
              "code_hash": code, "mix": noise.MIX, "runtime": {"exact_undo": True}}
    return header, [], {"session": sid, "end": "error", "steps": 0, "snapshot": None,
                        "problems": [last]}


def count_session(stats: Counter, header: dict, records: list[dict], end: dict) -> None:
    stats["sessions"] += 1
    stats[f"end:{end['end']}"] += 1
    if end["end"] != "done":
        return                          # a session that did not finish is not training data
    stats["steps"] += len(records)
    stats["sessions_with_problems"] += bool(end["problems"])
    stats[f"noise_level:{header['noise_level']}"] += 1
    stats[f"steps_at_noise:{header['noise_level']}"] += len(records)
    stats[f"start:{header['start']['kind']}"] += 1
    stats[f"plan_items:{len(header['plan'])}"] += 1
    stats["noise_cut_off"] += len(records) > NOISE_FACTOR * header["clean_length"]
    after_wrong_number = False
    for record, after in zip(records, [*records[1:], end], strict=True):
        names = [target["command"] for target in record["target"]]
        on_plan = record["progress"]["on_plan"]
        stats[f"choices:{len(names)}"] += 1
        stats["off_plan_steps"] += not on_plan
        for name in names:
            stats[f"target:{name}"] += 1
        # The rare states worth counting (see noise.py).
        stats["steps_with_a_failed_feature"] += any(not item["valid"]
                                                    for item in record["snapshot"]["items"])
        stats["steps_finished_too_early"] += record["snapshot"]["session"]["finished"]
        stats["repairs_by_new_document"] += not on_plan and names == ["new_document"]
        stats["steps_right_after_a_wrong_number"] += after_wrong_number
        stats["undo_history_lost"] += record.get("before") == "forget_undo"
        done = record["executed"]
        status = record["reply"]["status"]
        changed = step_changed(record, after)
        after_wrong_number = done["noise"] == "wrong_argument" and status == "ok" and changed
        if done["noise"]:
            stats["noisy_steps"] += 1
            stats[f"noise:{done['noise']}:{status}:{'changed' if changed else 'no change'}"] += 1
            stats[f"noise_kind:{done['noise']}"] += 1
            if "flavour" in done:
                stats[f"wrong_number:{done['flavour']}"] += 1
        elif status != "ok":
            stats["teacher_commands_refused"] += 1


def write_shard(task: tuple[str, int, list[dict], str]) -> dict:
    """Play every part of one shard and write its file. Returns the shard's counts."""
    name, number, parts, out_dir = task
    if name in FROZEN:
        raise ValueError(f"{name} is frozen: it was recorded with the first noise mix")
    path = Path(out_dir) / name / f"shard{number:03d}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    stats: Counter = Counter()
    started = time.perf_counter()
    code = code_hash()
    raw_bytes = 0
    # mtime=0 keeps a timestamp out of the file, so the same run gives the same bytes.
    with partial.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                                  mtime=0, compresslevel=6) as f:
        for part in parts:
            header, records, end = _session(part, name, stats, code)
            count_session(stats, header, records, end)
            text = "".join(json.dumps(line, separators=(",", ":")) + "\n"
                           for line in (header, *records, end)).encode("utf-8")
            raw_bytes += len(text)
            f.write(text)
    if code_hash() != code:             # somebody edited the recorder while it was running
        partial.unlink()
        raise RuntimeError(f"{name}/shard{number:03d}: the code changed while it was recorded "
                           f"({code} -> {code_hash()}); the shard was thrown away")
    os.replace(partial, path)           # the shard appears only when it is whole
    stats.update(parts=len(parts), raw_bytes=raw_bytes, gz_bytes=path.stat().st_size)
    result = {"slice": name, "split": SLICES[name][0], "file": f"{name}/{path.name}",
              "sha256": sha256(path), "code_hash": code, "mix": noise.MIX,
              "seconds": round(time.perf_counter() - started, 2), "counts": dict(stats)}
    path.with_name(f"shard{number:03d}.stats.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


# --- the code that made a shard -------------------------------------------------------------------

def code_hash() -> str:
    """A hash of the code that decides what a session contains (CODE_FILES)."""
    digest = hashlib.sha256()
    here = Path(__file__).parent
    for name in CODE_FILES:
        digest.update(name.encode())
        digest.update((here / name).read_bytes())
    return digest.hexdigest()[:12]


def legacy_code_hash() -> str:
    """The hash as the first recording computed it: every .py file of this folder, tools
    included. It was 1c27ac87bd63 until 6 Oct 2026 09:10, when the second mix was begun;
    kept so that the old value can be explained (see manifest.py, `code_versions`)."""
    digest = hashlib.sha256()
    here = Path(__file__).parent
    for path in [*sorted(here.glob("*.py")), *sorted((here / "inside").glob("*.py")),
                 PROJECT_ROOT / "forge/system1/steps.py", PROJECT_ROOT / "forge/system1/splits.py"]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


# --- the command -------------------------------------------------------------------------------

def plan_shards(out_dir: Path, limit_per_slice: int | None = None) -> list[tuple]:
    """Every shard of every slice, in the order of SLICES. Always the same shards."""
    by_split: dict[str, list[dict]] = {split: [] for split in SPLITS}
    for part in session_parts(added=True):
        by_split[split_of(part)].append(part)
    for parts in by_split.values():     # a fixed shuffle: the files are sorted base by base
        parts.sort(key=lambda part: unit_hash(f"shard-order:{part['id']}"))
    tasks = []
    for name, (split, _, _, at_most) in SLICES.items():
        parts, size = by_split[split][:at_most][:limit_per_slice], PARTS_PER_SHARD[split]
        tasks += [(name, number, parts[at:at + size], str(out_dir))
                  for number, at in enumerate(range(0, len(parts), size))]
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-minutes", type=float, default=None,
                        help="start no new shard after this many minutes (run again to go on)")
    parser.add_argument("--limit-per-slice", type=int, default=None,
                        help="only the first N parts of each slice (for a trial)")
    parser.add_argument("--slices", nargs="*", default=list(RECORDED_NOW),
                        choices=list(RECORDED_NOW))
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = {"limit_per_slice": args.limit_per_slice,
              "slices": {name: list(value) for name, value in SLICES.items()},
              "mix": noise.MIX, "noise_kinds": noise.NOISE_KINDS,
              "wrong_number_flavours": noise.FLAVOURS,
              "chance_before_done": noise.CHANCE_BEFORE_DONE,
              "chance_at_treatment": noise.CHANCE_AT_TREATMENT,
              "carry_on": [player.CARRY_ON_START, player.CARRY_ON_NEXT], "reopen": player.REOPEN,
              "start_kinds": starts.START_KINDS, "noise_factor": NOISE_FACTOR,
              "hard_limit": f"{HARD_FACTOR} x clean length + {HARD_EXTRA}",
              "parts_per_shard": PARTS_PER_SHARD, "exact_undo": True,
              "code_hash": code_hash()}
    run_dir = start_run("freecad-sessions", {**config, **vars(args)})

    tasks = [task for task in plan_shards(out_dir, args.limit_per_slice)
             if task[0] in args.slices]
    planned = Counter(task[0] for task in tasks)
    todo = [task for task in tasks
            if not (out_dir / task[0] / f"shard{task[1]:03d}.stats.json").exists()]
    print(f"{len(tasks)} shards planned {dict(planned)}, {len(tasks) - len(todo)} already "
          f"complete, {len(todo)} to do", flush=True)

    started = time.time()
    done_now: Counter = Counter()
    out_of_time = False
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        waiting = iter(todo)
        running = set()
        while True:
            out_of_time = args.max_minutes is not None \
                and time.time() - started > args.max_minutes * 60
            while len(running) < args.workers and not out_of_time:
                task = next(waiting, None)
                if task is None:
                    break
                running.add(pool.submit(write_shard, task))
            if not running:
                break
            finished, running = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                shard = future.result()     # raises if the code changed under the run
                done_now.update(shard["counts"])
                done_now["shards"] += 1
                elapsed = time.time() - started
                print(f"[{elapsed / 60:6.1f} min] {shard['file']}  "
                      f"{shard['counts']['sessions']} sessions {shard['counts'].get('steps', 0)} "
                      f"steps in {shard['seconds']:.0f}s | this run: {done_now['sessions']} "
                      f"sessions, {done_now['sessions'] / elapsed:.1f}/s, "
                      f"{done_now['steps'] / elapsed:.0f} steps/s", flush=True)
                log_metrics(run_dir, shard=shard["file"], seconds=shard["seconds"],
                            sessions=shard["counts"]["sessions"],
                            steps=shard["counts"].get("steps", 0))
            if finished:
                write_manifest(out_dir, config, dict(planned))  # whatever exists is usable
    elapsed = time.time() - started

    manifest = write_manifest(out_dir, config, dict(planned))
    left = sum(1 for task in tasks
               if not (out_dir / task[0] / f"shard{task[1]:03d}.stats.json").exists())
    speed = {"wall_seconds": round(elapsed, 1), "workers": args.workers,
             "sessions_this_run": done_now["sessions"], "steps_this_run": done_now["steps"],
             "sessions_per_second": round(done_now["sessions"] / max(elapsed, 1e-9), 2),
             "steps_per_second": round(done_now["steps"] / max(elapsed, 1e-9), 1),
             "shards_left": left}
    log_metrics(run_dir, final=True, **speed, **{f"{name}_{key}": manifest["slices"][name][key]
                                                 for name in manifest["slices"]
                                                 for key in ("sessions", "steps", "shards")})
    totals = manifest["totals"]
    (run_dir / "NOTES.md").write_text(
        f"# freecad-sessions\n\nFreeCAD command sessions for Forge-S1 (forge/freecad/sessions.py).\n"
        f"This run: {done_now['sessions']} sessions, {done_now['steps']} steps in "
        f"{elapsed / 60:.1f} min with {args.workers} workers. Shards left: {left}.\n"
        f"On disk now: {totals['sessions']} sessions, {totals['steps']} steps.\n"
        f"Counts and file hashes: {out_dir / 'manifest.json'}.\n"
        f"Check with: uv run python -m forge.freecad.audit --dir {out_dir}\n")
    print(f"\nthis run: {done_now['sessions']} sessions, {done_now['steps']} steps in "
          f"{elapsed:.0f}s ({speed['sessions_per_second']} sessions/s, "
          f"{speed['steps_per_second']} steps/s, {args.workers} workers)")
    print(f"on disk:  {totals['sessions']} sessions, {totals['steps']} steps, "
          f"{totals['gz_bytes'] / 1e6:.1f} MB gzip ({totals['gz_bytes_per_step']} bytes/step; "
          f"{totals['raw_bytes_per_step']} before gzip)")
    for split in manifest["slices"]:
        info = manifest["slices"][split]
        print(f"  {split:11s} {info['shards']:3d}/{info['shards_planned']:3d} shards "
              f"{info['sessions']:7d} sessions {info['steps']:9d} steps  ended "
              f"{info['sessions_ended']}  noise {info['sessions_by_noise_level']}")
    print(f"undo share of targets: {totals['undo_share']}; sessions cut off by the hard limit: "
          f"{totals['sessions_ended'].get('budget', 0)}, stuck: "
          f"{totals['sessions_ended'].get('stuck', 0)}; worker restarts: "
          f"{totals['worker_restarts']}")
    print(f"shards left: {left}" + ("  (out of time: run the same command again)"
                                    if left else ""))
    print(f"manifest: {out_dir / 'manifest.json'}\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
