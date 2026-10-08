"""Second arithmetic top-up (6 Oct 2026, morning): the ten cells still under their floor.

Same as topup.py with its own focus list (below, not a config file: it is short) and one
change: a distance is written as a reference form (`inset same as X's depth`) six times
in ten instead of six in a hundred, because `amount:same as` was among the short cells.
"""

from __future__ import annotations

from typing import ClassVar

from forge.plans_data.random_gen import num
from forge.plans_data.topup import TopUpMaker

GENERATOR = "topup2-1"
SIDES = ("in front of", "behind", "left of", "right of")


class TopUp2Maker(TopUpMaker):
    focus: ClassVar[list[dict]] = (
        [{"kind": "under", "option": "gap"}] * 3
        + [{"kind": "under", "option": "across", "sets": True}] * 3
        + [{"kind": kind, "rep": "grid"} for kind in ("behind", "behind")]      # front-back face
        + [{"group": True}] * 8 + [{"raised": True}] * 2
        + [{"kind": kind, "align": ["flush", "left"]} for kind in ("on top of", "under")] * 2)

    def amount(self, limit: float = 50) -> str:
        if self.rng.random() < 0.6:
            options = [f"same as {self.own(info.name, dim)}" for info in self.infos().values()
                       for dim, got in self._dims(info).items() if 2 <= got <= limit]
            if options:
                return self.rng.choice(options)
        return num(self.pick(limit, (5, 10, 15, 20, 25, 30, 40, 50)))
