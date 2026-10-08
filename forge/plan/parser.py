"""Parse a whole plan: one Line per line of text, with the rules that span lines.

    plan = parse_plan(text)
    plan.accepted                 # True only if every line is in the language
    plan.lines[3].rejections      # why line 4 was not accepted

The parser is STRICT: a line is accepted only if it is one of the forms written in
the plan language (Draft 2). It never guesses what a planner meant.

How a line is read:
  1. grammar.clean      capitals, spaces and apostrophes are evened out
  2. the line's kind    done / undo / group / end / `on X: feature` / `name: shape ...`
  3. clauses.py         each comma-separated piece is read on its own
  4. names              every part a piece points at must have been defined earlier
  5. checks.py          the pieces must make sense together

A line that is rejected changes nothing, as the executor would behave (section 10), with
one exception made for reading whole plans: its name is remembered as a "ghost", so that
later lines pointing at it are not all rejected too. One mistake is counted once.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from forge.plan import checks, clauses, features
from forge.plan import grammar as g
from forge.plan.model import (
    DUPLICATE_NAME,
    FIRST_PART_NOT_ON_GROUND,
    MISSING_DONE,
    MISSING_PLACEMENT,
    MORE_THAN_ONE_PLACEMENT,
    MORE_THAN_ONE_REPETITION,
    NOT_IN_LANGUAGE,
    OUT_OF_PLACE,
    UNKNOWN_ALIGNMENT,
    UNKNOWN_ORIENTATION,
    UNKNOWN_PART,
    UNKNOWN_PLACEMENT,
    UNKNOWN_REPETITION,
    UNKNOWN_SHAPE,
    UNKNOWN_SIZE_FORM,
    WRONG_NUMBER_OF_SIZES,
    Alignment,
    Line,
    Option,
    Placement,
    Plan,
    Reject,
    Rejection,
    Repetition,
    Size,
)
from forge.plan.names import find

_UNKNOWN_PIECE = {"placement": UNKNOWN_PLACEMENT, "alignment": UNKNOWN_ALIGNMENT,
                  "repetition": UNKNOWN_REPETITION, "orientation": UNKNOWN_ORIENTATION}
_TURNING = re.compile(r"\b(standing|lying|pointing|flat)\b.*$")


@dataclass
class _Part:
    """What later lines need to know about an earlier part."""

    accepted: bool = True       # False: a "ghost", defined by a rejected line
    copies: int | None = 1      # how many parts the name stands for; None: cannot be told
    hollow: bool | None = False  # has an inside (a tube, or hollowed out); None: cannot be told


@dataclass
class _State:
    """Everything the parser remembers between lines. `undo` restores an older copy."""

    parts: dict[str, _Part] = field(default_factory=dict)      # top-level parts
    members: dict[str, _Part] = field(default_factory=dict)    # parts of the open group
    groups: dict[str, list[str]] = field(default_factory=dict)   # closed groups -> member names
    open_group: str | None = None
    taken: set[str] = field(default_factory=set)               # every name in use, plan-wide
    done: bool = False

    @property
    def visible(self) -> dict[str, _Part]:
        """The parts a line may point at. Inside a group, only that group's own parts."""
        return self.members if self.open_group is not None else self.parts


class _PlanParser:
    def __init__(self) -> None:
        self.state = _State()
        # (the name the line defined, the state BEFORE the line) for every accepted line.
        self.history: list[tuple[str | None, _State]] = []

    # --- one line ---------------------------------------------------------------------------

    def read(self, number: int, text: str) -> Line:
        line = Line(number, text, in_group=self.state.open_group)
        before = copy.deepcopy(self.state)
        cleaned = g.clean(text)
        try:
            self._dispatch(line, cleaned)
        except Reject as stop:
            line.rejections.append(stop.rejection)
        if line.accepted and line.kind not in ("undo", "undo to"):
            self.history.append((line.name, before))
        return line

    def _dispatch(self, line: Line, text: str) -> None:
        if self.state.done:
            raise Reject(OUT_OF_PLACE, text, "`done` must be the last line")
        if text == "done":
            line.kind = "done"
            if self.state.open_group is not None:
                raise Reject(OUT_OF_PLACE, text, f"group {self.state.open_group} has no `end`")
            self.state.done = True
        elif text == "undo" or text.startswith("undo to "):
            self._undo(line, text)
        elif text == "end":
            self._end_group(line)
        elif re.fullmatch(r"group [^:]+", text):
            self._start_group(line, text[len("group "):])
        elif match := re.fullmatch(r"on ([^:]+): ?(.*)", text):
            self._feature(line, match.group(1), match.group(2))
        elif ":" in text:
            name, _, body = text.partition(":")
            self._part(line, name.strip(), body.strip())
        else:
            raise Reject(NOT_IN_LANGUAGE, text)

    # --- control: undo, groups --------------------------------------------------------------

    def _undo(self, line: Line, text: str) -> None:
        if text == "undo":
            line.kind = "undo"
            if not self.history:
                raise Reject(OUT_OF_PLACE, text, "there is nothing to undo")
            _, self.state = self.history.pop()
            return
        line.kind = "undo to"
        written = text[len("undo to "):]
        defined = [name for name, _ in self.history if name is not None]
        line.name = find(written, defined)
        if line.name is None:
            raise Reject(UNKNOWN_PART, written, "no accepted line defined this name")
        # Keep NAME itself and drop every line after it.
        keep = max(i for i, (name, _) in enumerate(self.history) if name == line.name) + 1
        if keep < len(self.history):
            self.state = self.history[keep][1]
            del self.history[keep:]

    def _start_group(self, line: Line, name: str) -> None:
        line.kind, line.name = "group", name
        if self.state.open_group is not None:
            raise Reject(OUT_OF_PLACE, f"group {name}", "a group cannot be opened inside a group")
        # Open the group even when its name is refused, so that its lines and its `end`
        # are still read as a group and one mistake is counted once.
        self.state.open_group, self.state.members = name, {}
        self._check_new_name(name)
        self.state.taken.add(name)

    def _end_group(self, line: Line) -> None:
        line.kind, line.name = "end", self.state.open_group
        if self.state.open_group is None:
            raise Reject(OUT_OF_PLACE, "end", "no group is open")
        name, members = self.state.open_group, list(self.state.members)
        self.state.open_group, self.state.members = None, {}
        if not members:
            self.state.taken.discard(name)
            raise Reject(OUT_OF_PLACE, "end", f"group {name} has no parts")
        self.state.groups.setdefault(name, members)

    def _check_new_name(self, name: str) -> None:
        if not g.NAME_RE.fullmatch(name) or name in g.RESERVED_NAMES or name.startswith("on "):
            raise Reject(NOT_IN_LANGUAGE, name, "not a usable name")
        if name in self.state.taken:
            raise Reject(DUPLICATE_NAME, name, "this name is already used in the plan")

    # --- pointing at parts ------------------------------------------------------------------

    def _knows(self, written: str) -> bool:
        return find(written, self.state.visible) is not None

    def _point_at(self, line: Line, written: str) -> str:
        """The real name of the part `written` points at; a rejection if there is none."""
        name = find(written, self.state.visible)
        if name is None:
            where = f" in group {self.state.open_group}" if self.state.open_group else ""
            detail = f"no earlier line{where} defines this part"
            if written in self.state.groups:
                detail = "this is a group, not a part; place the group first"
            line.rejections.append(Rejection(UNKNOWN_PART, written, detail))
            return written
        if name != written:
            line.notes.append(f'read "{written}" as the part "{name}"')
        if not self.state.visible[name].accepted:
            line.notes.append(f'"{name}" was defined by a rejected line')
        return name

    def _info(self, name: str) -> _Part:
        """What is known about a part; a name that is not there is treated as a ghost."""
        return self.state.visible.get(name, _Part(False, None, None))

    def _needs_hollow(self, line: Line, name: str, written: str) -> None:
        if self._info(name).hollow is False:
            line.rejections.append(Rejection(
                OUT_OF_PLACE, written,
                f"{name} has no inside: only a tube or a part with `hollowed out` has one"))

    # --- `on X's FACE: FEATURE` -------------------------------------------------------------

    def _feature(self, line: Line, head: str, body: str) -> None:
        line.kind = "feature"
        target, line.face = features.read_head(head)
        line.target = self._point_at(line, target)
        try:
            line.feature = features.read_feature(body)
        except Reject as stop:
            line.rejections.append(stop.rejection)
            return
        for edge in line.feature.edges:
            edge.part = self._point_at(line, edge.part)
            if edge.amount.part is not None:
                edge.amount.part = self._point_at(line, edge.amount.part)
        if line.face.startswith("inner "):
            self._needs_hollow(line, line.target, f"{line.target}'s {line.face}")
        line.rejections.extend(checks.check_feature(line))
        if line.accepted and line.feature.name == g.HOLLOWING_FEATURE:
            self.state.visible[line.target].hollow = True

    # --- `NAME: SHAPE SIZES, PLACEMENT, ...` ------------------------------------------------

    def _part(self, line: Line, name: str, body: str) -> None:
        line.kind, line.name = "part", name
        problems = line.rejections
        name_is_usable = True
        try:
            self._check_new_name(name)
        except Reject as stop:
            problems.append(stop.rejection)
            name_is_usable = False

        pieces = [piece.strip() for piece in body.split(",")]
        if "" in pieces:
            problems.append(Rejection(NOT_IN_LANGUAGE, body, "an empty piece between commas"))
            pieces = [piece for piece in pieces if piece]
        if pieces:
            pieces = self._shape_and_sizes(line, pieces)
        first_in_group = self.state.open_group is not None and not self.state.members
        if first_in_group:
            # The first part of a group is where the group's frame starts: nothing to say
            # about where it goes (section 8).
            if pieces:
                problems.append(Rejection(OUT_OF_PLACE, ", ".join(pieces),
                                          "the first part of a group has a shape and sizes only"))
        else:
            self._pieces_after_the_shape(line, pieces)
            self._point_at_everything(line)
            self._check_line_rules(line)
        checks.add_notes(line)

        if name_is_usable:
            hollow = None if line.group else line.shape == "tube"
            self.state.visible[name] = _Part(line.accepted, line.copies, hollow)
            if line.accepted:
                self.state.taken.add(name)

    def _shape_and_sizes(self, line: Line, pieces: list[str]) -> list[str]:
        """Read the first piece (shape, sizes, turning); return the pieces that follow it."""
        problems = line.rejections
        text, rest = pieces[0], pieces[1:]

        # Sizes joined by commas ("cylinder 40, 100") break the line into too many pieces.
        commas_between_sizes = False
        while rest and clauses.looks_like_a_size(_TURNING.sub("", rest[0]).strip() or "?"):
            text, rest, commas_between_sizes = f"{text}, {rest[0]}", rest[1:], True
        if commas_between_sizes:
            problems.append(Rejection(UNKNOWN_SIZE_FORM, text,
                                      "sizes are joined with `by`, never with commas"))

        turned = _TURNING.search(text)
        lying = pointing = None
        if turned:
            text, written = text[:turned.start()].strip(), turned.group(0)
            lying, pointing = self._turning(line, written)
        if commas_between_sizes:
            return rest

        group = find(text, self.state.groups)
        if group is not None:
            line.group = group
            problems.extend(checks.check_turning(line, lying, pointing))
            return rest
        shape = next((name for name in sorted(g.SHAPES, key=len, reverse=True)
                      if text == name or text.startswith(name + " ")), None)
        if text == g.BAR or text.startswith(g.BAR + " "):
            profile, _, sizes_text = text[len(g.BAR):].strip().partition(" ")
            if profile not in g.BAR_PROFILES:
                problems.append(Rejection(UNKNOWN_SHAPE, f"bar {profile}".strip(),
                                          "a bar needs a profile: L, T, U, I or tube"))
                return rest
            line.shape, line.profile, slots = g.BAR, profile, g.BAR_PROFILES[profile]
        elif shape is not None:
            line.shape, slots, sizes_text = shape, g.SHAPES[shape], text[len(shape):]
        elif any(text.startswith(name + " ") for name in self.state.groups):
            problems.append(Rejection(WRONG_NUMBER_OF_SIZES, text, "a group takes no sizes"))
            return rest
        else:
            # The words before the first number are the shape the planner asked for.
            asked = re.split(r" (?=\d)", text)[0]
            problems.append(Rejection(UNKNOWN_SHAPE, asked,
                                      "not a shape, and no group of this name is declared yet"))
            return rest
        line.sizes = self._sizes(line, sizes_text.strip(), slots)
        problems.extend(checks.check_turning(line, lying, pointing))
        return rest

    def _turning(self, line: Line, written: str) -> tuple[str | None, str | None]:
        """`lying along depth`, `pointing left`, `pointing left flat back` after the sizes."""
        pointings, faces = "|".join(g.POINTINGS), "|".join(g.FACES)
        if written in g.ORIENTATIONS:
            line.orientation = written
            return written, None
        if m := re.fullmatch(rf"pointing ({pointings})(?: flat ({faces}))?", written):
            line.pointing, line.flat = m.group(1), m.group(2)
            return None, m.group(1)
        line.rejections.append(Rejection(UNKNOWN_ORIENTATION, written))
        return None, None

    def _sizes(self, line: Line, text: str, slots: tuple[str, ...]) -> list[Size]:
        written = [piece.strip() for piece in g.SIZE_SEPARATOR.split(text)] if text else []
        if len(written) != len(slots):
            shape = f"{line.shape} {line.profile}" if line.profile else line.shape
            order = " by ".join(slots)
            line.rejections.append(Rejection(
                WRONG_NUMBER_OF_SIZES, text or shape,
                f"{shape} takes {len(slots)} sizes ({order}); {len(written)} written. "
                "Write `default` for a size you leave to a rule"))
        sizes = []
        for index, piece in enumerate(written):
            slot = slots[index] if index < len(slots) else "extra"
            try:
                sizes.append(clauses.read_size(piece, slot))
            except Reject as stop:
                line.rejections.append(stop.rejection)
        return sizes

    def _read_piece(self, piece: str) -> Placement | Option | Alignment | Repetition | None:
        return (clauses.read_placement(piece, self._knows) or clauses.read_option(piece)
                or clauses.read_alignment(piece) or clauses.read_repetition(piece))

    def _pieces_after_the_shape(self, line: Line, pieces: list[str]) -> None:
        """The placement first, then options and alignments in any order, the repetition last."""
        problems = line.rejections
        tried_a_placement = False
        repetition_at = None
        misplaced_turning = 0       # such pieces are reported once and then not counted
        for index, piece in enumerate(pieces):
            try:
                item = self._read_piece(piece)
            except Reject as stop:
                problems.append(stop.rejection)
                tried_a_placement = True     # the piece was understood; only its number was not
                continue
            if isinstance(item, Placement):
                tried_a_placement = True
                if line.placement is not None:
                    problems.append(Rejection(MORE_THAN_ONE_PLACEMENT, piece))
                    continue
                line.placement = item
                if index != misplaced_turning:
                    problems.append(Rejection(OUT_OF_PLACE, piece,
                                              "the placement comes straight after the sizes"))
            elif isinstance(item, Option):
                line.options.append(item)
            elif isinstance(item, Alignment):
                line.alignments.append(item)
            elif isinstance(item, Repetition):
                if line.repetition is not None:
                    problems.append(Rejection(MORE_THAN_ONE_REPETITION, piece))
                    continue
                line.repetition, repetition_at = item, index
            else:
                kind = clauses.guess_kind(piece)
                if kind == "orientation" and _TURNING.fullmatch(piece) and (
                        piece in g.ORIENTATIONS or piece.startswith("pointing ")):
                    problems.append(Rejection(OUT_OF_PLACE, piece,
                                              "turning words come straight after the sizes, "
                                              "with no comma"))
                    misplaced_turning += 1
                    continue
                if kind is None and index == 0 and line.placement is None:
                    kind = "placement"      # the piece sits where the placement belongs
                tried_a_placement = tried_a_placement or kind == "placement"
                problems.append(Rejection(_UNKNOWN_PIECE.get(kind, NOT_IN_LANGUAGE), piece))
        one_repetition = all(r.reason != MORE_THAN_ONE_REPETITION for r in problems)
        if repetition_at is not None and one_repetition and repetition_at != len(pieces) - 1:
            problems.append(Rejection(OUT_OF_PLACE, pieces[repetition_at],
                                      "the repetition comes last"))
        if line.placement is None and not tried_a_placement:
            problems.append(Rejection(MISSING_PLACEMENT, line.text.strip(),
                                      "every part line needs exactly one placement"))

    def _point_at_everything(self, line: Line) -> None:
        """Replace every written part name by the real one; reject names that do not exist."""
        # The first part of a plan is `on ground` or `above ground`. Whatever else it is
        # placed against cannot exist yet; that is reported once, by _check_line_rules.
        if line.placement and self.state.visible:
            line.placement.parts = [self._point_at(line, p) for p in line.placement.parts]
        amounts = [a.amount for a in [*line.alignments, *line.options] if a.amount]
        if line.repetition and line.repetition.diameter:
            amounts.append(line.repetition.diameter)
        for item in [*line.sizes, *amounts, *line.options, *line.alignments, line.repetition]:
            if item is not None and item.part is not None:
                item.part = self._point_at(line, item.part)

    def _check_line_rules(self, line: Line) -> None:
        problems = line.rejections
        placement = line.placement
        if not self.state.visible and placement and placement.kind not in (g.ON_GROUND,
                                                                          g.ABOVE_GROUND):
            problems.append(Rejection(
                FIRST_PART_NOT_ON_GROUND, placement.kind,
                "the first part of a plan is `on ground` or `above ground`"))
        if placement is None:
            return      # the missing or unknown placement is already reported, once
        if placement.kind == "inside":
            self._needs_hollow(line, placement.parts[0], f"inside {placement.parts[0]}")
        for alignment in line.alignments:
            if alignment.other_face and alignment.other_face.startswith("inner "):
                self._needs_hollow(line, alignment.part,
                                   f"{alignment.part}'s {alignment.other_face}")
        problems.extend(self._count_copies(line))
        problems.extend(checks.check_placement(line))
        problems.extend(checks.check_alignments(line))
        problems.extend(checks.check_repetition(line))
        problems.extend(checks.check_rest(line))

    def _count_copies(self, line: Line) -> list[Rejection]:
        """How many parts the line makes, and the rules about sets as targets (section 7)."""
        placement = line.placement
        counts = [self._info(name).copies for name in placement.parts]
        text = " and ".join(placement.parts)
        line.copies = None
        if None in counts:
            return []                        # a ghost or a group: cannot be told
        per_target = 1
        if placement.kind == g.BETWEEN and len(counts) == 1:
            if counts[0] != 2:
                return [Rejection(UNKNOWN_PLACEMENT, f"between {text}",
                                  f"`between X` needs a name that stands for two copies; "
                                  f"{text} stands for {counts[0]}")]
        elif placement.kind == g.BETWEEN:
            if placement.parts[0] == placement.parts[1]:
                return [Rejection(UNKNOWN_PLACEMENT, f"between {text}",
                                  "`between A and B` needs two different names")]
            if counts[0] != counts[1] and 1 not in counts:
                return [Rejection(UNKNOWN_PLACEMENT, f"between {text}",
                                  f"the two sets have {counts[0]} and {counts[1]} copies; "
                                  "they cannot be paired")]
            per_target = max(counts)
        elif placement.kind == g.SPANS:
            if counts != [1, 1]:
                return [Rejection(UNKNOWN_PLACEMENT, f"spans ... {text}",
                                  "each end of a spanning part is on a single part, not a set")]
        elif counts and not line.option("across"):
            per_target = counts[0]           # one new part per member of the set
        line.copies = per_target * (line.repetition.copies if line.repetition else 1)
        return []

    # --- the end of the plan ----------------------------------------------------------------

    def problems_at_end(self) -> list[Rejection]:
        problems = []
        if self.state.open_group is not None:
            problems.append(Rejection(OUT_OF_PLACE, f"group {self.state.open_group}",
                                      "the group has no `end`"))
        if not self.state.done:
            problems.append(Rejection(MISSING_DONE, "", "a plan ends with `done`"))
        return problems


def parse_plan(text: str) -> Plan:
    """Parse plan text. Blank lines are skipped; every other line becomes a Line."""
    parser = _PlanParser()
    plan = Plan()
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw.strip():
            plan.lines.append(parser.read(number, raw))
    plan.problems = parser.problems_at_end()
    return plan
