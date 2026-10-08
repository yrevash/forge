"""Wrong steps, injected into sessions on purpose.

A model trained only on perfect sessions has never seen a mistake, so the first
one it makes puts it in a state it knows nothing about. The fix is to
carry out a wrong action now and then while recording the data, and to label
the states that follow with what the teacher would do.

Five kinds of wrong step. The first four are the mistakes a model that points at
numbers is likely to make; the fifth is a plain random action.

    wrong_kind      the right numbers on the wrong kind of step (a boss for a hole)
    wrong_mention   the right kind with a number taken from the wrong place
    out_of_order    a later item of the plan built now
    repeat          something already built, built again
    random          any kind with any mentions (may be `undo`, never `done`)

A wrong step is only ever EXECUTED. It is never stored as a target.
"""

from __future__ import annotations

import random

from forge.system1.engine import State, resolve
from forge.system1.steps import CONTROL, FEATURES, KINDS, SLOTS, STARTS, TREATMENTS, UNDO, Step

WRONG_KINDS = ("wrong_kind", "wrong_mention", "out_of_order", "repeat", "random")
_GROUPS = (tuple(STARTS), tuple(TREATMENTS), tuple(FEATURES))
# `done` is never injected: a session ends only when the teacher says so,
# which is what guarantees every session ends at its target part.
_RANDOM_KINDS = tuple(kind for kind in KINDS if kind != "done")


def same_step(a: Step, b: Step) -> bool:
    return a.kind == b.kind and a.sources == b.sources


def _wrong_kind(rng: random.Random, plan: list[Step], state: State, target: Step) -> Step | None:
    """Another kind from the same group. Slots the two kinds share keep the target's mention
    (a boss for a hole keeps the diameter, x and y); the rest get a random mention."""
    if target.kind in CONTROL:
        return None
    group = next(g for g in _GROUPS if target.kind in g)
    kind = rng.choice([k for k in group if k != target.kind])
    sources = {slot: target.sources[slot] if slot in target.sources
               else rng.randrange(len(state.mentions)) for slot in SLOTS[kind]}
    return resolve(kind, sources, state.mentions)


def _wrong_mention(rng: random.Random, plan: list[Step], state: State, target: Step) -> Step | None:
    """The right kind, but two slots swapped, or one slot pointed at another number.
    The value always changes, so the step really is wrong."""
    if target.kind in CONTROL:
        return None
    values, sources = state.mentions, dict(target.sources)
    slots = list(sources)
    swaps = [(a, b) for i, a in enumerate(slots) for b in slots[i + 1:]
             if values[sources[a]] != values[sources[b]]]
    if swaps and rng.random() < 0.5:
        a, b = rng.choice(swaps)
        sources[a], sources[b] = sources[b], sources[a]
    else:
        slot = rng.choice(slots)
        others = [i for i, value in enumerate(values) if value != values[sources[slot]]]
        if not others:
            return None
        sources[slot] = rng.choice(others)
    return resolve(target.kind, sources, values)


def _out_of_order(rng: random.Random, plan: list[Step], state: State, target: Step) -> Step | None:
    """An item the plan has further on, built now."""
    later = plan[len(state.built) + 1:]
    return rng.choice(later) if later else None


def _repeat(rng: random.Random, plan: list[Step], state: State, target: Step) -> Step | None:
    """An item that is already built, built a second time (usually the latest one)."""
    if not state.built:
        return None
    item = state.built[-1] if rng.random() < 0.5 else rng.choice(state.built)
    return Step(item.kind, dict(item.slots), dict(item.sources))


def _random(rng: random.Random, plan: list[Step], state: State, target: Step) -> Step | None:
    kind = rng.choice(_RANDOM_KINDS)
    if kind == "undo":
        return UNDO
    sources = {slot: rng.randrange(len(state.mentions)) for slot in SLOTS[kind]}
    return resolve(kind, sources, state.mentions)


_MAKERS = {"wrong_kind": _wrong_kind, "wrong_mention": _wrong_mention,
           "out_of_order": _out_of_order, "repeat": _repeat, "random": _random}


def wrong_step(rng: random.Random, plan: list[Step], state: State,
               target: Step) -> tuple[Step, str]:
    """A step that is not the teacher's, and the name of the kind of mistake it is.

    The five kinds are tried in a random order; one that does not apply to this
    state (nothing built yet to repeat, say) passes to the next. `random` always
    applies, so a wrong step is always found.
    """
    order = list(WRONG_KINDS)
    rng.shuffle(order)
    for name in order:
        step = _MAKERS[name](rng, plan, state, target)
        if step is not None and not same_step(step, target):
            return step, name
    while True:     # only reached if `random` happened to draw the teacher's own step
        step = _random(rng, plan, state, target)
        if not same_step(step, target):
            return step, "random"
