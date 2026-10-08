"""The teacher: for any state, the one correct next step.

It is a function of the plan and the state and nothing else. It keeps no memory
of how the state was reached, so it can label any state at all: one from a clean
session, one after injected mistakes, or later one a model drove itself into.

The rule, in order:
1. if what is built is not the plan so far, the answer is `undo`;
2. otherwise the answer is the next item of the plan;
3. when the whole plan is built, the answer is `done`.

"The plan so far" means every built item equals the plan's item at the same
position. Rule 1 can be worded as "the last built item is not what the
plan has at that position"; checking every item gives the same answer whenever
only the last one is wrong, and stays right in the one case the short wording
misses: a wrong item with a correct one built on top of it. Undo removes only
the last item, so the answer there is still `undo`, again and again, until the
wrong item is gone.

"Equals" compares the kind and the VALUE of each slot, not which mention the
value was copied from. A 40 by 40 plate built from the "wrong" 40 is the right
plate, and a careful person would not undo it.
"""

from __future__ import annotations

from forge.system1.engine import Built, State
from forge.system1.steps import DONE, UNDO, Step


def matches(item: Built, step: Step) -> bool:
    """Is this built item what the plan asks for at its position?"""
    return item.kind == step.kind and item.slots == step.slots


def on_plan(plan: list[Step], state: State) -> bool:
    """Is everything built so far exactly the beginning of the plan?"""
    return len(state.built) <= len(plan) and all(
        matches(item, step) for item, step in zip(state.built, plan, strict=False))


def teacher(plan: list[Step], state: State) -> Step:
    """The one correct next step for this state."""
    if not on_plan(plan, state):
        return UNDO
    if len(state.built) < len(plan):
        return plan[len(state.built)]
    return DONE
