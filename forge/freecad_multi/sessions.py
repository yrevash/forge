"""Record FreeCAD sessions for structures: one session per chosen plan, in real FreeCAD.

The structure counterpart of forge/freecad/sessions.py, on the same pattern. selection.py chose the plans (from data/plans: generator
output with provenance) and wrote each slice in recording order. Here every plan is
resolved again from its text (it must give its stored parts, or it gets no session),
played once in a headless FreeCAD (play.py), and stored one record per step. Only the
teacher's set is ever a target.

Run:    uv run python -m forge.freecad_multi.sessions [--workers 2] [--max-minutes 60]
        (the same command again carries on where the last run stopped)
Trial:  uv run python -m forge.freecad_multi.sessions --limit-per-slice 60 --out data/freecad_multi/trial_v2
Output: data/freecad_multi/sessions/<slice>/shardNNNN.jsonl.gz    the sessions
        data/freecad_multi/sessions/<slice>/shardNNNN.stats.json  that shard's counts and the
                                                                  hash of the code that made it
                                                                  (it marks the shard as complete)
        data/freecad_multi/sessions/manifest.json                 counts over all complete shards,
                                                                  rewritten after every shard

Slices (shards.SLICES): `iid`, `kinds`, `combo`, `long` are tests and are recorded first;
then `train_rare_s1..s3` (the train plans with a feature or another shape, played again
with other seeds) and `train`. What each holds is decided in selection.py on what the
executor sees. Shard N of a slice is the plans of
data/freecad_multi/selection/<folder>/partN.jsonl.gz:
the same command always plans the same shards while the selection is unchanged, and the
selection's hash is in every shard's stats.

THE EDIT GUARD. Every shard carries the hash of the code that decides what a session
contains (CODE_FILES). The hash is taken when a shard starts and again when it ends; if
it changed, the shard is thrown away and the run stops. So no shard is ever a mix of two
versions of the code.

File format: one JSON object per line. A session is
    HEADER  {"session", "plan_id", "origin", "where", "kind", "split", "plans_split", "slice",
             "seed", "noise_level", "clean_length", "start": {"kind", "commands", "forget_undo"},
             "parts", "plan": [...] (play.plan_json), "source", "license", "generator_version",
             "code_hash", "mix", "selection"}
    STEP    {"session", "t", "snapshot", "valid", "target": [{"command", "args", "item",
             "sources"}], "progress": {"on_plan", "built", "active"},
             "executed": {"command", "args", "noise", "flavour"?}, "reply": {"status", "reason"},
             "before"?: "forget_undo"}
    END     {"session", "end": "done" | "budget" | "stuck" | "error", "steps", "snapshot", "problems"}
`snapshot` is the lean structure snapshot (lean.py) BEFORE the step's command, stored as
a delta to the step before (shards.py; `shards.read_sessions` gives it back whole).

A model must never be handed these records as they are. Read them through load.py.
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

from forge.freecad_multi import noise, sources
from forge.freecad_multi import play as player
from forge.freecad_multi.build import run_script
from forge.freecad_multi.client import MultiClient
from forge.freecad_multi.manifest import write_manifest
from forge.freecad_multi.play import (
    HARD_EXTRA,
    HARD_FACTOR,
    NOISE_FACTOR,
    end_check,
    play,
    session_id,
)
from forge.freecad_multi.recipes import Unsupported, structure_items
from forge.freecad_multi.shards import (
    OUT_DIR,
    SELECTION_DIR,
    SLICES,
    changed,
    pack,
    read_sessions,
    sha256,
    unpack,
)
from forge.resolve.judge import KernelJudge
from forge.runs import PROJECT_ROOT, log_metrics, start_run

__all__ = ["CODE_FILES", "OUT_DIR", "SLICES", "code_hash", "read_sessions", "sha256"]

ATTEMPTS = (0, 0, 1, 2)     # the draws tried, in order, when FreeCAD itself fails under a session
PLAIN_SHAPES = ("box", "cylinder")
# The files that decide what a session contains, relative to this folder. Tools that only
# read sessions (audit.py, oracle.py, load.py, manifest.py, show.py) are left out on
# purpose, so that working on them does not change the hash of the data.
CODE_FILES = (
    "multi_catalogue.py", "multi_valid.py", "recipes.py", "teacher.py", "noise.py", "play.py",
    "lean.py", "client.py", "build.py", "sources.py", "sessions.py", "shards.py",
    "selection.py", "plan_facts.py",
    "inside/worker.py", "inside/multi_session.py", "inside/part_shapes.py", "inside/sides.py",
    "inside/structure.py",
    "../freecad/catalogue.py", "../freecad/valid.py", "../freecad/recipes.py",
    "../freecad/teacher.py", "../freecad/noise.py", "../freecad/play.py", "../freecad/lean.py",
    "../freecad/client.py", "../freecad/inside/session.py", "../freecad/inside/shapes.py",
    "../freecad/inside/features.py", "../freecad/inside/rules.py",
    "../freecad/inside/snapshot.py",
    "../resolve/resolver.py", "../resolve/bodies.py", "../resolve/shapes.py",
    "../resolve/placing.py", "../resolve/repeating.py", "../resolve/featuring.py",
    "../resolve/contact.py", "../resolve/space.py", "../resolve/prototype.py",
    "../resolve/spans.py", "../resolve/scene.py", "../resolve/defaults.py",
)

_client: MultiClient | None = None      # one FreeCAD worker per process
_judge: KernelJudge | None = None       # and one kernel judge, started only if a plan needs it


def code_hash() -> str:
    """A hash of the code that decides what a session contains (CODE_FILES)."""
    digest = hashlib.sha256()
    here = Path(__file__).parent
    for name in CODE_FILES:
        digest.update(name.encode())
        digest.update((here / name).read_bytes())
    return digest.hexdigest()[:12]


def selection_hash(selection_dir: Path = SELECTION_DIR) -> str:
    path = selection_dir / "selection.json"
    return json.loads(path.read_text())["selection_hash"] if path.exists() else "none"


# --- one shard (runs in a worker process) ------------------------------------------------------

def _fresh_client() -> MultiClient:
    global _client
    if _client is not None:
        _client.close()
    _client = MultiClient(recycle_after=100, exact_undo=True)
    _client.start()
    return _client


def _session(unit: sources.Unit, slice_name: str, stats: Counter, code: str,
             ) -> tuple[dict, list[dict], dict]:
    """Play one session; if FreeCAD fails underneath it, start a new worker and play it again."""
    _, _, seed, noise_levels, _ = SLICES[slice_name]
    # The session's own draw twice (a lost worker is usually a passing thing), then other
    # draws of the same session (play.py, `attempt`): a draw whose wrong command hangs the
    # kernel hangs it every time.
    for attempt in ATTEMPTS:
        try:
            fc = _client or _fresh_client()
            before = fc.restarts
            result = play(fc, unit, seed, noise_levels, slice_name, code, attempt)
            stats["worker_restarts"] += fc.restarts - before
            stats["sessions_with_another_draw"] += attempt > 0
            return result
        except Exception as error:      # noqa: BLE001 - a lost worker, a broken pipe, bad JSON
            stats["sessions_played_again"] += 1
            stats["worker_restarts"] += 1
            last = f"{type(error).__name__}: {error}"
            _fresh_client()
    sid = session_id(unit.id, seed)
    header = {"session": sid, "plan_id": unit.id, "origin": unit.origin, "where": unit.where,
              "kind": unit.kind, "split": unit.split, "slice": slice_name, "seed": seed,
              "noise_level": None, "clean_length": 0,
              "start": {"kind": "error", "commands": [], "forget_undo": False}, "parts": 0,
              "plan": [], "source": unit.source, "license": unit.license,
              "generator_version": unit.generator_version, "code_hash": code, "mix": noise.MIX}
    return header, [], {"session": sid, "end": "error", "steps": 0, "snapshot": None,
                        "problems": [last]}


def needs_proof(items: list) -> bool:
    """Does this plan get a clean trial build before its session? Every plan with a feature
    or with another shape than box or cylinder."""
    return any(item.features or (item.kind == "part" and item.shape not in PLAIN_SHAPES)
               for item in items)


def proof(unit: sources.Unit, items: list) -> str | None:
    """Build the plan once without mistakes. None if FreeCAD ends at the resolver's
    structure with every command accepted; otherwise the first thing that went wrong.

    Why: data/plans proves a plan on the reference kernel (CadQuery). The FreeCAD commands
    are a second way to the same solid, and for some featured plans they do not get there
    (found in the first hour of this recording, 6 Oct 2026): a `top chamfer` on a lying
    cylinder has no edge "around the top" as the body sits; some pockets leave a PartDesign
    body in two solids, which the reference kernel accepts as one part and FreeCAD's `done`
    refuses. A session on such a plan can only end `stuck`. So a plan gets a session only
    if its clean build is proved here first; the others are counted, with the reason, in
    the shard's stats file (`not_built`).
    """
    for attempt in range(2):
        try:
            fc = _client or _fresh_client()
            fc.reset()
            result = run_script(fc, items)
            if result.problems:
                return result.problems[0]
            problems = end_check(unit, items, result.snapshot)
            return problems[0] if problems else None
        except Exception as error:      # noqa: BLE001 - a lost worker: try once more
            last = f"worker lost: {type(error).__name__}"
            _fresh_client()
    return last


def cause(why: str) -> str:
    """A short name for the reason a clean build failed, for the counts."""
    if "done" in why and "rejected" in why:
        return "done refused (a body is not one valid solid)"
    if "rejected" in why:
        command = why.split(": ", 1)[-1].split(" ")[0]
        return f"{command} refused"
    if "invalid" in why:
        return "a feature came out invalid"
    if "not one valid solid" in why or "solids in" in why:
        return "a body is not one solid"
    if "worker lost" in why:
        return "worker lost"
    return "the structure is not the resolver's"


def bodies_group(count: int) -> str:
    return ("1" if count == 1 else "2-10" if count <= 10 else "11-20" if count <= 20
            else "21-30" if count <= 30 else "over 30")


def count_session(stats: Counter, header: dict, records: list[dict], end: dict) -> None:
    stats["sessions"] += 1
    stats[f"end:{end['end']}"] += 1
    if end["end"] != "done":
        return                          # a session that did not finish is not training data
    plan = header["plan"]
    stats["steps"] += len(records)
    stats["parts_built"] += header["parts"]
    stats["sessions_with_problems"] += bool(end["problems"])
    stats[f"kind:{header['kind'] or header['source'].split(':')[-1]}"] += 1
    stats[f"bodies:{bodies_group(header['parts'])}"] += 1
    shapes = {item["shape"] for item in plan if item["kind"] == "part"}
    features = {(feature["feature"], feature["side"]) for item in plan
                for feature in item["features"]}
    for shape in shapes:
        stats[f"shape:{shape}"] += 1
    for kind in {kind for kind, _ in features}:
        stats[f"feature:{kind}"] += 1
    for side in {side for _, side in features}:
        stats[f"feature_side:{side}"] += 1
    stats["sessions_with_another_shape"] += bool(shapes - set(PLAIN_SHAPES))
    stats["sessions_with_a_feature"] += bool(features)
    stats["sessions_with_a_turned_part"] += any(item.get("turn", [0, 0, 0]) != [0.0, 0.0, 0.0]
                                                for item in plan)
    stats[f"noise_level:{header['noise_level']}"] += 1
    stats[f"steps_at_noise:{header['noise_level']}"] += len(records)
    stats[f"start:{header['start']['kind']}"] += 1
    stats["noise_cut_off"] += len(records) > NOISE_FACTOR * header["clean_length"]
    goes_back = False
    after_wrong_number = False
    for record, after in zip(records, [*records[1:], end], strict=True):
        names = [target["command"] for target in record["target"]]
        on_plan = record["progress"]["on_plan"]
        stats[f"choices:{len(names)}"] += 1
        stats["off_plan_steps"] += not on_plan
        for name in names:
            stats[f"target:{name}"] += 1
        goes_back = goes_back or "activate_body" in names
        stats["steps_with_a_failed_feature"] += any(not item["valid"]
                                                    for item in record["snapshot"]["items"])
        stats["steps_finished_too_early"] += record["snapshot"]["session"]["finished"]
        stats["repairs_by_new_document"] += not on_plan and names == ["new_document"]
        stats["steps_right_after_a_wrong_number"] += after_wrong_number
        stats["undo_history_lost"] += record.get("before") == "forget_undo"
        done = record["executed"]
        status = record["reply"]["status"]
        moved = changed(record, after)
        after_wrong_number = done["noise"] in noise.WRONG_NUMBERS and status == "ok" and moved
        if done["noise"]:
            stats["noisy_steps"] += 1
            stats[f"noise:{done['noise']}:{status}:{'changed' if moved else 'no change'}"] += 1
            if "flavour" in done:
                stats[f"wrong_number:{done['flavour']}"] += 1
        elif status != "ok":
            stats["teacher_commands_refused"] += 1
    stats["sessions_with_activate_body"] += goes_back


def _lines(header: dict, records: list[dict], end: dict) -> bytes:
    return "".join(json.dumps(line, separators=(",", ":")) + "\n"
                   for line in (header, *records, end)).encode("utf-8")


def write_shard(task: tuple[str, int, str, str, str]) -> dict:
    """Play the plans of one selection file and write a shard. Returns its counts."""
    global _judge
    name, number, plans_file, out_dir, chosen = task
    path = Path(out_dir) / name / f"shard{number:04d}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    stats: Counter = Counter()
    started = time.perf_counter()
    code = code_hash()
    raw_bytes = whole_bytes = plans = 0
    not_built: list[list[str]] = []
    if _judge is None:
        _judge = KernelJudge()
    # mtime=0 keeps a timestamp out of the file, so the same run gives the same bytes.
    with partial.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                                  mtime=0, compresslevel=6) as f:
        for record in sources.plan_records(Path(plans_file)):
            plans += 1
            try:
                unit = sources.unit_from_record(record, PROJECT_ROOT / record["where"], _judge)
            except (KeyError, ValueError):
                stats["plans_not_as_stored"] += 1       # no session for a plan we cannot trust
                continue
            try:
                items = structure_items(unit.bodies)
            except Unsupported:
                stats["plans_not_buildable"] += 1       # selection.py should have kept it out
                continue
            if needs_proof(items):
                why = proof(unit, items)
                if why is not None:                     # FreeCAD does not build it: no session
                    stats["plans_freecad_cannot_build"] += 1
                    stats[f"cannot_build:{cause(why)}"] += 1
                    not_built.append([record["id"], why[:300]])
                    continue
            unit.split = record["s1_split"]             # the split a session is in (selection.py)
            header, records, end = _session(unit, name, stats, code)
            header["plans_split"] = record["split"]     # data/plans' own split, for the record
            header["selection"] = chosen
            count_session(stats, header, records, end)
            *packed, packed_end = pack(records, end)
            if unpack([*packed, packed_end]) != (records, end):
                raise RuntimeError(f"{name}/shard{number:04d}: packing lost something in "
                                   f"session {header['session']}")
            text = _lines(header, packed, packed_end)
            raw_bytes += len(text)
            whole_bytes += len(_lines(header, records, end))
            f.write(text)
    if code_hash() != code:             # somebody edited the recorder while it was running
        partial.unlink()
        raise RuntimeError(f"{name}/shard{number:04d}: the code changed while it was recorded "
                           f"({code} -> {code_hash()}); the shard was thrown away")
    os.replace(partial, path)           # the shard appears only when it is whole
    stats.update(plans=plans, raw_bytes=raw_bytes, whole_bytes=whole_bytes,
                 gz_bytes=path.stat().st_size)
    result = {"slice": name, "file": f"{name}/{path.name}", "sha256": sha256(path),
              "code_hash": code, "mix": noise.MIX, "selection": chosen,
              "plans_file": f"{Path(plans_file).parent.name}/{Path(plans_file).name}",
              "not_built": not_built,
              "seconds": round(time.perf_counter() - started, 2), "counts": dict(stats)}
    path.with_name(f"shard{number:04d}.stats.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


# --- the command -------------------------------------------------------------------------------

def plan_shards(out_dir: Path, selection_dir: Path = SELECTION_DIR,
                limit_per_slice: int | None = None) -> list[tuple]:
    """Every shard of every slice, in the order of SLICES. Always the same shards."""
    per_file = json.loads((selection_dir / "selection.json").read_text())["plans_per_part"]
    chosen = selection_hash(selection_dir)
    tasks = []
    for name, (folder, _, _, _, at_most) in SLICES.items():
        files = sorted((selection_dir / folder).glob("part*.jsonl.gz"))
        limit = min(at_most or 10 ** 9, limit_per_slice or 10 ** 9)
        files = files[:-(-limit // per_file)]
        tasks += [(name, number, str(path), str(out_dir), chosen)
                  for number, path in enumerate(files)]
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-minutes", type=float, default=None,
                        help="start no new shard after this many minutes (run again to go on)")
    parser.add_argument("--limit-per-slice", type=int, default=None,
                        help="only the first N plans of each slice (for a trial)")
    parser.add_argument("--slices", nargs="*", default=list(SLICES), choices=list(SLICES))
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--selection", default=str(SELECTION_DIR))
    args = parser.parse_args()
    out_dir, selection_dir = Path(args.out), Path(args.selection)
    out_dir.mkdir(parents=True, exist_ok=True)
    config = {"limit_per_slice": args.limit_per_slice,
              "slices": {name: list(value) for name, value in SLICES.items()},
              "mix": noise.MIX, "noise_kinds": noise.NOISE_KINDS,
              "wrong_number_flavours": noise.FLAVOURS,
              "chance_before_done": noise.CHANCE_BEFORE_DONE,
              "chance_at_feature": noise.CHANCE_AT_FEATURE,
              "carry_on": [player.CARRY_ON_START, player.CARRY_ON_WRONG_BODY,
                           player.CARRY_ON_NEXT], "reopen": player.REOPEN,
              "start_kinds": player.START_KINDS, "noise_factor": NOISE_FACTOR,
              "hard_limit": f"{HARD_FACTOR} x clean length + {HARD_EXTRA}",
              "exact_undo": True, "code_hash": code_hash(),
              "selection": selection_hash(selection_dir)}
    run_dir = start_run("freecad-multi-sessions", {**config, **vars(args)})

    tasks = [task for task in plan_shards(out_dir, selection_dir, args.limit_per_slice)
             if task[0] in args.slices]
    planned = Counter(task[0] for task in tasks)
    todo = [task for task in tasks
            if not (out_dir / task[0] / f"shard{task[1]:04d}.stats.json").exists()]
    print(f"{len(tasks)} shards planned {dict(planned)}, {len(tasks) - len(todo)} already "
          f"complete, {len(todo)} to do", flush=True)

    started = time.time()
    done_now: Counter = Counter()
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
                elapsed = time.time() - started
                print(f"[{elapsed / 60:6.1f} min] {shard['file']}  "
                      f"{shard['counts']['sessions']} sessions {shard['counts'].get('steps', 0)} "
                      f"steps in {shard['seconds']:.0f}s | this run: {done_now['sessions']} "
                      f"sessions, {done_now['steps'] / elapsed:.0f} steps/s", flush=True)
                log_metrics(run_dir, shard=shard["file"], seconds=shard["seconds"],
                            sessions=shard["counts"]["sessions"],
                            steps=shard["counts"].get("steps", 0))
            if finished:
                write_manifest(out_dir, config, dict(planned))  # whatever exists is usable
    elapsed = time.time() - started

    manifest = write_manifest(out_dir, config, dict(planned))
    left = sum(1 for task in tasks
               if not (out_dir / task[0] / f"shard{task[1]:04d}.stats.json").exists())
    totals = manifest["totals"]
    log_metrics(run_dir, final=True, wall_seconds=round(elapsed, 1), workers=args.workers,
                sessions_this_run=done_now["sessions"], steps_this_run=done_now["steps"],
                sessions_on_disk=totals["sessions"], steps_on_disk=totals["steps"],
                shards_left=left)
    (run_dir / "NOTES.md").write_text(
        f"# freecad-multi-sessions\n\nStructure sessions (forge/freecad_multi/sessions.py), "
        f"mix {noise.MIX}.\nThis run: {done_now['sessions']} sessions, "
        f"{done_now['steps']} steps in {elapsed / 60:.1f} min with {args.workers} workers. "
        f"Shards left: {left}.\nOn disk now: {totals['sessions']} sessions, {totals['steps']} "
        f"steps.\nCheck with: uv run python -m forge.freecad_multi.audit --dir {out_dir}\n")
    print(f"\nthis run: {done_now['sessions']} sessions, {done_now['steps']} steps in "
          f"{elapsed:.0f}s ({done_now['sessions'] / max(elapsed, 1e-9):.2f} sessions/s, "
          f"{done_now['steps'] / max(elapsed, 1e-9):.0f} steps/s, {args.workers} workers)")
    print(f"on disk:  {totals['sessions']} sessions, {totals['steps']} steps, "
          f"{totals['gz_bytes'] / 1e6:.1f} MB gzip ({totals['gz_bytes_per_step']} bytes/step; "
          f"{totals['raw_bytes_per_step']} before gzip; {totals['whole_bytes_per_step']} if "
          f"every snapshot were stored whole)")
    for name in SLICES:
        info = manifest["slices"][name]
        print(f"  {name:13s} {info['shards']:4d}/{info['shards_planned']:4d} shards "
              f"{info['sessions']:6d} sessions {info['steps']:8d} steps  ended "
              f"{info['sessions_ended']}")
    print(f"undo share of targets: {totals['undo_share']}; sessions with problems: "
          f"{totals['sessions_with_problems']}; ended budget / stuck / error: "
          f"{[totals['sessions_ended'].get(key, 0) for key in ('budget', 'stuck', 'error')]}; "
          f"worker restarts: {totals['worker_restarts']}")
    print(f"shards left: {left}" + ("  (run the same command again to go on)" if left else ""))
    print(f"manifest: {out_dir / 'manifest.json'}\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
