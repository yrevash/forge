"""Structures as plans: a generator's structure, re-said in the plan language.

A structure from forge/generators/structures is an ordered list of steps; each step's
parts are boxes and upright cylinders at exact places. This file writes each step as
plan lines WITHOUT coordinates, by finding a relation the language has:

    what it touches    between A and B / under X / on top of X / left of X ... / on ground
    where on that      centred (said by saying nothing), flush with a face, a face a
                       stated distance inside or beyond another face, at a height
    how many           at each corner of X, at the front corners of X, mirrored,
                       N evenly spaced / spread, N around X on circle D, one per copy of a set

It does not know what a chair is. For each group of like parts it lists candidate lines
in a fixed order of preference, offers each to the reader and the resolver, and keeps the
first one whose built parts are exactly the generator's parts (same count, same shape,
every face within 1e-6 mm). When no candidate works for a group of parts, the group is
split (four corners -> two pairs -> single parts) and tried again. A part that still
cannot be said is a FINDING about the language: the conversion fails and says which step.

The slot sources decide one thing: a height that is `stated` or `default` in the
generator is written `top at height H`; an `arithmetic` one is written against a part.

    plan = convert(structure)      # plans_data.structures.Converted
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from forge.generators.structures.draft import Step, Structure
from forge.plans_data.record import NoKernel
from forge.plans_data.session import Session
from forge.resolve.resolver import Resolution
from forge.resolve.space import Box

EPS = 1e-6
MATCH = 1e-5                      # a built face must be this close to the generator's
MAX_TRIES_PER_LINE = 60
LOW_FACE = ("left", "front", "bottom")
HIGH_FACE = ("right", "back", "top")
DIRECTION = ("length", "depth", "height")
# face placement -> (axis, +1 when the new part is on the high side of the target)
KINDS = (("under", 2, -1), ("on top of", 2, 1), ("left of", 0, -1), ("right of", 0, 1),
         ("in front of", 1, -1), ("behind", 1, 1))


def fmt(value: float) -> str:
    """A number written exactly enough to touch (the resolver's tolerance is 1e-6)."""
    text = f"{value:.9f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


@dataclass(frozen=True)
class Part:
    name: str
    shape: str
    low: tuple
    high: tuple

    @property
    def box(self) -> Box:
        return Box(self.low, self.high)

    @property
    def size(self) -> tuple:
        return tuple(self.high[k] - self.low[k] for k in range(3))

    @property
    def mid(self) -> tuple:
        return tuple((self.high[k] + self.low[k]) / 2 for k in range(3))


@dataclass
class Item:
    """A name the plan has defined: its copies' frames, as the resolver built them."""

    name: str
    shape: str
    copies: list[Box]

    @property
    def frame(self) -> Box:
        return Box(tuple(min(c.low[k] for c in self.copies) for k in range(3)),
                   tuple(max(c.high[k] for c in self.copies) for k in range(3)))


@dataclass
class Converted:
    ok: bool
    text: str = ""
    plan: object = None
    resolution: Resolution | None = None
    failed_step: str = ""
    reason: str = ""
    shift: tuple = (0.0, 0.0)
    flags: dict = field(default_factory=dict)


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= EPS


def _key(shape: str, low, high) -> tuple:
    """A part as something to compare: its shape and frame. A lying cylinder and an upright
    one are both "cylinder"; their frames tell them apart."""
    return (shape.removesuffix("_x"), *(round(v / MATCH) for v in (*low, *high)))


class Converter:
    def __init__(self, structure: Structure) -> None:
        self.structure = structure
        self.session = Session(NoKernel())
        self.items: dict[str, Item] = {}
        self.names: set[str] = set()
        self.flags = {"lines": 0, "above_ground_after_first": 0, "long_decimals": 0,
                      "split_steps": 0, "candidates_tried": 0}
        first = structure.steps[0].parts[0]
        # The plan's origin is the middle of its first part (resolver README, gap 12).
        self.shift = (-first.at[0], -first.at[1])
        self.steps: list[tuple[Step, list[Part]]] = []
        for step in structure.steps:
            parts = []
            for solid in step.parts:
                low = (solid.low[0] + self.shift[0], solid.low[1] + self.shift[1], solid.low[2])
                high = (solid.high[0] + self.shift[0], solid.high[1] + self.shift[1], solid.high[2])
                parts.append(Part(solid.name, solid.shape, low, high))
            self.steps.append((step, parts))

    # --- names --------------------------------------------------------------------------------

    def name_for(self, step: Step, group: list[Part]) -> str:
        base = step.name.replace("_", " ")
        if len(group) != len(step.parts):
            endings = []
            for part in group:
                ending = part.name[len(step.name):].strip("_").replace("_", " ")
                words = [w for w in ending.split() if not w.isdigit()]
                if words and " ".join(words) not in endings:
                    endings.append(" ".join(words))
            shared = [word for word in (endings[0].split() if endings else [])
                      if all(word in ending.split() for ending in endings)]
            if len(group) == 1:
                base = group[0].name.replace("_", " ")
            elif len(endings) > 1 and shared:
                base = f"{' '.join(shared)} {base}"         # front legs, back legs
            elif len(endings) == 1:
                base = f"{base} {endings[0]}"
            elif len(endings) == 2 and sorted(endings) in (["left", "right"], ["back", "front"]):
                base = f"{base} {'-'.join(endings)}"
            elif endings:
                base = f"{base} {endings[0]}"
        # The resolver calls the copies of a set "legs 1", "legs 2": a part may not take
        # one of those names, nor a name whose own copies would take a part's name.
        bodies = set(self.session.resolver.state.world.bodies)

        def taken(name: str) -> bool:
            return (name in self.names or name in bodies
                    or name.rstrip("s") in {n.rstrip("s") for n in self.names}
                    or any(other.startswith(name + " ") and other[len(name) + 1:].isdigit()
                           for other in self.names | bodies))

        name, number = base, 0
        while taken(name):
            number += 1
            name = f"{base} {'bcdefghjk'[number - 1]}" if number < 10 else f"{base} x{number}"
        return name

    # --- how a group of like parts is arranged --------------------------------------------------

    def arrangements(self, group: list[Part]) -> list[dict]:
        """Ways to say the group with ONE line: each is a repetition (or none), the part the
        line describes, the axes the repetition takes care of, and how many parts the line
        must make per copy of the repetition (`per`)."""
        n = len(group)
        ordered = sorted(group, key=lambda p: (p.mid[2], p.mid[1], p.mid[0]))
        first = ordered[0]
        if n == 1:
            return [{"rep": None, "proto": first, "free": (), "per": 1}]
        # First: one new part per copy of a set (`between front legs and back legs`).
        found = [{"rep": None, "proto": first, "free": (), "per": n}]
        values = [sorted({round(p.mid[k], 6) for p in group}) for k in range(3)]
        varying = [k for k in range(3) if len(values[k]) > 1]
        low = tuple(min(p.low[k] for p in group) for k in range(3))
        high = tuple(max(p.high[k] for p in group) for k in range(3))

        def corner_items(axes_fixed: dict) -> list[tuple[str, float]]:
            """Items X such that the group is tucked into corners of X (with one inset)."""
            made = []
            # single parts first: "the corners of seat" reads better than "of back legs"
            for item in sorted(self.items.values(), key=lambda i: len(i.copies) > 1):
                frame = item.frame
                inset = low[0] - frame.low[0]
                ok = inset >= -EPS
                for k in (0, 1):
                    want_low = k not in axes_fixed or axes_fixed[k] < 0
                    want_high = k not in axes_fixed or axes_fixed[k] > 0
                    if want_low and not _same(low[k] - frame.low[k], inset):
                        ok = False
                    if want_high and not _same(frame.high[k] - high[k], inset):
                        ok = False
                if ok:
                    made.append((item.name, max(0.0, inset)))
            return made

        def with_inset(words: str, inset: float) -> str:
            return (f"inset {fmt(inset)}, " if inset > EPS else "") + words

        if n == 4 and varying == [0, 1] and len(values[0]) == 2 and len(values[1]) == 2:
            for name, inset in corner_items({}):
                found.append({"rep": with_inset(f"at each corner of {name}", inset),
                              "proto": first, "free": (0, 1), "per": 1})
        if n == 2 and varying in ([0], [1]):
            axis = varying[0]
            other = 1 - axis
            sided = [(name, inset, word) for word, sign in ((LOW_FACE[other], -1), (HIGH_FACE[other], 1))
                     for name, inset in corner_items({other: sign})]
            for name, inset, side_word in sorted(sided, key=lambda t: len(self.items[t[0]].copies) > 1):
                found.append({"rep": with_inset(f"at the {side_word} corners of {name}", inset),
                              "proto": first, "free": (0, 1), "per": 1})
            way = "left-right" if axis == 0 else "front-back"
            found.append({"rep": f"mirrored {way}", "proto": first, "free": (), "per": 1,
                          "mirror": axis})
        # rows: equal steps along one axis; the other parts of the group repeat the row
        for axis in varying:
            count = len(values[axis])
            steps = [b - a for a, b in itertools.pairwise(values[axis])]
            if count < 2 or n % count or any(abs(s - steps[0]) > 1e-5 for s in steps):
                continue
            for word in ("evenly spaced", "spread"):
                found.append({"rep": f"{count} {word} along {DIRECTION[axis]}", "proto": first,
                              "free": (axis,), "per": n // count})
        if n >= 3 and varying == [0, 1]:
            centre = (sum(p.mid[0] for p in group) / n, sum(p.mid[1] for p in group) / n)
            radii = [math.dist(centre, p.mid[:2]) for p in group]
            if max(radii) - min(radii) < 1e-6:
                for item in self.items.values():
                    mid = item.frame.mid if len(item.copies) == 1 else None
                    if mid and _same(mid[0], centre[0]) and _same(mid[1], centre[1]):
                        found.append({"rep": f"{n} around {item.name} on circle "
                                             f"{fmt(2 * radii[0])}", "proto": first,
                                      "free": (0, 1), "per": 1})
        return found

    # --- where one part sits ------------------------------------------------------------------

    def placements(self, part: Part, per: int, group: list[Part], step: Step) -> list[dict]:
        """The relations the language has for this part, best first."""
        found = []
        items = list(self.items.values())
        if not items:
            if _same(part.low[2], 0.0):
                return [{"text": "on ground", "fixed": (2,), "mid": (0.0, 0.0), "ref": None}]
            return [{"text": "above ground", "fixed": (), "mid": (0.0, 0.0), "ref": None,
                     "height": True}]

        def meets(box: Box, axis: int) -> bool:
            return all(min(part.high[k], box.high[k]) - max(part.low[k], box.low[k]) > EPS
                       for k in range(3) if k != axis)

        def between(a: Box, b: Box):
            for axis in range(3):
                for low, high in ((a, b), (b, a)):
                    if (_same(part.low[axis], low.high[axis]) and _same(part.high[axis], high.low[axis])
                            and meets(a, axis) and meets(b, axis)):
                        shared_low = [max(a.low[k], b.low[k]) for k in range(3)]
                        shared_high = [min(a.high[k], b.high[k]) for k in range(3)]
                        if any(shared_high[k] - shared_low[k] < -EPS for k in range(3) if k != axis):
                            return None
                        return axis, tuple((shared_low[k] + shared_high[k]) / 2 for k in range(3))
            return None

        # between
        for item in items:
            if len(item.copies) == 2 and per == 1:
                made = between(*item.copies)
                if made:
                    found.append({"text": f"between {item.name}", "fixed": (made[0],),
                                  "rest": made[0], "mid": made[1], "ref": item.name})
        for a, b in itertools.permutations(items, 2):
            counts = (len(a.copies), len(b.copies))
            if per == 1 and counts != (1, 1):
                continue
            if per > 1 and max(counts) != per:
                continue
            if per > 1 and min(counts) not in (1, per):
                continue
            hits = [between(ca, cb) for ca in a.copies for cb in b.copies]
            hits = [h for h in hits if h]
            if hits and a.name < b.name or (hits and counts[0] > counts[1]):
                found.append({"text": f"between {a.name} and {b.name}", "fixed": (hits[0][0],),
                              "rest": hits[0][0], "mid": hits[0][1], "ref": a.name})
        # against one face
        for item in items:
            if len(item.copies) != per and not (per == 1 and len(item.copies) == 1):
                continue
            for copy in item.copies:
                hit = False
                for kind, axis, side in KINDS:
                    touching = (_same(part.low[axis], copy.high[axis]) if side > 0
                                else _same(part.high[axis], copy.low[axis]))
                    overlap = all(min(part.high[k], copy.high[k]) - max(part.low[k], copy.low[k]) > EPS
                                  for k in range(3) if k != axis)
                    if touching and overlap:
                        made = {"text": f"{kind} {item.name}", "fixed": (axis,), "mid": copy.mid,
                                "ref": item.name}
                        if kind == "under" and _same(part.low[2], 0.0):
                            made.update(rest=2, ground=True)
                        found.append(made)
                        hit = True
                if hit:
                    break
        # one part against a whole set: `on top of legs, across legs` (section 5)
        for item in items:
            if per != 1 or len(item.copies) < 2:
                continue
            frame = item.frame
            for kind, axis, side in KINDS:
                touching = (_same(part.low[axis], frame.high[axis]) if side > 0
                            else _same(part.high[axis], frame.low[axis]))
                overlap = all(min(part.high[k], frame.high[k]) - max(part.low[k], frame.low[k]) > EPS
                              for k in range(3) if k != axis)
                if touching and overlap:
                    found.append({"text": f"{kind} {item.name}, across {item.name}",
                                  "fixed": (axis,), "mid": frame.mid, "ref": item.name})
        if per == 1:
            if _same(part.low[2], 0.0):
                found.append({"text": "on ground", "fixed": (2,), "mid": (0.0, 0.0), "ref": None})
            found.append({"text": "above ground", "fixed": (), "mid": (0.0, 0.0), "ref": None,
                          "height": True})
        return found

    def pins(self, part: Part, axis: int, default: float | None, ref: str | None,
             stated_height: bool) -> list[list[str]]:
        """Ways to say where the part sits along one axis, best first. [] means: say nothing."""
        options: list[list[str]] = []
        if default is not None and _same((part.low[axis] + part.high[axis]) / 2, default):
            options.append([])
        low_word, high_word = LOW_FACE[axis], HIGH_FACE[axis]
        at_height = []
        if axis == 2:
            at_height = [[f"top at height {fmt(part.high[2])}"],
                         [f"bottom at height {fmt(part.low[2])}"]]
            if stated_height:
                options += at_height
        refs = [self.items[ref]] if ref else []
        others = [item for item in self.items.values() if item.name != ref]
        for item in refs + others:
            frame = item.frame
            if _same(part.low[axis], frame.low[axis]):
                options.append([f"flush with {item.name}'s {low_word}"])
            if _same(part.high[axis], frame.high[axis]):
                options.append([f"flush with {item.name}'s {high_word}"])
        for item in refs + others:
            frame = item.frame
            if _same(part.high[axis], frame.low[axis]):
                options.append([f"{high_word} flush with {item.name}'s {low_word}"])
            if _same(part.low[axis], frame.high[axis]):
                options.append([f"{low_word} flush with {item.name}'s {high_word}"])
        if axis == 2 and not stated_height:
            options += at_height
        for item in (refs or others[:1]):
            frame = item.frame
            use_low = (part.low[axis] + part.high[axis]) / 2 <= frame.mid[axis]
            mine = part.low[axis] if use_low else part.high[axis]
            theirs = frame.low[axis] if use_low else frame.high[axis]
            word = low_word if use_low else high_word
            move = mine - theirs
            if axis == 2:
                relation = "above" if move > 0 else "below"
            else:
                inward = move > 0 if use_low else move < 0
                relation = "inside" if inward else "beyond"
            options.append([f"{word} {fmt(abs(move))} {relation} {item.name}'s {word}"])
        unique = []
        for option in options:
            if option not in unique:
                unique.append(option)
        return unique[:3]

    def shape_words(self, part: Part, rest_axis: int | None) -> str | None:
        size = [fmt(v) for v in part.size]
        if part.shape == "cylinder_x":          # a round bar lying left to right
            if rest_axis not in (None, 0):
                return None
            return f"cylinder {size[1]} by {'rest' if rest_axis == 0 else size[0]} lying along length"
        if part.shape == "cylinder":
            if rest_axis not in (None, 2):
                return None
            return f"cylinder {size[0]} by {'rest' if rest_axis == 2 else size[2]}"
        if rest_axis is not None:
            size[rest_axis] = "rest"
        return "box " + " by ".join(size)

    def candidates(self, group: list[Part], step: Step, floating: bool):
        """Candidate lines, best first. `floating`: also offer `above ground` for a part that
        is not the first (a part held by its height alone: the last resort)."""
        slot = step.slots.get("top") or step.slots.get("bottom")
        stated_height = slot is not None and slot.source in ("stated", "default")
        for how in self.arrangements(group):
            part, free = how["proto"], how["free"]
            for place in self.placements(part, how["per"], group, step):
                if place["text"] == "above ground" and self.items and not floating:
                    continue
                if how["rep"] and how["rep"].startswith("mirrored") and place["text"] in (
                        "on ground", "above ground"):
                    continue
                if how["rep"] and ("spaced" in how["rep"] or "spread" in how["rep"]) \
                        and place["text"] in ("on ground", "above ground"):
                    continue
                rests = [None] if "rest" not in place else [place["rest"], None]
                if place["text"].startswith("between"):
                    rests = [place["rest"]]
                for rest in rests:
                    head = self.shape_words(part, rest)
                    if head is None:
                        continue
                    pieces = [place["text"]]
                    if place.get("ground") and rest == 2:
                        pieces.append("down to ground")
                    axes = [k for k in range(3) if k not in place["fixed"] and k not in free]
                    if place.get("ground") and rest is None and 2 not in place["fixed"]:
                        axes.append(2)
                    per_axis = []
                    for axis in axes:
                        default = place["mid"][axis] if axis < len(place["mid"]) else None
                        if place.get("height") and axis == 2:
                            options = self.pins(part, 2, None, None, True)
                            options = [o for o in options if o and "at height" in o[0]] or options
                        else:
                            options = self.pins(part, axis, default, place["ref"], stated_height)
                        per_axis.append(options or [[]])
                    for combo in itertools.product(*per_axis):
                        said = [piece for option in combo for piece in option]
                        tail = [how["rep"]] if how["rep"] else []
                        yield f"{head}, " + ", ".join(pieces + said + tail)

    # --- one group of like parts --------------------------------------------------------------

    def say(self, step: Step, group: list[Part], floating: bool = False) -> bool:
        want = sorted(_key(p.shape, p.low, p.high) for p in group)
        name = self.name_for(step, group)
        tried = 0
        for body in self.candidates(group, step, floating):
            if tried >= MAX_TRIES_PER_LINE:
                break
            tried += 1
            text = f"{name}: {body}"
            reply = self.session.offer(text)
            if reply.built:
                made = self.session.made(reply)
                got = sorted(_key(b.shape, b.frame.low, b.frame.high) for b in made)
                if got == want and not self.session.judge.asked:
                    self.session.keep()
                    self.names.add(name)
                    self.items[name] = Item(name, group[0].shape, [b.frame for b in made])
                    self.flags["lines"] += 1
                    self.flags["candidates_tried"] += tried
                    if "above ground" in text and len(self.items) > 1:
                        self.flags["above_ground_after_first"] += 1
                    if any(len(word.partition(".")[2]) > 3 for word in text.replace(",", " ").split()
                           if word.replace(".", "").isdigit()):
                        self.flags["long_decimals"] += 1
                    return True
            self.session.drop()
        self.flags["candidates_tried"] += tried
        return False

    def split(self, group: list[Part]) -> list[list[Part]]:
        """Smaller groups to try when one line cannot say the whole group."""
        n = len(group)
        values = [sorted({round(p.mid[k], 6) for p in group}) for k in range(3)]
        for axis in (1, 0, 2):              # front / back first, as people name furniture
            if 1 < len(values[axis]) < n:
                return [[p for p in group if round(p.mid[axis], 6) == value]
                        for value in values[axis]]
        return [[p] for p in group]

    def say_or_split(self, step: Step, group: list[Part], split_first: bool = False,
                     floating: bool = False) -> str | None:
        """None when the group was said; else the name of the part that could not be."""
        if not split_first and self.say(step, group, floating):
            return None
        if len(group) == 1:
            return group[0].name
        self.flags["split_steps"] += 1
        for smaller in self.split(group):
            failed = self.say_or_split(step, smaller, floating=floating)
            if failed:
                return failed
        return None

    def say_group(self, step: Step, group: list[Part], split_first: bool) -> str | None:
        """Say the group against other parts if that can be done at all (as one line or
        split up); only then fall back on parts held by their height alone."""
        mark = (self.session.mark(), dict(self.items), set(self.names), dict(self.flags))
        failed = self.say_or_split(step, group, split_first)
        if not failed:
            return None
        session_mark, self.items, self.names, self.flags = mark
        self.session.rollback(session_mark)
        return self.say_or_split(step, group, split_first, floating=True)

    def spanned_later(self, index: int, group: list[Part]) -> bool:
        """Does a later part run between two of these uprights? Then name them as two pairs,
        so that `between front legs` can be said (the chair of PLAN_LANGUAGE section 11)."""
        if len(group) != 4:
            return False
        for _, later in self.steps[index + 1:]:
            for part in later:
                for axis in (0, 1):
                    lows = [g for g in group if _same(part.low[axis], g.high[axis])]
                    highs = [g for g in group if _same(part.high[axis], g.low[axis])]
                    if lows and highs:
                        return True
        return False

    def convert(self) -> Converted:
        for index, (step, parts) in enumerate(self.steps):
            groups: dict[tuple, list[Part]] = {}
            for part in parts:              # like parts: same shape and size
                groups.setdefault((part.shape, *(round(v, 6) for v in part.size)), []).append(part)
            for group in groups.values():
                failed = self.say_group(step, group, self.spanned_later(index, group))
                if failed:
                    return Converted(False, failed_step=f"{step.name} ({step.role}, {step.placement})",
                                     reason=f"no line of the language builds {failed} where the "
                                            f"generator put it", flags=self.flags)
        reply = self.session.offer("done")
        if not reply.built:
            self.session.drop()
            return Converted(False, failed_step="done", reason=reply.reply, flags=self.flags)
        self.session.keep()
        finished = self.session.finish()
        if finished is None:
            return Converted(False, failed_step="(whole plan)", reason="a fresh reading differs",
                             flags=self.flags)
        text, plan, resolution = finished
        problems = compare(self.structure, resolution, self.shift)
        if problems:
            return Converted(False, failed_step="(whole plan)", reason="; ".join(problems[:3]),
                             flags=self.flags)
        return Converted(True, text, plan, resolution, shift=self.shift, flags=self.flags)


def compare(structure: Structure, resolution: Resolution, shift: tuple) -> list[str]:
    """Is the resolver's structure the generator's? Part count, every part, overall size."""
    problems = []
    want = sorted(_key(s.shape, (s.low[0] + shift[0], s.low[1] + shift[1], s.low[2]),
                       (s.high[0] + shift[0], s.high[1] + shift[1], s.high[2]))
                  for s in structure.solids)
    got = sorted(_key(b.shape, b.frame.low, b.frame.high) for b in resolution.bodies)
    if len(got) != len(want):
        problems.append(f"part count: plan {len(got)}, generator {len(want)}")
    elif got != want:
        problems.append("a part's size or position differs")
    if resolution.bodies:
        low = [min(b.frame.low[k] for b in resolution.bodies) for k in range(3)]
        high = [max(b.frame.high[k] for b in resolution.bodies) for k in range(3)]
        for axis in range(3):
            if abs((high[axis] - low[axis]) - structure.expected_bbox[axis]) > 1e-3:
                problems.append(f"overall {'XYZ'[axis]}: plan {high[axis] - low[axis]:g}, "
                                f"generator {structure.expected_bbox[axis]:g}")
    if not resolution.complete:
        problems.append("the plan is not complete")
    return problems


def convert(structure: Structure) -> Converted:
    return Converter(structure).convert()
