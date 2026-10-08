"""Play one structure session in real FreeCAD: the teacher labels every state, noise sometimes acts.

    header, records, end = play(client, unit, seed)        # unit: sources.Unit

The loop is forge/freecad/play.py's (second mix), with the structure teacher:
  1. take the session's snapshot and ask the teacher for the acceptable commands;
  2. pick what is executed: a wrong command (noise.py), the next command of a builder who
     has not noticed his mistake (`carry_on`), or else one of the teacher's at random;
  3. FreeCAD carries it out, or refuses it; store the record; go to 1.
The session ends when the teacher's `done` is carried out.

Besides single wrong commands, as in the single-part sessions:
    carry_on   after a wrong number, a wrong side or a wrong `activate_body` was carried
               out, the session may go on as if nothing were wrong: the rest of that plan
               item is issued, one command per step, each recorded as noise `carry_on`.
               This is how a feature lands on the wrong body or the wrong side, how a
               wrongly sized body gets placed, and why the repair is a chain of undos.
    reopen     with chance REOPEN at an off-plan step the undo history is forgotten, as
               if the file had been closed and opened. Nothing can be undone, so the
               teacher says `new_document`. The next record carries `"before":
               "forget_undo"` so a replay knows when it happened.

THE LOOP ALWAYS ENDS. Noise may strike only during the first NOISE_FACTOR x L steps (L =
the length of a clean build); after that the teacher alone drives. Two exits remain:
    "stuck"   the teacher's own command, issued on an on-plan state, failed twice at the
              same place (teacher.Watch of forge/freecad): the teacher cannot make progress;
    "budget"  HARD_FACTOR x L + HARD_EXTRA steps were used up.
Neither should happen. Such a session is never training data and FAILS the audit.

The plan of a session is a `sources.Unit`: a record of data/plans (generator output with
`source`, `license` and `generator_version`, the provenance every training row carries),
resolved to exact parts.

Everything random comes from one generator seeded with (seed, plan id).
"""

from __future__ import annotations

import hashlib
import random

from forge.freecad.client import FreeCADClient
from forge.freecad.noise import is_target
from forge.freecad.play import HARD_EXTRA, HARD_FACTOR, NOISE_FACTOR, rest_of_item, run_start
from forge.freecad.teacher import Watch
from forge.freecad_multi.build import compare_with_resolver, compare_with_row
from forge.freecad_multi.lean import lean
from forge.freecad_multi.noise import (
    MIX,
    NOISE_LEVELS,
    WRONG_NUMBERS,
    Moment,
    chance_at,
    follow_up,
    numbers_of,
    wrong_command,
)
from forge.freecad_multi.recipes import Item, flatten, script_of, structure_items
from forge.freecad_multi.sources import Unit
from forge.freecad_multi.teacher import clean_length, places_of, teacher
from forge.resolve.bodies import Body

__all__ = [
    "CARRY_ON_NEXT",
    "CARRY_ON_START",
    "HARD_EXTRA",
    "HARD_FACTOR",
    "NOISE_FACTOR",
    "REOPEN",
    "START_KINDS",
    "end_check",
    "plan_json",
    "play",
    "session_id",
]

START_KINDS = {"empty": 0.60, "document": 0.10, "partial": 0.15, "stray_body": 0.10,
               "opened_partial": 0.05}
CARRY_ON_START = 0.25   # after a wrong number: the chance that the builder does not notice
CARRY_ON_WRONG_BODY = 0.6   # after a wrong activate_body or a wrong side: more often, or a
                            # feature on the wrong body would hardly ever be seen
CARRY_ON_NEXT = 0.8     # and at each later step: the chance that he still has not
REOPEN = 0.004          # at an off-plan step: the chance that the undo history is lost. A fifth of
                        # the single-part value: a structure session has many more off-plan steps,
                        # and each loss means building the whole structure again (the trial with
                        # 0.02 lost the history 1.4 times per session)
ORIGIN = [0.0, 0.0, 0.0]
Command = tuple[str, dict]


def session_id(plan_id: str, seed: int) -> str:
    return hashlib.sha1(f"freecad_multi:{plan_id}:{seed}".encode()).hexdigest()[:16]


def _drawn(item: Item) -> dict:
    """The numbers of a part's shape that are not one of its sizes: the resolver's outline
    of a bar or wedge (and the plane a wedge is drawn on), a pipe bar's bore, an odd
    prism's corner circle and the height of its axis."""
    drawn: dict = {}
    for command in flatten(item.entries):
        if command.name == "select_plane" and command.args["plane"] != "XY":
            drawn["plane"] = command.args["plane"]
        for arg, source in command.sources.items():
            if source.startswith(("standing:", "derived:")):
                name = command.name.removeprefix("constrain_") if arg == "value" else "outline"
                drawn["bore" if source.startswith("derived:") else name] = command.args[arg]
    return drawn


def plan_json(bodies: list[Body], items: list[Item]) -> list[dict]:
    """The plan as a session file stores it. Two kinds of field:

    what the executor is told (load.py passes these on): `kind` (part / feature), `body`
    (the body's number), `shape`, `sizes`, `at` and `turn` (exactly the numbers of
    `move_body` and `turn_body`; a part at the origin or unturned has zeros), `drawn`
    (see _drawn) and `features` (recipes.describe_cut);

    bookkeeping (never model input): `item`, `name`, `line`, `cuts`, and the resolver's
    own `centre` and `matrix`, which the audit compares with data/plans.
    """
    stored = []
    for number, item in enumerate(items):
        body = bodies[item.source]
        entry = {"item": number, "kind": item.kind, "body": item.body, "name": item.name,
                 "line": item.line, "shape": item.shape, "cuts": list(item.cuts),
                 "sizes": dict(body.sizes), "centre": list(body.centre),
                 "matrix": [list(row) for row in body.matrix],
                 "features": [dict(feature) for feature in item.features]}
        if item.kind == "part":
            at, turn = list(ORIGIN), list(ORIGIN)
            for command in flatten(item.entries):
                if command.name == "move_body":
                    at = [command.args[name] for name in ("x", "y", "z")]
                elif command.name == "turn_body":
                    turn = [command.args[name] for name in ("yaw", "pitch", "roll")]
            entry.update(at=at, turn=turn, drawn=_drawn(item))
        stored.append(entry)
    return stored


def _partial(rng: random.Random, items: list[Item]) -> list[Command]:
    """The first commands of a correct build (any-order groups shuffled)."""
    commands = [(command.name, dict(command.args))
                for command in flatten([entry for _, entry in script_of(items)], rng)]
    return commands[:rng.randint(3, max(3, len(commands) - 2))]


def _stray_body(rng: random.Random, numbers: list[float]) -> list[Command]:
    """A document holding one body of the wrong size, moved somewhere."""
    sizes = [n for n in numbers if n > 0] or [10.0]
    return [("new_document", {}), ("new_body", {}), ("select_plane", {"plane": "XY"}),
            ("new_sketch", {"offset": 0.0}), ("sketch_rectangle", {}),
            ("constrain_length", {"value": rng.choice(sizes)}),
            ("constrain_width", {"value": rng.choice(sizes)}),
            ("leave_sketch", {}), ("pad_symmetric", {"length": rng.choice(sizes)}),
            ("move_body", {"x": rng.choice(numbers), "y": 0.0, "z": rng.choice(sizes)})]


def draw_start(rng: random.Random, items: list[Item], numbers: list[float],
               ) -> tuple[str, list[Command], bool]:
    """(kind, the commands to carry out before step 0, forget the undo history afterwards?)"""
    kind = rng.choices(list(START_KINDS), weights=list(START_KINDS.values()))[0]
    if kind == "empty":
        return kind, [], False
    if kind == "document":
        return kind, [("new_document", {})], False
    if kind == "stray_body":
        return kind, _stray_body(rng, numbers), False
    return kind, _partial(rng, items), kind == "opened_partial"


def end_check(unit: Unit, items: list[Item], full_snapshot: dict) -> list[str]:
    """What is wrong with a finished session (nothing, we hope): every part must be the
    resolver's and touch what the resolver says, no two parts may overlap unless the plan
    says `sunk`, and the teacher must find the document complete, on plan and clean."""
    problems = compare_with_resolver(unit.bodies, items, full_snapshot, unit.touching)
    if unit.row is not None:
        problems += compare_with_row(unit.row, full_snapshot)
    advice = teacher(items, lean(full_snapshot))
    if advice.targets or not advice.on_plan:
        problems.append(f"the document is not clean and complete: {advice.why}")
    return problems


def play(fc: FreeCADClient, unit: Unit, seed: int, noise_levels: tuple[float, ...] = NOISE_LEVELS,
         slice_name: str = "structures", code: str | None = None, attempt: int = 0,
         ) -> tuple[dict, list[dict], dict]:
    """One session of one structure. Returns its header, its step records and its end
    record. `code` is the hash of the code that records it (sessions.code_hash).

    `attempt`: 0 for the session's own draw. A higher number gives ANOTHER draw of the same
    session (other mistakes, another start), for the rare session whose own draw makes
    FreeCAD itself fail: a wrong command the kernel never returns from. It is written in
    the header, so a session is still a function of (plan, seed, attempt)."""
    bodies, items = unit.bodies, structure_items(unit.bodies)
    script, places, length = script_of(items), places_of(items), clean_length(items)
    item_starts = {at for at, (index, _) in enumerate(script)
                   if at == 0 or script[at - 1][0] != index}
    numbers = numbers_of(items)
    rng = random.Random(f"{seed}:{unit.id}" + (f":{attempt}" if attempt else ""))
    noise = rng.choice(noise_levels)
    start_kind, start_commands, forget = draw_start(rng, items, numbers)
    sid = session_id(unit.id, seed)

    reply, carried_out = run_start(fc, start_commands, forget)
    header = {
        "session": sid, "plan_id": unit.id, "origin": unit.origin, "where": unit.where,
        "kind": unit.kind, "split": unit.split, "slice": slice_name, "seed": seed,
        "noise_level": noise, "clean_length": length,
        "start": {"kind": start_kind, "commands": carried_out, "forget_undo": forget},
        "parts": len(bodies), "plan": plan_json(bodies, items),
        # Provenance every training row must carry.
        "source": unit.source, "license": unit.license,
        "generator_version": unit.generator_version,
        # What recorded it: the code and the noise mix.
        "code_hash": code, "mix": MIX, "attempt": attempt,
    }
    records: list[dict] = []
    previous = None
    ended = "budget"
    watch = Watch()
    ghost: list[tuple[str, dict, str]] = []     # what a builder who has not noticed issues next
    reopened = False
    snapshot, valid = lean(reply["snapshot"]), reply["valid"]
    advice = teacher(items, snapshot, script, places)
    for t in range(HARD_FACTOR * length + HARD_EXTRA):
        noisy = noise > 0 and t < NOISE_FACTOR * length
        if noisy and not advice.on_plan and snapshot["session"]["undo_depth"] > 0 \
                and rng.random() < REOPEN:
            reply = fc.forget_undo()            # reopen: nothing can be undone any more
            snapshot, valid = lean(reply["snapshot"]), reply["valid"]
            advice = teacher(items, snapshot, script, places)
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
                made = sum(item["type"] == "body" for item in snapshot["items"])
                wrong = wrong_command(rng, Moment(items, numbers, places, advice, valid, previous,
                                                  made, advice.position in item_starts))
        if wrong is not None:
            name, args, kind, detail = wrong
        else:
            target = rng.choice(advice.targets)
            name, args, kind, detail = target.command, target.args, None, None
        reply = fc.command(name, **args)
        if "snapshot" not in reply:         # the worker itself failed on the command
            raise RuntimeError(f"{name} got no snapshot: {reply.get('reason')}")

        executed = {"command": name, "args": args, "noise": kind}
        if detail is not None:
            executed["flavour"] = detail        # how a wrong number was chosen (noise.FLAVOURS)
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
        advice = teacher(items, snapshot, script, places)
        if watch.stuck(before, name, args, kind is None, reply["status"], advice):
            ended = "stuck"
            break
        # A mistake that was carried out and can be built upon: maybe carry on.
        carried = reply["status"] == "ok" and before.on_plan and snapshot != record["snapshot"]
        in_item = before.position is not None and script[before.position][0] is not None
        if carried and in_item and (
                (kind in WRONG_NUMBERS and rng.random() < (
                    CARRY_ON_WRONG_BODY if kind == "wrong_side" else CARRY_ON_START))
                or (kind == "wrong_body" and rng.random() < CARRY_ON_WRONG_BODY)):
            ghost = [(*command, "carry_on")
                     for command in rest_of_item(script, before, name, rng)]
        elif kind is not None and kind != "carry_on" and reply["status"] == "ok" and not ghost:
            ghost = [(*command, kind) for command in follow_up(rng, numbers, kind, name)]
    problems = end_check(unit, items, reply["snapshot"]) if ended == "done" else []
    end = {"session": sid, "end": ended, "steps": len(records),
           "snapshot": lean(reply["snapshot"]), "problems": problems}
    return header, records, end
