"""Proof of recovery at interface level: mistakes and crashes leave nothing behind.

Four experiments, each on verified composed parts built through the interface.
In every one the part must still end as its stored solid.

  cancel    BETWEEN steps a dialog is opened that the plan never asked for (fillet,
            chamfer, thickness, a pattern, a mirror, a datum plane, the sketch-plane
            list), sometimes a number is typed into it, then Cancel is clicked.
            The document must be exactly what it was: same items, same solid, same
            number of undo steps.
  undo      a step's last dialog is confirmed with a WRONG number. One press of Undo
            must bring back the document from just before that dialog; the dialog is
            then filled in correctly.
  refused   a fillet far too large is confirmed. FreeCAD answers with its own error
            box and keeps the dialog open; the runtime must report the refusal with
            FreeCAD's words, and Cancel must leave nothing behind.
  kill      the FreeCAD process is killed at a random action (SIGKILL, as a crash
            would leave it). The client must start a new one, replay the part so
            far, and carry on.

Run:    uv run python -m forge.freecad_ui.recover [--per-base 10] [--kills 12]
Exit code 0 only if every check held.
"""

from __future__ import annotations

import argparse
import random
import time
from collections import Counter

from forge.freecad.parts import sample_rows
from forge.freecad_ui.build import BuildResult, against_stored, run_recipe
from forge.freecad_ui.client import InstanceLost, UIClient
from forge.freecad_ui.recipes import Act, AnyOrder, Recipe, context_of, recipe
from forge.runs import log_metrics, start_run
from forge.system1.steps import Step, steps_of

# Dialogs a plan might never ask for: (what to select first, the button, a field to type into).
WRONG_DIALOGS = [
    ("pick:edges:all", "button:PartDesign_Fillet", "field:filletRadius"),
    ("pick:edges:top_face", "button:PartDesign_Chamfer", "field:chamferSize"),
    ("pick:face:top", "button:PartDesign_Thickness", "field:Value"),
    ("tree:{tip}", "button:PartDesign_LinearPattern", "field:spinOccurrences"),
    ("tree:{tip}", "button:PartDesign_PolarPattern", "field:spinOccurrences"),
    ("tree:{tip}", "button:PartDesign_Mirrored", None),
    ("tree:XY_Plane", "button:Part_DatumPlane", "field:attachmentOffsetZ"),
    ("pick:plane:XZ", "button:Part_DatumPlane", "field:attachmentOffsetZ"),
    (None, "button:PartDesign_NewSketch", None),
]
DIALOG_BUTTONS = ("button:PartDesign_Pad", "button:PartDesign_Pocket", "button:PartDesign_Hole",
                  "button:PartDesign_Fillet", "button:PartDesign_Chamfer",
                  "button:PartDesign_Thickness", "button:PartDesign_LinearPattern",
                  "button:PartDesign_PolarPattern", "button:PartDesign_Mirrored")


def same_document(a: dict, b: dict) -> bool:
    """Same items and the same solid (volume to one part in 10^12, box to a nanometre)."""
    if a["items"] != b["items"] or a["context"]["undo"] != b["context"]["undo"]:
        return False
    sa, sb = a["solid"], b["solid"]
    if sa is None or sb is None:
        return sa is sb
    return (abs(sa["volume"] - sb["volume"]) <= 1e-12 * abs(sa["volume"])
            and all(abs(x - y) <= 1e-9 for x, y in zip(sa["bbox"], sb["bbox"], strict=True))
            and sa["solids"] == sb["solids"] and sa["valid"] == sb["valid"])


def _fill(action_id: str, reply: dict) -> str:
    return action_id.replace("{tip}", str(reply["context"].get("tip")))


# --- experiment 1: a wrong dialog, cancelled -----------------------------------------------------

def wrong_dialog_cancelled(ui: UIClient, reply: dict, rng: random.Random, tally: Counter) -> dict:
    """Open a dialog the plan did not ask for, maybe type into it, Cancel. Returns the new reply."""
    select, button, field = rng.choice(WRONG_DIALOGS)
    before = ui.document()
    if select is not None:
        reply = ui.act(_fill(select, reply))
        if reply["status"] != "ok":
            tally["cancel: could not select (skipped)"] += 1
            return ui.elements()
    reply = ui.act(button)
    if reply["status"] == "refused":
        # FreeCAD would not even open it (a pattern of a fillet, say): also nothing left?
        tally["cancel: FreeCAD refused to open the dialog"] += 1
        tally["cancel: nothing left behind"] += same_document(before, ui.document())
        tally["cancel: tried"] += 1
        return ui.elements()
    if reply["status"] != "ok" or reply["context"]["dialog"] is None:
        tally[f"cancel: {button} did not open a dialog"] += 1
        return ui.elements()
    tally["cancel: tried"] += 1
    tally["cancel: FreeCAD made the feature when the dialog opened"] += (
        len(ui.document()["items"]) > len(before["items"]))
    showing = {element["id"] for element in reply["elements"]}
    if field in showing and rng.random() < 0.7:
        ui.act(field, rng.choice((2, 3, 4)))
        tally["cancel: a number was typed first"] += 1
    reply = ui.act("dialog:Cancel")
    after = ui.document()
    clean = reply["status"] == "ok" and reply["context"]["dialog"] is None
    tally["cancel: nothing left behind"] += clean and same_document(before, after)
    if not (clean and same_document(before, after)):
        tally[f"cancel: LEFT SOMETHING after {button}"] += 1
    return reply


# --- experiment 2: a wrong number, confirmed, undone -----------------------------------------------

def _split_at_last_dialog(entries: Recipe) -> tuple[Recipe, Recipe] | None:
    """(everything before the step's last dialog button, that button and what follows)."""
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if isinstance(entry, Act) and entry.id in DIALOG_BUTTONS:
            return entries[:index], entries[index:]
    return None


def _with_wrong_number(tail: Recipe, rng: random.Random) -> Recipe | None:
    """The same dialog actions with ONE field's number changed (None if it has no field)."""
    fields = [action for entry in tail
              for action in (entry.actions if isinstance(entry, AnyOrder) else (entry,))
              if action.id.startswith("field:")]
    if not fields:
        return None
    target = rng.choice(fields)
    # Counts go up by one; sizes shrink, so the wrong feature still fits in the part.
    wrong = target.value + 1 if target.id == "field:spinOccurrences" else round(target.value * 0.5, 3)

    def swap(action: Act) -> Act:
        return Act(action.id, wrong, "wrong") if action is target else action

    return [AnyOrder(tuple(swap(a) for a in entry.actions)) if isinstance(entry, AnyOrder)
            else swap(entry) for entry in tail]


def _reselect(head: Recipe, document: dict) -> list[Act]:
    """What must be selected again before the dialog button is pressed a second time."""
    last = head[-1] if head else None
    if isinstance(last, Act) and last.id.split(":")[0] in ("pick", "tree"):
        return [last]
    unused = [item["name"] for item in document["items"]
              if item["type"] == "sketch" and item["used_by"] is None]
    return [Act(f"tree:{unused[-1]}")] if unused else []


def step_with_wrong_number(ui: UIClient, step: Step, entries: Recipe, reply: dict,
                           result: BuildResult, rng: random.Random,
                           tally: Counter) -> tuple[dict, str | None]:
    """Build one step, confirming its last dialog with a wrong number first, then undoing."""
    split = _split_at_last_dialog(entries)
    wrong_tail = _with_wrong_number(split[1], rng) if split else None
    if wrong_tail is None:
        return run_recipe(ui, step, entries, reply, result, rng)
    head, tail = split
    reply, problem = run_recipe(ui, step, head, reply, result, rng)
    if problem:
        return reply, problem
    before = ui.document()
    reply, problem = run_recipe(ui, step, wrong_tail, reply, result, rng)
    if problem:
        # FreeCAD refused the wrong number: close the dialog and carry on with the right one.
        tally["undo: the wrong number was refused by FreeCAD"] += 1
        ui.act("dialog:Cancel")
    else:
        tally["undo: tried"] += 1
        wrong = ui.document()
        tally["undo: the wrong number changed the document"] += not same_document(before, wrong)
        reply = ui.act("button:Std_Undo")
        after = ui.document()
        # One undo step fewer than after the wrong OK, and everything else as before the dialog.
        restored = (reply["status"] == "ok" and after["items"] == before["items"]
                    and same_document({**before, "context": after["context"]}, after))
        tally["undo: one Undo restored the document exactly"] += restored
        if not restored:
            tally[f"undo: NOT RESTORED after {step.kind}"] += 1
    reply = ui.elements()
    return run_recipe(ui, step, [*_reselect(head, ui.document()), *tail], reply, result, rng)


# --- experiment 3: an OK that FreeCAD refuses ------------------------------------------------------

def refused_ok(ui: UIClient, size: float, tally: Counter) -> dict:
    """Confirm a fillet far too large. FreeCAD must refuse and we must see it."""
    before = ui.document()
    ui.act("pick:edges:all")
    reply = ui.act("button:PartDesign_Fillet")
    if reply["status"] != "ok":
        tally["refused: the fillet dialog did not open (skipped)"] += 1
        return ui.elements()
    tally["refused: tried"] += 1
    ui.act("field:filletRadius", round(10 * size, 3))
    reply = ui.act("dialog:OK")
    if reply["status"] == "refused":
        tally["refused: reported as refused"] += 1
        tally[f"refused: FreeCAD said: {(reply.get('reason') or '')[:70]!r}"] += 1
        tally["refused: the dialog stayed open"] += reply["context"]["dialog"] is not None
        reply = ui.act("dialog:Cancel")
    else:
        tally["refused: FreeCAD ACCEPTED it"] += 1
        reply = ui.act("button:Std_Undo")
    tally["refused: nothing left behind"] += same_document(before, ui.document())
    return reply


# --- one part with experiments 1 to 3 --------------------------------------------------------------

def build_with_mistakes(ui: UIClient, row: dict, rng: random.Random, tally: Counter) -> list[str]:
    steps = steps_of(row["family"], row["params"])
    context = context_of(steps)
    result = BuildResult()
    ui.new_part()
    reply = ui.elements()
    for index, step in enumerate(steps):
        entries = recipe(step, context)
        if index > 0 and rng.random() < 0.6:
            reply, problem = step_with_wrong_number(ui, step, entries, reply, result, rng, tally)
        else:
            reply, problem = run_recipe(ui, step, entries, reply, result, rng)
        if problem:
            return [problem]
        for _ in range(rng.choice((0, 1, 1, 2))):
            reply = wrong_dialog_cancelled(ui, reply, rng, tally)
        if index == 0 or rng.random() < 0.25:
            reply = refused_ok(ui, max(row["measured"]["bbox"]), tally)
    return against_stored(ui.document()["solid"], row["measured"])


# --- experiment 4: the instance is killed ------------------------------------------------------------

def build_with_kill(ui: UIClient, row: dict, rng: random.Random) -> tuple[list[str], float, int]:
    """Kill FreeCAD at one random action of the part. Returns (problems, seconds lost, replayed)."""
    steps = steps_of(row["family"], row["params"])
    context = context_of(steps)
    flat = sum(len(e.actions) if isinstance(e, AnyOrder) else 1
               for step in steps for e in recipe(step, context))
    kill_at = rng.randrange(1, flat)
    result = BuildResult()
    lost = [0.0, 0]

    def maybe_kill(_step: Step, _action: Act, _reply: dict) -> None:
        if result.actions == kill_at:
            lost[1] = len(ui._log)          # how many actions the client will have to replay
            ui.kill_instance()

    ui.new_part()
    reply = ui.elements()
    for step in steps:
        started, restarts = time.time(), ui.restarts
        reply, problem = run_recipe(ui, step, recipe(step, context), reply, result, rng, maybe_kill)
        if ui.restarts > restarts:
            lost[0] = time.time() - started
        if problem:
            return [problem], lost[0], lost[1]
    return against_stored(ui.document()["solid"], row["measured"]), lost[0], lost[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-base", type=int, default=10)
    parser.add_argument("--long-per-base", type=int, default=2)
    parser.add_argument("--kills", type=int, default=12)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    run_dir = start_run("freecad-ui-recover", vars(args))
    rows = sample_rows(args.per_base, args.long_per_base, args.seed)
    tally: Counter = Counter()
    failed: list[str] = []
    started = time.time()
    with UIClient() as ui:
        for row in rows:
            problems = build_with_mistakes(ui, row, random.Random(row["id"]), tally)
            tally["parts built with mistakes"] += 1
            tally["parts that still match the stored solid"] += not problems
            if problems:
                failed.append(f"{row['id']} {row['family']}: {problems[:2]}")
        crashes_before = ui.restarts
        losses, replays = [], []
        for row in rows[:args.kills]:
            try:
                problems, lost, replayed = build_with_kill(ui, row,
                                                           random.Random(row["id"] + "kill"))
            except InstanceLost as error:
                problems, lost, replayed = [f"replay failed: {str(error)[:600]}"], 0.0, 0
                ui.close()
            tally["kill: tried"] += 1
            tally["kill: part finished and matched"] += not problems
            losses.append(lost)
            replays.append(replayed)
            if problems:
                failed.append(f"kill {row['id']} {row['family']}: {problems[:2]}")
        tally["kill: instances restarted"] = ui.restarts - crashes_before
        unplanned = crashes_before
    seconds = time.time() - started

    for name, count in sorted(tally.items()):
        print(f"  {count:5d}  {name}")
    if losses:
        print(f"  a kill cost {sum(losses) / len(losses):.1f}s on average (slowest {max(losses):.1f}s); "
              f"{sum(replays) / len(replays):.0f} actions replayed on average (most {max(replays)})")
    print(f"  FreeCAD crashes or hangs that we did not cause: {unplanned}")
    for line in failed[:20]:
        print("  FAILED", line)

    def held(tried: str, good: str) -> bool:
        return tally[tried] == tally[good]

    all_held = (not failed and held("cancel: tried", "cancel: nothing left behind")
                and held("undo: tried", "undo: one Undo restored the document exactly")
                and held("refused: tried", "refused: reported as refused")
                and held("refused: tried", "refused: nothing left behind")
                and held("kill: tried", "kill: part finished and matched"))
    print(f"{'every check held' if all_held else 'SOME CHECK FAILED'} ({seconds:.0f}s)")
    log_metrics(run_dir, final=True, tally=dict(tally), failed=failed, seconds=round(seconds, 1),
                kill_seconds=losses, kill_replays=replays, all_held=all_held)
    raise SystemExit(0 if all_held else 1)


if __name__ == "__main__":
    main()
