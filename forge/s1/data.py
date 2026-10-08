"""Prepared arrays -> batches. Needs only numpy and torch (it runs on Kaggle without forge).

    data = StepData.load("data/s1/v1", ["train_v2"], device)
    batch = data.batch(indices, random_ids=generator)     # a dict of tensors, see `batch`

All the arrays live on the training device (the GPU when there is one). A batch is cut out
of them with a few tensor operations, so there is no Python loop per step and no worker
processes. The whole of train_v2 is about 4 GB like this.

The one idea worth understanding here is `take`: every step owns a different number of
facts, laid end to end in one long array. `offset[i]` says where step i's facts start.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch

PLAN_IDS = 64       # size of the id table for plan items
DOC_IDS = 128       # size of the id table for document objects
FAMILIES = {"word": ("word_row", "word_id"), "num": ("num_row", "num_field", "num_value"),
            "link": ("link_row", "link_role", "link_target")}
STEP_FIELDS = ("n_rows", "n_cand", "target", "group")


def read_manifest(folder: str | Path) -> dict:
    return json.loads((Path(folder) / "manifest.json").read_text())


def offsets(counts: np.ndarray) -> np.ndarray:
    """[N] counts -> [N + 1] starts: step i owns the facts offsets[i] .. offsets[i + 1]."""
    return np.concatenate([[0], np.cumsum(counts, dtype=np.int64)])


class StepData:
    """Steps of one or more slices, as tensors on one device."""

    # What a shard holds per step and per fact. The third model's data adds to both
    # (forge/s1/third/data.py); nothing else differs.
    step_fields: tuple[str, ...] = STEP_FIELDS
    families: dict[str, tuple[str, ...]] = FAMILIES

    def __init__(self, arrays: dict[str, np.ndarray], device: torch.device | str,
                 role_is_doc: list[bool] | None = None) -> None:
        """`role_is_doc[r]`: does link role r point at a document object (True) or at a plan
        item (False)? Only needed for random row ids, which must know how many ids of each
        table a step uses (`row_ids`)."""
        self.n = len(arrays["n_rows"])
        self.device = torch.device(device)
        self.role_is_doc = None if role_is_doc is None \
            else torch.tensor(role_is_doc, device=self.device)
        self.t = {name: torch.from_numpy(arrays[name]).to(self.device)
                  for names in FAMILIES.values() for name in names}
        self.t.update({name: torch.from_numpy(arrays[name].astype(np.int64)).to(self.device)
                       for name in STEP_FIELDS})
        self.offset = {family: torch.from_numpy(offsets(arrays[f"n_{family}"])).to(self.device)
                       for family in FAMILIES}

    @classmethod
    def load(cls, folder: str | Path, slices: list[str], device: torch.device | str,
             shards: list[str] | None = None, max_steps: int | None = None) -> StepData:
        """Read prepared shards. `shards` picks files by name ("train_v2/shard000.npz");
        `max_steps` keeps only the first so many steps (for a fixed evaluation sample)."""
        manifest = read_manifest(folder)
        files = [note["file"] for name in slices for note in manifest["slices"][name]["shards"]]
        if shards is not None:
            files = [name for name in files if name in set(shards)]
        return cls(read_shards(folder, files, max_steps, cls.step_fields, cls.families), device,
                   [table == "doc" for _, table in manifest["vocab"]["roles"]])

    def __len__(self) -> int:
        return self.n

    def take(self, family: str, steps: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Where the facts of these steps are.

        steps    [B] step numbers
        returns  (where [F], owner [F]): the places of their F facts in the long arrays, and
                 for each fact which of the B steps it belongs to (0 .. B-1).
        """
        start = self.offset[family][steps]                         # [B]
        count = self.offset[family][steps + 1] - start             # [B]
        owner = torch.repeat_interleave(torch.arange(len(steps), device=self.device), count)
        first = torch.cumsum(count, 0) - count                     # [B] where each step starts in F
        inside = torch.arange(int(count.sum()), device=self.device) - first[owner]
        return start[owner] + inside, owner

    def batch(self, steps: torch.Tensor, random_ids: torch.Generator | None = None) -> dict:
        """The model's input and the labels for these steps.

        steps        [B] step numbers (on any device)
        random_ids   a generator: draw random row ids (training). None: identity (evaluation).

        Returns (B steps, R = the most rows any of them has, flat row = step * R + row):
            n_steps, n_rows_max          B and R
            word_at [W] word_id [W]                     the flat row of each word fact, its word
            num_at [N] num_field [N] num_value [N]
            link_at [L] link_role [L] link_target [L] link_owner [L]
                                         link_target: a plan item or object index;
                                         link_owner: which of the B steps the link is in
            plan_ids [B, 32]  doc_ids [B, 64]      this step's id for each index (row_ids)
            is_row [B, R]  is_candidate [B, R]  is_target [B, R]      bool
            group [B]                    evaluation group bits (never read by the model)
        """
        steps = steps.to(self.device)
        size = len(steps)
        n_rows, n_cand = self.t["n_rows"][steps], self.t["n_cand"][steps]
        most = int(n_rows.max())
        row = torch.arange(most, device=self.device)[None, :]                   # [1, R]
        is_row = row < n_rows[:, None]
        is_candidate = is_row & (row >= (n_rows - n_cand)[:, None])             # the last rows
        # candidate k of a step is row (n_rows - n_cand + k); its target bit is bit k.
        k = (row - (n_rows - n_cand)[:, None]).clamp(min=0)
        is_target = is_candidate & (((self.t["target"][steps][:, None] >> k) & 1) > 0)

        out = {"n_steps": size, "n_rows_max": most, "is_row": is_row,
               "is_candidate": is_candidate, "is_target": is_target,
               "group": self.t["group"][steps]}
        for family, (row_name, *value_names) in FAMILIES.items():
            where, owner = self.take(family, steps)
            out[f"{family}_at"] = owner * most + self.t[row_name][where].long()
            for name in value_names:
                out[name] = self.t[name][where]
            if family == "link":
                out["link_owner"] = owner
        n_plan = n_doc = None
        if random_ids is not None:
            # How many ids each step needs: one more than the highest index a link points at.
            if self.role_is_doc is None:
                raise ValueError("random row ids need role_is_doc (use StepData.load)")
            to_doc = self.role_is_doc[out["link_role"].long()]
            highest = out["link_target"].long() + 1
            zero = torch.zeros(size, dtype=torch.long, device=self.device)
            n_plan = zero.scatter_reduce(0, out["link_owner"][~to_doc], highest[~to_doc], "amax")
            n_doc = zero.scatter_reduce(0, out["link_owner"][to_doc], highest[to_doc], "amax")
        plan_ids, doc_ids = row_ids(size, random_ids, self.device, n_plan=n_plan, n_doc=n_doc)
        out["plan_ids"], out["doc_ids"] = plan_ids, doc_ids
        return out


def row_ids(size: int, generator: torch.Generator | None, device: torch.device,
            plan_items: int = 32, doc_items: int = 64, n_plan: torch.Tensor | None = None,
            n_doc: torch.Tensor | None = None) -> tuple[torch.Tensor, torch.Tensor]:
    """The id of the i-th plan item and of the j-th object, per step.

    n_plan [B], n_doc [B]   how many plan ids and object ids each step uses (training only)
    returns  plan_ids [B, 32] values in 0 .. PLAN_IDS-1,  doc_ids [B, 64] values in 0 .. DOC_IDS-1
    Evaluation (generator None): the identity, id = index.
    Training: a step that uses n ids gets n ids drawn from the WHOLE table, sorted, in its
    first n columns. So the order is kept, no id is tied to a place, and every id of the
    table is used equally often whatever the length of the plan. The columns after n are
    never read. The generator makes the draw repeatable (resume gives the same batches).

    Until 7 Oct 2026 this drew 32 sorted ids of 64 for every step and used the first n. A
    plan of 6 items then only ever saw the 6 SMALLEST of 32 ids: ids above about 20 were
    almost never trained, which is exactly what random ids are meant to prevent.
    """
    def draw(count: int, table: int, needed: torch.Tensor | None) -> torch.Tensor:
        if generator is None:
            return torch.arange(count).expand(size, count).to(device)
        if needed is None:
            raise ValueError("random row ids need the number of ids each step uses")
        # A random order of the whole table per step (drawn on the CPU, so that the same
        # seed gives the same ids on every device); the ids in its first n places are chosen.
        place = torch.rand(size, table, generator=generator).argsort(dim=1).argsort(dim=1)
        chosen = place.to(device) < needed.to(device)[:, None]                  # [B, table]
        # Sort so that the chosen ids come first, in increasing order.
        key = torch.arange(table, device=device)[None, :] + table * (~chosen)
        return key.sort(dim=1).values[:, :count].remainder(table)
    return draw(plan_items, PLAN_IDS, n_plan), draw(doc_items, DOC_IDS, n_doc)


def read_shards(folder: str | Path, files: list[str], max_steps: int | None = None,
                step_fields: tuple[str, ...] = STEP_FIELDS,
                families: dict[str, tuple[str, ...]] = FAMILIES) -> dict[str, np.ndarray]:
    """Several .npz shards joined end to end (session ids are dropped: bookkeeping only)."""
    names = (*step_fields, *(f"n_{family}" for family in families),
             *(name for family in families.values() for name in family))
    parts: dict[str, list[np.ndarray]] = {name: [] for name in names}
    steps = 0
    for file in files:
        with np.load(Path(folder) / file) as shard:
            for name in names:
                parts[name].append(shard[name])
        steps += len(parts["n_rows"][-1])
        if max_steps is not None and steps >= max_steps:
            break
    # One array at a time, dropping its pieces as it is joined: memory peaks at the whole
    # plus one array, not at twice the whole (the third model's training set is 19 GB).
    joined = {name: np.concatenate(parts.pop(name)) for name in list(parts)}
    if max_steps is not None and steps > max_steps:
        joined = first_steps(joined, max_steps, step_fields, families)
    return joined


def first_steps(arrays: dict[str, np.ndarray], n: int,
                step_fields: tuple[str, ...] = STEP_FIELDS,
                families: dict[str, tuple[str, ...]] = FAMILIES) -> dict[str, np.ndarray]:
    """Only the first n steps, with exactly their facts."""
    cut = {name: arrays[name][:n]
           for name in (*step_fields, *(f"n_{family}" for family in families))}
    for family, names in families.items():
        facts = int(arrays[f"n_{family}"][:n].sum(dtype=np.int64))
        cut.update({name: arrays[name][:facts] for name in names})
    return cut
