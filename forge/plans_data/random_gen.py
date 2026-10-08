"""The random plan generator: plans from the whole vocabulary, built up line by line.

The generator knows the WORDS of the language and a little geometry (how big the face it
is about to use is). It does not decide whether a line works: it writes a candidate line,
the READER parses it and the RESOLVER answers `built` or `rejected`. A built line is
kept. A rejected candidate is thrown away, except in a plan marked `negative`, where a
controlled share is kept with its reply as a negative example.

Every choice is drawn UNIFORMLY OVER COVERAGE CELLS of its family (which shape and
turn, which placement, which alignment form, which repetition form ...), not by how
common the form is in real objects. That is what balances the coverage table; it is also
why these plans are clutter, not objects (the structure and part sources are the
realistic ones).

Two tiers:
    arithmetic   boxes and cylinders, no features. The resolver decides everything by
                 arithmetic; asking the kernel throws the line away.
    kernel       every shape, features, inside / through / around / spans. Each such line
                 costs one sandbox call.

    maker = Maker(seed, tier, judge)
    made = maker.make()          # None when the plan had to be thrown away
    made.text, made.plan, made.resolution
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

from forge.plan import echo_line, parse_plan
from forge.plan import grammar as g
from forge.plan.model import Line, Plan
from forge.plan.names import singular
from forge.plan.parser import _PlanParser
from forge.plans_data import config, coverage
from forge.resolve import contact, placing
from forge.resolve import space as sp
from forge.resolve.judge import KernelFailed, KernelJudge
from forge.resolve.resolver import Reply, Resolution, Resolver
from forge.resolve.scene import World
from forge.resolve.space import Box

GENERATOR = "random_gen-1"

# Neutral names for parts (template wording; labelled in provenance).
WORDS = ("base", "plate", "block", "post", "rail", "beam", "deck", "shelf", "panel", "pad",
         "peg", "pin", "cap", "knob", "lug", "rib", "fin", "arm", "foot", "leg", "stud",
         "spacer", "bracket", "brace", "cover", "lid", "tray", "core", "hub", "axle", "collar",
         "sleeve", "plug", "stop", "guide", "key", "tab", "wing", "tower", "column", "pillar",
         "slab", "tile", "strip", "band", "stay", "strut", "link", "tie", "wall", "fence",
         "lip", "ledge", "step", "riser", "skid", "cleat", "gusset", "web", "flange")
ADJECTIVES = ("upper", "lower", "outer", "main", "small", "long", "short", "side", "end", "mid",
              "rear", "cross")

AXIS = {"on top of": (2, 1), "under": (2, -1), "in front of": (1, -1), "behind": (1, 1),
        "left of": (0, -1), "right of": (0, 1)}
FACES_OF_AXIS = {0: ("left", "right"), 1: ("front", "back"), 2: ("bottom", "top")}
OPPOSITE = {"left": "right", "right": "left", "front": "back", "back": "front",
            "top": "bottom", "bottom": "top"}
WORD_OF_AXIS = ("length", "depth", "height")
LYING_OF_AXIS = {0: "lying along length", 1: "lying along depth", 2: "standing"}
POINT_OF_AXIS = {0: ("right", "left"), 1: ("back", "front"), 2: ("up", "down")}

ARITHMETIC_SHAPES = [("box", turn) for turn in g.ORIENTATIONS] \
    + [("cylinder", turn) for turn in g.ORIENTATIONS]


def all_shape_cells() -> list[tuple[str, str]]:
    cells = []
    for cell in coverage._shape_cells():
        shape, turn = cell.removeprefix("shape:").split("|")
        cells.append((shape, turn))
    return cells


KERNEL_SHAPES = [cell for cell in all_shape_cells() if cell not in ARITHMETIC_SHAPES]
FEATURE_CELLS = [cell.removeprefix("feat:").split("|") for cell, spec in coverage.universe().items()
                 if cell.startswith("feat:")]
ALIGN_FORMS = ([("flush", f) for f in g.FACES] + [("flush-opposite", f) for f in g.FACES]
               + [("offset", f) for f in g.FACES]
               + [(r, f) for r in ("above", "below") for f in ("top", "bottom")]
               + [(r, f) for r in ("inside", "beyond") for f in ("left", "right", "front", "back")]
               + [("inset", ""), ("top at height", ""), ("bottom at height", ""),
                  ("same axis", "")])
SIZE_FORMS = ([("same as", d) for d in g.DIMENSIONS] + [("share", s) for s in g.SHARES])


def num(value: float) -> str:
    return f"{round(value, 3):g}"


@dataclass
class Info:
    """What the generator knows about one named item: read off the resolver's world."""

    name: str
    shape: str | None           # None for a placed group
    count: int
    frame: Box                  # round all copies
    first: Box                  # the first copy alone
    hollow: bool = False
    holes: list[tuple[int, float]] = field(default_factory=list)   # (axis, diameter)
    diameter: float | None = None
    round_axis: int | None = None
    plain: bool = True          # a box: every face is flat and whole

    @property
    def single(self) -> bool:
        return self.count == 1 and self.shape is not None


@dataclass
class Made:
    text: str
    plan: Plan
    resolution: Resolution
    stats: dict


class GiveUp(Exception):
    pass


# Shapes that hold the middle of their own frame (a tube, a bar and a wedge do not).
SOLID_MIDDLE = ("box", "cylinder", "sphere", "cone", "dome", "prism", "tapered box")


class ScreenedJudge(KernelJudge):
    """The kernel judge, with one saving: it is not asked about a hopeless candidate.

    Many rejected candidates of the kernel tier are parts dropped where a plain box
    already is. When a new plain box runs into an earlier plain box, or the middle of a
    new solid shape lies inside one, the two certainly share volume, and the candidate is
    thrown away without a sandbox call (`screened` counts them). A screened candidate is NEVER stored, also
    not as a negative example: only real kernel verdicts are. Screening is off for
    `inside`, `through`, `around`, `spans` and features, where frames overlap on purpose.
    """

    def __init__(self, sandbox) -> None:
        super().__init__(sandbox)
        self.screened = 0
        self.line: int | None = None        # the plan line being offered; None: do not screen

    def look(self, bodies: list):
        if self.line is not None:
            new = [b for b in bodies if b.line == self.line]
            old = [b for b in bodies if b.line != self.line]
            for a in new:
                for b in old:
                    if b.name in a.may_overlap or a.name in b.may_overlap:
                        continue
                    if b.kind != "box" or a.cuts:
                        continue            # only against a plain box: its frame IS the solid
                    depth = a.frame.depth_into(b.frame)
                    # a plain box in a plain box; or a solid shape whose middle is in the box
                    hopeless = (min(depth) > 1e-3 if a.kind == "box" else
                                a.shape in SOLID_MIDDLE and all(
                                    depth[k] > a.frame.size[k] / 2 + 1e-3 for k in range(3)))
                    if hopeless:
                        self.screened += 1
                        raise KernelFailed("screened: the new part runs into a box")
        return super().look(bodies)


class Balance:
    """Running counts of the choices that ended up in BUILT lines, for one shard.

    `Maker.choose` asks it for the least-used option six times in ten. Without it the
    forms that are often rejected (a grid on a small face) would be rare in the data.
    One Balance lives for one shard, so a shard depends only on its own seeds.
    """

    def __init__(self) -> None:
        self.counts: dict[tuple, int] = {}

    def least(self, family: str, options: list, rng: random.Random):
        low = min(self.counts.get((family, option), 0) for option in options)
        return rng.choice([o for o in options if self.counts.get((family, o), 0) == low])


class Maker:
    def __init__(self, seed: int | str, tier: str, judge: KernelJudge, negative: bool = False,
                 long: bool = False, balance: Balance | None = None) -> None:
        self.rng = random.Random(f"{GENERATOR}:{tier}:{seed}")
        self.balance = balance or Balance()
        self.picked: list[tuple] = []
        self.tier, self.judge, self.negative, self.long = tier, judge, negative, long
        self.resolver = Resolver(judge)
        self.reader = _PlanParser()
        self.text: list[str] = []
        self.replies: list[Reply] = []
        self.names: set[str] = set()
        self.used: set[tuple[str, str]] = set()         # (target, placement) faces taken
        self.kept_rejections = 0
        self.max_rejections = self.rng.choice((1, 1, 2, 2, 3)) if negative else 0
        self.ghosts: list[str] = []
        self.in_group = False
        self.stats: dict[str, int] = {}
        self._infos: dict[str, Info] | None = None
        self.shapes = ARITHMETIC_SHAPES if tier == "arithmetic" else KERNEL_SHAPES

    # --- talking to the reader and the resolver -----------------------------------------------

    def count(self, what: str) -> None:
        self.stats[what] = self.stats.get(what, 0) + 1

    def choose(self, family: str, options):
        """One of `options`: the least used so far (six times in ten), else any."""
        options = list(options)
        if self.rng.random() < 0.6:
            chosen = self.balance.least(family, options, self.rng)
        else:
            chosen = self.rng.choice(options)
        self.picked.append((family, chosen))
        return chosen

    def offer(self, text: str) -> tuple[Line, Reply, bool]:
        """Read one candidate line and resolve it. The reader is rolled back by `unread`
        when the candidate is thrown away, so it never remembers a line that is not kept."""
        self._reader_before = (copy.deepcopy(self.reader.state), list(self.reader.history))
        line = self.reader.read(len(self.text) + 1, text)
        before = getattr(self.judge, "asked", 0) + getattr(self.judge, "screened", 0)
        if hasattr(self.judge, "screened"):
            plain = not text.lstrip().startswith("on ") and not any(
                word in text for word in (" inside ", " through ", " around ", " spans from "))
            self.judge.line = line.number if plain and not self.in_group else None
        reply = self.resolver.read(line)
        spoiled = getattr(self.judge, "asked", 0) + getattr(self.judge, "screened", 0) != before
        return line, reply, spoiled

    def unread(self) -> None:
        self.reader.state, self.reader.history = self._reader_before

    def add(self, text: str | None, may_keep_rejected: bool = True) -> bool:
        """Offer one line. True when it was built (and so kept)."""
        picked, self.picked = self.picked, []
        if text is None:
            return False
        if self.in_group:
            text = "  " + text
        line, reply, spoiled = self.offer(text)
        self.picked = picked
        self.count("offered")
        if spoiled:
            self.count("needed the kernel in an arithmetic plan" if self.tier == "arithmetic"
                       else "screened before the kernel")
            self.unread()
            return False
        if reply.built:
            self._keep(text, reply)
            self.picked = []
            return True
        self.picked = []
        self.count(coverage.reply_word(reply.reply))
        if (may_keep_rejected and self.negative and not self.in_group
                and self.kept_rejections < self.max_rejections and self.rng.random() < 0.35):
            self._keep(text, reply)
            self.kept_rejections += 1
            if line.kind == "part" and line.name and line.accepted:
                self.ghosts.append(line.name)
        else:
            self.unread()
        return False

    def force(self, text: str) -> Reply:
        """Offer a line and keep it whatever the reply (negative lines made on purpose)."""
        line, reply, spoiled = self.offer(text)
        if spoiled:
            self.unread()
            raise GiveUp("kernel needed")
        self._keep(text, reply)
        if not reply.built:
            self.kept_rejections += 1
            if line.kind == "part" and line.name and line.accepted:
                self.ghosts.append(line.name)
        return reply

    def _keep(self, text: str, reply: Reply) -> None:
        if reply.built:
            for pick in self.picked:
                self.balance.counts[pick] = self.balance.counts.get(pick, 0) + 1
        self.text.append(text)
        self.replies.append(reply)
        self._infos = None

    def snapshot(self) -> tuple:
        return (copy.deepcopy(self.resolver.state), len(self.resolver.history),
                len(self.resolver.stages), len(self.text), set(self.names), set(self.used),
                self.kept_rejections, list(self.ghosts), self.in_group,
                copy.deepcopy(self.reader.state), list(self.reader.history))

    def restore(self, snap: tuple) -> None:
        state, history, stages, lines, names, used, kept, ghosts, in_group, read, read_history = snap
        self.reader.state, self.reader.history = read, read_history
        self.resolver.state = state
        del self.resolver.history[history:]
        del self.resolver.stages[stages:]
        del self.text[lines:]
        del self.replies[lines:]
        self.names, self.used, self.kept_rejections = names, used, kept
        self.ghosts, self.in_group, self._infos = ghosts, in_group, None

    # --- what exists --------------------------------------------------------------------------

    @property
    def world(self) -> World:
        return self.resolver.state.visible

    def infos(self) -> dict[str, Info]:
        if self._infos is None:
            world, found = self.world, {}
            for name, item in world.items.items():
                bodies = world.bodies_of(name)
                first = [world.bodies[n] for n in item.copies[0]]
                body = first[0]
                info = Info(name, None if item.is_group else body.shape, len(item.copies),
                            sp.around([b.frame for b in bodies]),
                            sp.around([b.frame for b in first]))
                if info.single:
                    info.hollow = body.inner is not None
                    info.plain = body.shape == "box" and not body.cuts
                    standing = body.standing
                    if standing.is_round and body.shape != "sphere" and sp.is_square(body.matrix):
                        direction = sp.apply(body.matrix, (0.0, 0.0, 1.0))
                        info.round_axis = max(range(3), key=lambda k: abs(direction[k]))
                        info.diameter = standing.diameter
                    if sp.is_square(body.matrix):
                        if body.shape == "tube":
                            info.holes.append((info.round_axis, body.sizes["inner diameter"]))
                        for cut in body.cuts:
                            if cut.kind in placing.THROUGH_CUTS:
                                way = sp.apply(body.matrix, cut.normal)
                                wide = cut.slots.get("hole diameter", cut.slots.get("diameter"))
                                info.holes.append((max(range(3), key=lambda k, w=way: abs(w[k])),
                                                   wide or 0.0))
                found[name] = info
            self._infos = found
        return self._infos

    def new_name(self, plural: bool = False) -> str:
        for _ in range(200):
            roll = self.rng.random()
            if roll < 0.2:
                name = f"{self.rng.choice('abcdefghkmnpqrtuvw')}{self.rng.choice('abcdefghkmnpqrtuvw')}{self.rng.randint(1, 40)}"
            else:
                word = self.rng.choice(WORDS)
                if roll < 0.45:
                    word = f"{self.rng.choice(ADJECTIVES)} {word}"
                name = word + ("s" if plural else "")
                if roll > 0.9:
                    name += f" {self.rng.randint(2, 9)}"
            # One base word per plan: "lug 3" blocks "lug", "lugs" and "lugs 2". The resolver
            # names the copies of a set "lugs 1", "lugs 2", and a part with such a name would
            # silently replace a copy.
            base = singular(name.rstrip("0123456789").strip())
            if base not in self.names and name not in g.RESERVED_NAMES:
                self.names.add(base)
                return name
        raise GiveUp("no free name")

    def own(self, name: str, face: str) -> str:
        """`X's top`, with the two spellings after a plural."""
        if name.endswith("s") and self.rng.random() < 0.5:
            return f"{name}' {face}"
        return f"{name}'s {face}"

    # --- sizes --------------------------------------------------------------------------------

    def pick(self, limit: float, grid: tuple = config.SMALL, low: float = 5) -> float:
        """A grid size no larger than `limit`."""
        fits = sorted(v for v in grid if low <= v <= limit)
        if fits:
            # the larger half of what fits, three times in four: fewer specks on big slabs
            if len(fits) > 3 and self.rng.random() < 0.75:
                fits = fits[len(fits) // 2:]
            value = float(self.rng.choice(fits))
            if self.rng.random() < 0.04 and value + 0.5 <= limit:
                value += 0.5                       # decimals are part of the language
            return value
        return float(max(1.0, round(limit * 0.8)))

    def shape_text(self, shape: str, turn: str, want: list) -> str | None:
        """The shape, its sizes and turning words, for a frame of about `want` (x, y, z).

        An entry of `want` that is None is written `rest`. Returns None when this shape
        cannot have its `rest` along that direction.
        """
        rng = self.rng
        rest_axes = [k for k in range(3) if want[k] is None]
        w = [float(v) if v is not None else 20.0 for v in want]

        def size(axis: int) -> str:
            return "rest" if axis in rest_axes else num(w[axis])

        if shape == "box":
            order = {"standing": (0, 1, 2), "lying along length": (2, 1, 0),
                     "lying along depth": (0, 2, 1)}[turn]
            return f"box {' by '.join(size(k) for k in order)}" + self._turn(turn)
        if shape == "wedge":
            way, flat = turn.removeprefix("pointing ").split(" flat ")
            words = f" pointing {way}"
            if flat != g.WEDGE_DEFAULT_FLAT[way] or rng.random() < 0.3:
                words += f" flat {flat}"
            if way == "up" and words == " pointing up":
                words = "" if rng.random() < 0.5 else words
            return f"wedge {' by '.join(size(k) for k in range(3))}{words}"
        if shape == "sphere":
            return None if rest_axes else f"sphere {num(min(w))}"

        # Every other shape has one size that runs along its axis.
        if turn.startswith("pointing"):
            way = turn.removeprefix("pointing ")
            axis = {"up": 2, "down": 2, "left": 0, "right": 0, "front": 1, "back": 1}[way]
            words = "" if way == "up" and rng.random() < 0.6 else f" pointing {way}"
        else:
            axis = {"standing": 2, "lying along length": 0, "lying along depth": 1}[turn]
            words = self._turn(turn)
        if any(k != axis for k in rest_axes):
            return None
        others = [k for k in range(3) if k != axis]
        across = min(w[k] for k in others)
        long = size(axis)
        if shape == "cylinder":
            return f"cylinder {num(across)} by {long}{words}"
        if shape == "tube":
            inner = max(1.0, round(across * rng.choice((0.5, 0.6, 0.7, 0.8))))
            return f"tube {num(across)} by {num(inner)} by {long}{words}"
        if shape == "cone":
            top = rng.choice((0, 0, round(across * 0.4), round(across * 0.6)))
            pair = (across, top) if rng.random() < 0.85 else (max(top, 1), across)
            return f"cone {num(pair[0])} by {num(pair[1])} by {long}{words}"
        if shape == "dome":
            if rest_axes:
                return None
            high = min(w[axis], across / 2)
            return f"dome {num(across)} by {num(max(1.0, high))}{words}"
        if shape == "prism":
            return f"prism {rng.choice((3, 4, 5, 6, 6, 8))} by {num(across)} by {long}{words}"
        if shape == "tapered box":
            # pointing left/right: the written length runs up; front/back: the written depth.
            if axis == 2:
                bottom = (w[0], w[1])
            elif axis == 0:
                bottom = (w[2], w[1])
            else:
                bottom = (w[0], w[2])
            factor = rng.choice((0, 0.4, 0.5, 0.6, 0.8))
            top = (round(bottom[0] * factor), round(bottom[1] * rng.choice((factor, 0.5, 1.0))))
            return (f"tapered box {num(bottom[0])} by {num(bottom[1])} by {num(top[0])} by "
                    f"{num(top[1])} by {long}{words}")
        if shape.startswith("bar "):
            profile = shape.removeprefix("bar ")
            if profile == "tube":
                wall = max(1.0, round(across * rng.choice((0.1, 0.15, 0.2))))
                return f"bar tube {num(across)} by {num(wall)} by {long}{words}"
            wide, deep = w[others[0]], w[others[1]]
            thick = max(1.0, round(min(wide, deep) * rng.choice((0.1, 0.15, 0.2, 0.25))))
            return f"bar {profile} {num(wide)} by {num(deep)} by {num(thick)} by {long}{words}"
        return None

    def _turn(self, turn: str) -> str:
        if turn == "standing":
            return " standing" if self.rng.random() < 0.05 else ""
        return f" {turn}"

    def vary_sizes(self, head: str, limit: float) -> str:
        """Sometimes write one size as `same as`, a share, or `default` (section 4)."""
        roll = self.rng.random()
        if roll > 0.28 or " by " not in head and not head.startswith("sphere"):
            return head
        words = head.split(" by ")
        first = words[0].rsplit(" ", 1)
        tail_words = words[-1].split(" ", 1)
        sizes = [first[1], *words[1:-1], tail_words[0]] if len(words) > 1 else [first[1]]
        numeric = [i for i, s in enumerate(sizes) if s.replace(".", "").isdigit()]
        if head.startswith("prism"):
            numeric = [i for i in numeric if i != 0]
        if not numeric:
            return head
        at = self.rng.choice(numeric)
        value = float(sizes[at])
        if roll < 0.05:
            sizes[at] = "default"
        else:
            form, word = self.choose("size form", SIZE_FORMS)
            options = []
            for info in self.infos().values():
                for dim, got in self._dims(info).items():
                    if form == "same as" and dim != word:
                        continue
                    factor = {"half": 0.5, "third": 1 / 3, "quarter": 0.25, "double": 2.0}.get(word, 1.0)
                    new = got * factor
                    if 3 <= new <= max(limit, value) * 1.5 and new >= value * 0.3:
                        options.append((info.name, dim))
            if not options:
                return head
            name, dim = self.rng.choice(options)
            if form == "same as":
                sizes[at] = f"same as {self.own(name, dim)}"
            elif word == "double":
                sizes[at] = f"double {self.own(name, dim)}"
            else:
                sizes[at] = f"{word} of {self.own(name, dim)}"
        rebuilt = first[0] + " " + " by ".join(sizes)
        return rebuilt + (" " + tail_words[1] if len(tail_words) > 1 else "")

    def _dims(self, info: Info) -> dict[str, float]:
        dims = {"length": info.frame.size[0], "depth": info.frame.size[1],
                "height": info.frame.size[2]}
        if info.diameter and info.count >= 1 and info.shape in ("cylinder", "tube", "cone",
                                                                 "dome", "sphere"):
            dims["diameter"] = info.diameter
        return dims

    def amount(self, limit: float = 50) -> str:
        """A distance: a number, or now and then a reference form (section 4)."""
        value = self.pick(limit, config.AMOUNTS)
        if self.rng.random() < 0.06:
            options = []
            for info in self.infos().values():
                for dim, got in self._dims(info).items():
                    for word, factor in (("same as", 1.0), ("half of", 0.5), ("third of", 1 / 3),
                                         ("quarter of", 0.25)):
                        if 2 <= got * factor <= limit:
                            options.append(f"{word} {self.own(info.name, dim)}")
            if options:
                return self.rng.choice(options)
        return num(value)

    # --- pieces of a line ---------------------------------------------------------------------

    def alignment(self, kind: str, target: Info, part: list, form: tuple | None = None
                  ) -> list[str]:
        """One alignment (sometimes with an `inset`), as text pieces."""
        rng = self.rng
        infos = list(self.infos().values())
        axis = AXIS.get(kind, (2, 1))[0]
        in_face = [f for k in range(3) if k != axis for f in FACES_OF_AXIS[k]]
        form, face = form or self.choose(f"align|{kind}", ALIGN_FORMS)
        other = target if rng.random() < 0.7 or len(infos) < 2 else rng.choice(infos)
        if form in ("flush", "flush-opposite", "offset", "inside", "beyond") and face not in in_face \
                and rng.random() < 0.8 and form != "flush-opposite":
            choices = [f for f in in_face if (form not in ("inside", "beyond")
                                              or f not in ("top", "bottom"))]
            if not choices:
                return []
            face = rng.choice(choices)
        if form == "flush":
            pieces = [f"flush with {self.own(other.name, face)}"]
            if rng.random() < 0.3:
                pieces.append(f"inset {self.amount(30)}")
            return pieces
        if form == "inset":
            return [f"flush with {self.own(target.name, rng.choice(in_face))}",
                    f"inset {self.amount(30)}"]
        if form == "flush-opposite":
            return [f"{face} flush with {self.own(other.name, OPPOSITE[face])}"]
        if form in ("above", "below"):
            if axis == 2 and other is target and rng.random() < 0.8:
                others = [i for i in infos if i is not target]
                if not others:
                    return []
                other = rng.choice(others)
            return [f"{face} {self.amount()} {form} {self.own(other.name, face)}"]
        if form in ("inside", "beyond"):
            return [f"{face} {self.amount()} {form} {self.own(other.name, face)}"]
        if form == "offset":
            return [f"offset {self.amount()} to the {face}"]
        if form in ("top at height", "bottom at height"):
            if axis == 2:
                return []
            low, high = target.first.low[2], target.first.high[2]
            if form == "top at height":
                value = rng.choice((high, high, low + part[2], round((low + high) / 2 + part[2] / 2)))
            else:
                value = rng.choice((low, low, max(low, high - part[2]), round((low + high) / 2)))
            return [f"{form} {num(max(value, 0))}"]
        if form == "same axis":
            others = [i for i in infos if i is not target] or infos
            return [f"on the same axis as {rng.choice(others).name}"]
        return []

    def repetition(self, kind: str, target: Info, form: str | None = None
                   ) -> tuple[str, tuple[int, int], list[str]] | None:
        """(text, how many copies along the face's two directions, extra alignment pieces)."""
        rng = self.rng
        axis = AXIS[kind][0]
        u, v = [k for k in range(3) if k != axis]
        side = axis != 2
        forms = ["each corner", "corners", "evenly spaced", "spread", "grid", "around",
                 "around outward", "mirrored"] + ([] if side else ["corner"])
        form = form or self.choose(f"rep|{kind}", forms)
        name = target.name
        if rng.random() < 0.12 and form in ("each corner", "corners", "corner"):
            others = [i.name for i in self.infos().values() if i.name != name]
            if others:
                name = rng.choice(others)       # the repetition may name another part
        inset = [f"inset {self.amount(30)}"] if rng.random() < 0.4 else []
        if form == "each corner":
            return f"at each corner of {name}", (2, 2), inset
        if form == "corners":
            sides = [f for k in (u, v) if k != 2 for f in FACES_OF_AXIS[k]]
            return f"at the {self.choose('corners', sides)} corners of {name}", (2, 2), inset
        if form == "corner":
            which = self.choose("corner", ("front-left", "front-right", "back-left", "back-right"))
            if rng.random() < 0.1:
                which = which.replace("-", " ")
            return f"at the {which} corner of {name}", (2, 2), inset
        if form in ("evenly spaced", "spread"):
            way = self.choose(form, (u, v))
            count = rng.randint(2, 6 if self.tier == "arithmetic" else 3)
            copies = (count + 1, 1) if way == u else (1, count + 1)
            return f"{count} {form} along {WORD_OF_AXIS[way]}", copies, []
        if form == "grid":
            n, m = (rng.randint(1, 4), rng.randint(1, 3)) if self.tier == "arithmetic" else (2, rng.randint(1, 2))
            if n * m == 1:
                n = 2
            by = "by" if rng.random() < 0.9 else "x"
            return f"grid {n} {by} {m}", (n + 1, m + 1), []
        if form.startswith("around"):
            outward = " facing outward" if form.endswith("outward") else ""
            square = self.tier == "arithmetic" and (side or outward)
            count = rng.choice((2, 4)) if square else rng.randint(2, 8 if self.tier == "arithmetic" else 5)
            if side:
                return f"{count} around {name}{outward}", (1, 1), []
            wide = min(target.first.size[u], target.first.size[v])
            circle = self.pick(wide * 0.8, tuple(range(20, 400, 10)), 20)
            return f"{count} around {name} on circle {num(circle)}{outward}", (3, 3), []
        way = self.choose(f"mirrored|{kind}", ("left-right", "front-back"))
        mirror_axis = 0 if way == "left-right" else 1
        extra = []
        if mirror_axis != axis:
            extra = [f"flush with {self.own(name, FACES_OF_AXIS[mirror_axis][rng.randint(0, 1)])}"]
            if rng.random() < 0.4:
                extra.append(f"inset {self.amount(30)}")
        if rng.random() < 0.1:
            way = way.replace("-", " ")
        return f"mirrored {way}", ((2, 1) if mirror_axis == u else (1, 2)), extra

    # --- whole lines --------------------------------------------------------------------------

    def targets(self, kind: str, sets: bool | None = None) -> list[Info]:
        found = []
        for info in self.infos().values():
            if (info.name, kind) in self.used and self.rng.random() < 0.95:
                continue
            if sets is not None and (info.count > 1) != sets:
                continue
            if kind == "under" and info.frame.low[2] < 10 and not self.in_group:
                continue
            found.append(info)
        return found

    def face_line(self, kind: str | None = None, target: Info | None = None,
                  shape: tuple | None = None, sets: bool | None = None, group: str | None = None,
                  align: tuple | None = None, rep: str | None = None, option: str | None = None,
                  plain: bool = False) -> str | None:
        """A part (or a placed group) against one face of an earlier part."""
        rng = self.rng
        kind = kind or self.choose("place", coverage.FACE_PLACEMENTS)
        if target is None:
            found = self.targets(kind, sets)
            if not found:
                return None
            wanted = self.choose(f"target|{kind}", sorted({i.shape or "group" for i in found}))
            target = rng.choice([i for i in found if (i.shape or "group") == wanted])
        axis, _side = AXIS[kind]
        u, v = [k for k in range(3) if k != axis]
        face = target.first
        pieces_after: list[str] = []
        copies = (1, 1)
        rep_text = None
        if rep or (not plain and rng.random() < 0.4):
            made = self.repetition(kind, target, rep)
            if made:
                rep_text, copies, extra = made
                pieces_after += extra
        option = option or (None if plain else rng.choices(
            ("gap", "sunk", "across", None), (0.04, 0.05, 0.05 if target.count > 1 else 0, 0.86))[0])
        if option == "across" and target.count < 2:
            option = None
        if option == "across":
            face = target.frame
        want: list = [None, None, None]
        want[u] = self.pick(max(5.0, face.size[u] / (max(1, copies[0])) * 0.9))
        want[v] = self.pick(max(5.0, face.size[v] / (max(1, copies[1])) * 0.9))
        room = target.frame.low[2] if kind == "under" and not self.in_group else 400.0
        want[axis] = self.pick(min(room, 120.0), config.SMALL + (80, 100, 120))
        if rng.random() < 0.15 and copies == (1, 1):          # as wide as the face it sits on
            want[u], want[v] = face.size[u], face.size[v]
        rest_height = False
        pieces = []
        if kind == "under" and not self.in_group and (rng.random() < 0.3
                                                      or abs(want[2] - room) < 1e-6):
            want[2], rest_height = None, True
        elif axis != 2 and not self.in_group and target.first.low[2] > 5 and rng.random() < 0.12:
            want[2], rest_height = None, True
            pieces.append(f"flush with {self.own(target.name, 'top')}" if rng.random() < 0.6
                          else f"top {self.amount(20)} below {self.own(target.name, 'top')}")
        if option == "across" and rng.random() < 0.8:
            for k in (u, v):
                if rng.random() < 0.7 and (k != 2 or not rest_height):
                    want[k] = None
        if group:
            head = group
        else:
            cell = shape or self.choose(f"shape|{kind}", self.shapes)
            head = self.shape_text(cell[0], cell[1], want)
            if head is None:
                return None
            head = self.vary_sizes(head, max(face.size[u], face.size[v]))
        pieces = [f"{kind} {target.name}"] + pieces
        if option == "gap":
            pieces.append(f"gap {self.amount(30)}")
        elif option == "sunk":
            pieces.append(f"sunk {self.amount(15)} into {target.name}")
        elif option == "across":
            pieces.append(f"across {target.name}")
        if rest_height:
            pieces.append("down to ground")
        part = [w if w is not None else 20.0 for w in want]
        if align or (not plain and rng.random() < 0.55):
            for _ in range(1 if align else rng.choice((1, 1, 2))):
                pieces += self.alignment(kind, target, part, align)
        pieces += pieces_after
        if not any(p.startswith("flush with") for p in pieces) and "corner" not in (rep_text or ""):
            pieces = [p for p in pieces if not p.startswith("inset")]
        if rep_text:
            pieces.append(rep_text)
        plural = bool(rep_text) or (target.count > 1 and option != "across")
        name = self.new_name(plural and rng.random() < 0.8)
        self._last = (name, target.name, kind, option)
        return f"{name}: {head}, " + ", ".join(pieces)

    def raised_line(self, first: bool = False) -> str:
        """`above ground` with one of its two height forms."""
        rng = self.rng
        thick = rng.choice(config.BASE_THICK)
        high = rng.choice(config.HEIGHTS)
        form = rng.choice(("top", "bottom"))
        cell = ("box", "standing") if first or rng.random() < 0.6 else rng.choice(self.shapes)
        want = [rng.choice(config.BASE_LONG), rng.choice(config.BASE_LONG), thick]
        if not first:
            want = [self.pick(200, config.BLOCK, 40), self.pick(200, config.BLOCK, 40), thick]
        head = self.shape_text(cell[0], cell[1], want) or f"box {num(want[0])} by {num(want[1])} by {thick}"
        pieces = ["above ground", f"{form} at height {high + (thick if form == 'top' else 0)}"]
        if not first:
            infos = list(self.infos().values())
            other = rng.choice(infos)
            face = rng.choice(("left", "right", "front", "back"))
            pieces.append(rng.choice((
                f"flush with {self.own(other.name, face)}",
                f"{face} {self.amount()} beyond {self.own(other.name, face)}",
                f"offset {num(rng.choice(config.BLOCK))} to the {face}")))
        name = self.new_name()
        self._last = (name, None, "above ground", None)
        return f"{name}: {head}, " + ", ".join(pieces)

    def support_line(self, target: str) -> str:
        """Something that reaches from a raised part down to the ground."""
        rng = self.rng
        info = self.infos()[target]
        wide = self.pick(min(info.first.size[0], info.first.size[1]) / 3, config.SMALL, 10)
        cell = rng.choice(self.shapes if rng.random() < 0.5 else ARITHMETIC_SHAPES)
        want = [wide, wide if rng.random() < 0.7 else self.pick(info.first.size[1] / 2), None]
        head = self.shape_text(cell[0], cell[1], want) or f"box {num(wide)} by {num(wide)} by rest"
        pieces = [f"under {target}", "down to ground"]
        roll = rng.random()
        plural = True
        if roll < 0.35:
            if rng.random() < 0.4:
                pieces.append(f"inset {self.amount(30)}")
            pieces.append(f"at each corner of {target}")
        elif roll < 0.5:
            sides = rng.choice(g.CORNER_SIDES)
            pieces.append(f"at the {sides} corners of {target}")
        elif roll < 0.65:
            pieces += [f"flush with {self.own(target, rng.choice(('left', 'right')))}",
                       "mirrored left-right"]
        elif roll < 0.75:
            pieces.append(f"{rng.randint(2, 4)} spread along {rng.choice(('length', 'depth'))}")
        else:
            plural = False
        name = self.new_name(plural)
        return f"{name}: {head}, " + ", ".join(pieces)

    def ground_line(self) -> str | None:
        """A part standing on the ground beside something already there."""
        rng = self.rng
        grounded = [i for i in self.infos().values() if abs(i.frame.low[2]) < 1e-6]
        if not grounded:
            return None
        other = rng.choice(grounded)
        cell = rng.choice(self.shapes)
        want = [self.pick(160, config.BLOCK + config.SMALL), self.pick(160, config.BLOCK + config.SMALL),
                self.pick(min(200.0, max(10.0, other.frame.size[2] * 2)), config.BLOCK + config.SMALL)]
        head = self.shape_text(cell[0], cell[1], want)
        if head is None:
            return None
        head = self.vary_sizes(head, 200)
        face = rng.choice(("left", "right", "front", "back"))
        pieces = ["on ground", f"{face} flush with {self.own(other.name, OPPOSITE[face])}"]
        roll = rng.random()
        cross = ("front", "back") if face in ("left", "right") else ("left", "right")
        if roll < 0.3:
            pieces.append(f"flush with {self.own(other.name, rng.choice(cross))}")
        elif roll < 0.5:
            side = rng.choice(cross)
            pieces.append(f"{side} {self.amount()} {rng.choice(('inside', 'beyond'))} "
                          f"{self.own(other.name, side)}")
        elif roll < 0.6:
            pieces.append(f"offset {self.amount()} to the {rng.choice(cross)}")
        elif roll < 0.68:
            pieces.insert(1, f"top at height {num(want[2])}")
        name = self.new_name()
        self._last = (name, None, "on ground", None)
        return f"{name}: {head}, " + ", ".join(pieces)

    def reach_line(self) -> str | None:
        """A `rest` size fixed by two stated faces (section 4, third row of the table)."""
        rng = self.rng
        infos = list(self.infos().values())
        raised = [i for i in infos if i.frame.low[2] >= 20]
        s = [num(self.pick(40)), num(self.pick(40))]
        forms = ["on top", "side"] + (["post", "under", "legs"] if raised else [])
        form = self.choose("reach", forms)
        name = self.new_name(form == "legs")
        if form == "post":
            x = rng.choice(raised)
            side = rng.choice((f", flush with {self.own(x.name, rng.choice(('left', 'right', 'front', 'back')))}", ""))
            self._last = (name, x.name, "under", None)
            return (f"{name}: box {s[0]} by {s[1]} by rest, on ground, top flush with "
                    f"{self.own(x.name, 'bottom')}{side}")
        if form == "legs":
            x = rng.choice(raised)
            height = "rest" if rng.random() < 0.5 else num(x.frame.low[2])
            top = f", top flush with {self.own(x.name, 'bottom')}" if height == "rest" else ""
            inset = f", inset {self.amount(20)}" if rng.random() < 0.4 else ""
            which = rng.choice((f"at each corner of {x.name}",
                                f"at the {rng.choice(g.CORNER_SIDES)} corners of {x.name}"))
            cell = rng.choice(("box {0} by {1} by {2}", "cylinder {0} by {2}"))
            self._last = (name, x.name, "under", None)
            return f"{name}: {cell.format(s[0], s[1], height)}, on ground{top}{inset}, {which}"
        if form == "under":
            x = rng.choice(raised)
            low = num(rng.choice([v for v in (0, 5, 10, 20, 40) if v < x.frame.low[2] - 4]))
            self._last = (name, x.name, "under", None)
            return f"{name}: box {s[0]} by {s[1]} by rest, under {x.name}, bottom at height {low}"
        x = rng.choice(infos)
        if form == "on top":
            top = x.frame.high[2] + rng.choice((10, 20, 30, 50, 80))
            higher = [i for i in infos if i.frame.high[2] > x.frame.high[2] + 5]
            how = (f"top flush with {self.own(rng.choice(higher).name, 'top')}"
                   if higher and rng.random() < 0.4 else f"top at height {num(top)}")
            self._last = (name, x.name, "on top of", None)
            return f"{name}: box {s[0]} by {s[1]} by rest, on top of {x.name}, {how}"
        kind = rng.choice(("left of", "right of", "in front of", "behind"))
        top = rng.choice((f"flush with {self.own(x.name, 'top')}",
                          f"top {self.amount(20)} below {self.own(x.name, 'top')}"))
        bottom = rng.choice((f"flush with {self.own(x.name, 'bottom')}",
                             f"bottom {self.amount(20)} above {self.own(x.name, 'bottom')}"))
        self._last = (name, x.name, kind, None)
        return f"{name}: box {s[0]} by {s[1]} by rest, {kind} {x.name}, {top}, {bottom}"

    def between_line(self) -> str | None:
        """A part between two parts that face each other (and the set forms of section 7)."""
        rng = self.rng
        world = self.world
        infos = list(self.infos().values())
        found = []          # (text of the placement, axis, gap, shared box sizes)

        def facing(a: Box, b: Box):
            gaps = [max(a.low[k] - b.high[k], b.low[k] - a.high[k]) for k in range(3)]
            apart = [k for k in range(3) if gaps[k] > 5]
            if len(apart) != 1:
                return None
            axis = apart[0]
            shared = [min(a.high[k], b.high[k]) - max(a.low[k], b.low[k]) for k in range(3)]
            if any(shared[k] < 5 for k in range(3) if k != axis):
                return None
            return axis, gaps[axis], shared

        for info in infos:
            if info.count == 2 and (info.name, "between") not in self.used:
                item = world.items[info.name]
                a, b = (sp.around([world.bodies[n].frame for n in names]) for names in item.copies)
                made = facing(a, b)
                if made:
                    found.append((f"between {info.name}", *made, [info.name]))
        pairs = [(a, b) for i, a in enumerate(infos) for b in infos[i + 1:]]
        rng.shuffle(pairs)
        for a, b in pairs[:150]:
            if (a.name, b.name) in self.used:
                continue
            if a.count > 1 and b.count > 1 and a.count != b.count:
                continue
            made = facing(a.first if a.count > 1 else a.frame, b.first if b.count > 1 else b.frame)
            if a.count > 1 and b.count > 1:
                made = facing(a.frame, b.frame)
                if made:        # the nearest pair decides; take the frames' sizes as a guide
                    first = facing(a.first, b.first)
                    made = first or made
            if made:
                order = (a, b) if rng.random() < 0.5 else (b, a)
                found.append((f"between {order[0].name} and {order[1].name}", *made,
                              [a.name, b.name]))
        if not found:
            return None
        # Prefer the set forms: they are the rare ones.
        special = [f for f in found if len(f[4]) == 1
                   or any(self.infos()[n].count > 1 for n in f[4])]
        text, axis, gap, shared, names = rng.choice(special if special and rng.random() < 0.6
                                                    else found)
        u, v = [k for k in range(3) if k != axis]
        want: list = [None, None, None]
        want[u] = self.pick(shared[u]) if rng.random() < 0.8 else shared[u]
        want[v] = self.pick(shared[v]) if rng.random() < 0.8 else shared[v]
        nice = abs(gap - round(gap, 3)) < 1e-9
        if nice and rng.random() < 0.15:
            want[axis] = gap                                  # a number that equals the gap
        cell = rng.choice(self.shapes)
        if cell[0] not in ("box", "wedge"):
            # a long shape: turn it so its axis runs along the gap
            if cell[0] in g.LYING_SHAPES or cell[0].startswith("bar"):
                cell = (cell[0], LYING_OF_AXIS[axis])
            elif cell[0] in ("cone", "tapered box"):
                cell = (cell[0], f"pointing {rng.choice(POINT_OF_AXIS[axis])}")
            else:
                cell = ("box", "standing")
        head = self.shape_text(cell[0], cell[1], want)
        if head is None:
            return None
        pieces = [text]
        if rng.random() < 0.45:
            part = [w or 20.0 for w in want]
            target = self.infos()[names[0]]
            form = rng.choice([f for f in ALIGN_FORMS if f[0] in (
                "flush", "offset", "inside", "beyond", "above", "below", "top at height",
                "bottom at height", "flush-opposite") and (not f[1] or f[1] not in FACES_OF_AXIS[axis])])
            kind = {0: "left of", 1: "behind", 2: "on top of"}[axis]
            pieces += self.alignment(kind, target, part, form)
        if rng.random() < 0.2:
            way = rng.choice((u, v))
            roll = rng.random()
            if roll < 0.5:
                pieces.append(f"{rng.randint(2, 3)} {rng.choice(('evenly spaced', 'spread'))} "
                              f"along {WORD_OF_AXIS[way]}")
            elif way != 2:
                pieces.append("mirrored " + ("left-right" if way == 0 else "front-back"))
        name = self.new_name(any(self.infos()[n].count > 1 for n in names) and len(names) == 2)
        self._last = (name, tuple(names), "between", None)
        return f"{name}: {head}, " + ", ".join(pieces)

    # --- kernel-only lines --------------------------------------------------------------------

    def inside_line(self) -> str | None:
        rng = self.rng
        hollow = [i for i in self.infos().values() if i.hollow]
        if not hollow:
            return None
        target = rng.choice(hollow)
        body = self.world.bodies_of(target.name)[0]
        room = body.inner
        if room is None:
            return None
        want: list = [self.pick(room.size[0] * 0.8), self.pick(room.size[1] * 0.8),
                      self.pick(max(5.0, room.size[2] * 1.2))]
        walls = body.shape != "tube" and body.hollow is not None
        for k in (0, 1):
            if walls and rng.random() < 0.3:
                want[k] = None
        cell = ("box", "standing") if None in want or rng.random() < 0.5 else rng.choice(
            ARITHMETIC_SHAPES + KERNEL_SHAPES)
        head = self.shape_text(cell[0], cell[1], want)
        if head is None:
            return None
        pieces = [f"inside {target.name}"]
        roll = rng.random()
        if roll < 0.2:
            pieces.append(f"flush with {target.name}'s inner bottom")
        elif roll < 0.5 and walls:
            face = rng.choice(("left", "right", "front", "back"))
            pieces.append(f"{face} {self.amount(20)} inside {target.name}'s inner {face}")
        elif roll < 0.6:
            pieces.append(f"bottom {self.amount(20)} above {target.name}'s inner bottom")
        if walls and rng.random() < 0.15 and None not in want[:2]:
            pieces.append(f"{rng.randint(2, 3)} evenly spaced along "
                          f"{rng.choice(('length', 'depth'))}")
        name = self.new_name()
        self._last = (name, target.name, "inside", None)
        return f"{name}: {head}, " + ", ".join(pieces)

    def through_line(self) -> str | None:
        rng = self.rng
        holed = [i for i in self.infos().values() if i.holes]
        if not holed:
            return None
        target = rng.choice(holed)
        axis, wide = target.holes[-1]
        if wide <= 0:
            return None
        across = wide if rng.random() < 0.75 else max(1.0, round(wide * 0.6))
        long = target.frame.size[axis] + rng.choice((10, 20, 40, 60))
        shape = rng.choice(("cylinder", "cylinder", "tube", "prism", "bar tube", "box"))
        want = [across, across, across]
        want[axis] = long
        if shape == "box":
            side = max(1.0, round(across * 0.6))
            want = [side, side, side]
            want[axis] = long
            head = self.shape_text("box", "standing", want)
        else:
            head = self.shape_text(shape, LYING_OF_AXIS[axis], want)
        if head is None:
            return None
        name = self.new_name()
        self._last = (name, target.name, "through", None)
        return f"{name}: {head}, through {target.name}"

    def around_line(self) -> str | None:
        rng = self.rng
        singles = [i for i in self.infos().values() if i.single
                   and (i.name, "around") not in self.used]
        if not singles:
            return None
        round_ones = [i for i in singles if i.round_axis is not None and i.diameter]
        target = rng.choice(round_ones if round_ones and rng.random() < 0.8 else singles)
        if target.round_axis is not None and target.diameter:
            axis, inner = target.round_axis, target.diameter
        else:
            axis = 2
            inner = round((target.frame.size[0] ** 2 + target.frame.size[1] ** 2) ** 0.5 + 1)
        outer = inner + rng.choice((6, 10, 16, 20, 30))
        high = self.pick(max(5.0, target.frame.size[axis] * 0.6))
        turn = self._turn(LYING_OF_AXIS[axis])
        pieces = [f"around {target.name}"]
        low_face, high_face = FACES_OF_AXIS[axis]
        roll = rng.random()
        if roll < 0.35:
            pieces.append(f"flush with {self.own(target.name, rng.choice((low_face, high_face)))}")
        elif roll < 0.55 and axis == 2:
            pieces.append(f"bottom {self.amount(30)} above {self.own(target.name, 'bottom')}")
        elif roll < 0.7 and axis == 2:
            pieces.append(f"top {self.amount(30)} below {self.own(target.name, 'top')}")
        name = self.new_name()
        self._last = (name, target.name, "around", None)
        return (f"{name}: tube {num(outer)} by {num(inner)} by {num(high)}{turn}, "
                + ", ".join(pieces))

    def spans_line(self) -> str | None:
        rng = self.rng
        singles = [i for i in self.infos().values() if i.count == 1]
        if len(singles) < 2:
            return None
        a, b = rng.sample(singles, 2)

        def place(info: Info) -> str:
            kind = self.choose("span end", ("face", "edge", "corner"))
            if kind == "face":
                return self.own(info.name, rng.choice(g.FACES))
            words = [rng.choice(("top", "bottom")), rng.choice(("front", "back")),
                     rng.choice(("left", "right"))]
            if kind == "edge":
                words.pop(rng.randrange(3))
            return self.own(info.name, "-".join(words) + f" {kind}")

        thin = self.pick(15, (4, 5, 6, 8, 10, 12, 15), 4)
        shape = rng.choice(("box", "box", "cylinder", "tube", "prism", "bar"))
        if shape == "box":
            sizes = [num(thin), num(thin), num(thin)]
            sizes[rng.randrange(3)] = "rest"
            head = "box " + " by ".join(sizes)
        elif shape == "cylinder":
            head = f"cylinder {num(thin)} by rest"
        elif shape == "tube":
            head = f"tube {num(thin + 4)} by {num(thin)} by rest"
        elif shape == "prism":
            head = f"prism {rng.choice((3, 6, 8))} by {num(thin)} by rest"
        else:
            profile = rng.choice(("L", "T", "U", "I", "tube"))
            head = (f"bar tube {num(thin + 4)} by 2 by rest" if profile == "tube"
                    else f"bar {profile} {num(thin + 5)} by {num(thin + 5)} by 2 by rest")
        name = self.new_name()
        self._last = (name, (a.name, b.name), "spans", None)
        return f"{name}: {head}, spans from {place(a)} to {place(b)}"

    def feature_line(self, cell: list | None = None, target: Info | None = None) -> str | None:
        rng = self.rng
        name, face = cell or self.choose("feature", [tuple(c) for c in FEATURE_CELLS])
        inner = face.startswith("inner ")
        infos = [i for i in self.infos().values() if i.shape is not None]
        if inner:
            infos = [i for i in infos if i.hollow and i.single and i.shape != "tube"]
        elif name == g.HOLLOWING_FEATURE:
            infos = [i for i in infos if i.single and not i.hollow and i.shape in (
                "box", "cylinder", "cone", "dome", "sphere", "tapered box", "prism")]
        elif name == "rounded corners":
            infos = [i for i in infos if i.shape == "box"]
        if target is None:
            if not infos:
                return None
            boxes = [i for i in infos if i.shape == "box"]
            if boxes and rng.random() < 0.6:
                infos = boxes
            wanted = self.choose(f"feature on|{name}", sorted({i.shape for i in infos}))
            target = rng.choice([i for i in infos if i.shape == wanted])
        axis, _ = sp.FACE[face.removeprefix("inner ")]
        first, second = [k for k in range(3) if k != axis]
        frame = target.first
        if inner:
            body = self.world.bodies_of(target.name)[0]
            if body.inner is None:
                return None
            frame = body.inner
        a, b, under = frame.size[first], frame.size[second], target.first.size[axis]
        small = min(a, b)
        if small < 8:
            return None
        if face == "bottom" and name in ("boss", "pad", "pair of bosses") \
                and target.first.low[2] < small * 0.25 and not self.in_group:
            return None         # it would stick out below the ground

        def n(value: float, low: float = 1.0) -> str:
            return num(max(low, round(value * 2) / 2 if value < 20 else round(value)))

        d = small * rng.choice((0.1, 0.15, 0.2, 0.25, 0.3))
        depth = under * rng.choice((0.2, 0.3, 0.5)) if not inner else 1.0
        slots = {
            "hole": {"diameter": n(d)},
            "blind hole": {"diameter": n(d), "depth": n(depth)},
            "counterbored hole": {"hole diameter": n(d * 0.6), "diameter": n(d * 1.3 + 1),
                                  "depth": n(depth * 0.6)},
            "boss": {"diameter": n(d * 1.2), "height": n(small * rng.choice((0.05, 0.1, 0.2)))},
            "pad": {"length": n(a * rng.choice((0.2, 0.3, 0.4))),
                    "width": n(b * rng.choice((0.2, 0.3, 0.4))),
                    "height": n(small * rng.choice((0.05, 0.1)))},
            "pocket": {"length": n(a * rng.choice((0.2, 0.3, 0.4))),
                       "width": n(b * rng.choice((0.2, 0.3, 0.4))), "depth": n(depth)},
            "slot": {"length": n(small * 0.4), "width": n(small * 0.12), "depth": n(depth),
                     "angle": num(rng.choice((0, 0, 30, 45, 90, 135)))},
            "circle of holes": {"count": str(rng.randint(3, 8)), "hole diameter": n(small * 0.06),
                                "circle diameter": n(small * rng.choice((0.4, 0.5, 0.6)))},
            "row of holes": {"count": str(rng.randint(2, 4)), "hole diameter": n(a * 0.05),
                             "spacing": n(a * 0.15)},
            "pair of holes": {"diameter": n(d * 0.8)},
            "pair of bosses": {"diameter": n(d * 0.8), "height": n(small * 0.08)},
            "pair of pockets": {"length": n(a * 0.15), "width": n(b * 0.2), "depth": n(depth)},
            "rounded corners": {"radius": n(small * rng.choice((0.05, 0.1, 0.2)))},
            "top chamfer": {"size": n(min(small, under) * rng.choice((0.05, 0.1)))},
            "top fillet": {"radius": n(min(small, under) * rng.choice((0.05, 0.1)))},
            "hollowed out": {"wall": n(min(small, under) * rng.choice((0.08, 0.1, 0.15)))},
        }[name]
        _, slot_words, positioned = g.FEATURES[name]
        written = [f"{word} {slots[word]}" for word in slot_words
                   if rng.random() > 0.12 or name.startswith("pair")]
        pieces = [f"{name} " + ", ".join(written) if written else name]
        if name == g.HOLLOWING_FEATURE:
            roll = rng.random()
            if roll < 0.85:
                pieces.append("open at " + ("top" if roll < 0.4 else rng.choice(g.FACES)))
        pair = name.startswith("pair")
        if positioned:
            faces_a, faces_b = FACES_OF_AXIS[first], FACES_OF_AXIS[second]
            roll = rng.random()
            if roll < 0.3 and not pair:
                if rng.random() < 0.3:
                    pieces.append("centred")
            elif roll < 0.5:
                pieces.append(f"{n(a * rng.choice((0.2, 0.25, 0.3)))} from "
                              f"{self.own(target.name, rng.choice(faces_a))}")
            elif roll < 0.72:
                pieces.append(f"{n(a * rng.choice((0.2, 0.25, 0.3)))} from "
                              f"{self.own(target.name, rng.choice(faces_a))}")
                pieces.append(f"{n(b * rng.choice((0.2, 0.3, 0.5)))} from "
                              f"{self.own(target.name, rng.choice(faces_b))}")
            else:
                x = round(a * rng.choice((0.15, 0.2, 0.25, 0.3)) * rng.choice((-1, 1)))
                y = round(b * rng.choice((0, 0.1, 0.2, 0.25)) * rng.choice((-1, 1)))
                if pair and x == 0:
                    x = max(1, round(a * 0.2))
                pieces.append(f"at ({num(x)}, {num(y)})")
        on = target.name if face == "top" else self.own(target.name, face)
        return f"on {on}: " + ", ".join(pieces)

    # --- several lines that belong together ---------------------------------------------------

    def joined(self) -> bool:
        world = self.resolver.state.world
        groups = contact.joined_groups(list(world.bodies), world.touching)
        return len(groups) == 1 and any(contact.on_ground(world.bodies[n]) for n in groups[0])

    def together(self, steps) -> bool:
        """Run `steps()`; keep its lines only if it says so and nothing is left floating."""
        snap = self.snapshot()
        try:
            ok = steps()
        except GiveUp:
            ok = False
        if ok and (self.negative or self.joined()):
            return True
        if ok:
            self.count("left something floating")
        self.restore(snap)
        return False

    def try_lines(self, make, tries: int = 4, **kw) -> bool:
        for _ in range(tries):
            if self.add(make(**kw)):
                return True
        return False

    def note_used(self) -> None:
        _name, target, kind, _ = self._last
        if isinstance(target, str):
            self.used.add((target, kind))
        elif isinstance(target, tuple) and len(target) == 2:
            self.used.add(target)
            self.used.add(target[::-1])
        elif isinstance(target, tuple):
            self.used.add((target[0], "between"))

    def move_face(self, **kw) -> bool:
        def steps() -> bool:
            if not self.try_lines(self.face_line, **kw):
                return False
            name, target, kind, option = self._last
            self.note_used()
            if option == "gap" or not self.joined():
                infos = self.infos()
                if name not in infos or infos[name].count != 1 or infos[target].count != 1:
                    return self.negative
                axis = AXIS[kind][0]
                want: list = [self.pick(10, (5, 8, 10)), self.pick(10, (5, 8, 10)),
                              self.pick(10, (5, 8, 10))]
                want[axis] = None
                order = (target, name) if self.rng.random() < 0.5 else (name, target)
                link = (f"{self.new_name()}: {self.shape_text('box', 'standing', want)}, "
                        f"between {order[0]} and {order[1]}")
                return self.add(link) or self.negative
            return True
        return self.together(steps)

    def move_raised(self, first: bool = False) -> bool:
        def steps() -> bool:
            if not self.add(self.raised_line(first)):
                return False
            name = self._last[0]
            for _ in range(5):
                if self.add(self.support_line(name)):
                    self.used.add((name, "under"))
                    return True
            return False
        return self.together(steps)

    def move_group(self) -> bool:
        rng = self.rng

        def steps() -> bool:
            group = self.new_name()
            if not self.add(f"group {group}"):
                return False
            self.in_group = True
            cell = rng.choice(self.shapes)
            want = [self.pick(60, config.SMALL, 10), self.pick(60, config.SMALL, 10),
                    self.pick(80, config.SMALL + (80,), 10)]
            head = self.shape_text(cell[0], cell[1], want) or "box 20 by 20 by 40"
            first = self.new_name()
            if not self.add(f"{first}: {head}"):
                return False
            for _ in range(rng.choice((1, 1, 2, 3))):
                self.try_lines(self.face_line, tries=3, plain=rng.random() < 0.7)
                if self.tier == "kernel" and rng.random() < 0.25:
                    self.add(self.feature_line())
            self.in_group = False
            if len(self.resolver.state.draft.bodies) < 2 or not self.add("end"):
                return False
            placed = False
            for _ in range(rng.choice((1, 1, 2))):
                for _ in range(5):
                    kind = self.choose("group place", coverage.FACE_PLACEMENTS)
                    rep = rng.choice((None, None, "mirrored", "each corner", "corners",
                                      "evenly spaced", "spread", "grid", "around"))
                    text = self.face_line(kind=kind, group=group, rep=rep, option=None)
                    if text and AXIS[kind][0] != 2 and rng.random() < 0.3 \
                            and "down to ground" not in text:
                        head, _, tail = text.partition(f"{kind} ")
                        target, _, rest = tail.partition(", ")
                        text = f"{head}{kind} {target}, down to ground" + (f", {rest}" if rest else "")
                    if self.add(text):
                        self.note_used()
                        placed = True
                        break
            return placed
        ok = self.together(steps)
        self.in_group = False
        return ok

    def move_undo(self) -> bool:
        """Build one to three more lines, then take them back (section 10)."""
        if self.negative or len(self.resolver.history) < 2:
            return False
        rng = self.rng
        used = set(self.used)
        if rng.random() < 0.5:
            if not self.try_lines(self.face_line, plain=rng.random() < 0.5):
                return False
            ok = self.add("undo", may_keep_rejected=False)
        else:
            keep = [name for name, _ in self.resolver.history if name and name in self.world.items]
            if not keep:
                return False
            anchor = keep[-1]
            built = sum(self.try_lines(self.face_line, tries=3, plain=True)
                        for _ in range(rng.randint(1, 3)))
            if not built:
                return False
            ok = self.add(f"undo to {anchor}", may_keep_rejected=False)
        self.used = used
        return ok

    # --- lines that are meant to be rejected (negative plans only) ----------------------------

    def negative_line(self) -> None:
        rng = self.rng
        infos = list(self.infos().values())
        if not infos:
            return
        target = rng.choice(infos)
        kind = rng.choice(coverage.FACE_PLACEMENTS)
        axis, side = AXIS[kind]
        away = FACES_OF_AXIS[axis][1 if side > 0 else 0]
        name = self.new_name()
        s = [num(self.pick(30)) for _ in range(3)]
        box = f"box {s[0]} by {s[1]} by {s[2]}"
        roll = rng.choice(("overlap", "float", "fit", "fit", "unknown", "words", "words"))
        if roll == "overlap":
            built = [text for text, reply in zip(self.text, self.replies, strict=True)
                     if reply.built and reply.made and ": " in text and not text.startswith("on ")
                     and "group" not in text and not text.startswith(" ")]
            if not built:
                return
            old = rng.choice(built)
            self.force(f"{name}: {old.split(': ', 1)[1]}")
        elif roll == "float":
            self.force(f"{name}: {box}, {kind} {target.name}, offset {self.amount()} to the {away}")
        elif roll == "fit":
            choice = rng.random()
            if choice < 0.3:
                wide = num(max(target.first.size) + rng.choice((10, 50)))
                way = WORD_OF_AXIS[next(k for k in range(3) if k != axis)]
                self.force(f"{name}: box {wide} by {wide} by {wide}, {kind} {target.name}, "
                           f"{rng.randint(2, 5)} evenly spaced along {way}")
            elif choice < 0.55:
                low = num(max(0.0, target.frame.high[2] - rng.choice((0, 5, 10))))
                self.force(f"{name}: box {s[0]} by {s[1]} by rest, on top of {target.name}, "
                           f"top at height {low}")
            elif choice < 0.8:
                grounded = [i for i in infos if abs(i.frame.low[2]) < 1e-6]
                if grounded:
                    self.force(f"{name}: {box}, under {rng.choice(grounded).name}")
            else:
                self.force(f"{name}: dome {s[0]} by {num(float(s[0]) * 0.8)}, on top of {target.name}")
        elif roll == "unknown":
            if self.ghosts:
                self.force(f"{name}: {box}, {kind} {rng.choice(self.ghosts)}")
        else:
            good = self.face_line(plain=True, shape=("box", "standing"))
            if good is None:
                return
            head, _, tail = good.partition(", ")
            mutations = [
                lambda: good.replace(" by ", ", ", 1),
                lambda: good.replace(" on top of ", " on top of the ").replace(" under ", " under the ")
                if (" on top of " in good or " under " in good) else good + ".",
                lambda: good.replace(": box ", f": {rng.choice(('pyramid', 'slab', 'rod', 'block'))} "),
                lambda: head,
                lambda: good + ".",
                lambda: good.replace(", ", "; ", 1),
                lambda: good.replace(" by ", " by ten by ", 1),
                lambda: f"{head}, on the ground",
                lambda: f"{head}, next to {target.name}",
                lambda: f"{head}, {tail}, leaning 30 degrees",
            ]
            self.force(rng.choice(mutations)())

    # --- the whole plan -----------------------------------------------------------------------

    def start(self) -> None:
        rng = self.rng
        roll = rng.random()
        if roll < 0.3:
            if not self.move_raised(first=True):
                raise GiveUp("no raised start")
            return
        thick = rng.choice(config.BASE_THICK + (80, 100))
        want = [rng.choice(config.BASE_LONG), rng.choice(config.BASE_LONG), thick]
        cell = ("box", "standing")
        if roll > 0.8:
            cell = rng.choice(self.shapes)
            want = [rng.choice(config.BLOCK), rng.choice(config.BLOCK), rng.choice(config.BLOCK)]
        head = self.shape_text(cell[0], cell[1], want) or f"box {want[0]} by {want[1]} by {thick}"
        for text in (f"{self.new_name()}: {head}, on ground",
                     f"{self.new_name()}: box {want[0]} by {want[1]} by {thick}, on ground"):
            if self.add(text, may_keep_rejected=False):
                return
        raise GiveUp("no start")

    def moves(self) -> list[tuple[float, object]]:
        if self.tier == "arithmetic":
            return [(46, self.move_face), (8, lambda: self.move_face(sets=True)),
                    (5, lambda: self.move_face(sets=True, option="across")),
                    (12, lambda: self.together(lambda: self.try_lines(self.between_line)
                                               and (self.note_used() or True))),
                    (6, lambda: self.together(lambda: self.try_lines(self.ground_line))),
                    (5, self.move_raised), (8, self.move_group), (5, self.move_undo),
                    (5, lambda: self.together(lambda: self.try_lines(self.reach_line))),
                    (4, lambda: self.move_face(option="gap")),
                    (3, lambda: self.move_face(option="sunk"))]
        return [(30, self.move_face),
                (30, lambda: self.together(lambda: self.add(self.feature_line()))),
                (4, lambda: self.move_face(sets=True)),
                (5, lambda: self.together(lambda: self.try_lines(self.inside_line, tries=2))),
                (5, lambda: self.together(lambda: self.try_lines(self.through_line, tries=2))),
                (6, lambda: self.together(lambda: self.try_lines(self.around_line, tries=2))),
                (7, lambda: self.together(lambda: self.try_lines(self.spans_line, tries=2))),
                (3, lambda: self.together(lambda: self.try_lines(self.between_line)
                                          and (self.note_used() or True))),
                (2, lambda: self.together(lambda: self.try_lines(self.ground_line))),
                (2, self.move_raised), (3, self.move_group), (1, self.move_undo),
                (3, self.seed_hollow)]

    def seed_hollow(self) -> bool:
        """A tube, or a hollowed part: what `inside`, `through` and inner faces need."""
        rng = self.rng
        if rng.random() < 0.5:
            return self.move_face(shape=("tube", rng.choice(g.ORIENTATIONS)), plain=True)
        boxes = [i for i in self.infos().values() if i.plain and i.single and not i.hollow
                 and min(i.frame.size) >= 20]
        if not boxes:
            return False
        return self.add(self.feature_line(["hollowed out", "top"], rng.choice(boxes)))

    def make_single(self, cell: tuple[str, str]) -> Made | None:
        """A plan of one part on the ground: every shape in every turn, without the kernel
        (a lone part has nothing to be checked against)."""
        rng = self.rng
        want = [rng.choice(config.BLOCK), rng.choice(config.BLOCK), rng.choice(config.BLOCK)]
        if rng.random() < 0.3:
            want[rng.randrange(3)] = rng.choice(config.SMALL[4:])
        head = self.shape_text(cell[0], cell[1], want)
        if head is None:
            return None
        if rng.random() < 0.25:             # one size left to the default rule (section 4)
            words = head.split(" by ")
            if len(words) > 1:
                at = rng.randrange(1, len(words))
                if cell[0] == "prism" and at == 0:
                    at = 1
                tail = words[at].split(" ", 1)
                words[at] = "default" + (f" {tail[1]}" if len(tail) > 1 else "")
                head = " by ".join(words)
        try:
            if not self.add(f"{self.new_name()}: {head}, on ground", may_keep_rejected=False):
                return None
            self.force("done")
        except GiveUp:
            return None
        return self.finish(True)

    def make(self) -> Made | None:
        rng = self.rng
        if self.long:
            wanted = rng.randint(*config.LONG_LINES)
        else:
            top = config.MAX_TRAIN_LINES if self.tier == "arithmetic" else 14
            wanted = rng.randint(2, top)
        try:
            self.start()
            weights, makers = zip(*self.moves(), strict=True)
            budget = wanted * 6
            negatives_at = sorted(rng.sample(range(2, max(4, wanted)), k=min(2, self.max_rejections))) \
                if self.negative else []
            while len(self.text) < wanted - 1 and budget > 0:
                budget -= 1
                if negatives_at and len(self.text) >= negatives_at[0] and not self.in_group:
                    negatives_at.pop(0)
                    self.negative_line()
                    continue
                rng.choices(makers, weights)[0]()
            if self.negative and self.kept_rejections == 0:
                if rng.random() < 0.5:          # leave something floating: `done` is refused
                    self.add(self.raised_line(), may_keep_rejected=False)
                else:
                    self.negative_line()
            limit = config.LONG_LINES[1] if self.long else config.MAX_TRAIN_LINES
            if len(self.text) > limit - 1:
                return None
            if self.long and len(self.text) < config.LONG_LINES[0] - 1:
                self.count("too short for a long plan")
                return None
            done = self.force("done")
        except GiveUp as why:
            self.count(f"gave up: {why}")
            return None
        complete = done.built and all(reply.built for reply in self.replies)
        if complete == self.negative:
            self.count("negative plan with nothing rejected" if self.negative
                       else "done was refused")
            return None
        return self.finish(complete)

    def finish(self, complete: bool) -> Made | None:
        text = "\n".join(self.text) + "\n"
        plan = parse_plan(text)
        # The lines were read one at a time while candidates came and went. A fresh reading
        # of the finished text must give the same lines, or the plan is not kept.
        if [echo_line(line) for line in plan.lines] != [reply.echo for reply in self.replies]:
            self.count("fresh reading differs")
            return None
        world = self.resolver.state.world
        resolution = Resolution(list(self.replies), list(world.bodies.values()),
                                set(world.touching), [], complete,
                                self.resolver.by_arithmetic, self.resolver.by_kernel,
                                self.judge.calls)
        return Made(text, plan, resolution, dict(self.stats))
