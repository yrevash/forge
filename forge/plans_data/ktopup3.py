"""Third kernel top-up (6 Oct 2026, 10:00): the last kernel cells under their floor.

After ktopup2 these were still short: the inner-face alignments, a feature inside a
group, `through` and `around` along length and depth (a lying tube, a lying cylinder), and
one pairing: a part placed `under` a cone (the cone has to hang beside something raised).
Same builders, another mix.
"""

from __future__ import annotations

from forge.plans_data import config
from forge.plans_data.ktopup2 import KernelTopUp2
from forge.plans_data.random_gen import GiveUp, Made

GENERATOR = "ktopup3-1"
# Every plan first hollows a box on the raised slab and puts parts against its inner faces
# (only the first box on the slab's top is sure to find room); then one to three of these:
MOVES = {"group": 15, "around": 12, "through": 12, "under cone": 6}


class KernelTopUp3(KernelTopUp2):
    def through(self) -> None:
        turn = self.rng.choice(("lying along length", "lying along depth"))
        if self.move_face(kind=self.rng.choice(("on top of", "left of", "behind")),
                          shape=("tube", turn), sets=False, plain=True):
            self.together(lambda: self.try_lines(self.through_line, tries=3))

    def around(self) -> None:
        turn = self.rng.choice(("lying along length", "lying along depth"))
        if self.move_face(kind=self.rng.choice(("on top of", "left of", "behind")),
                          shape=("cylinder", turn), sets=False, plain=True):
            made = self.last()
            for _ in range(4):
                text = self.around_line()
                if text and made and f"around {made.name}" in text and self.add(text):
                    break

    def under_cone(self) -> None:
        kind = self.rng.choice(("left of", "right of", "in front of", "behind"))
        if self.move_face(kind=kind, shape=("cone", "pointing up"), sets=False, plain=True):
            made = self.last()
            if made is not None and made.count == 1 and made.frame.low[2] >= 10:
                self.move_face(kind="under", target=made, shape=("box", "standing"), plain=True)

    def make(self) -> Made | None:
        rng = self.rng
        try:
            if not self.move_raised(first=True):
                return None
            self.inner()
            for _ in range(rng.randint(1, 3)):
                if len(self.text) > config.MAX_TRAIN_LINES - 6:
                    break
                move = rng.choices(list(MOVES), list(MOVES.values()))[0]
                {"group": self.move_group, "around": self.around, "through": self.through,
                 "under cone": self.under_cone}[move]()
            if len(self.text) < 3 or not self.joined():
                self.count("nothing built or left floating")
                return None
            done = self.force("done")
        except GiveUp as why:
            self.count(f"gave up: {why}")
            return None
        if not (done.built and all(reply.built for reply in self.replies)):
            self.count("done was refused")
            return None
        return self.finish(True)
