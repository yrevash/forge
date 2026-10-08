"""Wrong interface actions, carried out on purpose while sessions are recorded.

The reason is the command level's (forge/freecad/noise.py): a model that has only
seen perfect sessions is lost after its own first mistake. So now and then a
wrong action is carried out instead of the teacher's, and the states that follow
are labelled with what the teacher says there. The wrong action is only ever
EXECUTED; it is never stored as a target.

Each session draws one noise level from NOISE_LEVELS: the chance, at every step,
that a wrong action replaces the teacher's.

Seven kinds of wrong action
    wrong_button   another enabled toolbar button from WRONG_BUTTONS (a wrong tool, a wrong
                   feature, leaving the sketch too early)
    wrong_value    a teacher action that types a number, with another number of the plan
    wrong_entry    a teacher action that chooses a dropdown or list entry, with another entry
    ok_too_early   OK while the teacher still wants settings changed
    stray_dialog   a dialog-opening button the teacher did not ask for
    stray_click    a click the teacher did not ask for: a tree item, a pick, Cancel
    extra_undo     Undo when the teacher did not ask for it

What is never injected
    - a button outside WRONG_BUTTONS. In particular anything that opens one of macOS's
      own panels (those are not even listed as elements, see inside/widgets.py), and
      "new body" and "new document", which would leave FreeCAD's active body or
      document somewhere a single Undo does not bring back;
    - Undo inside a sketch that has no shape yet (it would undo the making of the
      very sketch that is open), or while a drawing tool is part-way through a shape
      (FreeCAD crashed on the next pointer move);
    - `done`, which would end the session early.
A wrong action only ever touches an element the teacher's own actions touch, or
presses a button: it never changes a setting no recipe sets (a pad's "Symmetric"
mode, say), because nothing in the document would show the teacher what to repair.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from forge.freecad_ui.teacher import CANCEL, DONE, UNDO, Advice, mid_shape, same
from forge.system1.steps import Step

NOISE_LEVELS = (0.0, 0.0, 0.1, 0.2, 0.3)
NOISE_KINDS = {"wrong_button": 1.0, "wrong_value": 1.5, "wrong_entry": 1.0, "ok_too_early": 1.0,
               "stray_dialog": 1.0, "stray_click": 1.0, "extra_undo": 0.7}
DIALOG_BUTTONS = ("button:PartDesign_Pad", "button:PartDesign_Pocket", "button:PartDesign_Hole",
                  "button:PartDesign_Fillet", "button:PartDesign_Chamfer",
                  "button:PartDesign_Thickness", "button:PartDesign_Mirrored",
                  "button:PartDesign_LinearPattern", "button:PartDesign_PolarPattern",
                  "button:Part_DatumPlane", "button:PartDesign_NewSketch")
WRONG_BUTTONS = (*DIALOG_BUTTONS, "button:Sketcher_CreateCircle",
                 "button:Sketcher_CreateRectangle_Center", "button:Sketcher_CreateHexagon",
                 "button:Sketcher_CreateSlot", "button:Sketcher_LeaveSketch")

Action = tuple[str, object]     # (element id, value)


@dataclass(frozen=True)
class Moment:
    """What a wrong action may depend on: the plan, the snapshot and the teacher's answer."""

    plan: list[Step]
    snapshot: dict
    advice: Advice


def numbers_of(plan: list[Step]) -> list[float]:
    return [float(value) for step in plan for value in step.slots.values()]


def _showing(m: Moment, kinds: tuple[str, ...]) -> list[dict]:
    return [element for element in m.snapshot["elements"] if element["kind"] in kinds]


def _wrong_button(rng: random.Random, m: Moment) -> Action | None:
    options = [name for name in WRONG_BUTTONS if name in m.snapshot["buttons"]]
    return (rng.choice(options), None) if options else None


def _stray_dialog(rng: random.Random, m: Moment) -> Action | None:
    options = [name for name in DIALOG_BUTTONS if name in m.snapshot["buttons"]]
    return (rng.choice(options), None) if options else None


def _wrong_value(rng: random.Random, m: Moment) -> Action | None:
    typed = [t for t in m.advice.targets
             if isinstance(t.value, (int, float)) and not isinstance(t.value, bool)]
    if not typed:
        return None
    target = rng.choice(typed)
    whole = target.id == "field:spinOccurrences"
    others = [n for n in numbers_of(m.plan)
              if not same(n, target.value) and n > 0 and (not whole or n.is_integer())]
    if whole:
        others = [n for n in {*others, 2.0, 3.0, 4.0, 6.0} if 2 <= n <= 12
                  and not same(n, target.value)]
    if not others:
        return None
    wrong = rng.choice(sorted(others))
    return target.id, int(wrong) if whole else wrong


def _wrong_entry(rng: random.Random, m: Moment) -> Action | None:
    by_id = {element["id"]: element for element in _showing(m, ("dropdown", "list"))}
    chosen = [t for t in m.advice.targets if isinstance(t.value, str) and t.id in by_id]
    if not chosen:
        return None
    target = rng.choice(chosen)
    # "Select reference…" starts a pick in the 3D view that only a mouse can finish.
    others = [entry for entry in by_id[target.id]["entries"]
              if entry != target.value and "reference" not in entry.lower()]
    return (target.id, rng.choice(others)) if others else None


def _ok_too_early(rng: random.Random, m: Moment) -> Action | None:
    if any(e["id"] == "dialog:OK" for e in _showing(m, ("dialog_button",))):
        return "dialog:OK", None
    return None


def _stray_click(rng: random.Random, m: Moment) -> Action | None:
    options = [e["id"] for e in _showing(m, ("tree", "pick"))
               if e.get("type") not in ("origin", "base_axis", "base_point")]
    if any(e["id"] == CANCEL for e in _showing(m, ("dialog_button",))):
        options += [CANCEL] * 3
    return (rng.choice(options), None) if options else None


def _extra_undo(rng: random.Random, m: Moment) -> Action | None:
    if UNDO not in m.snapshot["buttons"] or m.snapshot["context"]["undo"] < 1:
        return None
    name = m.snapshot["context"]["sketch_open"]
    if name is not None:
        sketch = next(item for item in m.snapshot["items"] if item["name"] == name)
        if not sketch["shapes"] or mid_shape(m.snapshot):
            return None
    return UNDO, None


_MAKERS = {"wrong_button": _wrong_button, "wrong_value": _wrong_value,
           "wrong_entry": _wrong_entry, "ok_too_early": _ok_too_early,
           "stray_dialog": _stray_dialog, "stray_click": _stray_click, "extra_undo": _extra_undo}


def is_target(action: Action, advice: Advice) -> bool:
    """Is this exactly one of the teacher's actions (same element, same value)?"""
    return any(target.id == action[0] and same(target.value, action[1])
               for target in advice.targets)


def wrong_action(rng: random.Random, m: Moment) -> tuple[str, object, str] | None:
    """An action the teacher does not accept here, and the kind of mistake it is.

    The kinds are tried in a weighted random order; one that does not apply passes
    to the next. None if no kind applies.
    """
    left = dict(NOISE_KINDS)
    order = []
    while left:     # a weighted shuffle: draw without putting back
        kind = rng.choices(list(left), weights=list(left.values()))[0]
        order.append(kind)
        del left[kind]
    for kind in order:
        action = _MAKERS[kind](rng, m)
        if action is not None and action[0] != DONE and not is_target(action, m.advice):
            return action[0], action[1], kind
    return None
