"""The executor's side of the contract: read a plan line by line, build or reject each.

    resolution = resolve_text(plan_text)
    for reply in resolution.replies:
        print(reply.number, reply.reply, "|", reply.echo)
    resolution.bodies            # every solid, exact, when the plan is complete

Every line gets one reply from the fixed list of PLAN_LANGUAGE section 10. A rejected
line changes nothing. `undo` and `undo to NAME` put back an earlier state.

How one part line is handled:
  names      every part it mentions must exist                (else: unknown part X)
  scenes     one new part per copy when the target is a set   (scenes_for)
  prototype  sizes, turn, pins, solve                         (prototype.py)
  copies     the repetition                                   (repeating.py)
  checks     overlap, contact                                 (contact.py, else judge.py)
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace

from forge.plan import echo_line, parse_plan
from forge.plan import grammar as g
from forge.plan.model import Line, Plan
from forge.resolve import contact, featuring, prototype, repeating
from forge.resolve import space as sp
from forge.resolve.bodies import Body
from forge.resolve.judge import KernelFailed, KernelJudge
from forge.resolve.scene import Item, Scene, World
from forge.resolve.shapes import DoesNotFit

BUILT = "built"
NOT_UNDERSTOOD = "rejected: not understood"
DOES_NOT_FIT = "rejected: does not fit"
TOUCHES_NOTHING = "rejected: touches nothing"
NOT_JOINED = "rejected: not joined to the ground"


@dataclass
class Attempt:
    """What a rejected line tried to add, kept so a test can ask the kernel about it."""

    before: list[Body]
    tried: list[Body]
    against: list[str] = field(default_factory=list)     # the two bodies found to overlap
    floating: list[str] = field(default_factory=list)    # bodies found to touch nothing


@dataclass
class Reply:
    number: int
    text: str
    echo: str                           # how the line was read (forge.plan.echo)
    reply: str                          # one of the fixed replies of section 10
    notes: list[str] = field(default_factory=list)       # defaults used, rest values, why
    made: list[str] = field(default_factory=list)        # the bodies this line built
    by_kernel: bool = False             # the checks of this line needed the CAD kernel
    attempt: Attempt | None = None

    @property
    def built(self) -> bool:
        return self.reply == BUILT


@dataclass
class _State:
    world: World = field(default_factory=World)
    draft: World | None = None          # the open group's own parts
    draft_name: str | None = None
    groups: dict[str, World] = field(default_factory=dict)
    done: bool = False

    @property
    def visible(self) -> World:
        return self.draft if self.draft is not None else self.world


@dataclass
class Resolution:
    replies: list[Reply]
    bodies: list[Body]                  # the structure as it stands after the last line
    touching: set[frozenset[str]]
    stages: list[tuple[int, list[Body]]]    # (line number, bodies) after each line that built
    complete: bool                      # every line built and `done` was accepted
    pairs_by_arithmetic: int = 0
    pairs_by_kernel: int = 0
    kernel_calls: int = 0


class Rejected(Exception):
    def __init__(self, reply: str, note: str = "", attempt: Attempt | None = None) -> None:
        super().__init__(reply)
        self.reply, self.note, self.attempt = reply, note, attempt


class Resolver:
    def __init__(self, judge: KernelJudge | None = None) -> None:
        self.judge = judge or KernelJudge()
        self._owns_judge = judge is None
        self.state = _State()
        self.history: list[tuple[str | None, _State]] = []      # (name, the state BEFORE)
        self.stages: list[tuple[int, list[Body]]] = []
        self.by_arithmetic = self.by_kernel = 0
        self._used_kernel = False

    def close(self) -> None:
        if self._owns_judge:
            self.judge.close()

    # --- one line ---------------------------------------------------------------------------

    def read(self, line: Line) -> Reply:
        reply = Reply(line.number, line.text.strip(), echo_line(line), BUILT)
        if not line.accepted or line.kind == "unknown":
            reply.reply = NOT_UNDERSTOOD
            return reply
        before = copy.deepcopy(self.state)
        self._used_kernel = False
        try:
            if self.state.done:
                raise Rejected(NOT_UNDERSTOOD, "the plan is already done")
            handler = {"part": self._part, "feature": self._feature, "group": self._group,
                       "end": self._end, "undo": self._undo, "undo to": self._undo,
                       "done": self._done}[line.kind]
            handler(line, reply)
        except Rejected as stop:
            self.state = before             # a rejected line changes nothing
            reply.reply, reply.attempt = stop.reply, stop.attempt
            if stop.note:
                reply.notes.append(stop.note)
        except DoesNotFit as stop:
            self.state = before
            reply.reply = DOES_NOT_FIT
            reply.notes.append(str(stop))
        else:
            if line.kind not in ("undo", "undo to"):
                self.history.append((line.name, before))
            if self.state.draft is None:
                self.stages.append((line.number, list(self.state.world.bodies.values())))
        reply.by_kernel = self._used_kernel
        return reply

    # --- names ------------------------------------------------------------------------------

    def _check_names(self, line: Line) -> None:
        known = self.state.visible.items
        amounts = [a.amount for a in [*line.alignments, *line.options] if a.amount]
        if line.repetition and line.repetition.diameter:
            amounts.append(line.repetition.diameter)
        named = list(line.placement.parts) if line.placement else []
        named += [item.part for item in [*line.sizes, *amounts, *line.options, *line.alignments,
                                         line.repetition] if item is not None and item.part]
        if line.kind == "feature":
            named = [line.target] + [e.part for e in line.feature.edges]
            named += [e.amount.part for e in line.feature.edges if e.amount.part]
        for name in named:
            if name not in known:
                raise Rejected(f"rejected: unknown part {name}")

    # --- part lines -------------------------------------------------------------------------

    def _scenes(self, line: Line) -> list[Scene]:
        """One scene per new part: a placement on a set makes one part per copy (section 7)."""
        world = self.state.visible
        placement = line.placement
        counts = [len(world.items[name].copies) for name in placement.parts]
        if placement.kind == g.SPANS or not counts:
            return [Scene(world)]
        if placement.kind == g.BETWEEN:
            if len(counts) == 1:
                return [Scene(world)]
            first, second = placement.parts
            if counts[0] > 1 and counts[1] > 1:
                if counts[0] != counts[1]:
                    raise DoesNotFit("the two sets have different numbers of copies")
                return [Scene(world, {first: i, second: j})
                        for i, j in _nearest_pairs(world, first, second)]
            if counts[0] > 1:
                return [Scene(world, {first: i}) for i in range(counts[0])]
            return [Scene(world, {second: i}) for i in range(counts[1])]
        if counts[0] > 1 and not line.option("across"):
            return [Scene(world, {placement.parts[0]: i}) for i in range(counts[0])]
        return [Scene(world)]

    def _part(self, line: Line, reply: Reply) -> None:
        world = self.state.visible
        self._check_names(line)
        if line.name in world.items or line.name in self.state.world.items:
            raise Rejected(NOT_UNDERSTOOD, f"the name {line.name} is already used")
        units: list[list[Body]] = []
        may_float = False
        if line.placement is None:          # the first part of a group
            if self.state.draft is None or self.state.draft.bodies:
                raise Rejected(NOT_UNDERSTOOD, "a part line needs a placement")
            made = prototype.first_of_group(line, Scene(world))
            units, may_float = [made.bodies], True
            reply.notes += made.notes
        else:
            for scene in self._scenes(line):
                made = prototype.build(line, scene, self.state.groups)
                reply.notes += [note for note in made.notes if note not in reply.notes]
                may_float = may_float or made.facts.may_float
                starts = [(sp.IDENTITY, (0.0, 0.0, 0.0))] + [(sp.IDENTITY, at)
                                                             for at in made.facts.also_at]
                for start in starts:
                    placed = [body.moved(*start) for body in made.bodies]
                    for turn, shift in repeating.moves(line, scene, made.facts, placed,
                                                       made.inset):
                        units.append([body.moved(turn, shift) for body in placed])
        units = _named(line, units)
        template = self.state.groups[line.group] if line.group else None
        self._check_and_add(world, line, units, may_float, template)
        reply.made = [body.name for unit in units for body in unit]

    def _relations(self, pairs: list[tuple[Body, Body]], everyone: list[Body]) -> dict:
        """overlap / touch / apart for each pair: arithmetic first, the kernel for the rest."""
        found, unsure = {}, []
        for a, b in pairs:
            verdict = contact.relation(a, b)
            if verdict is None:
                unsure.append((a, b))
            else:
                found[frozenset((a.name, b.name))] = verdict
                self.by_arithmetic += 1
        if unsure:
            self._used_kernel = True
            wanted = {body.name for pair in unsure for body in pair}
            try:
                view = self.judge.look([body for body in everyone if body.name in wanted])
            except KernelFailed as stop:
                raise DoesNotFit(f"the CAD kernel could not build it ({stop})") from stop
            bad = [name for name in wanted if view.solids.get(name) != 1]
            if bad:
                raise DoesNotFit(f"{bad[0]} would not be one sound solid")
            for a, b in unsure:
                pair = frozenset((a.name, b.name))
                found[pair] = (contact.OVERLAP if pair in view.overlaps else contact.TOUCH
                               if pair in view.touching else contact.APART)
                self.by_kernel += 1
        return found

    def _check_and_add(self, world: World, line: Line, units: list[list[Body]], may_float: bool,
                       template: World | None) -> None:
        new = [body for unit in units for body in unit]
        old = list(world.bodies.values())
        attempt = Attempt(old, new)
        if world.has_ground and prototype.below_ground(new):
            raise DoesNotFit("part of it would be below the ground")
        unit_of = {body.name: index for index, unit in enumerate(units) for body in unit}
        pairs = [(a, b) for a in new for b in old]
        pairs += [(a, b) for i, a in enumerate(new) for b in new[i + 1:]
                  if unit_of[a.name] != unit_of[b.name]]
        found = self._relations(pairs, old + new)

        touched: set[int] = set()
        for a, b in pairs:
            verdict = found[frozenset((a.name, b.name))]
            allowed = b.name in a.may_overlap or a.name in b.may_overlap
            if verdict == contact.OVERLAP and not allowed:
                if b.name in unit_of:
                    raise DoesNotFit(f"its copies {a.name} and {b.name} run into each other")
                attempt.against = [a.name, b.name]
                raise Rejected(f"rejected: overlaps {b.owner}",
                               f"{a.name} runs into {b.name}", attempt)
            if verdict != contact.APART:
                world.touching.add(frozenset((a.name, b.name)))
                if b.name not in unit_of:       # touching an earlier part is what counts
                    touched.add(unit_of[a.name])
        for index, unit in enumerate(units):
            grounded = world.has_ground and any(contact.on_ground(body) for body in unit)
            if index not in touched and not grounded and not may_float:
                attempt.floating = [body.name for body in unit]
                raise Rejected(TOUCHES_NOTHING, f"{unit[0].name} would float", attempt)

        for unit in units:
            for body in unit:
                world.bodies[body.name] = body
        world.items[line.name] = Item(line.name, [[b.name for b in unit] for unit in units],
                                      is_group=template is not None)
        if template is not None:        # the joints inside each copy of the group travel with it
            for unit in units:
                names = dict(zip(template.bodies, (body.name for body in unit), strict=True))
                world.touching |= {frozenset(names[n] for n in pair) for pair in template.touching}

    # --- feature lines ----------------------------------------------------------------------

    def _feature(self, line: Line, reply: Reply) -> None:
        world = self.state.visible
        self._check_names(line)
        item = world.items[line.target]
        if item.is_group:
            raise DoesNotFit("a feature goes on one part, not on a placed group")
        changed = []
        for index in range(len(item.copies)):
            scene = Scene(world, {line.target: index})
            body = scene.body(line.target)
            cut = featuring.make_cut(line, scene, body, notes := [])
            reply.notes += [note for note in notes if note not in reply.notes]
            changed.append(replace(body, cuts=[*body.cuts, cut]))
        for body in changed:
            world.bodies[body.name] = body
        # A feature changes the solid, so its contacts are asked again: the kernel says
        # whether it is still one solid, what it now touches and what a boss runs into.
        names = {body.name for body in changed}
        near = [other for other in world.bodies.values() if other.name not in names
                and any(min(body.frame.depth_into(other.frame)) >= -sp.TOL for body in changed)]
        self._used_kernel = True
        try:
            view = self.judge.look(changed + near)
        except KernelFailed as stop:
            raise DoesNotFit(f"the CAD kernel could not build it ({stop})") from stop
        self.by_kernel += len(changed) * len(near)
        for body in changed:
            if view.solids.get(body.name) != 1:
                raise DoesNotFit(f"{body.name} would not be one sound solid")
        world.touching = {pair for pair in world.touching if not pair & names}
        for pair in view.touching | set(view.overlaps):
            if pair & names:
                a, b = (world.bodies[name] for name in pair)
                allowed = b.name in a.may_overlap or a.name in b.may_overlap
                if pair in view.overlaps and not allowed:
                    other = b if a.name in names else a
                    raise Rejected(f"rejected: overlaps {other.owner}",
                                   f"the feature runs into {other.name}")
                world.touching.add(pair)
        reply.made = sorted(names)

    # --- groups, undo, done -----------------------------------------------------------------

    def _group(self, line: Line, reply: Reply) -> None:
        if self.state.draft is not None or line.name in self.state.groups:
            raise Rejected(NOT_UNDERSTOOD, "a group is already open, or the name is used")
        self.state.draft, self.state.draft_name = World(has_ground=False), line.name

    def _end(self, line: Line, reply: Reply) -> None:
        if self.state.draft is None or not self.state.draft.bodies:
            raise Rejected(NOT_UNDERSTOOD, "no group with parts is open")
        self.state.groups[self.state.draft_name] = self.state.draft
        self.state.draft = self.state.draft_name = None

    def _undo(self, line: Line, reply: Reply) -> None:
        if line.kind == "undo":
            if not self.history:
                raise Rejected(NOT_UNDERSTOOD, "there is nothing to undo")
            _, self.state = self.history.pop()
            return
        places = [i for i, (name, _) in enumerate(self.history) if name == line.name]
        if not places:
            raise Rejected(f"rejected: unknown part {line.name}")
        keep = places[-1] + 1               # NAME stays; everything built after it goes
        if keep < len(self.history):
            self.state = self.history[keep][1]
            del self.history[keep:]

    def _done(self, line: Line, reply: Reply) -> None:
        """One connected body that reaches the ground (section 10)."""
        if self.state.draft is not None:
            raise Rejected(NOT_UNDERSTOOD, f"group {self.state.draft_name} has no `end`")
        world = self.state.world
        groups = contact.joined_groups(list(world.bodies), world.touching)
        loose = [group for group in groups
                 if not any(contact.on_ground(world.bodies[name]) for name in group)]
        if loose:
            raise Rejected(NOT_JOINED, f"not joined to the ground: {sorted(loose[0])[:4]}")
        if len(groups) != 1:
            raise Rejected(NOT_JOINED, f"the structure is {len(groups)} separate bodies; "
                                       "section 10 asks for one")
        self.state.done = True


def _nearest_pairs(world: World, first: str, second: str) -> list[tuple[int, int]]:
    """Pair each copy of one set with the copy of the other nearest to it, closest first."""
    def middle(name: str, index: int) -> sp.Vec:
        return sp.around([b.frame for b in world.bodies_of(name, index)]).mid

    count = len(world.items[first].copies)
    distances = sorted((sp.length(sp.sub(middle(first, i), middle(second, j))), i, j)
                       for i in range(count) for j in range(count))
    pairs, used_first, used_second = [], set(), set()
    for _, i, j in distances:
        if i not in used_first and j not in used_second:
            pairs.append((i, j))
            used_first.add(i)
            used_second.add(j)
    return sorted(pairs)


def _named(line: Line, units: list[list[Body]]) -> list[list[Body]]:
    """Give every body its name: "legs", or "legs 1" ... for a set; group parts keep theirs."""
    named = []
    for index, unit in enumerate(units, start=1):
        base = line.name if len(units) == 1 else f"{line.name} {index}"
        new_name = {body.name: base if len(unit) == 1 else f"{base} {body.name}" for body in unit}
        # A part of a group may be `sunk` into another part of the same group. That
        # permission is a NAME, so it must be renamed with the parts: "outer tab" sunk into
        # "fm15" becomes "ch20 outer tab" sunk into "ch20 fm15". Left as "fm15" (the fault
        # found on 6 Oct 2026) the declared overlap was reported as an undeclared one by
        # every checker that reads `may_overlap`, and it would have allowed an overlap with
        # an unrelated part that happened to be called "fm15".
        named.append([replace(body, name=new_name[body.name], owner=line.name, line=line.number,
                              may_overlap=frozenset(new_name.get(other, other)
                                                    for other in body.may_overlap))
                      for body in unit])
    return named


def resolve_plan(plan: Plan, judge: KernelJudge | None = None) -> Resolution:
    resolver = Resolver(judge)
    try:
        replies = [resolver.read(line) for line in plan.lines]
    finally:
        kernel_calls = resolver.judge.calls
        resolver.close()
    world = resolver.state.world
    complete = resolver.state.done and all(reply.built for reply in replies)
    return Resolution(replies, list(world.bodies.values()), world.touching, resolver.stages,
                      complete, resolver.by_arithmetic, resolver.by_kernel, kernel_calls)


def resolve_text(text: str, judge: KernelJudge | None = None) -> Resolution:
    return resolve_plan(parse_plan(text), judge)
