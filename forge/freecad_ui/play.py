"""Play one session through FreeCAD's interface: the teacher labels every state, noise sometimes acts.

    header, records, end = play(ui, part, seed, split)

The loop is the command level's (forge/freecad/play.py):
  1. take the interface snapshot and ask the teacher for the acceptable actions;
  2. with the session's noise chance, carry out a wrong action instead (noise.py);
     otherwise one of the teacher's, chosen at random;
  3. FreeCAD does it, or refuses; store the record; go to 1.
The session ends when the teacher's `done` is reached. `done` is not sent to
FreeCAD (it has no such button); it is the record that says "stop here".

Noise may strike only during the first NOISE_FACTOR x L steps (L = the length of
a clean build); HARD_FACTOR x L + HARD_EXTRA is a hard limit. A session that hits
it is marked "budget"; one where the teacher has no answer is marked "stuck".
Neither is training data.

Every session starts from an empty FreeCAD (no document). The command level also
starts from half-built documents (starts.py); that is not done here yet.
"""

from __future__ import annotations

import hashlib
import random

from forge.freecad.build import against_stored
from forge.freecad_ui.client import UIClient
from forge.freecad_ui.noise import NOISE_LEVELS, Moment, wrong_action
from forge.freecad_ui.snapshots import lean
from forge.freecad_ui.teacher import DONE, clean_length, plan_json, segments_of, teacher
from forge.system1.steps import Step, steps_of

NOISE_FACTOR = 3
HARD_FACTOR = 8
HARD_EXTRA = 40


def session_id(part_id: str, seed: int) -> str:
    return hashlib.sha1(f"freecad_ui:{part_id}:{seed}".encode()).hexdigest()[:16]


def observe(ui: UIClient, reply: dict | None = None) -> dict:
    """The lean snapshot after an action (asking again if the reply does not carry one)."""
    if reply is None or "elements" not in reply or "document" not in reply:
        reply = ui.elements(document=True)
    return lean(reply)


def end_check(plan: list[Step], snapshot: dict, measured: dict) -> list[str]:
    """What is wrong with a finished session (nothing, we hope)."""
    solid = snapshot["solid"]
    if solid is not None:
        low, high = solid["bbox"][:3], solid["bbox"][3:]
        solid = {**solid, "size": [round(b - a, 9) for a, b in zip(low, high, strict=True)]}
    problems = against_stored(solid, measured)
    advice = teacher(plan, snapshot)
    if advice.ids() != [DONE] or not advice.on_plan:
        problems.append(f"the document is not clean and complete: {advice.why}")
    return problems


def play(ui: UIClient, part: dict, seed: int, split: str,
         noise_levels: tuple[float, ...] = NOISE_LEVELS, slice_name: str | None = None,
         ) -> tuple[dict, list[dict], dict]:
    """One session of one part. Returns its header, its step records and its end record."""
    plan = steps_of(part["family"], part["params"])
    segments, length = segments_of(plan), clean_length(plan)
    rng = random.Random(f"{seed}:{part['id']}")
    noise = rng.choice(noise_levels)
    sid = session_id(part["id"], seed)
    header = {
        "session": sid, "part_id": part["id"], "family": part["family"], "split": split,
        "slice": slice_name or split, "seed": seed, "noise_level": noise, "clean_length": length,
        "start": {"kind": "empty"}, "plan": plan_json(plan),
        # Provenance every training row must carry.
        "source": part["source"], "license": part["license"],
        "generator_version": part["generator_version"],
    }
    ui.new_part()
    snapshot = observe(ui)
    records: list[dict] = []
    ended = "budget"
    for t in range(HARD_FACTOR * length + HARD_EXTRA):
        advice = teacher(plan, snapshot, segments)
        if not advice.targets:
            ended = "stuck"
            break
        wrong = None
        if noise > 0 and t < NOISE_FACTOR * length and rng.random() < noise:
            wrong = wrong_action(rng, Moment(plan, snapshot, advice))
        if wrong is not None:
            element, value, kind = wrong
        else:
            target = rng.choice(advice.targets)
            element, value, kind = target.id, target.value, None
        record = {
            "session": sid, "t": t, "snapshot": snapshot,
            "target": [target.to_json() for target in advice.targets],
            "progress": {"on_plan": advice.on_plan, "built": advice.built,
                         "active": advice.active},
            "executed": {"id": element, "value": value, "noise": kind},
        }
        if element == DONE:
            record["reply"] = {"status": "ok"}
            records.append(record)
            ended = "done"
            break
        reply = ui.act(element, value, document=True)
        record["reply"] = {key: reply[key] for key in ("status", "reason") if key in reply}
        records.append(record)
        snapshot = observe(ui, reply)
    problems = end_check(plan, snapshot, part["measured"]) if ended == "done" else []
    end = {"session": sid, "end": ended, "steps": len(records), "snapshot": snapshot,
           "problems": problems}
    return header, records, end
