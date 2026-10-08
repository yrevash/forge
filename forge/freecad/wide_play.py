"""Play one session of the THIRD mix in real FreeCAD (wide parts, wider starts, more mistakes).

    header, records, end = play(client, part, seed, slice_name, other_plan=...)

The loop is play.py's, and a record has the same fields, so every tool that reads sessions
(load.py, the oracle, baselines.py, show_session.py) reads these too. play.py itself is not
changed. What is different here:

    the plan      is the part's own `plan` (wide rows hold it; nothing is read off names)
    the start     is drawn by wide_starts.py, or GIVEN: `start=(kind, commands, forget)`
                  begins the session in any state a list of commands reaches, for example
                  the state a model drove into. The teacher labels what follows.
    the mistakes  are noise.py's, plus ORDER mistakes, DECIMAL slips and BURSTS
                  (wide_noise.py). A session with bursts says so in its header.
    the budget    counts the start: undoing a deep start takes one step per command.

How an order mistake is played. The wrong item's commands wait in the same queue (`ghost`)
that play.py uses for a builder who has not noticed his wrong number. One difference: a
command of the wrong item that happens to be exactly what the teacher asks for (the first
commands of two holes are the same) is carried out as the teacher's own and the queue goes
on; the mistake starts at the first command that differs.
"""

from __future__ import annotations

import random
from dataclasses import replace

from forge.freecad.catalogue import COMMANDS
from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean
from forge.freecad.noise import NOISE_LEVELS, Moment, chance_at, follow_up, is_target
from forge.freecad.play import (
    CARRY_ON_NEXT,
    CARRY_ON_START,
    HARD_EXTRA,
    HARD_FACTOR,
    NOISE_FACTOR,
    REOPEN,
    end_check,
    rest_of_item,
    run_start,
    session_id,
)
from forge.freecad.recipes import context_of
from forge.freecad.teacher import Watch, clean_length, plan_json, script_of, teacher
from forge.freecad.wide_noise import (
    BURST_LEVELS,
    BURST_MOST,
    BURST_SHARE,
    BURST_START,
    MIX,
    ORDER_CHANCE,
    ORDER_KINDS,
    ORDER_NEXT,
    order_mistake,
    wrong_command,
)
from forge.freecad.wide_starts import TRAIN_STARTS, draw_start
from forge.system1.steps import Step

Command = tuple[str, dict]
Start = tuple[str, list[Command], bool]     # (kind, commands, forget the undo history?)


def play(fc: FreeCADClient, part: dict, seed: int, slice_name: str, *,
         other_plan: list[Step] | None = None, start_kinds: dict[str, float] = TRAIN_STARTS,
         start: Start | None = None, noise_levels: tuple[float, ...] = NOISE_LEVELS,
         code: str | None = None) -> tuple[dict, list[dict], dict]:
    """One session of one wide part. Returns its header, its step records and its end record.

    `part` needs id, family, set, plan, measured, source, license, generator_version.
    `other_plan` is another part's plan, for the starts that show someone else's work.
    """
    plan = [Step(item["kind"], dict(item["slots"])) for item in part["plan"]]
    script, context, length = script_of(plan), context_of(plan), clean_length(plan)
    item_starts = {at for at, (index, _) in enumerate(script)
                   if at == 0 or script[at - 1][0] != index}
    # Where the teacher asks for the FIRST command of a plan item. That command is a selection,
    # and the teacher's walk reports it from the entry after it, so both positions count.
    first_command: dict[int, str] = {}
    for at in item_starts:
        first_command[at] = script[at][1].name
        if not COMMANDS[first_command[at]]["changes_document"]:
            first_command[at + 1] = first_command[at]
    rng = random.Random(f"{seed}:{part['id']}")
    noise = rng.choice(noise_levels)
    burst = noise > 0 and rng.random() < BURST_SHARE
    if burst:
        noise = rng.choice(BURST_LEVELS)
    drawn = draw_start(rng, plan, other_plan or plan, start_kinds)   # drawn even when given,
    start_kind, start_commands, forget = start or drawn             # so the rest stays the same
    sid = session_id(part["id"], seed)

    reply, carried_out = run_start(fc, start_commands, forget)
    header = {
        "session": sid, "part_id": part["id"], "family": part["family"], "split": part["set"],
        "slice": slice_name, "seed": seed, "noise_level": noise, "burst": burst,
        "clean_length": length,
        "start": {"kind": start_kind, "commands": carried_out, "forget_undo": forget},
        "plan": plan_json(plan),
        "source": part["source"], "license": part["license"],
        "generator_version": part["generator_version"],
        "code_hash": code, "mix": MIX, "runtime": {"exact_undo": fc.exact},
    }
    records: list[dict] = []
    previous = None
    ended = "budget"
    watch = Watch()
    # Commands waiting to be issued by a builder who is on the wrong track:
    # (command, arguments, kind of mistake, may it coincide with the teacher's command?).
    ghost: list[tuple[str, dict, str, bool]] = []
    burst_left = 0
    reopened = False
    snapshot, valid = lean(reply["snapshot"]), reply["valid"]
    advice = teacher(plan, snapshot, script)
    for t in range(HARD_FACTOR * length + HARD_EXTRA + len(carried_out)):
        noisy = noise > 0 and t < NOISE_FACTOR * length
        if noisy and not advice.on_plan and snapshot["session"]["undo_depth"] > 0 \
                and rng.random() < REOPEN:
            reply = fc.forget_undo()            # reopen: nothing can be undone any more
            snapshot, valid = lean(reply["snapshot"]), reply["valid"]
            advice = teacher(plan, snapshot, script)
            reopened, ghost = True, []
        if not noisy:
            ghost, burst_left = [], 0

        chosen: tuple[str, dict, str | None, str | None] | None = None
        in_burst = False
        if ghost:
            name, args, kind, lenient = ghost[0]
            go_on = rng.random() < (ORDER_NEXT if lenient else CARRY_ON_NEXT) and name in valid
            if go_on and not is_target((name, args), advice):
                chosen = (name, args, kind, None)
            elif go_on and lenient:
                chosen = (name, args, None, None)       # the teacher's own command, as it happens
            ghost = ghost[1:] if chosen is not None else []
        if chosen is None and noisy \
                and rng.random() < (1.0 if burst_left else chance_at(noise, advice)):
            in_burst, burst_left = burst_left > 0, max(0, burst_left - 1)
            moment = Moment(plan, context, advice, valid, previous, advice.position in item_starts)
            at_start = advice.names() == [first_command.get(advice.position)]
            order = order_mistake(rng, replace(moment, item_start=at_start), script) \
                if at_start and rng.random() < ORDER_CHANCE else None
            if order is not None and order[1][0][0] in valid:
                kind, commands = order
                ghost = [(name, args, kind, True) for name, args in commands[1:]]
                first = commands[0]
                chosen = (*first, None if is_target(first, advice) else kind, None)
            else:
                chosen = wrong_command(rng, moment)
        if chosen is None:
            target = rng.choice(advice.targets)
            chosen = (target.command, target.args, None, None)
        name, args, kind, detail = chosen
        reply = fc.command(name, **args)

        executed = {"command": name, "args": args, "noise": kind}
        if detail is not None:
            executed["flavour"] = detail        # which kind of wrong number
        if in_burst and kind is not None:
            executed["burst"] = True            # one of several mistakes in a row
        record = {
            "session": sid, "t": t, "snapshot": snapshot, "valid": valid,
            "target": [target.to_json() for target in advice.targets],
            "progress": {"on_plan": advice.on_plan, "built": advice.built,
                         "active": advice.active},
            "executed": executed,
            "reply": {key: reply[key] for key in ("status", "reason") if key in reply},
        }
        if reopened:
            record["before"] = "forget_undo"
            reopened = False
        records.append(record)
        previous = (name, dict(args))
        if name == "done" and kind is None and reply["status"] == "ok":
            ended = "done"
            break

        before = advice
        snapshot, valid = lean(reply["snapshot"]), reply["valid"]
        advice = teacher(plan, snapshot, script)
        if watch.stuck(before, name, args, kind is None, reply["status"], advice):
            ended = "stuck"
            break
        carried = kind is not None and reply["status"] == "ok"
        if kind == "wrong_argument" and carried and before.on_plan \
                and snapshot != record["snapshot"] and rng.random() < CARRY_ON_START:
            ghost = [(*command, "carry_on", False)
                     for command in rest_of_item(script, before, name, rng)]
        elif carried and kind != "carry_on" and kind not in ORDER_KINDS and not ghost:
            ghost = [(*command, kind, False) for command in follow_up(rng, plan, kind, name)]
        if burst and carried and not burst_left and rng.random() < BURST_START:
            burst_left = rng.randint(1, BURST_MOST)
    problems = end_check(plan, reply["snapshot"], part["measured"]) if ended == "done" else []
    end = {"session": sid, "end": ended, "steps": len(records),
           "snapshot": lean(reply["snapshot"]), "problems": problems}
    return header, records, end
