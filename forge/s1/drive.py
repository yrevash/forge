"""Closed loop: the model drives real FreeCAD, and the finished part is checked.

    uv run python -m forge.s1.drive --checkpoint checkpoints/s1/s1-first-best.pt --parts 100

This is the headline measure. Step accuracy asks "would the model
have agreed with the teacher on recorded states?". Here the model's own choices decide
which states come next, so one bad choice has consequences it must then repair.

One part, one episode:
    1. FreeCAD is reset (condition "clean") or put in a messy start state and fed wrong
       commands at a given rate (condition "mistakes"; the starts and the wrong commands are
       those of the recorder: forge/freecad/starts.py and noise.py).
    2. At every step the model sees `load.view(plan, snapshot, valid)`, exactly what it saw in
       training, and picks a command name.
    3. ARGUMENTS ARE FILLED BY CODE. If the name is in the teacher's set, the
       teacher's arguments are used. If it is not, that step is a MODEL MISTAKE: it is
       counted, and the command is still executed, with arguments drawn from the plan's
       numbers by noise._any_args. So this run measures command choice and recovery, not
       whether the model can fill in numbers.
    4. The episode ends at the first `done` FreeCAD carries out, or when the step budget of
       play.py (8 x clean length + 40) is used up. It is also ended, as a failure, when it
       is certain that the budget would run out: the whole state (FreeCAD's snapshot with
       its undo depth, the valid commands, the random generator, the driver rule's memory)
       is exactly one it was in before. The model and FreeCAD are deterministic, so from
       there the episode can only repeat itself. This saves time and changes no result
       (checked: the first model, seed 0, with and without it).
    5. Success = it ended with `done` and play.end_check finds the stored solid, complete
       and clean.

WHAT THIS NUMBER IS: "command choice with teacher-supplied arguments". The model cannot
make a wrong-number mistake of its own here, and cannot build anything without the teacher.

WHICH PARTS. The parts of a slice (those with a usable recorded session) are put in one
fixed random order (seeded by the slice name), and seed k takes parts k x N to k x N + N - 1
of that order. So every seed is a random sample of the slice, the seeds share no part, and
every model run with the same seed gets the same parts. The seed also drives the messy
starts and the injected mistakes. `--offset` overrides where the sample starts.
(The first model's run of 7 Oct 2026 took the first 100 recorded sessions of each slice
instead; `--first-sessions` does that again, to reproduce it.)

`--random-ids` gives the model random row ids at every step, drawn from a
generator seeded by (seed, part). Use it for a model that was trained with the repaired
random ids: on held-out TRAINING shards the second model is clearly better with them than
with the identity. The first model is driven with the identity, as measured then.

`--policy rule` drives with the plan-blind rule of forge/freecad/baselines.py (the most
frequent teacher command for what the screen looks like, fitted on 20 shards of train_v2
exactly as baselines_v2.log was). It never reads the plan: it is the floor.

`--no-repeat-undo` is NOT the model. It is a rule in this driver, measured separately and
never the headline: when the model's first choice is `undo` in a view IDENTICAL
to the view in which it last chose `undo`, the driver takes the model's next-best command
instead. It exists to measure how much of the failure is the one loop "undo a correct
state, redo it, undo it again". The model's input is the same both times, so without the
rule its answer is the same both times, for ever.

`--policy teacher` runs the scripted teacher through the same loop. It should score 100%;
if it does not, the harness is wrong, not the model.

FreeCAD runs headless in at most 5 worker processes; no window is opened. The run stops
before starting new parts when the battery is below 30%.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import random
import subprocess
import time

import torch

from forge.freecad.baselines import fit, keys_of
from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean
from forge.freecad.load import usable, view
from forge.freecad.noise import Moment, _any_args, wrong_command
from forge.freecad.parts import session_parts
from forge.freecad.play import HARD_EXTRA, HARD_FACTOR, NOISE_FACTOR, end_check, run_start
from forge.freecad.recipes import context_of
from forge.freecad.shards import OUT_DIR, SLICES, TRAIN_SPLITS, complete_shards, read_sessions
from forge.freecad.starts import draw_start
from forge.freecad.teacher import clean_length, plan_json, script_of, teacher
from forge.runs import log_metrics, start_run
from forge.s1.encode import encode
from forge.s1.evaluate import load_model
from forge.s1.live import rank_commands
from forge.s1.loss import closed_loop_success, wilson_interval
from forge.s1.prepare import PREPARED_DIR
from forge.system1.steps import steps_of

MIN_BATTERY = 30
_client: FreeCADClient | None = None
_model: torch.nn.Module | None = None
_rule: dict | None = None             # the plan-blind lookup tables (--policy rule)
_random_ids = False


def shards_split(slice_name: str) -> str:
    return SLICES[slice_name][0]


def battery_percent() -> int | None:
    """The Mac's battery level, or None where there is no `pmset`."""
    try:
        text = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True,
                              check=False).stdout
        return int(text.split("%")[0].split()[-1])
    except (OSError, ValueError, IndexError):
        return None


def first_choice(ranked: list[str], seen: dict, undone_at: dict | None, no_repeat_undo: bool,
                 ) -> tuple[str, dict | None]:
    """(the command to run, the view in which the model last chose `undo`).

    Without the rule: the model's first choice, always. With `no_repeat_undo`: if the first
    choice is `undo` and this view is identical to the one in which the model last chose
    `undo`, its second choice (that is the driver's doing, not the model's)."""
    if ranked[0] != "undo":
        return ranked[0], undone_at
    if no_repeat_undo and seen == undone_at and len(ranked) > 1:
        return ranked[1], undone_at
    return "undo", seen


def episode(fc: FreeCADClient, model: torch.nn.Module | None, part: dict, seed: int,
            mistakes: float, no_repeat_undo: bool = False, rule: dict | None = None,
            random_ids: bool = False) -> dict:
    """Build one part. `model` None = the teacher picks. `mistakes` > 0 = a messy start and
    wrong commands injected at that rate during the first 3 x (clean length) steps.
    `no_repeat_undo`: the driver-side rule described at the top of this file."""
    plan = steps_of(part["family"], part["params"])
    script, context, length = script_of(plan), context_of(plan), clean_length(plan)
    item_starts = {at for at, (index, _) in enumerate(script)
                   if at == 0 or script[at - 1][0] != index}
    rng = random.Random(f"drive:{seed}:{part['id']}")
    # Its own generator, seeded apart from `rng`, so that turning it on changes nothing else.
    ids = torch.Generator().manual_seed(
        int(hashlib.sha1(f"ids:{seed}:{part['id']}".encode()).hexdigest()[:12], 16)) \
        if random_ids else None
    if mistakes > 0:
        start_kind, commands, forget = draw_start(rng, plan)
        reply, _ = run_start(fc, commands, forget)
    else:
        start_kind, reply = "empty", fc.reset()

    out = {"part_id": part["id"], "plan_items": len(plan), "clean_length": length,
           "start": start_kind, "steps": 0, "model_mistakes": 0, "injected": 0, "refused": 0,
           "said_done": False, "first_mistakes": [], "undo_refused_by_rule": 0}
    previous = None
    undone_at = None            # the view in which the model last chose `undo`
    been_here: set[bytes] = set()
    for _ in range(HARD_FACTOR * length + HARD_EXTRA):
        snapshot, valid = lean(reply["snapshot"]), reply["valid"]
        state = hashlib.sha1(repr((json.dumps(snapshot, sort_keys=True), valid, rng.getstate(),
                                   json.dumps(undone_at, sort_keys=True), previous,
                                   None if ids is None else ids.get_state().tolist(),
                                   )).encode()).digest()
        if state in been_here:                      # nothing can change any more
            out["cycle"] = True
            break
        been_here.add(state)
        advice = teacher(plan, snapshot, script)
        wrong = None
        if mistakes > 0 and out["steps"] < NOISE_FACTOR * length and rng.random() < mistakes:
            wrong = wrong_command(rng, Moment(plan, context, advice, valid, previous,
                                              advice.position in item_starts))
        if wrong is not None:                       # an injected mistake: not the model's
            name, args = wrong[0], wrong[1]
            out["injected"] += 1
        else:
            if rule is not None:            # the plan-blind rule: the screen only
                key = keys_of(view(plan_json(plan), snapshot, valid),
                              previous[0] if previous else None)["plan_blind_state"]
                name = rule["plan_blind_state"].get(key, rule["overall"][None])
            elif model is None:
                name = rng.choice(advice.targets).command
            else:
                seen = view(plan_json(plan), snapshot, valid)
                ranked = rank_commands(model, encode(seen), "cpu", ids)
                name, undone_at = first_choice(ranked, seen, undone_at, no_repeat_undo)
                out["undo_refused_by_rule"] += name != ranked[0]
            accepted = [target for target in advice.targets if target.command == name]
            if accepted:
                args = accepted[0].args
            else:                                   # the model's own mistake
                out["model_mistakes"] += 1
                if len(out["first_mistakes"]) < 5:  # kept, to see what kind of mistake it is
                    out["first_mistakes"].append(
                        {"step": out["steps"], "chose": name, "teacher": advice.names(),
                         "on_plan": advice.on_plan, "built": advice.built,
                         "objects": len(snapshot["items"])})
                args = _any_args(rng, name, plan)
        reply = fc.command(name, **args)
        out["steps"] += 1
        out["refused"] += reply["status"] != "ok"
        previous = (name, dict(args))
        if name == "done" and wrong is None and reply["status"] == "ok":
            out["said_done"] = True
            break
    problems = end_check(plan, reply["snapshot"], part["measured"]) if out["said_done"] \
        else ["a state cycle: the step budget would run out before `done`" if out.get("cycle")
              else "the step budget ran out before `done`"]
    return {**out, "success": not problems, "problems": problems[:3]}


def _start_worker(checkpoint: str | None, rule: dict | None = None,
                  random_ids: bool = False) -> None:
    global _client, _model, _rule, _random_ids
    _rule, _random_ids = rule, random_ids
    torch.set_num_threads(1)
    _model = load_model(checkpoint, "cpu")[0] if checkpoint else None
    _client = FreeCADClient(exact_undo=True)
    _client.start()


def _run(task: tuple[dict, int, float, str, bool]) -> dict:
    global _client
    part, seed, mistakes, slice_name, no_repeat_undo = task
    for _ in range(2):                              # FreeCAD itself may fail once: start again
        try:
            result = episode(_client, _model, part, seed, mistakes, no_repeat_undo, _rule,
                             _random_ids)
            break
        except Exception as error:      # noqa: BLE001 - a lost worker, a broken pipe
            result = {"part_id": part["id"], "success": False, "said_done": False,
                      "problems": [f"harness: {type(error).__name__}: {error}"], "harness": True}
            _client.close()
            _client = FreeCADClient(exact_undo=True)
            _client.start()
    return {**result, "slice": slice_name, "mistakes": mistakes}


def usable_parts(name: str) -> list[str]:
    """The part ids of a slice's usable sessions, in recorded order (kept in a small file
    beside the prepared arrays, because finding them means reading every shard)."""
    shards = complete_shards(OUT_DIR, (name,))
    key = [stats["sha256"] for stats, _ in shards]
    cache = PREPARED_DIR / "drive_parts" / f"{name}.json"
    if cache.exists():
        stored = json.loads(cache.read_text())
        if stored["shards"] == key:
            return stored["parts"]
    parts = [header["part_id"] for _, path in shards
             for header, _, end in read_sessions(path) if usable(end)]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"shards": key, "parts": parts}))
    return parts


def parts_of_slice(name: str, count: int, by_id: dict[str, dict], offset: int = 0,
                   first_sessions: bool = False) -> list[dict]:
    """`count` parts of a test slice: places `offset` .. `offset + count - 1` of one fixed
    random order of its parts (or of the recorded order, with `first_sessions`)."""
    ids = [part_id for part_id in usable_parts(name) if part_id in by_id]
    if not first_sessions:
        random.Random(f"drive-parts:{name}").shuffle(ids)
    return [by_id[part_id] for part_id in ids[offset:offset + count]]


def fit_rule(workers: int) -> dict:
    """The plan-blind lookup tables, fitted as forge/freecad/baselines.py fits them."""
    of_fit = [str(path) for _, path in complete_shards(OUT_DIR, ("train_v2",))]
    rest = of_fit[:len(of_fit) - 3]
    tables = fit(rest[::max(1, len(rest) // 20)][:20], workers)
    return {"plan_blind_state": tables["plan_blind_state"], "overall": tables["overall"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--policy", choices=("model", "teacher", "rule"), default="model")
    parser.add_argument("--slices", nargs="+", default=["iid_v2", "pairing_v2", "long_v2"])
    parser.add_argument("--parts", type=int, default=100, help="parts per slice and condition")
    parser.add_argument("--mistakes", type=float, nargs="+", default=[0.0, 0.1],
                        help="conditions: 0 = clean start, no injected mistakes")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--offset", type=int, default=None,
                        help="where the sample starts in the slice's random order of parts "
                             "(default: seed x parts)")
    parser.add_argument("--random-ids", action="store_true",
                        help="random row ids at every step (for a model trained with them)")
    parser.add_argument("--first-sessions", action="store_true",
                        help="the first recorded sessions instead of a random sample (how "
                             "the first model's run of 7 Oct 2026 chose its parts)")
    parser.add_argument("--no-repeat-undo", action="store_true",
                        help="driver-side rule, measured separately (see the top of this file)")
    args = parser.parse_args()
    if args.policy == "model" and not args.checkpoint:
        raise SystemExit("--checkpoint is needed for --policy model")
    if args.workers > 5:
        raise SystemExit("at most 5 FreeCAD workers")
    for name in args.slices:
        if shards_split(name) in TRAIN_SPLITS:
            raise SystemExit(f"{name} is a training slice: closed loop is measured on tests")
    if args.offset is None:
        args.offset = 0 if args.first_sessions else args.seed * args.parts
    run_dir = start_run(f"s1-drive-{args.policy}", vars(args))

    by_id = {part["id"]: part for part in session_parts(added=True)}
    tasks = [(part, args.seed, rate, name, args.no_repeat_undo) for name in args.slices
             for rate in args.mistakes
             for part in parts_of_slice(name, args.parts, by_id, args.offset,
                                        args.first_sessions)]
    rule = fit_rule(args.workers) if args.policy == "rule" else None
    checkpoint = args.checkpoint if args.policy == "model" else None
    results, started = [], time.time()
    with multiprocessing.Pool(args.workers, initializer=_start_worker,
                              initargs=(checkpoint, rule, args.random_ids)) as pool, \
            (run_dir / "episodes.jsonl").open("w") as f:
        for at in range(0, len(tasks), 30):         # in small lots, so the battery is checked
            level = battery_percent()
            if level is not None and level < MIN_BATTERY:
                print(f"battery at {level}%: stopping before part {at} of {len(tasks)}")
                break
            for result in pool.imap_unordered(_run, tasks[at:at + 30]):
                results.append(result)
                f.write(json.dumps(result) + "\n")
            f.flush()
            print(f"{len(results)}/{len(tasks)} episodes, {time.time() - started:.0f} s",
                  flush=True)

    heading = ("| Slice | Condition | Parts | Built right | 95% interval | Said done | "
               "Model mistakes per part | Steps per part |")
    lines = [heading, "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |"]
    for name in args.slices:
        for rate in args.mistakes:
            lot = [r for r in results if r["slice"] == name and r["mistakes"] == rate]
            if not lot:
                continue
            wins = sum(r["success"] for r in lot)
            low, high = wilson_interval(wins, len(lot))
            scored = [r for r in lot if "steps" in r]
            row = {"slice": name, "mistakes": rate, "parts": len(lot), "built_right": wins,
                   "success": closed_loop_success([r["success"] for r in lot]),
                   "low": low, "high": high, "said_done": sum(r["said_done"] for r in lot),
                   "harness_failures": sum(1 for r in lot if r.get("harness")),
                   "model_mistakes_per_part":
                       sum(r["model_mistakes"] for r in scored) / max(1, len(scored)),
                   "steps_per_part": sum(r["steps"] for r in scored) / max(1, len(scored))}
            log_metrics(run_dir, policy=args.policy, checkpoint=args.checkpoint, **row)
            condition = "clean start" if rate == 0 else f"messy start, mistakes at {rate}"
            lines.append(f"| {name} | {condition} | {len(lot)} | {wins} ({row['success']:.3f}) | "
                         f"{low:.3f} to {high:.3f} | {row['said_done']} | "
                         f"{row['model_mistakes_per_part']:.2f} | {row['steps_per_part']:.1f} |")
    text = "\n".join(lines) + "\n"
    (run_dir / "table.md").write_text(f"Policy: {args.policy}. Checkpoint: {args.checkpoint}. "
                                      f"Seed {args.seed}, parts {args.offset} to "
                                      f"{args.offset + args.parts - 1} of each slice's "
                                      f"{'recorded' if args.first_sessions else 'random'} "
                                      f"order. Row ids: "
                                      f"{'random' if args.random_ids else 'identity'}. "
                                      f"Command choice with teacher-supplied arguments."
                                      + (" DRIVER RULE --no-repeat-undo IS ON: this is not "
                                         "the model alone." if args.no_repeat_undo else "")
                                      + "\n\n" + text)
    print("\n" + text + f"\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
