"""One part with features on several of its faces: the cheapest plans that need the kernel.

    block: box 160 by 90 by 40, on ground
    on block: hollowed out wall 5, open at top
    on block's front: slot length 30, width 6, depth 3, angle 90
    on block's inner bottom: boss diameter 12, height 6
    on block's left: pair of holes diameter 5, 20 from block's back
    done

Every feature line is one question to the CAD kernel ("is it still one sound solid?").
With one part in the plan that question is as small as it can be, so this stream fills
the feature-on-a-face cells of the coverage table several times faster than the general
kernel tier does. The lines themselves come from `random_gen.Maker.feature_line`; this
file only decides the order: an edge treatment or `hollowed out` first (the resolver
wants them before other features), then features on faces chosen by the shard's balance.
"""

from __future__ import annotations

from forge.plan import grammar as g
from forge.plans_data import config
from forge.plans_data.random_gen import FEATURE_CELLS, GiveUp, Made, Maker

GENERATOR = "featured-1"
BASES = (("box", "standing"), ("box", "standing"), ("box", "standing"), ("box", "lying along length"),
         ("cylinder", "standing"), ("cylinder", "lying along depth"), ("prism", "standing"),
         ("tapered box", "pointing up"), ("cone", "pointing up"), ("dome", "pointing up"),
         ("wedge", "pointing left flat bottom"), ("tube", "standing"), ("sphere", "standing"))
FIRST = ("hollowed out", "rounded corners", "top chamfer", "top fillet")


class FeaturedMaker(Maker):
    def make(self) -> Made | None:
        rng = self.rng
        cell = self.choose("featured base", BASES)
        want = [rng.choice(config.BLOCK[2:]), rng.choice(config.BLOCK[2:]), rng.choice(config.BLOCK)]
        head = self.shape_text(cell[0], cell[1], want)
        try:
            if head is None or not self.add(f"{self.new_name()}: {head}, on ground",
                                            may_keep_rejected=False):
                return None
            target = next(iter(self.infos().values()))
            if rng.random() < 0.45:         # what must come first, if it comes at all
                first = self.choose("featured first", FIRST)
                self.add(self.feature_line([first, "top"], target), may_keep_rejected=False)
            wanted = rng.randint(2, 6)
            tries = wanted * 3
            while len(self.text) < wanted + 1 and tries > 0:
                tries -= 1
                target = next(iter(self.infos().values()))
                later = [tuple(c) for c in FEATURE_CELLS if c[0] not in g.TOP_ONLY_FEATURES
                         and c[0] != "rounded corners" and (target.hollow or not c[1].startswith("inner"))]
                self.add(self.feature_line(list(self.choose("featured", later)), target),
                         may_keep_rejected=False)
            if len(self.text) < 2:
                self.count("no feature fitted")
                return None
            self.force("done")
        except GiveUp:
            return None
        return self.finish(all(reply.built for reply in self.replies))
