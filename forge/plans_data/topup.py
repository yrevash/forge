"""Top-up plans: short arithmetic plans that aim at the cells still under their minimum.

The ordinary random stream draws every choice evenly, but some forms are rejected far
more often than others (a single named corner needs a top or bottom face with room; a
group placed `under` needs something raised), so their cells fill slowly. After the
main run, the cells under their minimum are written as a list of FOCUS MOVES into
`configs/plans_topup.json`; each entry is the arguments of one `Maker.move_face` call:

    {"kind": "under", "rep": "corner"}          a named corner under a raised part
    {"kind": "behind", "align": ["same axis", ""]}
    {"kind": "left of", "option": "across", "sets": true}
    {"group": true}                              a group, declared and placed
    {"between": true}                            a part between two parts (set forms preferred)

A top-up plan is an ordinary random plan in which six moves in ten are focus moves. It
uses the same line builders, the same oracle and the same checks as every other random
plan; only the mix of moves differs. The list is part of the run's config, so the stream
is as deterministic as the others.
"""

from __future__ import annotations

import json
from typing import ClassVar

from forge.plans_data import config
from forge.plans_data.random_gen import GiveUp, Made, Maker
from forge.runs import PROJECT_ROOT

GENERATOR = "topup-1"
FOCUS_FILE = PROJECT_ROOT / "configs" / "plans_topup.json"


def focus_moves() -> list[dict]:
    return json.loads(FOCUS_FILE.read_text())["moves"]


class TopUpMaker(Maker):
    focus: ClassVar[list[dict]] = []

    def focus_move(self) -> bool:
        move = dict(self.rng.choice(self.focus))
        if move.pop("group", False):
            return self.move_group()
        if move.pop("raised", False):
            return self.move_raised()
        if move.pop("between", False):
            return self.together(lambda: self.try_lines(self.between_line)
                                 and (self.note_used() or True))
        if "align" in move:
            move["align"] = tuple(move["align"])
        return self.move_face(**move)

    def make(self) -> Made | None:
        rng = self.rng
        wanted = rng.randint(4, 14)
        try:
            self.start()
            if rng.random() < 0.5:
                self.move_raised()              # something to put parts under
            weights, makers = zip(*self.moves(), strict=True)
            budget = wanted * 6
            while len(self.text) < wanted - 1 and budget > 0:
                budget -= 1
                if rng.random() < 0.6:
                    self.focus_move()
                else:
                    rng.choices(makers, weights)[0]()
            if len(self.text) > config.MAX_TRAIN_LINES - 1:
                return None
            done = self.force("done")
        except GiveUp as why:
            self.count(f"gave up: {why}")
            return None
        if not (done.built and all(reply.built for reply in self.replies)):
            self.count("done was refused")
            return None
        return self.finish(True)
