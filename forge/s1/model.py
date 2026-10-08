"""The Forge-S1 network: rows in, one score per candidate row out.

    scores = model(batch)        # [B, R]; batch comes from data.StepData.batch

Three stages, each a few lines:

1. ROW VECTORS. Every row is the SUM of its facts' vectors (encode.py says what a fact is):
       word fact    -> a learned vector per word
       number fact  -> a learned linear map per field, applied to 6 simple features of the value
       link fact    -> (a learned vector per role) * (a learned vector per row id), element by element
   Why multiply for links: a row can hold several links ("my sketch is row 5", "my body is
   row 0"). Adding role and id vectors would lose which id goes with which role; multiplying
   binds each pair before everything is summed.

2. A TRANSFORMER ENCODER over the rows of one step: every row looks at every other row
   (attention), a few layers deep. This is where "the sketch's length equals plan item 0's
   length slot, and plan item 0 is the item being built" gets worked out.

3. A SCORE per row from a small linear layer. Only candidate rows are used (loss.py).

Sizes come from the config (configs/s1_first.yaml) and the vocabulary sizes from the data
manifest, so this file imports nothing from the rest of forge.
"""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F
from torch import nn

from forge.s1.data import DOC_IDS, PLAN_IDS

N_FEATURES = 6      # how many features a number is turned into (number_features)


def number_features(value: torch.Tensor, whole: bool = True) -> torch.Tensor:
    """[N] values -> [N, 6] features. A number never becomes a token.

    `whole=False` sets the last feature to zero for every number (config `use_whole: false`):
    in the generated training parts most sizes are always whole numbers and a
    wrong number often is not, so "is it whole" can stand in for "is it right".

    1                       "this field is present" (lets the field have a vector of its own)
    signed log              sign(v) * log(1 + |v|) / log(1001): 1 mm and 1000 mm both readable
    v / 100, clipped        fine detail for ordinary sizes
    v == 0, v < 0, whole    three yes/no facts that are hard to read off a scaled float
    """
    value = value.float()
    return torch.stack([
        torch.ones_like(value),
        torch.sign(value) * torch.log1p(value.abs()) / math.log(1001.0),
        (value / 100.0).clamp(-5.0, 5.0),
        (value == 0).float(),
        (value < 0).float(),
        (value == value.round()).float() if whole else torch.zeros_like(value),
    ], dim=1)


class Block(nn.Module):
    """One transformer layer (pre-norm): attention, then a small MLP, each added to the input."""

    def __init__(self, width: int, heads: int, mlp_width: int, dropout: float) -> None:
        super().__init__()
        self.heads = heads
        self.norm1 = nn.LayerNorm(width)
        self.to_qkv = nn.Linear(width, 3 * width)       # queries, keys and values in one layer
        self.attn_out = nn.Linear(width, width)
        self.norm2 = nn.LayerNorm(width)
        self.mlp = nn.Sequential(nn.Linear(width, mlp_width), nn.GELU(),
                                 nn.Linear(mlp_width, width))
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, may_see: torch.Tensor) -> torch.Tensor:
        """x [B, R, D];  may_see [B, 1, 1, R] bool: False for padding rows (nobody looks at them)."""
        size, rows, width = x.shape
        q, k, v = self.to_qkv(self.norm1(x)).chunk(3, dim=-1)                # each [B, R, D]
        # split D into `heads` smaller vectors: [B, R, D] -> [B, heads, R, D / heads]
        q, k, v = (t.view(size, rows, self.heads, width // self.heads).transpose(1, 2)
                   for t in (q, k, v))
        # softmax(q . k / sqrt(d)) weights, applied to v; rows that may not be seen get weight 0
        mixed = F.scaled_dot_product_attention(q, k, v, attn_mask=may_see)   # [B, heads, R, D/heads]
        mixed = mixed.transpose(1, 2).reshape(size, rows, width)
        x = x + self.dropout(self.attn_out(mixed))
        return x + self.dropout(self.mlp(self.norm2(x)))


class ForgeS1(nn.Module):
    def __init__(self, n_words: int, n_num_fields: int, role_is_doc: list[bool], width: int = 160,
                 layers: int = 6, heads: int = 4, mlp_width: int = 640, dropout: float = 0.0,
                 use_whole: bool = True) -> None:
        super().__init__()
        self.width = width
        self.use_whole = use_whole
        self.word = nn.Embedding(n_words, width)
        # per number field a [6, D] matrix: the field's vector for each of the 6 features
        self.number = nn.Embedding(n_num_fields, N_FEATURES * width)
        self.role = nn.Embedding(len(role_is_doc), width)
        # one table for both kinds of row id: plan ids first, then document ids
        self.row_id = nn.Embedding(PLAN_IDS + DOC_IDS, width)
        self.register_buffer("role_is_doc", torch.tensor(role_is_doc), persistent=False)
        self.norm_in = nn.LayerNorm(width)
        self.blocks = nn.ModuleList(Block(width, heads, mlp_width, dropout) for _ in range(layers))
        self.norm_out = nn.LayerNorm(width)
        self.score = nn.Linear(width, 1)
        self.apply(self._init)

    @staticmethod
    def _init(module: nn.Module) -> None:
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(module.weight, std=0.02)
        if isinstance(module, nn.Linear) and module.bias is not None:
            nn.init.zeros_(module.bias)

    def rows(self, batch: dict) -> torch.Tensor:
        """Stage 1. Returns [B, R, D]: every row as the sum of its facts' vectors."""
        size, most = batch["n_steps"], batch["n_rows_max"]
        words = self.word(batch["word_id"].long())                                   # [W, D]
        table = self.number(batch["num_field"].long()).view(-1, N_FEATURES, self.width)   # [N, 6, D]
        numbers = (table * number_features(batch["num_value"], self.use_whole)[:, :, None]).sum(dim=1)    # [N, D]
        role = batch["link_role"].long()
        owner, target = batch["link_owner"], batch["link_target"].long()
        # The id of the row a link points at, looked up in this step's (random) id lists.
        # Both lists side by side: [B, 32 plan columns + 64 object columns]. A link to an
        # object reads from the object columns, whose ids come after the plan ids in the table.
        ids = torch.cat([batch["plan_ids"], PLAN_IDS + batch["doc_ids"]], dim=1)
        column = target + self.role_is_doc[role] * batch["plan_ids"].shape[1]
        link_id = ids[owner, column]
        links = self.role(role) * self.row_id(link_id)                               # [L, D]
        flat = torch.zeros(size * most, self.width, device=words.device, dtype=words.dtype)
        flat.index_add_(0, batch["word_at"], words)
        flat.index_add_(0, batch["num_at"], numbers.to(words.dtype))
        flat.index_add_(0, batch["link_at"], links.to(words.dtype))
        return flat.view(size, most, self.width)

    def forward(self, batch: dict) -> torch.Tensor:
        """Returns scores [B, R], one per row. Use them on candidate rows only (loss.py)."""
        x = self.norm_in(self.rows(batch))
        may_see = batch["is_row"][:, None, None, :]                                  # [B, 1, 1, R]
        for block in self.blocks:
            x = block(x, may_see)
        return self.score(self.norm_out(x)).squeeze(-1)


def build_model(config: dict, vocab: dict) -> ForgeS1:
    """The model for a `model:` config section and a manifest's `vocab` section."""
    return ForgeS1(n_words=len(vocab["words"]), n_num_fields=len(vocab["num_fields"]),
                   role_is_doc=[table == "doc" for _, table in vocab["roles"]], **config)


def count_parameters(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())
