"""One live view -> the third model's decision: a command, a plan item, a kind per argument.

    decision = decide(model, view)
    decision.command        "constrain_length"
    decision.item           2                      the plan item it pointed at
    decision.kinds          ["slot:length"]        where each argument comes from
    decision.args           {"value": 15.0}        worked out by bindings.resolve from the
                                                   view's own plan: no teacher is involved

The only help the model gets is a MASK on the kinds (bindings.allowed): a kind that cannot
be read at all for the pointed item (the item has no such slot; a word where a number is
needed) cannot be chosen. Whether a readable value is the RIGHT one is the model's business.

`explore` (DAgger only, never in a measurement): draw the command, the item and the kinds
from the model's own probabilities at a temperature instead of taking the best.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import torch

from forge.freecad.catalogue import COMMANDS
from forge.s1 import vocab
from forge.s1.encode import Encoded, encode
from forge.s1.live import steps_arrays
from forge.s1.third.bindings import KINDS, MAX_ARGS, allowed, resolve
from forge.s1.third.data import BIND_FIELDS, BindData


@dataclass
class Decision:
    ranked: list[str]           # every valid command, the model's first choice first
    command: str
    item: int | None            # None for a command without arguments
    kinds: list[str]
    args: dict | None           # None only if a kind could not be read (the mask prevents it)


def steps_data(encoded: list[Encoded], device: torch.device | str) -> BindData:
    """Live steps as a BindData without labels (no accepted commands, no binding queries)."""
    arrays = steps_arrays(encoded)
    arrays["n_plan"] = np.asarray([enc.n_plan for enc in encoded], dtype=np.uint8)
    arrays["n_bind"] = np.zeros(len(encoded), dtype=np.uint8)
    arrays.update({name: np.zeros(0, dtype=np.uint8) for name in BIND_FIELDS})
    roles = [table == "doc" for _, table in vocab.third_json()["roles"]]
    return BindData(arrays, device, roles)


def _pick(scores: torch.Tensor, explore: tuple[random.Random, float] | None) -> int:
    """The best place of [n] scores, or (exploring) one drawn from softmax(scores / T)."""
    if explore is None:
        return int(scores.argmax())
    rng, temperature = explore
    weights = torch.softmax(scores.double() / temperature, dim=0).tolist()
    return rng.choices(range(len(weights)), weights=weights)[0]


@torch.no_grad()
def decide(model: torch.nn.Module, seen: dict, device: torch.device | str = "cpu",
           explore: tuple[random.Random, float] | None = None,
           ids: torch.Generator | None = None,
           avoid: Callable[[str, dict | None], bool] | None = None) -> Decision:
    """The model's command and binding for this view (load.view with `history`).
    `ids`: draw random row ids from this generator, for a model trained
    with them; None = the identity.
    `avoid` (a driver-side extra, never the headline): a test on (command, arguments). The
    commands are tried from the model's first choice down and the first one that `avoid`
    lets through is taken; if it lets none through, the first choice stands."""
    enc = encode(seen, third=True)
    batch = steps_data([enc], device).batch(torch.arange(1), ids)
    scores, h = model(batch)
    first = enc.n_rows - len(enc.candidates)
    own = scores[0, first:enc.n_rows].float()                               # [candidates]
    order = [int(k) for k in torch.argsort(own, descending=True, stable=True)]
    ranked = [enc.candidates[k] for k in order]

    def bound(place: int) -> Decision:
        """Candidate `place` as a full decision: its plan item and one kind per argument."""
        command = enc.candidates[place]
        if not COMMANDS[command]["args"]:
            return Decision(ranked, command, None, [], {})
        zero, row = torch.zeros(1, dtype=torch.long), torch.tensor([first + place])
        items = model.item_scores(h, zero, row, batch["n_plan"])[0, :enc.n_plan]
        item = _pick(items, explore)
        logits = model.kind_logits(h, zero, row, torch.tensor([item]))[0]   # [A, K]
        plan, kinds = seen["plan"], []
        for a, may in enumerate(allowed(plan, command, item)):
            masked = logits[a].masked_fill(~torch.tensor(may), float("-inf"))
            kinds.append(KINDS[_pick(masked, explore)] if any(may) else KINDS[0])
        assert len(kinds) <= MAX_ARGS
        args = None if KINDS[0] in kinds else resolve(plan, command, item, kinds)
        return Decision(ranked, command, item, kinds, args)

    chosen = bound(_pick(own, explore))
    if avoid is None or explore is not None or not avoid(chosen.command, chosen.args):
        return chosen
    for place in order[1:]:
        other = bound(place)
        if not avoid(other.command, other.args):
            return other
    return chosen
