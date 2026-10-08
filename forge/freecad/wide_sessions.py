"""Record sessions of the THIRD mix on the wide parts, in real (headless) FreeCAD.

One session per part, resumable, in a folder of its own. Nothing under
data/freecad/sessions/ (the frozen recordings) is read or written here.

Slices (SLICES below):
    train_wide_v3        wide_train parts        TRAINING
    train_wide_long_v3   wide_train_long parts   training only when asked for (14 to 16 items)
    wide_iid_v3          wide_iid parts          TEST: same sampler, other parts
    wide_numbers_v3      wide_numbers parts      TEST: held-out scales and decimal patterns
    wide_order_v3        wide_order parts        TEST: held-out orders of two plan items
    abnormal_starts_v3   wide_starts parts       TEST: every session begins in a held-out start

A shard holds parts of every base and length (a fixed shuffle), so whatever is recorded so
far is a balanced sample of its slice.

Every part is checked by FreeCAD here. A session ends with the comparison of FreeCAD's
solid with the part's stored kernel measurement (play.end_check). When a session does not
end `done` on the stored solid, the part's plan is built once more WITHOUT mistakes
(wide_build.build_plan):
    the clean build fails too   the part is one FreeCAD builds differently from the CadQuery
                                reference (about 3 in 100 wide plans: a chamfer or fillet the
                                kernel gets wrong). It is written to
                                `shardNNN.rejected.jsonl` with the reason and gets NO session.
    the clean build passes      the part is fine and the session is not: it is kept in the
                                shard, so that the audit sees it and fails or lists it.

Run:    uv run python -m forge.freecad.wide_sessions --workers 3 [--slices wide_iid_v3] [--max-minutes N]
        (the same command again carries on where the last run stopped)
Output: data/freecad/sessions_wide/<slice>/shardNNN.jsonl.gz, .stats.json, .rejected.jsonl
        data/freecad/sessions_wide/manifest.json
The file format is sessions.py's; the header has one more field, `burst`. Read through load.py:
    forge.freecad.load.examples(("train_wide_v3",), out_dir=wide_sessions.OUT_DIR)
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from forge.freecad import wide_noise, wide_starts
from forge.freecad.client import FreeCADClient
from forge.freecad.manifest import summary
from forge.freecad.noise import NOISE_LEVELS
from forge.freecad.sessions import CODE_FILES, count_session
from forge.freecad.shards import complete_shards, sha256
from forge.freecad.wide_build import build_plan
from forge.freecad.wide_parts import SETS, read_wide, steps_of_part
from forge.freecad.wide_play import play
from forge.runs import PROJECT_ROOT, git_commit, log_metrics, start_run
from forge.system1.splits import unit_hash

OUT_DIR = PROJECT_ROOT / "data" / "freecad" / "sessions_wide"
SEED = 20
# name -> (set of parts, start kinds, parts per shard), in the order they are recorded:
# the tests first (they are small and must be complete), then training.
SLICES: dict[str, tuple[str, dict[str, float], int]] = {
    "wide_iid_v3": ("wide_iid", wide_starts.TRAIN_STARTS, 250),
    "wide_numbers_v3": ("wide_numbers", wide_starts.TRAIN_STARTS, 250),
    "wide_order_v3": ("wide_order", wide_starts.TRAIN_STARTS, 250),
    "abnormal_starts_v3": ("wide_starts", wide_starts.TEST_STARTS, 250),
    "train_wide_v3": ("wide_train", wide_starts.TRAIN_STARTS, 250),
    "train_wide_long_v3": ("wide_train_long", wide_starts.TRAIN_STARTS, 100),
}
TRAIN_SLICES = ("train_wide_v3", "train_wide_long_v3")
TEST_SLICES = tuple(name for name in SLICES if name not in TRAIN_SLICES)
WIDE_FILES = ("wide_sessions.py", "wide_play.py", "wide_noise.py", "wide_starts.py",
              "wide_build.py", "wide_parts.py", "wide_sample.py", "wide_reference.py")
TRIES = 3

_client: FreeCADClient | None = None


def code_hash() -> str:
    """A hash of the code that decides what a session of the third mix contains."""
    digest = hashlib.sha256()
    here = Path(__file__).parent
    for name in (*CODE_FILES, *WIDE_FILES):
        digest.update(name.encode())
        digest.update((here / name).read_bytes())
    return digest.hexdigest()[:12]


def _fresh_client() -> FreeCADClient:
    global _client
    if _client is not None:
        _client.close()
    _client = FreeCADClient(exact_undo=True)
    _client.start()
    return _client


def _session(part: dict, other: dict, name: str, stats: Counter, code: str):
    """Play one session; if FreeCAD fails underneath it, start a new worker and play it again.
    Returns (header, records, end), or None with the part's rejection written to `stats`."""
    last = "?"
    for _ in range(TRIES):
        try:
            fc = _client or _fresh_client()
            before = fc.restarts
            result = play(fc, part, SEED, name, other_plan=steps_of_part(other),
                          start_kinds=SLICES[name][1], code=code)
            stats["worker_restarts"] += fc.restarts - before
            _, _, end = result
            if end["end"] == "done" and not end["problems"]:
                return result, None
            # Is it the part or the session? Build the plan once without mistakes.
            clean = build_plan(fc, steps_of_part(part), part["step_volumes"], part["measured"],
                               random.Random(part["id"]))
            if clean.problems:
                return None, {"part_id": part["id"], "why": clean.problems[0][:300],
                              "session_ended": end["end"]}
            return result, None
        except Exception as error:      # noqa: BLE001 - a lost worker, a broken pipe, bad JSON
            stats["sessions_played_again"] += 1
            stats["worker_restarts"] += 1
            last = f"{type(error).__name__}: {error}"
            _fresh_client()
    return None, {"part_id": part["id"], "why": f"FreeCAD failed {TRIES} times: {last}"[:300],
                  "session_ended": "error"}


def count_wide(stats: Counter, header: dict, records: list[dict], end: dict) -> None:
    """What the third mix adds to sessions.count_session."""
    if end["end"] != "done":
        return
    stats["burst_sessions"] += header["burst"]
    stats[f"base:{header['plan'][0]['kind']}"] += 1
    for item in header["plan"][1:]:
        stats[f"plan_kind:{item['kind']}"] += 1
    stats["burst_steps"] += sum(bool(record["executed"].get("burst")) for record in records)
    run = longest = 0
    for record in records:          # the longest run of wrong commands in a row
        run = run + 1 if record["executed"]["noise"] else 0
        longest = max(longest, run)
    stats[f"longest_run_of_mistakes:{min(longest, 8)}"] += 1


def write_shard(task: tuple[str, int, list[dict], list[dict], str]) -> dict:
    """Play every part of one shard and write its files. Returns the shard's counts."""
    name, number, parts, others, out_dir = task
    path = Path(out_dir) / name / f"shard{number:03d}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    stats: Counter = Counter()
    rejected = []
    started = time.perf_counter()
    code = code_hash()
    raw_bytes = 0
    with partial.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                                  mtime=0, compresslevel=6) as f:
        for part, other in zip(parts, others, strict=True):
            result, why = _session(part, other, name, stats, code)
            if result is None:
                rejected.append(why)
                continue
            header, records, end = result
            count_session(stats, header, records, end)
            count_wide(stats, header, records, end)
            text = "".join(json.dumps(line, separators=(",", ":")) + "\n"
                           for line in (header, *records, end)).encode("utf-8")
            raw_bytes += len(text)
            f.write(text)
    if code_hash() != code:
        partial.unlink()
        raise RuntimeError(f"{name}/shard{number:03d}: the code changed while it was recorded")
    os.replace(partial, path)
    path.with_name(f"shard{number:03d}.rejected.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in rejected))
    stats.update(parts=len(parts), parts_rejected_by_freecad=len(rejected),
                 raw_bytes=raw_bytes, gz_bytes=path.stat().st_size)
    result = {"slice": name, "split": SLICES[name][0], "file": f"{name}/{path.name}",
              "sha256": sha256(path), "code_hash": code, "mix": wide_noise.MIX,
              "seconds": round(time.perf_counter() - started, 2), "counts": dict(stats)}
    path.with_name(f"shard{number:03d}.stats.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


def ordered_parts(part_set: str) -> list[dict]:
    """The parts of one set in the order they are recorded.

    The generator fills every (base, length) cell at the same pace, so the k-th part of
    every cell forms a ROUND. Parts are ordered round by round (a fixed shuffle inside a
    round), and only complete rounds are taken. Two things follow: every shard is a balanced
    sample of the set, and parts generated LATER only add shards at the end, so a recording
    can go on after more parts were made without touching a shard that exists."""
    cells: Counter = Counter()
    rows = []
    for part in read_wide(part_set):        # the file order is the order the parts were kept in
        cell = (part["family"], len(part["plan"]))
        rows.append((cells[cell], unit_hash(f"shard-order:{part['id']}"), part))
        cells[cell] += 1
    _, _, low, high, _, _ = SETS[part_set]
    rounds = min(cells.values()) if len(cells) == 4 * (high - low + 1) else 0
    return [part for k, _, part in sorted(rows, key=lambda row: row[:2]) if k < rounds]


def plan_shards(out_dir: Path, limit_per_slice: int | None = None) -> list[tuple]:
    """Every shard of every slice. Always the same shards for the same part files."""
    tasks = []
    for name, (part_set, _, size) in SLICES.items():
        parts = ordered_parts(part_set)[:limit_per_slice]
        # The "other part" of a session (someone else's work in the start state) is the next
        # part of the same slice: same set, so nothing crosses from one set into another.
        others = parts[1:] + parts[:1]
        tasks += [(name, number, parts[at:at + size], others[at:at + size], str(out_dir))
                  for number, at in enumerate(range(0, len(parts), size))]
    return tasks


def write_manifest(out_dir: Path, config: dict | None = None) -> dict:
    """Counts over all complete shards, from their stats files alone."""
    slices: dict[str, Counter] = {}
    files, versions = {}, {}
    for stats, path in complete_shards(out_dir):
        slices.setdefault(stats["slice"], Counter()).update(stats["counts"])
        slices[stats["slice"]]["shards"] += 1
        files[stats["file"]] = stats["sha256"]
        versions.setdefault(stats["code_hash"], []).append(stats["file"])
    total: Counter = Counter()
    for counts in slices.values():
        total.update(counts)

    def entry(counts: Counter) -> dict:
        return {**summary(counts), "shards": counts["shards"],
                "parts_rejected_by_freecad": counts["parts_rejected_by_freecad"],
                "burst_sessions": counts["burst_sessions"], "burst_steps": counts["burst_steps"],
                "sessions_by_base": {key[5:]: value for key, value in sorted(counts.items())
                                     if key.startswith("base:")},
                "plan_items_by_kind": {key[10:]: value for key, value in sorted(counts.items())
                                       if key.startswith("plan_kind:")},
                "sessions_by_longest_run_of_mistakes":
                    {key[24:]: value for key, value in sorted(counts.items())
                     if key.startswith("longest_run_of_mistakes:")}}

    manifest = {"written": time.strftime("%Y-%m-%d %H:%M:%S"), "git_commit": git_commit(),
                "mix": wide_noise.MIX, "config": config,
                "slices": {name: entry(slices[name]) for name in SLICES if name in slices},
                "totals": entry(total), "code_versions": versions, "files": files}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument("--limit-per-slice", type=int, default=None)
    parser.add_argument("--slices", nargs="*", default=list(SLICES), choices=list(SLICES))
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = {"slices": {name: [value[0], value[2]] for name, value in SLICES.items()},
              "seed": SEED, "mix": wide_noise.MIX, "noise_levels": NOISE_LEVELS,
              "order_chance": wide_noise.ORDER_CHANCE, "order_next": wide_noise.ORDER_NEXT,
              "decimal_chance": wide_noise.DECIMAL_CHANCE,
              "burst": [wide_noise.BURST_SHARE, wide_noise.BURST_LEVELS, wide_noise.BURST_START,
                        wide_noise.BURST_MOST],
              "train_starts": wide_starts.TRAIN_STARTS, "test_starts": wide_starts.TEST_STARTS,
              "exact_undo": True, "code_hash": code_hash()}
    if args.manifest_only:
        manifest = write_manifest(out_dir, config)
        print(json.dumps({name: {key: info[key] for key in ("shards", "sessions", "steps")}
                          for name, info in manifest["slices"].items()}, indent=1))
        return
    run_dir = start_run("freecad-wide-sessions", {**config, **vars(args)})
    tasks = [task for task in plan_shards(out_dir, args.limit_per_slice) if task[0] in args.slices]
    todo = [task for task in tasks
            if not (out_dir / task[0] / f"shard{task[1]:03d}.stats.json").exists()]
    print(f"{len(tasks)} shards planned {dict(Counter(task[0] for task in tasks))}, "
          f"{len(tasks) - len(todo)} already complete, {len(todo)} to do", flush=True)
    started = time.time()
    done_now: Counter = Counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        waiting, running = iter(todo), set()
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
                elapsed = time.time() - started
                print(f"[{elapsed / 60:6.1f} min] {shard['file']}  "
                      f"{shard['counts'].get('sessions', 0)} sessions "
                      f"{shard['counts'].get('steps', 0)} steps, "
                      f"{shard['counts']['parts_rejected_by_freecad']} parts rejected, in "
                      f"{shard['seconds']:.0f}s | this run: {done_now['sessions']} sessions, "
                      f"{done_now['sessions'] / elapsed:.2f}/s, "
                      f"{done_now['steps'] / elapsed:.0f} steps/s", flush=True)
                log_metrics(run_dir, shard=shard["file"], seconds=shard["seconds"],
                            sessions=shard["counts"].get("sessions", 0),
                            steps=shard["counts"].get("steps", 0),
                            rejected=shard["counts"]["parts_rejected_by_freecad"])
            if finished:
                write_manifest(out_dir, config)
    manifest = write_manifest(out_dir, config)
    left = sum(1 for task in tasks
               if not (out_dir / task[0] / f"shard{task[1]:03d}.stats.json").exists())
    elapsed = time.time() - started
    log_metrics(run_dir, final=True, wall_seconds=round(elapsed, 1), workers=args.workers,
                sessions_this_run=done_now["sessions"], steps_this_run=done_now["steps"],
                shards_left=left)
    (run_dir / "NOTES.md").write_text(
        f"# freecad-wide-sessions\n\nThird-mix sessions on wide parts (forge/freecad/wide_sessions.py).\n"
        f"This run: {done_now['sessions']} sessions, {done_now['steps']} steps in "
        f"{elapsed / 60:.1f} min with {args.workers} workers. Shards left: {left}.\n"
        f"Counts and file hashes: {out_dir / 'manifest.json'}.\n")
    print(f"\nthis run: {done_now['sessions']} sessions, {done_now['steps']} steps in {elapsed:.0f}s")
    for name, info in manifest["slices"].items():
        print(f"  {name:20s} {info['shards']:4d} shards {info['sessions']:7d} sessions "
              f"{info['steps']:9d} steps  ended {info['sessions_ended']}  rejected parts "
              f"{info['parts_rejected_by_freecad']}")
    print(f"shards left: {left}\nmanifest: {out_dir / 'manifest.json'}\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
