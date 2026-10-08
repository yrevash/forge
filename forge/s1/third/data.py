"""The third model's batches: everything of forge/s1/data.py, plus the bindings.

A BINDING QUERY is one accepted command of a step that works on a plan item. A batch of B
steps holds Q of them (a step has none, one or a few), laid end to end like the facts:

    n_plan     [B]      how many plan rows each step has (they are its first rows)
    bind_step  [Q]      which of the B steps the query is in
    bind_row   [Q]      the row of its command among that step's rows (a candidate row)
    bind_item  [Q]      the plan item the teacher's command works on
    bind_kinds [Q, A]   the kind of each argument (bindings.KINDS); 0 = no such argument

These are LABELS. The model reads none of them when it scores commands; the binding head
is asked about (bind_step, bind_row) in training because that is where the answer is known.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import numpy as np
import torch

from forge.s1.data import (
    FAMILIES,
    STEP_FIELDS,
    StepData,
    first_steps,
    offsets,
    read_manifest,
    read_shards,
)

BIND_FIELDS = ("bind_cand", "bind_item", "bind_k0", "bind_k1", "bind_k2")


def select_steps(arrays: dict[str, np.ndarray], keep: np.ndarray, step_fields: tuple[str, ...],
                 families: dict[str, tuple[str, ...]]) -> dict[str, np.ndarray]:
    """Only the steps where `keep` [N] is True, with exactly their facts."""
    out = {name: arrays[name][keep] for name in step_fields}
    for family, names in families.items():
        counts = arrays[f"n_{family}"]
        of_fact = np.repeat(keep, counts)           # [F]: does this fact belong to a kept step?
        out[f"n_{family}"] = counts[keep]
        out.update({name: arrays[name][of_fact] for name in names})
    return out


class BindData(StepData):
    step_fields: ClassVar = (*STEP_FIELDS, "n_plan")
    families: ClassVar = {**FAMILIES, "bind": BIND_FIELDS}

    def __init__(self, arrays: dict[str, np.ndarray], device: torch.device | str,
                 role_is_doc: list[bool] | None = None) -> None:
        super().__init__(arrays, device, role_is_doc)
        self.t["n_plan"] = torch.from_numpy(arrays["n_plan"].astype(np.int64)).to(self.device)
        self.offset["bind"] = torch.from_numpy(offsets(arrays["n_bind"])).to(self.device)
        for name in BIND_FIELDS:
            self.t[name] = torch.from_numpy(arrays[name].astype(np.int64)).to(self.device)

    @classmethod
    def load(cls, folder: str | Path, slices: list[str], device: torch.device | str,
             shards: list[str] | None = None, max_steps: int | None = None,
             plan_items: list[int] | None = None) -> BindData:
        """As StepData.load. `plan_items` [low, high] keeps only the steps whose plan has that
        many items (for the experiment "train on short plans, score on longer ones")."""
        if plan_items is None:
            return super().load(folder, slices, device, shards, max_steps)
        manifest = read_manifest(folder)
        files = [note["file"] for name in slices for note in manifest["slices"][name]["shards"]]
        if shards is not None:
            files = [name for name in files if name in set(shards)]
        parts: dict[str, list[np.ndarray]] = {}
        for file in files:              # shard by shard: the whole set is never in memory twice
            shard = read_shards(folder, [file], None, cls.step_fields, cls.families)
            keep = (shard["n_plan"] >= plan_items[0]) & (shard["n_plan"] <= plan_items[1])
            for name, values in select_steps(shard, keep, cls.step_fields, cls.families).items():
                parts.setdefault(name, []).append(values)
        arrays = {name: np.concatenate(parts.pop(name)) for name in list(parts)}
        if max_steps is not None and len(arrays["n_rows"]) > max_steps:
            arrays = first_steps(arrays, max_steps, cls.step_fields, cls.families)
        return cls(arrays, device, [table == "doc" for _, table in manifest["vocab"]["roles"]])

    def batch(self, steps: torch.Tensor, random_ids: torch.Generator | None = None) -> dict:
        out = super().batch(steps, random_ids)
        steps = steps.to(self.device)
        where, owner = self.take("bind", steps)                                 # [Q], [Q]
        first_candidate = (self.t["n_rows"][steps] - self.t["n_cand"][steps])[owner]
        out["n_plan"] = self.t["n_plan"][steps]
        out["bind_step"] = owner
        out["bind_row"] = first_candidate + self.t["bind_cand"][where]
        out["bind_item"] = self.t["bind_item"][where]
        out["bind_kinds"] = torch.stack([self.t[name][where] for name in BIND_FIELDS[2:]], dim=1)
        return out
