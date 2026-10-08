"""Kernel top-up: short plans aimed at the cells only the CAD kernel can decide.

The general kernel tier spends most of its sandbox calls on lines that hit well-filled
cells. A plan here is a base and three to six FOCUS MOVES, each chosen by the shard's
balance, each built from the same line builders as every other random plan:

    pair      a non-box shape against a face (shape x placement), then a box against
              that shape in a chosen direction (placement x shape of target)
    inside    a hollow part (a tube, or a hollowed box), then a part inside it, with the
              inner-face alignments
    through   a tube standing or lying, then a part through its hole
    around    a cylinder standing or lying, then a tube around it
    spans     a part spanning from a place on one part to a place on another
    hollow    `hollowed out` with each open face (or closed), then features on inner faces
    setfeat   a set of boxes, then a feature on the set (every copy gets it)
    group     a group with a feature inside, placed
    sphere    a sphere whose one size is written `default`

Same oracle, same checks: a line is kept only if the resolver, asking the kernel, builds it.
"""

from __future__ import annotations

from forge.plan import grammar as g
from forge.plans_data import config, coverage
from forge.plans_data.random_gen import KERNEL_SHAPES, GiveUp, Made, Maker

GENERATOR = "ktopup-1"
MOVES = ("pair", "pair", "pair", "pair", "inside", "through", "around", "spans", "spans",
         "hollow", "hollow", "setfeat", "group", "sphere")
INNER = (("hole", "inner bottom"), ("blind hole", "inner bottom"), ("boss", "inner bottom"),
         ("pocket", "inner bottom"), ("hole", "inner left"), ("hole", "inner right"),
         ("hole", "inner front"), ("hole", "inner back"))
SHAPE_NAMES = ("tube", "cone", "sphere", "dome", "prism", "wedge", "tapered box", "bar")


class KernelTopUp(Maker):
    def last(self):
        return self.infos().get(self._last[0])

    def pair(self) -> None:
        shape = self.choose("kt shape", SHAPE_NAMES)
        cells = [c for c in KERNEL_SHAPES if c[0] == shape or c[0].startswith(shape + " ")]
        kind = self.choose(f"kt place|{shape}", coverage.FACE_PLACEMENTS)
        if not self.move_face(kind=kind, shape=self.rng.choice(cells), sets=False, plain=True):
            return
        made = self.last()
        if made is None or made.count != 1:
            return
        against = self.choose(f"kt target|{shape}", coverage.FACE_PLACEMENTS)
        self.move_face(kind=against, target=made, shape=("box", "standing"), plain=True)

    def hollow_box(self):
        """A box on top of something, hollowed out; returns its Info or None."""
        if not self.move_face(kind="on top of", shape=("box", "standing"), sets=False, plain=True):
            return None
        box = self.last()
        if box is None or box.count != 1 or min(box.frame.size) < 20:
            return None
        text = self.feature_line(["hollowed out", "top"], box)
        if text is None:
            return None
        face = self.choose("kt open", (*g.FACES, "closed"))
        head = text.split(", open at")[0]
        text = head if face == "closed" else f"{head}, open at {face}"
        return self.last() if self.add(text) else None

    def inside(self) -> None:
        if self.rng.random() < 0.6:
            box = self.hollow_box()
            if box is None:
                return
        elif not self.move_face(shape=("tube", "standing"), kind="on top of", plain=True):
            return
        for _ in range(2):
            self.together(lambda: self.try_lines(self.inside_line, tries=3))

    def hollow(self) -> None:
        box = self.hollow_box()
        if box is None:
            return
        for _ in range(self.rng.randint(1, 2)):
            cell = self.choose("kt inner", INNER)
            box = self.infos().get(box.name)
            if box is not None:
                self.add(self.feature_line(list(cell), box))

    def through(self) -> None:
        turn = self.choose("kt through", g.ORIENTATIONS)
        kind = "on top of" if turn == "standing" else self.rng.choice(coverage.FACE_PLACEMENTS)
        if self.move_face(kind=kind, shape=("tube", turn), sets=False, plain=True):
            self.together(lambda: self.try_lines(self.through_line, tries=3))

    def around(self) -> None:
        turn = self.choose("kt around", g.ORIENTATIONS)
        kind = "on top of" if turn == "standing" else self.rng.choice(("on top of", "left of",
                                                                       "behind"))
        if self.move_face(kind=kind, shape=("cylinder", turn), sets=False, plain=True):
            made = self.last()
            self.used = {pair for pair in self.used if "around" not in pair}
            for _ in range(4):
                text = self.around_line()
                if text and made and f"around {made.name}" in text and self.add(text):
                    break

    def spans(self) -> None:
        self.together(lambda: self.try_lines(self.spans_line, tries=4))

    def setfeat(self) -> None:
        rep = self.rng.choice(("evenly spaced", "mirrored", "each corner"))
        if not self.move_face(kind="on top of", shape=("box", "standing"), sets=False, rep=rep,
                              plain=True):
            return
        made = self.last()
        if made is not None and made.count > 1:
            self.together(lambda: self.add(self.feature_line(None, made)))

    def sphere(self) -> None:
        kind = self.rng.choice(coverage.FACE_PLACEMENTS)
        found = self.targets(kind, False)
        if found:
            target = self.rng.choice(found)
            self.together(lambda: self.add(f"{self.new_name()}: sphere default, {kind} {target.name}"))

    def make(self) -> Made | None:
        rng = self.rng
        try:
            if rng.random() < 0.4:
                if not self.move_raised(first=True):
                    return None
            else:
                size = [rng.choice(config.BASE_LONG[4:]), rng.choice(config.BASE_LONG[4:]),
                        rng.choice((30, 40, 50, 60, 80))]
                if not self.add(f"{self.new_name()}: box {size[0]} by {size[1]} by {size[2]}, "
                                "on ground", may_keep_rejected=False):
                    return None
            for _ in range(rng.randint(3, 6)):
                if len(self.text) > config.MAX_TRAIN_LINES - 6:
                    break
                move = self.choose("kt move", MOVES)
                {"pair": self.pair, "inside": self.inside, "through": self.through,
                 "around": self.around, "spans": self.spans, "hollow": self.hollow,
                 "setfeat": self.setfeat, "group": self.move_group, "sphere": self.sphere}[move]()
            if len(self.text) < 2 or not self.joined():
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
