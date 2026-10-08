"""Closed loop for all three models, on the old and the wide test slices.

    uv run python -m forge.s1.third.drive --checkpoint checkpoints/s1/s1-third-best.pt \
        --arguments model --seed 0                      # TEACHER-FREE: the third model's headline
    uv run python -m forge.s1.third.drive --checkpoint <any of the three> --arguments teacher

TWO NUMBERS, NEVER TO BE MIXED UP:

    --arguments model     TEACHER-FREE. The model names a command, a plan item and a source
                          for every argument; bindings.resolve turns that into arguments from
                          the PLAN ALONE and FreeCAD carries it out, right or wrong. The
                          teacher is asked only so that the model's mistakes can be COUNTED;
                          nothing it says reaches the model or the command. Third model only.
    --arguments teacher   "Command choice with teacher-supplied arguments", the measure of
                          the first two models (forge/s1/drive.py): when the model names a
                          command the teacher accepts, the teacher's arguments are used.

One episode is forge/s1/drive.py's: a clean start, or a messy start with wrong commands
injected at a rate; the first `done` FreeCAD carries out ends it; success = the stored solid,
checked by play.end_check. What differs here:

    wide slices      their parts carry their own plan; the messy start and the injected
                     mistakes are the wide recorder's (wide_starts.py with the slice's own
                     start kinds, wide_noise.py), so `abnormal_starts_v3` begins in the start
                     states that were kept out of training.
    history          the third model's view holds the last two commands carried out in the
                     episode and FreeCAD's answers (its own, and the injected ones: it is
                     what happened in the session). A start state's commands are not shown,
                     as in the recordings.
    own mistakes     counted by kind: "command" (the teacher does not accept it) or
                     "argument" (the command is accepted, the model's own arguments differ).
    recording        `record` collects every visited state with the teacher's label in the
                     session format (dagger.py); a measurement never records.

Which parts: seed k takes parts k x N .. k x N + N - 1 of one fixed random order of the
slice's parts that have a usable recorded session, exactly as forge/s1/drive.py does, so the
three models get the same parts and the same injected mistakes for the same seed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import random
import time
from pathlib import Path

import torch

from forge.freecad import noise, wide_noise, wide_sessions, wide_starts
from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean
from forge.freecad.load import usable, view
from forge.freecad.noise import Moment, _any_args
from forge.freecad.parts import session_parts
from forge.freecad.play import HARD_EXTRA, HARD_FACTOR, NOISE_FACTOR, end_check, run_start
from forge.freecad.recipes import context_of
from forge.freecad.shards import OUT_DIR, SLICES, TRAIN_SPLITS, complete_shards, read_sessions
from forge.freecad.starts import draw_start
from forge.freecad.teacher import clean_length, plan_json, same, script_of, teacher
from forge.freecad.wide_parts import read_wide, steps_of_part
from forge.runs import log_metrics, start_run
from forge.s1 import vocab
from forge.s1.drive import battery_percent
from forge.s1.encode import encode
from forge.s1.live import rank_commands
from forge.s1.loss import wilson_interval
from forge.s1.third.live import decide
from forge.s1.third.model import build_model
from forge.s1.third.prepare import PREPARED_DIR
from forge.s1.train import load_checkpoint
from forge.system1.steps import Step, steps_of

MAX_WORKERS = 6
_client: FreeCADClient | None = None
_model: torch.nn.Module | None = None
_third = False


def load_any_model(checkpoint: str | Path) -> tuple[torch.nn.Module, bool]:
    """(the model, is it a third model?). A third model's checkpoint lists the `kinds`."""
    state = load_checkpoint(checkpoint)         # data only: forge/s1/train.py
    third = "kinds" in state["vocab"]
    if third:
        model = build_model(state["config"]["model"], state["vocab"])
    else:
        from forge.s1.model import build_model as build_first
        model = build_first(state["config"]["model"], state["vocab"])
    model.load_state_dict(state["model"])
    # A third model trained with random row ids is driven and scored with them (it is what
    # it saw in training). The first two models keep the identity, as they were measured.
    model.trained_with_random_ids = third and bool(state["config"]["train"]["random_ids"])
    return model.eval(), third


def is_wide(slice_name: str) -> bool:
    return slice_name in wide_sessions.SLICES


def plan_of(part: dict) -> list[Step]:
    return steps_of_part(part) if "plan" in part else steps_of(part["family"], part["params"])


def same_args(mine: dict | None, teachers: dict) -> bool:
    return mine is not None and set(mine) == set(teachers) \
        and all(same(mine[name], teachers[name]) for name in mine)


def episode(fc: FreeCADClient, model: torch.nn.Module | None, third: bool, part: dict,
            seed: int, mistakes: float, arguments: str, slice_name: str,
            other: dict | None = None, explore: float = 0.0, temperature: float = 2.0,
            record: list[dict] | None = None, no_repeat: bool = False) -> dict:
    """Build one part. `model` None = the teacher picks (checks the harness: must score 100%).
    `explore` > 0 (DAgger only): at that share of the early steps the model's choice is drawn
    from its own probabilities at `temperature`.
    `no_repeat` (a DRIVER-SIDE EXTRA, never the headline; teacher-free only): the model may
    not carry out a (FreeCAD state, command, arguments) triple it has already carried out in
    this episode; its next-best command is taken instead."""
    plan = plan_of(part)
    script, context, length = script_of(plan), context_of(plan), clean_length(plan)
    seen_plan = plan_json(plan)
    item_starts = {at for at, (index, _) in enumerate(script)
                   if at == 0 or script[at - 1][0] != index}
    rng = random.Random(f"drive:{seed}:{part['id']}")
    wander = random.Random(f"explore:{seed}:{part['id']}")      # apart, so `rng` is untouched
    wide = is_wide(slice_name)
    ids = torch.Generator().manual_seed(
        int(hashlib.sha1(f"ids:{seed}:{part['id']}".encode()).hexdigest()[:12], 16)) \
        if getattr(model, "trained_with_random_ids", False) else None
    if mistakes > 0 and wide:
        start_kind, commands, forget = wide_starts.draw_start(
            rng, plan, plan_of(other) if other else plan, wide_sessions.SLICES[slice_name][1])
        reply, _ = run_start(fc, commands, forget)
    elif mistakes > 0:
        start_kind, commands, forget = draw_start(rng, plan)
        reply, _ = run_start(fc, commands, forget)
    else:
        start_kind, reply = "empty", fc.reset()

    out = {"part_id": part["id"], "plan_items": len(plan), "clean_length": length,
           "start": start_kind, "steps": 0, "model_mistakes": 0, "command_mistakes": 0,
           "argument_mistakes": 0, "injected": 0, "refused": 0, "said_done": False,
           "first_mistakes": [], "explored": 0, "repeats": 0, "redirected": 0}
    previous, history, been_here = None, [], set()
    taken: set[tuple[bytes, str, str]] = set()      # (state, command, arguments) the model did
    for _ in range(HARD_FACTOR * length + HARD_EXTRA):
        snapshot, valid = lean(reply["snapshot"]), reply["valid"]
        # What FreeCAD shows, without the dice: "the model was here before".
        where = hashlib.sha1(repr((json.dumps(snapshot, sort_keys=True), valid)).encode()).digest()
        state = hashlib.sha1(repr((json.dumps(snapshot, sort_keys=True), valid, rng.getstate(),
                                   wander.getstate(), history[-vocab.HISTORY:],
                                   None if ids is None else ids.get_state().tolist(),
                                   )).encode()).digest()
        # The same state, the same dice: it can only repeat. (With random row ids the dice
        # never repeat, so this never fires for such a model; `repeats` below is the measure
        # that holds for every model.) Under `no_repeat` a repeated state gets another action.
        if state in been_here and not no_repeat:
            out["cycle"] = True
            break
        been_here.add(state)
        advice = teacher(plan, snapshot, script)        # for counting and injected mistakes only

        def done_before(command: str, own: dict | None, where: bytes = where) -> bool:
            return (where, command, json.dumps(own or {}, sort_keys=True, default=str)) in taken

        wrong = None
        if mistakes > 0 and out["steps"] < NOISE_FACTOR * length and rng.random() < mistakes:
            moment = Moment(plan, context, advice, valid, previous,
                            advice.position in item_starts)
            wrong = (wide_noise if wide else noise).wrong_command(rng, moment)
        kind = None
        if wrong is not None:                           # an injected mistake: not the model's
            name, args, kind = wrong[0], wrong[1], wrong[2]
            out["injected"] += 1
        else:
            if model is None:
                target = rng.choice(advice.targets)
                name, own_args = target.command, target.args
            elif third:
                wandering = explore > 0 and out["steps"] < NOISE_FACTOR * length \
                    and wander.random() < explore
                out["explored"] += wandering
                first_choice = decide(model, view(seen_plan, snapshot, valid,
                                                  history[-vocab.HISTORY:]),
                                      explore=(wander, temperature) if wandering else None,
                                      ids=ids, avoid=done_before if no_repeat else None)
                decision = first_choice
                out["redirected"] += decision.command != decision.ranked[0] and not wandering
                name, own_args = decision.command, decision.args
            else:
                name = rank_commands(model, encode(view(seen_plan, snapshot, valid)), "cpu")[0]
                own_args = None
            accepted = [target for target in advice.targets if target.command == name]
            if arguments == "teacher":
                # The first two models' measure: the teacher's arguments for a right command,
                # arguments drawn from the plan's numbers for a wrong one.
                args = accepted[0].args if accepted else _any_args(rng, name, plan)
                fault = None if accepted else "command"
            else:                                       # teacher-free: the model's own arguments
                args = own_args if own_args is not None else {}
                fault = "command" if not accepted else \
                    None if same_args(own_args, accepted[0].args) else "argument"
            if fault is not None:
                kind = "model"
                out["model_mistakes"] += 1
                out[f"{fault}_mistakes"] += 1
                if len(out["first_mistakes"]) < 5:      # kept, to see what kind of mistake it is
                    out["first_mistakes"].append(
                        {"step": out["steps"], "fault": fault, "chose": name, "args": args,
                         "teacher": [target.to_json() for target in advice.targets][:3],
                         "on_plan": advice.on_plan, "built": advice.built})
        if wrong is None:               # a choice of the driver's own (not an injected one)
            triple = (where, name, json.dumps(args, sort_keys=True, default=str))
            out["repeats"] += triple in taken       # it did exactly this, exactly here, before
            taken.add(triple)
        reply = fc.command(name, **args)
        if record is not None:
            record.append({"t": out["steps"], "snapshot": snapshot, "valid": valid,
                           "target": [target.to_json() for target in advice.targets],
                           "progress": {"on_plan": advice.on_plan, "built": advice.built,
                                        "active": advice.active},
                           "executed": {"command": name, "args": args, "noise": kind},
                           "reply": {"status": reply["status"]}})
        out["steps"] += 1
        out["refused"] += reply["status"] != "ok"
        previous = (name, dict(args))
        history.append((name, reply["status"]))
        if name == "done" and wrong is None and reply["status"] == "ok":
            out["said_done"] = True
            break
    problems = end_check(plan, reply["snapshot"], part["measured"]) if out["said_done"] \
        else ["a state cycle: the step budget would run out before `done`" if out.get("cycle")
              else "the step budget ran out before `done`"]
    out["end_snapshot"] = lean(reply["snapshot"]) if record is not None else None
    return {**out, "success": not problems, "problems": problems[:3]}


def _start_worker(checkpoint: str | None) -> None:
    global _client, _model, _third
    torch.set_num_threads(1)
    _model, _third = load_any_model(checkpoint) if checkpoint else (None, False)
    _client = FreeCADClient(exact_undo=True)
    _client.start()


def _run(task: dict) -> dict:
    """One episode in this worker's FreeCAD. FreeCAD itself may fail once: start it again."""
    global _client
    part = task["part"]
    for _ in range(2):
        try:
            result = episode(_client, _model, _third, part, task["seed"], task["mistakes"],
                             task["arguments"], task["slice"], task.get("other"),
                             no_repeat=task.get("no_repeat", False))
            break
        except Exception as error:      # noqa: BLE001 - a lost worker, a broken pipe
            result = {"part_id": part["id"], "success": False, "said_done": False,
                      "plan_items": len(plan_of(part)),
                      "problems": [f"harness: {type(error).__name__}: {error}"], "harness": True}
            _client.close()
            _client = FreeCADClient(exact_undo=True)
            _client.start()
    result.pop("end_snapshot", None)
    return {**result, "slice": task["slice"], "mistakes": task["mistakes"]}


def usable_parts(name: str) -> list[str]:
    """The part ids of a slice's usable sessions, in recorded order (cached in a small file)."""
    folder = wide_sessions.OUT_DIR if is_wide(name) else OUT_DIR
    shards = complete_shards(folder, (name,))
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


def parts_by_id(name: str) -> dict[str, dict]:
    """Every part of the set a slice is recorded from, by id."""
    if is_wide(name):
        return {part["id"]: part for part in read_wide(wide_sessions.SLICES[name][0])}
    return {part["id"]: part for part in session_parts(added=True)}


def tasks_of_slice(name: str, count: int, offset: int, seed: int, rates: list[float],
                   arguments: str) -> list[dict]:
    """`count` parts of a slice from place `offset` of its fixed random order, per condition.
    The "other part" of a wide messy start is the next part of that order."""
    by_id = parts_by_id(name)
    ids = [part_id for part_id in dict.fromkeys(usable_parts(name)) if part_id in by_id]
    random.Random(f"drive-parts:{name}").shuffle(ids)
    chosen = ids[offset:offset + count]
    return [{"part": by_id[part_id], "other": by_id[ids[(offset + place + 1) % len(ids)]],
             "seed": seed, "mistakes": rate, "arguments": arguments, "slice": name}
            for rate in rates for place, part_id in enumerate(chosen)]


def is_training_slice(name: str) -> bool:
    return name in wide_sessions.TRAIN_SLICES or \
        (name in SLICES and SLICES[name][0] in TRAIN_SPLITS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--checkpoint", default=None, help="none: the teacher drives")
    parser.add_argument("--arguments", choices=("model", "teacher"), required=True)
    parser.add_argument("--slices", nargs="+",
                        default=["iid_v2", "pairing_v2", "long_v2", "xlong_v2", "wide_iid_v3",
                                 "wide_numbers_v3", "wide_order_v3", "abnormal_starts_v3"])
    parser.add_argument("--parts", type=int, default=100, help="parts per slice and condition")
    parser.add_argument("--mistakes", type=float, nargs="+", default=[0.0, 0.1])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--no-repeat", action="store_true",
                        help="driver-side extra: never the same (state, command, arguments) twice")
    args = parser.parse_args()
    if args.no_repeat and args.arguments != "model":
        raise SystemExit("--no-repeat is defined for --arguments model only")
    if args.workers > MAX_WORKERS:
        raise SystemExit(f"at most {MAX_WORKERS} FreeCAD workers")
    for name in args.slices:
        if is_training_slice(name):
            raise SystemExit(f"{name} is a training slice: closed loop is measured on tests")
    third = bool(args.checkpoint) and load_any_model(args.checkpoint)[1]
    if args.arguments == "model" and args.checkpoint and not third:
        raise SystemExit("--arguments model needs a third model: the others name no arguments")
    what = "TEACHER-FREE (the model's own arguments)" if args.arguments == "model" \
        else "command choice with teacher-supplied arguments"
    if args.no_repeat:
        what += " PLUS THE DRIVER'S NO-REPEAT RULE (an extra, not the model alone)"
    run_dir = start_run(f"s1-drive3-{args.arguments}", vars(args))
    tasks = [{**task, "no_repeat": args.no_repeat} for name in args.slices
             for task in tasks_of_slice(name, args.parts, args.seed * args.parts, args.seed,
                                        args.mistakes, args.arguments)]
    results, started = [], time.time()
    with multiprocessing.Pool(args.workers, initializer=_start_worker,
                              initargs=(args.checkpoint,)) as pool, \
            (run_dir / "episodes.jsonl").open("w") as f:
        for at in range(0, len(tasks), 60):         # in small lots, so the battery is checked
            level = battery_percent()
            if level is not None and level < 30:
                print(f"battery at {level}%: stopping before part {at} of {len(tasks)}")
                break
            for result in pool.imap_unordered(_run, tasks[at:at + 60]):
                results.append(result)
                f.write(json.dumps(result) + "\n")
            f.flush()
            print(f"{len(results)}/{len(tasks)} episodes, {time.time() - started:.0f} s",
                  flush=True)

    heading = ("| Slice | Condition | Parts | Built right | 95% interval | Own mistakes per "
               "part (command / argument) | Steps per part |")
    lines = [heading, "| --- | --- | ---: | ---: | --- | --- | ---: |"]
    for name in args.slices:
        for rate in args.mistakes:
            lot = [r for r in results if r["slice"] == name and r["mistakes"] == rate]
            if not lot:
                continue
            wins = sum(r["success"] for r in lot)
            low, high = wilson_interval(wins, len(lot))
            scored = [r for r in lot if "steps" in r]
            mean = {key: sum(r[key] for r in scored) / max(1, len(scored))
                    for key in ("command_mistakes", "argument_mistakes", "steps")}
            log_metrics(run_dir, checkpoint=args.checkpoint, arguments=args.arguments,
                        slice=name, mistakes=rate, parts=len(lot), built_right=wins,
                        success=wins / len(lot), low=low, high=high,
                        harness_failures=sum(1 for r in lot if r.get("harness")), **mean)
            condition = "clean start" if rate == 0 else f"messy start, mistakes at {rate}"
            lines.append(f"| {name} | {condition} | {len(lot)} | {wins} ({wins / len(lot):.3f}) "
                         f"| {low:.3f} to {high:.3f} | {mean['command_mistakes']:.2f} / "
                         f"{mean['argument_mistakes']:.2f} | {mean['steps']:.1f} |")
    text = "\n".join(lines) + "\n"
    (run_dir / "table.md").write_text(
        f"Checkpoint: {args.checkpoint}. Seed {args.seed}. WHAT THIS IS: {what}.\n\n{text}")
    print(f"\n{what}\n{text}\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
