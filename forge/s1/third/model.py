"""The third Forge-S1 network: the first model's body, new number features, a binding head.

    scores, h = model(batch)                     # [B, R] command scores, [B, R, D] row vectors
    items = model.item_scores(h, step, row, n_plan)        # [Q, P]  which plan item
    kinds = model.kind_logits(h, step, row, item)          # [Q, A, K]  which source per argument

WHAT IS THE SAME (forge/s1/model.py): a row is the sum of its facts' vectors, the rows of a
step look at each other through transformer layers, every row gets one score, and only the
candidate rows compete (one per valid command).

WHAT IS NEW

1. NUMBERS BY RELATION. The encoder already says "this length equals the length slot of plan
   item 0" as a link fact. The first models also got the raw size (log and v / 100) and "is
   it a whole number", and leaned on them: sizes outside the generator's habits were called
   wrong. Here a SIZE gives only: present, is zero, is negative, and a coarse size class
   (two classes per factor of ten). A COUNT (dof, sides, how many copies) keeps its value,
   because there the value itself is the fact. Which fields are counts comes from the
   vocabulary (vocab.COUNT_NAMES).

2. THE BINDING HEAD. Once a command is chosen, its candidate row vector c is asked two
   things (bindings.py says what they mean):
       which plan item?   score_i = (W_q c) . (W_k p_i) / sqrt(D)   over the plan rows p_i
       which source?      [c ; p_item] -> a small MLP -> A x K scores (A arguments, K kinds)
   Pointing by a dot product means a plan of 13 items needs no more weights than one of 2.

3. THE PREVIOUS COMMANDS are word facts on the session row. `use_history: false` makes the
   model blind to them (their vectors are set to zero), so the same arrays train both.
"""

from __future__ import annotations

import math

import torch
from torch import nn

from forge.s1.data import PLAN_IDS
from forge.s1.model import ForgeS1

SIZE_CLASSES = 12       # |v| from 0.01 to 10,000 in half decades
N_FEATURES = 5 + SIZE_CLASSES


def number_features(value: torch.Tensor, is_count: torch.Tensor) -> torch.Tensor:
    """[N] values, [N] bool -> [N, 17] features.

    0  1                    this field is present
    1  v == 0
    2  v < 0
    3  counts only: sign(v) * log(1 + |v|) / log(1001)
    4  counts only: v / 10, clipped
    5..16  sizes only: one of 12 size classes, class = floor(2 * log10 |v|) + 4, clipped to
           0..11 (so 35 and 42 share a class, 35 and 420 do not). Zero has no class.
    """
    # FreeCAD reports the bounding box of a broken solid as 1e100, which is infinite as a
    # 32-bit float. Clipped first, so that no feature becomes "not a number".
    value = torch.nan_to_num(value.float(), nan=0.0, posinf=1e30, neginf=-1e30).clamp(-1e30, 1e30)
    size = value.abs()
    cls = (torch.floor(2 * torch.log10(size.clamp(min=1e-6))) + 4).clamp(0, SIZE_CLASSES - 1)
    classes = torch.nn.functional.one_hot(cls.long(), SIZE_CLASSES).float()
    classes = classes * ((size > 0) & ~is_count)[:, None]
    zero = torch.zeros_like(value)
    plain = torch.stack([
        torch.ones_like(value), (value == 0).float(), (value < 0).float(),
        torch.where(is_count, torch.sign(value) * torch.log1p(size) / math.log(1001.0), zero),
        torch.where(is_count, (value / 10.0).clamp(-5.0, 5.0), zero)], dim=1)
    return torch.cat([plain, classes], dim=1)


class ForgeS1Third(ForgeS1):
    def __init__(self, n_words: int, n_num_fields: int, role_is_doc: list[bool],
                 num_is_count: list[bool], history_words: tuple[int, int],
                 kind_needs_item: list[bool], max_args: int, width: int = 160, layers: int = 6, heads: int = 4,
                 mlp_width: int = 640, dropout: float = 0.0, use_history: bool = True) -> None:
        super().__init__(n_words, n_num_fields, role_is_doc, width, layers, heads, mlp_width,
                         dropout)
        self.use_history, self.history_words = use_history, tuple(history_words)
        self.n_kinds, self.max_args = len(kind_needs_item), max_args
        # [K] bool: kinds that read the pointed plan item (a slot), not a constant or a rule
        self.register_buffer("kind_needs_item", torch.tensor(kind_needs_item), persistent=False)
        self.number = nn.Embedding(n_num_fields, N_FEATURES * width)    # 17 features, not 6
        self.register_buffer("num_is_count", torch.tensor(num_is_count), persistent=False)
        self.item_query = nn.Linear(width, width)
        self.item_key = nn.Linear(width, width)
        self.kind = nn.Sequential(nn.Linear(2 * width, mlp_width), nn.GELU(),
                                  nn.Linear(mlp_width, max_args * self.n_kinds))
        self.apply(self._init)

    def rows(self, batch: dict) -> torch.Tensor:
        """[B, R, D]: every row as the sum of its facts' vectors (see forge/s1/model.py)."""
        size, most = batch["n_steps"], batch["n_rows_max"]
        word_id = batch["word_id"].long()
        words = self.word(word_id)                                                   # [W, D]
        if not self.use_history:
            first, end = self.history_words
            words = words * ((word_id < first) | (word_id >= end))[:, None]
        field = batch["num_field"].long()
        table = self.number(field).view(-1, N_FEATURES, self.width)                  # [N, 17, D]
        features = number_features(batch["num_value"], self.num_is_count[field])     # [N, 17]
        numbers = (table * features[:, :, None]).sum(dim=1)                          # [N, D]
        role = batch["link_role"].long()
        ids = torch.cat([batch["plan_ids"], PLAN_IDS + batch["doc_ids"]], dim=1)
        column = batch["link_target"].long() + self.role_is_doc[role] * batch["plan_ids"].shape[1]
        links = self.role(role) * self.row_id(ids[batch["link_owner"], column])      # [L, D]
        flat = torch.zeros(size * most, self.width, device=words.device, dtype=words.dtype)
        flat.index_add_(0, batch["word_at"], words)
        flat.index_add_(0, batch["num_at"], numbers.to(words.dtype))
        flat.index_add_(0, batch["link_at"], links.to(words.dtype))
        return flat.view(size, most, self.width)

    def forward(self, batch: dict) -> tuple[torch.Tensor, torch.Tensor]:
        """(scores [B, R] one per row, h [B, R, D] the row vectors the binding head reads)."""
        x = self.norm_in(self.rows(batch))
        may_see = batch["is_row"][:, None, None, :]
        for block in self.blocks:
            x = block(x, may_see)
        h = self.norm_out(x)
        return self.score(h).squeeze(-1), h

    def item_scores(self, h: torch.Tensor, step: torch.Tensor, row: torch.Tensor,
                    n_plan: torch.Tensor) -> torch.Tensor:
        """Which plan item does the command at (step, row) work on?

        h [B, R, D];  step [Q], row [Q] where the command's candidate row is;  n_plan [B]
        returns [Q, P] scores over the plan rows (P = the longest plan of the batch); places
        beyond a step's own plan hold the lowest float, so they never win.
        """
        most = int(n_plan.max())
        query = self.item_query(h[step, row])                                   # [Q, D]
        keys = self.item_key(h[:, :most])[step]                                 # [Q, P, D]
        scores = (query[:, None, :] * keys).sum(dim=-1).float() / math.sqrt(self.width)
        beyond = torch.arange(most, device=h.device)[None, :] >= n_plan[step][:, None]
        return scores.masked_fill(beyond, torch.finfo(scores.dtype).min)

    def kind_logits(self, h: torch.Tensor, step: torch.Tensor, row: torch.Tensor,
                    item: torch.Tensor) -> torch.Tensor:
        """Where does each argument come from? [Q, A, K] scores over the kinds, given the
        command's row and the plan item it works on (`item` [Q]: a plan row of that step)."""
        both = torch.cat([h[step, row], h[step, item]], dim=-1)                 # [Q, 2D]
        return self.kind(both).float().view(-1, self.max_args, self.n_kinds)


def build_model(config: dict, vocab: dict) -> ForgeS1Third:
    """The model for a `model:` config section and the third manifest's `vocab` section."""
    return ForgeS1Third(
        n_words=len(vocab["words"]), n_num_fields=len(vocab["num_fields"]),
        role_is_doc=[table == "doc" for _, table in vocab["roles"]],
        num_is_count=vocab["num_is_count"], history_words=tuple(vocab["history_words"]),
        kind_needs_item=[kind.startswith(("slot:", "half:")) or kind == "row_first"
                         for kind in vocab["kinds"]],
        max_args=vocab["max_args"], **config)
