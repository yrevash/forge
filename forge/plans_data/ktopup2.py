"""Second kernel top-up (6 Oct 2026, morning): the kernel cells ktopup.py fills too slowly.

Measured after the first kernel top-up shards: a part spanning two places (nine cells, by
the kind of place at each end), the inner-face alignments (six cells), features on the
BOTTOM face that add material (they need a raised part: on a part standing on the ground
they would stick out below it), and `rounded corners` on a face other than the top. Every
plan starts with a raised slab on supports, so all of these have something to work on.
"""

from __future__ import annotations

from forge.plans_data import config
from forge.plans_data.ktopup import KernelTopUp
from forge.plans_data.random_gen import GiveUp, Made, num

GENERATOR = "ktopup2-1"
# move -> weight. The weights follow what was still missing at 09:00 on 6 Oct 2026, divided
# by how often the move succeeded in the first kernel top-up shards: lines needed per cell
# times cells, over the success rate (spans 0.4, pair 0.6, the rest 0.5 to 0.7).
MOVES = {"spans": 50, "pair": 40, "rounded": 23, "hollow": 21, "through": 15, "bottom": 13,
         "inner": 12, "around": 10, "group": 6, "setfeat": 2}
INNER_FORMS = ("flush bottom", "inside left", "inside right", "inside front", "inside back",
               "above bottom")


class KernelTopUp2(KernelTopUp):
    def inner(self) -> None:
        """A hollowed box, then parts inside it placed against its inner faces."""
        box = self.hollow_box()
        if box is None:
            return
        for _ in range(2):
            body = self.world.bodies_of(box.name)[0]
            room = body.inner
            if room is None:
                return
            form = self.choose("kt2 inner", INNER_FORMS)
            relation, face = form.split()
            size = [self.pick(room.size[0] * 0.4), self.pick(room.size[1] * 0.4),
                    self.pick(max(5.0, room.size[2] * 0.5))]
            words = [num(v) for v in size]
            if self.rng.random() < 0.3:
                words[self.rng.choice((0, 1))] = "rest"         # wall to wall
            if relation == "flush":
                how = f"flush with {box.name}'s inner bottom"
            elif relation == "above":
                how = f"bottom {num(self.pick(room.size[2] * 0.3, (2, 5, 8, 10)))} above {box.name}'s inner bottom"
            else:
                how = (f"{face} {num(self.pick(min(room.size[:2]) * 0.2, (2, 5, 8, 10, 15, 20)))} "
                       f"inside {box.name}'s inner {face}")
            self.together(lambda words=words, how=how: self.add(
                f"{self.new_name()}: box {' by '.join(words)}, inside {box.name}, {how}"))

    def through(self) -> None:
        """A tube and a part through it. A STANDING tube is put beside the raised slab, not
        on top of it: on top, whatever goes through the hole would run into the slab below
        (the first kernel top-up built one such plan in thirteen shards)."""
        turn = self.rng.choice(("standing", "standing", "standing", "lying along length",
                                "lying along depth"))
        kind = self.rng.choice(("left of", "right of", "in front of", "behind"))
        if self.move_face(kind=kind, shape=("tube", turn), sets=False, plain=True):
            self.together(lambda: self.try_lines(self.through_line, tries=3))

    def raised_parts(self) -> list:
        return [i for i in self.infos().values() if i.single and i.shape == "box"
                and i.frame.low[2] >= 40 and min(i.frame.size[:2]) >= 40]

    def bottom(self) -> None:
        found = self.raised_parts()
        if found:
            name = self.choose("kt2 bottom", ("boss", "pad", "pair of bosses"))
            self.together(lambda: self.add(self.feature_line([name, "bottom"], found[0])))

    def rounded(self) -> None:
        if not self.move_face(kind=self.rng.choice(("on top of", "left of", "right of", "behind")),
                              shape=("box", "standing"), sets=False, plain=True):
            return
        made = self.last()
        if made is not None and made.count == 1:
            face = self.choose("kt2 rounded", ("bottom", "front", "back", "left", "right"))
            self.together(lambda: self.add(self.feature_line(["rounded corners", face], made)))

    def make(self) -> Made | None:
        rng = self.rng
        try:
            if not self.move_raised(first=True):
                return None
            for _ in range(rng.randint(3, 6)):
                if len(self.text) > config.MAX_TRAIN_LINES - 6:
                    break
                move = rng.choices(list(MOVES), list(MOVES.values()))[0]
                {"spans": self.spans, "inner": self.inner, "through": self.through,
                 "around": self.around, "hollow": self.hollow, "bottom": self.bottom,
                 "rounded": self.rounded, "setfeat": self.setfeat, "group": self.move_group,
                 "pair": self.pair}[move]()
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
