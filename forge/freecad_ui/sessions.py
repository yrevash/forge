"""Record interface sessions for Forge-S1: one session per part, through FreeCAD's real interface.

The interface-level twin of forge/freecad/sessions.py, with the same layout,
the same part splits (`forge.system1.splits`) and the same three kinds of line.

Run:    uv run python -m forge.freecad_ui.sessions [--workers 2] [--max-minutes 240]
        (the same command again carries on where the last run stopped)
Output: data/freecad_ui/sessions/<slice>/shardNNN.jsonl.gz    the sessions
        data/freecad_ui/sessions/<slice>/shardNNN.stats.json  that shard's counts (it marks
                                                              the shard as complete)
        data/freecad_ui/sessions/manifest.json                counts over all complete
                                                              shards, rewritten after every shard

Slices, in the order they are made: a few hundred parts of each test split
(`iid`, `pairing`, `long`), then `train`. Inside a split the parts are in a fixed
shuffled order, so every shard mixes the four bases.

File format: one JSON object per line. A session is
    HEADER  {"session", "part_id", "family", "split", "slice", "seed", "noise_level",
             "clean_length", "start": {"kind": "empty"}, "plan": [{"kind", "slots"}],
             "source", "license", "generator_version"}
    STEP    {"session", "t", "snapshot", "target": [{"id", "value", "item", "source"}],
             "progress": {"on_plan", "built", "active"},
             "executed": {"id", "value", "noise"}, "reply": {"status", "reason"}}
    END     {"session", "end": "done" | "budget" | "stuck" | "error", "steps", "snapshot",
             "problems"}
`snapshot` is the lean interface snapshot (snapshots.py) BEFORE the step's action;
the END record holds the one after the last action. `target` is the teacher's
whole set. `executed.noise` is None, or the kind of wrong action that was run.
The last step of a finished session is the teacher's `done`, which is not an
interface element and is not sent to FreeCAD.

What is written. A session that reaches `done` is checked at once: the solid must
be the part's stored solid (bounding box 0.001 mm, volume one part in a million)
and the document clean. If it is not, the session is NOT written; it is counted
(`excluded_wrong_part`) and named in the shard's stats. Sessions that end any
other way ("budget", "stuck", "error") are written with their end record, counted,
and are not training data.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import threading
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from forge.freecad.parts import session_parts
from forge.freecad_ui.client import UIClient
from forge.freecad_ui.noise import NOISE_KINDS, NOISE_LEVELS, WRONG_BUTTONS
from forge.freecad_ui.play import HARD_EXTRA, HARD_FACTOR, NOISE_FACTOR, play, session_id
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.system1.splits import SPLITS, split_of, unit_hash

OUT_DIR = PROJECT_ROOT / "data" / "freecad_ui" / "sessions"
# name -> (split, seed, noise levels, how many parts at most), in the order they are made.
SLICES: dict[str, tuple[str, int, tuple[float, ...], int | None]] = {
    "iid": ("iid", 0, NOISE_LEVELS, 300),
    "pairing": ("pairing", 0, NOISE_LEVELS, 300),
    "long": ("long", 0, NOISE_LEVELS, 200),
    "train": ("train", 0, NOISE_LEVELS, None),
}
PARTS_PER_SHARD = {"iid": 50, "pairing": 50, "long": 25, "train": 50}
TRIES = 3           # how often one session is attempted when FreeCAD itself fails

FAILURES: list[dict] = []       # every action on which a FreeCAD instance died or hung
_local = threading.local()      # one hidden FreeCAD per worker thread
_clients: list[UIClient] = []
_clients_lock = threading.Lock()


# --- one shard (runs in a worker thread) ---------------------------------------------------------

def _fresh_client() -> UIClient:
    old = getattr(_local, "client", None)
    if old is not None:
        old.close()
    _local.client = UIClient()
    _local.client.start()
    with _clients_lock:
        _clients.append(_local.client)
    return _local.client


def close_clients() -> None:
    with _clients_lock:
        for client in _clients:
            client.close()
        _clients.clear()


def _session(part: dict, slice_name: str, stats: Counter) -> tuple[dict, list[dict], dict]:
    """Play one session; if FreeCAD fails underneath it, start a new one and play it again."""
    split, seed, noise_levels, _ = SLICES[slice_name]
    last = ""
    for _ in range(TRIES):
        try:
            ui = getattr(_local, "client", None) or _fresh_client()
            before, failed = ui.restarts, len(ui.failed_on)
            result = play(ui, part, seed, split, noise_levels, slice_name)
            stats["instance_restarts"] += ui.restarts - before
            for kind, element, value in ui.failed_on[failed:]:
                stats[f"instance_failed:{kind}:{element}"] += 1
                FAILURES.append({"part_id": part["id"], "kind": kind, "id": element,
                                 "value": value})
            return result
        except Exception as error:      # noqa: BLE001 - a lost instance, a broken socket
            stats["sessions_played_again"] += 1
            stats["instance_restarts"] += 1
            last = f"{type(error).__name__}: {str(error)[:300]}"
            _fresh_client()
    sid = session_id(part["id"], seed)
    header = {"session": sid, "part_id": part["id"], "family": part["family"], "split": split,
              "slice": slice_name, "seed": seed, "noise_level": None, "clean_length": 0,
              "start": {"kind": "error"}, "plan": [],
              "source": part["source"], "license": part["license"],
              "generator_version": part["generator_version"]}
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
    stats[f"plan_items:{len(header['plan'])}"] += 1
    stats["noise_cut_off"] += len(records) > NOISE_FACTOR * header["clean_length"]
    if not header["noise_level"]:
        # No wrong action was injected, yet the teacher had to repair something: FreeCAD
        # itself did not do what an action asked (a measure of how flaky the interface is).
        stats["clean_sessions_with_a_repair"] += any(not r["progress"]["on_plan"] for r in records)
        stats["clean_sessions_longer_than_clean"] += len(records) > header["clean_length"]
    for record, after in zip(records, [*records[1:], end], strict=True):
        stats[f"choices:{len(record['target'])}"] += 1
        stats["off_plan_steps"] += not record["progress"]["on_plan"]
        for target in record["target"]:
            stats[f"target:{target['id'].split(':')[0]}"] += 1
            stats["semantic_targets"] += target["id"].startswith("pick:")
            if target["id"] in ("button:Std_Undo", "dialog:Cancel", "done"):
                stats[f"target_is:{target['id']}"] += 1
        done = record["executed"]
        status = record["reply"]["status"]
        if done["noise"]:
            same = all(after["snapshot"][key] == record["snapshot"][key]
                       for key in ("context", "items", "elements"))
            stats["noisy_steps"] += 1
            stats[f"noise:{done['noise']}:{status}:{'no change' if same else 'changed'}"] += 1
        elif status != "ok":
            stats["teacher_actions_refused"] += 1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_shard(task: tuple[str, int, list[dict], str]) -> dict:
    """Play every part of one shard and write its file. Returns the shard's counts."""
    name, number, parts, out_dir = task
    path = Path(out_dir) / name / f"shard{number:03d}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    stats: Counter = Counter()
    excluded: list[dict] = []
    started = time.perf_counter()
    raw_bytes = 0
    # mtime=0 keeps a timestamp out of the file.
    with partial.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                                  mtime=0, compresslevel=6) as f:
        for part in parts:
            header, records, end = _session(part, name, stats)
            if end["end"] == "done" and end["problems"]:
                # It reached `done` on a part that is not the stored solid, or on a document
                # that is not clean. Such a session is never written; it is counted and named.
                stats["excluded:wrong_part"] += 1
                excluded.append({"session": header["session"], "part_id": part["id"],
                                 "problems": end["problems"][:3]})
                continue
            count_session(stats, header, records, end)
            text = "".join(json.dumps(line, separators=(",", ":")) + "\n"
                           for line in (header, *records, end)).encode("utf-8")
            raw_bytes += len(text)
            f.write(text)
    os.replace(partial, path)           # the shard appears only when it is whole
    stats.update(parts=len(parts), raw_bytes=raw_bytes, gz_bytes=path.stat().st_size)
    result = {"slice": name, "split": SLICES[name][0], "file": f"{name}/{path.name}",
              "sha256": sha256(path),
              "seconds": round(time.perf_counter() - started, 2), "counts": dict(stats),
              "excluded": excluded}
    path.with_name(f"shard{number:03d}.stats.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


# --- reading sessions back -------------------------------------------------------------------

def read_sessions(path: Path) -> Iterator[tuple[dict, list[dict], dict]]:
    """Every session in one shard: (header, its step records in order, its end record)."""
    header: dict | None = None
    records: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            if "plan" in item:
                header, records = item, []
            elif "end" in item:
                yield header, records, item
            else:
                records.append(item)


def code_hash() -> str:
    """A hash of the code that decides what a session contains."""
    digest = hashlib.sha256()
    here = Path(__file__).parent
    for path in [*sorted(here.glob("*.py")), *sorted((here / "inside").glob("*.py")),
                 PROJECT_ROOT / "forge/system1/steps.py", PROJECT_ROOT / "forge/system1/splits.py"]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


# --- the manifest ------------------------------------------------------------------------------

def grouped(counts: Counter, prefix: str) -> dict:
    return {key[len(prefix):]: value for key, value in sorted(counts.items())
            if key.startswith(prefix)}


def summary(counts: Counter) -> dict:
    steps = max(counts["steps"], 1)
    return {
        "parts": counts["parts"], "sessions": counts["sessions"],
        "sessions_ended": grouped(counts, "end:"),
        "sessions_with_problems": counts["sessions_with_problems"],
        "excluded_wrong_part": counts["excluded:wrong_part"],
        "steps": counts["steps"],
        "sessions_by_noise_level": grouped(counts, "noise_level:"),
        "steps_by_noise_level": grouped(counts, "steps_at_noise:"),
        "sessions_by_plan_items": grouped(counts, "plan_items:"),
        "targets_by_element_kind": grouped(counts, "target:"),
        "repair_and_done_targets": grouped(counts, "target_is:"),
        "semantic_targets": counts["semantic_targets"],
        "steps_by_number_of_targets": grouped(counts, "choices:"),
        "off_plan_steps": counts["off_plan_steps"],
        "clean_sessions_with_a_repair": counts["clean_sessions_with_a_repair"],
        "clean_sessions_longer_than_clean": counts["clean_sessions_longer_than_clean"],
        "noisy_steps": counts["noisy_steps"],
        "noisy_steps_by_kind_reply_effect": grouped(counts, "noise:"),
        "sessions_where_noise_was_cut_off": counts["noise_cut_off"],
        "teacher_actions_refused": counts["teacher_actions_refused"],
        "instance_restarts": counts["instance_restarts"],
        "instance_failed_on": grouped(counts, "instance_failed:"),
        "sessions_played_again": counts["sessions_played_again"],
        "raw_bytes": counts["raw_bytes"], "gz_bytes": counts["gz_bytes"],
        "raw_bytes_per_step": round(counts["raw_bytes"] / steps, 1),
        "gz_bytes_per_step": round(counts["gz_bytes"] / steps, 1),
    }


def write_manifest(out_dir: Path, config: dict, planned: dict[str, int]) -> dict:
    """Add up the stats of every complete shard. Safe to call at any time."""
    per_slice: dict[str, Counter] = {name: Counter() for name in SLICES}
    files, seconds = {}, 0.0
    for path in sorted(out_dir.glob("*/shard*.stats.json")):
        shard = json.loads(path.read_text())
        per_slice[shard["slice"]].update(shard["counts"])
        per_slice[shard["slice"]]["shards"] += 1
        files[shard["file"]] = shard["sha256"]
        seconds += shard["seconds"]
    total: Counter = Counter()
    for counts in per_slice.values():
        total.update(counts)
    manifest = {
        "config": config,
        "contract": "forge/freecad_ui/sessions.py (file format), teacher.py (rules), "
                    "snapshots.py (the snapshot)",
        "totals": summary(total),
        "slices": {name: {"split": SLICES[name][0], "seed": SLICES[name][1],
                          "noise_levels": list(SLICES[name][2]), **summary(per_slice[name]),
                          "shards": per_slice[name]["shards"],
                          "shards_planned": planned.get(name, 0)} for name in SLICES},
        "busy_seconds_all_workers": round(seconds, 1),
        "files": files,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


# --- the command -------------------------------------------------------------------------------

def plan_shards(out_dir: Path, limit_per_slice: int | None = None) -> list[tuple]:
    """Every shard of every slice, in the order of SLICES. Always the same shards."""
    by_split: dict[str, list[dict]] = {split: [] for split in SPLITS}
    for part in session_parts():
        by_split[split_of(part)].append(part)
    for parts in by_split.values():     # a fixed shuffle: the files are sorted base by base
        parts.sort(key=lambda part: unit_hash(f"shard-order:{part['id']}"))
    tasks = []
    for name, (split, _, _, at_most) in SLICES.items():
        parts, size = by_split[split][:at_most][:limit_per_slice], PARTS_PER_SHARD[name]
        tasks += [(name, number, parts[at:at + size], str(out_dir))
                  for number, at in enumerate(range(0, len(parts), size))]
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workers", type=int, default=2,
                        help="hidden FreeCAD windows (each about 1 GB and one core)")
    parser.add_argument("--max-minutes", type=float, default=None,
                        help="start no new shard after this many minutes (run again to go on)")
    parser.add_argument("--limit-per-slice", type=int, default=None,
                        help="only the first N parts of each slice (for a trial)")
    parser.add_argument("--slices", nargs="*", default=list(SLICES), choices=list(SLICES))
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = {"limit_per_slice": args.limit_per_slice,
              "slices": {name: list(value) for name, value in SLICES.items()},
              "noise_kinds": NOISE_KINDS, "wrong_buttons": list(WRONG_BUTTONS),
              "start_kinds": {"empty": 1.0}, "noise_factor": NOISE_FACTOR,
              "hard_limit": f"{HARD_FACTOR} x clean length + {HARD_EXTRA}",
              "parts_per_shard": PARTS_PER_SHARD, "code_hash": code_hash()}
    run_dir = start_run("freecad-ui-sessions", {**config, **vars(args)})

    tasks = [task for task in plan_shards(out_dir, args.limit_per_slice)
             if task[0] in args.slices]
    planned = Counter(task[0] for task in tasks)
    todo = [task for task in tasks
            if not (out_dir / task[0] / f"shard{task[1]:03d}.stats.json").exists()]
    print(f"{len(tasks)} shards planned {dict(planned)}, {len(tasks) - len(todo)} already "
          f"complete, {len(todo)} to do", flush=True)

    started = time.time()
    done_now: Counter = Counter()
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            waiting = iter(todo)
            running: set = set()
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
                    shard = future.result()
                    done_now.update(shard["counts"])
                    done_now["shards"] += 1
                    elapsed = time.time() - started
                    print(f"[{elapsed / 60:6.1f} min] {shard['file']}  "
                          f"{shard['counts']['sessions']} sessions "
                          f"{shard['counts'].get('steps', 0)} steps in {shard['seconds']:.0f}s "
                          f"ended {grouped(Counter(shard['counts']), 'end:')} | this run: "
                          f"{done_now['sessions']} sessions, "
                          f"{3600 * done_now['sessions'] / elapsed:.0f}/hour", flush=True)
                    log_metrics(run_dir, shard=shard["file"], seconds=shard["seconds"],
                                sessions=shard["counts"]["sessions"],
                                steps=shard["counts"].get("steps", 0))
                if finished:
                    write_manifest(out_dir, config, planned)    # whatever exists is usable
    finally:
        close_clients()                 # no hidden FreeCAD is left behind, whatever happened
    elapsed = time.time() - started

    manifest = write_manifest(out_dir, config, planned)
    left = sum(1 for task in tasks
               if not (out_dir / task[0] / f"shard{task[1]:03d}.stats.json").exists())
    totals = manifest["totals"]
    log_metrics(run_dir, final=True, wall_seconds=round(elapsed, 1), workers=args.workers,
                sessions_this_run=done_now["sessions"], steps_this_run=done_now["steps"],
                shards_left=left, sessions_on_disk=totals["sessions"],
                steps_on_disk=totals["steps"])
    (run_dir / "NOTES.md").write_text(
        f"# freecad-ui-sessions\n\nInterface sessions for Forge-S1 (forge/freecad_ui/sessions.py).\n"
        f"This run: {done_now['sessions']} sessions, {done_now['steps']} steps in "
        f"{elapsed / 60:.1f} min with {args.workers} hidden FreeCAD windows. Shards left: {left}.\n"
        f"On disk now: {totals['sessions']} sessions, {totals['steps']} steps.\n"
        f"Counts and file hashes: {out_dir / 'manifest.json'}.\n"
        f"Check with: uv run python -m forge.freecad_ui.audit --dir {out_dir}\n")
    print(f"\nthis run: {done_now['sessions']} sessions, {done_now['steps']} steps in "
          f"{elapsed:.0f}s ({args.workers} hidden FreeCAD windows)")
    print(f"on disk:  {totals['sessions']} sessions, {totals['steps']} steps, "
          f"{totals['gz_bytes'] / 1e6:.1f} MB gzip ({totals['gz_bytes_per_step']} bytes/step; "
          f"{totals['raw_bytes_per_step']} before gzip)")
    print(f"sessions that reached done on a wrong part (not written): "
          f"{totals['excluded_wrong_part']}")
    for name in SLICES:
        info = manifest["slices"][name]
        print(f"  {name:8s} {info['shards']:3d}/{info['shards_planned']:4d} shards "
              f"{info['sessions']:6d} sessions {info['steps']:8d} steps  ended "
              f"{info['sessions_ended']}  noise {info['sessions_by_noise_level']}")
    print(f"instance restarts: {totals['instance_restarts']}; sessions played again: "
          f"{totals['sessions_played_again']}")
    print(f"shards left: {left}" + ("  (run the same command again to go on)" if left else ""))
    print(f"manifest: {out_dir / 'manifest.json'}\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
