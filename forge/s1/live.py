"""Encoded views -> arrays -> a batch, without files. For one live step, and for tests.

prepare.py writes the arrays of millions of recorded steps to disk. When the model drives
FreeCAD there is one view at a time and no label; this file builds the same arrays for it.

    data = steps_data([encode(view)], device)
    scores = model(data.batch(torch.arange(1)))        # row ids: identity, as in evaluation
"""

from __future__ import annotations

import numpy as np
import torch

from forge.s1 import vocab
from forge.s1.data import StepData
from forge.s1.encode import Encoded
from forge.s1.loss import choose

# The same types prepare.py stores (prepare.STEP_ARRAYS and prepare.FACT_ARRAYS).
FACT_TYPES = {"word_row": np.uint8, "word_id": np.int16,
              "num_row": np.uint8, "num_field": np.uint8, "num_value": np.float32,
              "link_row": np.uint8, "link_role": np.int16, "link_target": np.uint8}


def steps_arrays(encoded: list[Encoded], targets: list[int] | None = None,
                 groups: list[int] | None = None) -> dict[str, np.ndarray]:
    """The arrays of these steps, laid out as in a prepared shard. Without `targets` the
    target set is empty (a live step has no label)."""
    count = len(encoded)
    arrays = {
        "n_rows": np.asarray([enc.n_rows for enc in encoded], dtype=np.uint8),
        "n_cand": np.asarray([len(enc.candidates) for enc in encoded], dtype=np.uint8),
        "target": np.asarray(targets or [0] * count, dtype=np.uint32),
        "group": np.asarray(groups or [0] * count, dtype=np.uint8),
        "n_word": np.asarray([len(enc.word_id) for enc in encoded], dtype=np.uint16),
        "n_num": np.asarray([len(enc.num_value) for enc in encoded], dtype=np.uint16),
        "n_link": np.asarray([len(enc.link_role) for enc in encoded], dtype=np.uint16),
    }
    for name, kind in FACT_TYPES.items():
        arrays[name] = np.asarray([value for enc in encoded for value in getattr(enc, name)],
                                  dtype=kind)
    return arrays


def steps_data(encoded: list[Encoded], device: torch.device | str,
               targets: list[int] | None = None, groups: list[int] | None = None) -> StepData:
    roles = [table == "doc" for _, table in vocab.as_json()["roles"]]
    return StepData(steps_arrays(encoded, targets, groups), device, roles)


@torch.no_grad()
def rank_commands(model: torch.nn.Module, encoded: Encoded, device: torch.device | str,
                  random_ids: torch.Generator | None = None) -> list[str]:
    """Every valid command of this view, the one the model scores highest first.
    `random_ids`: draw random row ids from this generator instead of the
    identity; for a model that was trained with them and scores better with them."""
    batch = steps_data([encoded], device).batch(torch.arange(1), random_ids)
    first = encoded.n_rows - len(encoded.candidates)
    scores = model(batch)[0, first:encoded.n_rows].float()                  # [candidates]
    order = torch.argsort(scores, descending=True, stable=True).tolist()
    return [encoded.candidates[place] for place in order]


@torch.no_grad()
def pick_command(model: torch.nn.Module, encoded: Encoded, device: torch.device | str) -> str:
    """The valid command the model scores highest in this view."""
    batch = steps_data([encoded], device).batch(torch.arange(1))
    row = int(choose(model(batch), batch["is_candidate"])[0])
    return encoded.candidates[row - (encoded.n_rows - len(encoded.candidates))]
