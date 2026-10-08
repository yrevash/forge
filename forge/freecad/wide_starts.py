"""Wider start states: a real session can begin anywhere.

starts.py has eight kinds of start (empty, a document, a body, a partial build, a stray
sketch, a stray solid, a partial build with a wrong number, and "opened from a file").
This file adds the kinds below, as an option of the new recorder (wide_play.py). starts.py
is not changed, so the `_v2` slices read as before.

Every start is REACHABLE: it is a list of ordinary commands carried out in FreeCAD before
the first recorded step (play.run_start; a command FreeCAD refuses is left out), sometimes
followed by "the undo history is gone". Nothing is written into a document by other means.

    kind               what is there at step 0                          the teacher's answer
    complete           the whole plan, correctly built, not yet         `done` (first
                       declared done; sometimes with a selection        `clear_selection`)
    beyond_plan        the whole plan correctly built AND more: the     `undo` until the extra
                       beginning or all of further features. For a      work is gone, then `done`
                       one-item plan: a base that already has features
    open_sketch_stray  a correct beginning, stopped inside a sketch,    `undo` the stray shapes,
                       with one to three stray shapes drawn in it       then carry on
    failed_feature     a correct beginning, then a fillet, chamfer or   `undo` it, carry on
                       wall FreeCAD could not build (marked invalid)
    selection          a correct beginning with something selected      replaces the selection
                       that the next command does not want              (nothing is undone)
    random_walk        a correct beginning, then 1 to 10 commands       `undo` back to the plan,
                       picked blindly from the catalogue: the kind of   whatever it takes
                       state a model's own mistakes lead to
  held out of training (test slice `abnormal_starts_v3` only):
    several_strays     two to four stray sketches and solids            `undo` until all are gone
    other_part         the beginning, or all, of ANOTHER part's build   `undo` it away, or carry
                                                                        on where the two agree
    two_bodies         two bodies, with a correct beginning in one      `undo` back to one body
  and for any of the kinds above:
    opened_<kind>      the same, with the undo history gone             carry on if it is on plan,
                                                                        else `new_document`

`complete` with the history gone is the plainest case of all: a finished, correct part was
opened from a file, and the only right command is `done`.

The recorder can also be handed ANY start as a list of commands (wide_play.play,
`start=`): that is how a state a model drove into is labelled by the teacher (DAgger).
"""

from __future__ import annotations

import random

from forge.freecad.catalogue import COMMANDS, EDGE_RULES, FACE_RULES
from forge.freecad.noise import NEVER_INJECTED, TOO_BIG, _any_args, numbers_of
from forge.freecad.recipes import context_of, flatten, recipe
from forge.freecad.starts import (
    BODY,
    _draw,
    _partial,
    _sizes,
    _stray_sketch,
    _stray_solid,
    _wrong_partial,
)
from forge.system1.steps import FEATURES, Step

Command = tuple[str, dict]

# How often each start is drawn in the TRAINING slices of the third mix.
TRAIN_STARTS = {"empty": 0.30, "document": 0.04, "body": 0.04, "partial": 0.08,
                "stray_sketch": 0.05, "stray_solid": 0.04, "wrong_partial": 0.05,
                "complete": 0.05, "beyond_plan": 0.06, "open_sketch_stray": 0.04,
                "failed_feature": 0.04, "selection": 0.04, "random_walk": 0.05, "opened": 0.12}
# Never in a training slice: the test `abnormal_starts_v3` is made of these alone.
HELD_OUT_STARTS = ("several_strays", "other_part", "two_bodies")
TEST_STARTS = {"several_strays": 0.26, "other_part": 0.26, "two_bodies": 0.26, "opened": 0.22}


def _build(plan: list[Step], rng: random.Random, context_plan: list[Step] | None = None,
           ) -> list[Command]:
    """Every command of a correct build, dimensions in a random order."""
    context = context_of(context_plan or plan)
    return [(c.name, dict(c.args)) for step in plan for c in flatten(recipe(step, context), rng)]


def _item_ends(plan: list[Step]) -> list[int]:
    """After how many commands each plan item is complete."""
    context, ends, total = context_of(plan), [], 0
    for step in plan:
        total += len(flatten(recipe(step, context)))
        ends.append(total)
    return ends


def _complete(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    commands = _build(plan, rng)
    if rng.random() < 0.3:
        commands.append(_a_selection(rng, plan))
    return commands


def _extra_features(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    """The commands of one or two features the plan does not have, taken from another plan."""
    features = [step for step in other if step.kind in FEATURES]
    if not features:    # the other plan is a bare base: a small boss of this plan's own numbers
        size = min(_sizes(plan))
        features = [Step("boss", {"diameter": size, "height": size, "x": 0.0, "y": 0.0})]
    chosen = rng.sample(features, min(len(features), rng.randint(1, 2)))
    context = context_of(plan)
    return [(c.name, dict(c.args)) for step in chosen for c in flatten(recipe(step, context), rng)]


def _beyond_plan(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    extra = _extra_features(rng, plan, other)
    # At least as far as the new sketch, so that the document really holds more than the plan.
    return _build(plan, rng) + extra[:rng.randint(2, len(extra))]


def _open_sketch_stray(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    commands = _build(plan, rng)
    inside = [at for at, (name, _) in enumerate(commands)
              if name.startswith(("sketch_", "constrain_"))]
    cut = rng.choice(inside) + 1
    strays = [command for _ in range(rng.randint(1, 3)) for command in _draw(rng, plan, rng.randint(0, 3))]
    return commands[:cut] + strays


def _failed_feature(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    commands = _build(plan, rng)
    cut = rng.choice(_item_ends(plan))
    largest = TOO_BIG * max(abs(float(n)) for n in numbers_of(plan))
    failing = rng.choice([
        [("select_edges", {"rule": rng.choice(EDGE_RULES)}), ("fillet", {"radius": largest})],
        [("select_edges", {"rule": rng.choice(EDGE_RULES)}), ("chamfer", {"size": largest})],
        [("select_face", {"rule": rng.choice(FACE_RULES)}), ("thickness", {"value": largest})]])
    return commands[:cut] + failing


def _a_selection(rng: random.Random, plan: list[Step]) -> Command:
    name = rng.choice(("select_plane", "select_edges", "select_face", "select_tip", "select_sketch"))
    return name, _any_args(rng, name, plan)


def _selection(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    commands = _build(plan, rng)
    cut = rng.randint(2, len(commands))
    # Two tries: a selection FreeCAD refuses in that state (no solid yet) is simply left out.
    return commands[:cut] + [_a_selection(rng, plan), _a_selection(rng, plan)][:rng.randint(1, 2)]


def _random_walk(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    commands = _build(plan, rng)
    cut = rng.randint(2, len(commands))
    names = [name for name in COMMANDS if name not in NEVER_INJECTED or name == "done"]
    walk = []
    for _ in range(rng.randint(1, 10)):
        name = rng.choice(names)
        if name == "done" and rng.random() < 0.7:   # `done` ends the walk; keep it rare
            continue
        walk.append((name, _any_args(rng, name, plan)))
    return commands[:cut] + walk


def _several_strays(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    commands = list(BODY)
    for _ in range(rng.randint(2, 4)):
        maker = rng.choice((_stray_sketch, _stray_solid))
        stray = maker(rng, plan)[len(BODY):]
        if stray[-1][0] not in ("leave_sketch", "pad"):
            stray.append(("leave_sketch", {}))      # closed, so that the next one can begin
        commands += stray
    return commands


def _other_part(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    commands = _build(other, rng)
    return commands[:rng.randint(min(8, len(commands)), len(commands))]


def _two_bodies(rng: random.Random, plan: list[Step], other: list[Step]) -> list[Command]:
    partial = _partial(rng, plan)
    if rng.random() < 0.5:      # an extra, empty body made last
        return partial + [("new_body", {})]
    return [*BODY, ("new_body", {}), *partial[len(BODY):]]   # the build is in the second body


def _old(maker):
    return lambda rng, plan, other: maker(rng, plan)


MAKERS = {"partial": _old(_partial), "stray_sketch": _old(_stray_sketch),
          "stray_solid": _old(_stray_solid), "wrong_partial": _old(_wrong_partial),
          "complete": _complete, "beyond_plan": _beyond_plan,
          "open_sketch_stray": _open_sketch_stray, "failed_feature": _failed_feature,
          "selection": _selection, "random_walk": _random_walk,
          "several_strays": _several_strays, "other_part": _other_part,
          "two_bodies": _two_bodies}
PLAIN = {"empty": [], "document": BODY[:1], "body": list(BODY)}
START_KINDS = (*PLAIN, *MAKERS)


def make_start(rng: random.Random, kind: str, plan: list[Step], other: list[Step],
               ) -> list[Command]:
    """The commands of one start kind (not an `opened_` one)."""
    return list(PLAIN[kind]) if kind in PLAIN else MAKERS[kind](rng, plan, other)


def draw_start(rng: random.Random, plan: list[Step], other: list[Step],
               kinds: dict[str, float] = TRAIN_STARTS) -> tuple[str, list[Command], bool]:
    """(kind, the commands to carry out before step 0, forget the undo history afterwards?)

    `other` is another part's plan (for `other_part` and for the extra features of
    `beyond_plan`). `kinds` says how often each kind is drawn; `opened` picks one of the
    other kinds of the same table that leave something in the document."""
    kind = rng.choices(list(kinds), weights=list(kinds.values()))[0]
    if kind != "opened":
        return kind, make_start(rng, kind, plan, other), False
    inner = rng.choice([name for name in kinds if name in MAKERS])
    return f"opened_{inner}", make_start(rng, inner, plan, other), True
