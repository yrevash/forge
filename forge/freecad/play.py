"""Play one session in real FreeCAD: the teacher labels every state, noise sometimes acts.

    header, records, end = play(client, part, seed, split)

One loop, the same at every step:
  1. take the session's snapshot and ask the teacher for the acceptable commands;
  2. pick what is executed: a wrong command (noise.py), the next command of a
     builder who has not noticed his mistake (`carry_on`, below), or else one of
     the teacher's at random;
  3. FreeCAD carries it out, or refuses it; store the record; go to 1.
The session ends when the teacher's `done` is carried out.

Two things happen to a noisy session besides single wrong commands
    carry_on   After a wrong number (wrong_argument) was carried out, the session may go
               on as if nothing were wrong: the rest of that plan item's recipe is issued,
               one command per step, each recorded as noise `carry_on`, while the teacher
               says `undo` at every one of those states. It stops when the recipe ends,
               when FreeCAD refuses a command, or at each step with chance 1 - CARRY_ON_NEXT.
               This is how failed features come about (a pocket above the part, a pad that
               floats), and it buries the mistake under several commands, so the repair is
               a chain of undos and not one.
               The second command of a `failing_feature` (noise.py) is issued the same way.
    reopen     With chance REOPEN at a step where the session is off plan, the undo history
               is forgotten, as if the document had been saved, closed and opened again.
               Nothing can be undone then, so the teacher says `new_document`. It is not a
               command and makes no record of its own: the next record carries
               `"before": "forget_undo"`, so a replay knows when it happened.

The step budget. Noise may strike only during the first NOISE_FACTOR x L steps
(L = the length of a clean build), so after that the teacher alone drives and the
session reaches `done`. Two exits make sure the loop always ends:
    "budget"  HARD_FACTOR x L + HARD_EXTRA steps were used up;
    "stuck"   teacher.Watch saw the teacher's own command fail twice at the same place.
              In the first recording one session in 239,471 did that 150 times, until the
              budget ended it (inside/session.py, "Exact undo", is the cause and the fix).
Neither should happen. Such a session is kept out of training and fails the audit.

Everything random comes from one generator seeded with (seed, part id), so the
same part and seed give the same session.
"""

from __future__ import annotations

import hashlib
import random

from forge.freecad.build import against_stored
from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean
from forge.freecad.noise import (
    MIX,
    NOISE_LEVELS,
    Moment,
    chance_at,
    follow_up,
    is_target,
    wrong_command_with_detail,
)
from forge.freecad.recipes import AnyOrder, context_of, flatten
from forge.freecad.starts import draw_start
from forge.freecad.teacher import (
    Advice,
    Script,
    Watch,
    clean_length,
    plan_json,
    script_of,
    teacher,
)
from forge.system1.steps import Step, steps_of

NOISE_FACTOR = 3    # noisy sessions get three times the clean step budget
HARD_FACTOR = 8
HARD_EXTRA = 40
CARRY_ON_START = 0.2    # after a wrong number: the chance that the builder does not notice
CARRY_ON_NEXT = 0.8     # and at each later step: the chance that he still has not
REOPEN = 0.02           # at an off-plan step: the chance that the undo history is lost

Command = tuple[str, dict]


def session_id(part_id: str, seed: int) -> str:
    return hashlib.sha1(f"freecad:{part_id}:{seed}".encode()).hexdigest()[:16]


def run_start(fc: FreeCADClient, commands: list[tuple[str, dict]], forget: bool,
              ) -> tuple[dict, list[list]]:
    """Put FreeCAD in a start state. Returns (the reply to read step 0 from, what was run)."""
    reply = fc.reset()
    carried_out = []
    for name, args in commands:
        answer = fc.command(name, **args)
        if answer["status"] == "ok":        # a refused start command is simply left out
            reply = answer
            carried_out.append([name, args])
    if forget:
        reply = fc.forget_undo()
    return reply, carried_out


def end_check(plan: list[Step], full_snapshot: dict, measured: dict) -> list[str]:
    """What is wrong with a finished session (nothing, we hope): the solid must be the stored
    one, and the teacher must find the document complete, on plan and clean."""
    problems = against_stored(full_snapshot["solid"], measured)
    advice = teacher(plan, lean(full_snapshot))
    if advice.targets or not advice.on_plan:
        problems.append(f"the document is not clean and complete: {advice.why}")
    return problems


def rest_of_item(script: Script, advice: Advice, executed: str, rng: random.Random,
                 ) -> list[Command]:
    """What a builder issues after `executed` to finish the plan item the teacher is on:
    the dimensions still missing from the open shape, then the item's later commands."""
    if advice.position is None:
        return []
    item, entry = script[advice.position]
    if isinstance(entry, AnyOrder):
        rest = [(target.command, target.args) for target in advice.targets
                if target.command != executed]
        rng.shuffle(rest)
    elif entry.name != executed:    # the wrong command was a selection made FOR this entry
        rest = [(entry.name, dict(entry.args))]
    else:
        rest = []
    later = [e for index, e in script[advice.position + 1:] if index == item]
    return rest + [(command.name, dict(command.args)) for command in flatten(later, rng)]


def play(fc: FreeCADClient, part: dict, seed: int, split: str,
         noise_levels: tuple[float, ...] = NOISE_LEVELS, slice_name: str | None = None,
         code: str | None = None) -> tuple[dict, list[dict], dict]:
    """One session of one part. Returns its header, its step records and its end record.
    `code` is the hash of the code that records it (sessions.code_hash)."""
    plan = steps_of(part["family"], part["params"])
    script, context, length = script_of(plan), context_of(plan), clean_length(plan)
    item_starts = {at for at, (index, _) in enumerate(script)
                   if at == 0 or script[at - 1][0] != index}
    rng = random.Random(f"{seed}:{part['id']}")
    noise = rng.choice(noise_levels)
    start_kind, start_commands, forget = draw_start(rng, plan)
    sid = session_id(part["id"], seed)

    reply, carried_out = run_start(fc, start_commands, forget)
    header = {
        "session": sid, "part_id": part["id"], "family": part["family"], "split": split,
        "slice": slice_name or split, "seed": seed, "noise_level": noise, "clean_length": length,
        "start": {"kind": start_kind, "commands": carried_out, "forget_undo": forget},
        "plan": plan_json(plan),
        # Provenance every training row must carry.
        "source": part["source"], "license": part["license"],
        "generator_version": part["generator_version"],
        # What recorded it: the code, the noise mix, and how the runtime undoes.
        "code_hash": code, "mix": MIX, "runtime": {"exact_undo": fc.exact},
    }
    records: list[dict] = []
    previous = None
    ended = "budget"
    watch = Watch()
    # What a builder who has not noticed his mistake issues next: (command, arguments, kind).
    ghost: list[tuple[str, dict, str]] = []
    reopened = False
    snapshot, valid = lean(reply["snapshot"]), reply["valid"]
    advice = teacher(plan, snapshot, script)
    for t in range(HARD_FACTOR * length + HARD_EXTRA):
        noisy = noise > 0 and t < NOISE_FACTOR * length
        if noisy and not advice.on_plan and snapshot["session"]["undo_depth"] > 0 \
                and rng.random() < REOPEN:
            reply = fc.forget_undo()            # reopen: nothing can be undone any more
            snapshot, valid = lean(reply["snapshot"]), reply["valid"]
            advice = teacher(plan, snapshot, script)
            reopened, ghost = True, []

        wrong = None
        if not noisy:
            ghost = []
        if ghost and rng.random() < CARRY_ON_NEXT and ghost[0][0] in valid \
                and not is_target(ghost[0][:2], advice):
            wrong = (*ghost.pop(0), None)
        else:
            ghost = []
            if noisy and rng.random() < chance_at(noise, advice):
                moment = Moment(plan, context, advice, valid, previous,
                                advice.position in item_starts)
                wrong = wrong_command_with_detail(rng, moment)
        if wrong is not None:
            name, args, kind, detail = wrong
        else:
            target = rng.choice(advice.targets)
            name, args, kind, detail = target.command, target.args, None, None
        reply = fc.command(name, **args)

        executed = {"command": name, "args": args, "noise": kind}
        if detail is not None:
            executed["flavour"] = detail        # which kind of wrong number (noise.FLAVOURS)
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
        # A wrong number (or a wrong plane or rule) that was carried out: maybe carry on.
        if kind == "wrong_argument" and reply["status"] == "ok" and before.on_plan \
                and snapshot != record["snapshot"] and rng.random() < CARRY_ON_START:
            ghost = [(*command, "carry_on") for command in rest_of_item(script, before, name, rng)]
        elif kind is not None and kind != "carry_on" and reply["status"] == "ok" and not ghost:
            ghost = [(*command, kind) for command in follow_up(rng, plan, kind, name)]
    problems = end_check(plan, reply["snapshot"], part["measured"]) if ended == "done" else []
    end = {"session": sid, "end": ended, "steps": len(records),
           "snapshot": lean(reply["snapshot"]), "problems": problems}
    return header, records, end
