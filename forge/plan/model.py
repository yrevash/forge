"""What a parsed plan is made of. Plain dataclasses; no parsing happens in this file.

A Plan is a list of Lines. A Line that the parser could not accept carries one or
more Rejections, each a REASON from the fixed list below plus the piece of text
that caused it. The coverage test counts those.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- the fixed list of rejection reasons ----------------------------------------------------
# The first nine are "the planner wrote something the language does not have".
UNKNOWN_SHAPE = "unknown shape"
UNKNOWN_PLACEMENT = "unknown placement"
UNKNOWN_ALIGNMENT = "unknown alignment"
UNKNOWN_REPETITION = "unknown repetition"
UNKNOWN_SIZE_FORM = "unknown size form"
UNKNOWN_ORIENTATION = "unknown orientation"
UNKNOWN_FEATURE = "unknown feature"
UNKNOWN_PART = "unknown part"
NOT_IN_LANGUAGE = "not in the language"
# The rest are "every piece is known, but the line or the plan breaks a rule".
WRONG_NUMBER_OF_SIZES = "wrong number of sizes"
MISSING_PLACEMENT = "missing placement"
MORE_THAN_ONE_PLACEMENT = "more than one placement"
MORE_THAN_ONE_REPETITION = "more than one repetition"
DUPLICATE_NAME = "duplicate name"
REST_NOT_DEFINED = "rest not defined"
FIRST_PART_NOT_ON_GROUND = "first part not on ground"
# A valid form in the wrong position (after done, wrong order of pieces) or on a line
# where it cannot apply (a gap without a face to leave it from, a tube-only placement ...).
OUT_OF_PLACE = "out of place"
MISSING_DONE = "missing done"

REASONS: tuple[str, ...] = (
    UNKNOWN_SHAPE, UNKNOWN_PLACEMENT, UNKNOWN_ALIGNMENT, UNKNOWN_REPETITION, UNKNOWN_SIZE_FORM,
    UNKNOWN_ORIENTATION, UNKNOWN_FEATURE, UNKNOWN_PART, NOT_IN_LANGUAGE, WRONG_NUMBER_OF_SIZES,
    MISSING_PLACEMENT, MORE_THAN_ONE_PLACEMENT, MORE_THAN_ONE_REPETITION, DUPLICATE_NAME,
    REST_NOT_DEFINED, FIRST_PART_NOT_ON_GROUND, OUT_OF_PLACE, MISSING_DONE,
)

# Reasons whose text fragment is a phrase the vocabulary lacks: "which move is missing".
MISSING_MOVE_REASONS: frozenset[str] = frozenset({
    UNKNOWN_SHAPE, UNKNOWN_PLACEMENT, UNKNOWN_ALIGNMENT, UNKNOWN_REPETITION, UNKNOWN_SIZE_FORM,
    UNKNOWN_ORIENTATION, UNKNOWN_FEATURE, NOT_IN_LANGUAGE,
})


@dataclass
class Rejection:
    reason: str         # one of REASONS
    fragment: str       # the piece of the line that caused it
    detail: str = ""    # one short sentence for a person


class Reject(Exception):
    """Raised inside the small clause readers; the line parser turns it into a Rejection."""

    def __init__(self, reason: str, fragment: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {fragment}")
        self.rejection = Rejection(reason, fragment, detail)


@dataclass
class Size:
    """One length and where its value comes from (PLAN_LANGUAGE section 4).

    Used for the sizes of a shape and also for every other distance in a line (an inset,
    a gap, a height), where `slot` is "amount" and `rest` / `default` cannot occur.
    """

    slot: str                       # "length", "outer diameter", "sides", "amount" ...
    source: str                     # "number" | "same as" | "share" | "rest" | "default"
    value: float | None = None      # for "number"
    share: str | None = None        # for "share": half | third | quarter | double
    part: str | None = None         # for "same as" and "share": the part pointed at
    dimension: str | None = None    # ... and which of its sizes


@dataclass
class Placement:
    # kind: "on ground" | "above ground" | "on top of" | "under" | "in front of" | "behind"
    #       | "left of" | "right of" | "inside" | "through" | "around" | "between" | "spans"
    kind: str
    parts: list[str] = field(default_factory=list)    # none, one or two
    anchors: list[str] = field(default_factory=list)  # spans: "top", "top-front edge" ...


@dataclass
class Option:
    """A piece that changes how the placement touches: gap, sunk, across (section 5)."""

    kind: str                       # "gap" | "sunk" | "across"
    amount: Size | None = None
    part: str | None = None


@dataclass
class Alignment:
    # kind: "flush" | "face offset" | "inset" | "offset" | "top at height" | "bottom at height"
    #       | "down to ground" | "same axis"
    kind: str
    face: str | None = None         # the new part's face
    amount: Size | None = None
    relation: str | None = None     # face offset: above | below | inside | beyond
    part: str | None = None
    other_face: str | None = None   # the other part's face: "top", or "inner left"


@dataclass
class Repetition:
    # kind: "each corner" | "corners" | "corner" | "evenly spaced" | "spread" | "grid"
    #       | "around" | "mirrored"
    kind: str
    count: int | None = None
    count2: int | None = None       # grid: the second number
    part: str | None = None
    side: str | None = None         # corners: front ...; corner: front-left ...
    direction: str | None = None    # spacing: length | depth | height; mirrored: left-right ...
    diameter: Size | None = None    # around: the circle, when written
    outward: bool = False           # around: `facing outward`

    @property
    def copies(self) -> int:
        """How many copies this repetition makes of one placed part."""
        if self.kind == "each corner":
            return 4
        if self.kind in ("corners", "mirrored"):
            return 2
        if self.kind == "corner":
            return 1
        return (self.count or 1) * (self.count2 or 1)


@dataclass
class EdgeDistance:
    """`20 from plate's left`: where a feature sits, measured from an edge of its face."""

    amount: Size
    part: str
    face: str


@dataclass
class Feature:
    name: str                       # as written: "counterbored hole"
    step_kind: str                  # the step kind: "counterbore"
    slots: dict[str, float] = field(default_factory=dict)   # only the slots that were written
    # Where on the face: centred (nothing written), edge distances, or `at (x, y)`.
    position: str = "centred"       # "centred" | "edges" | "at"
    edges: list[EdgeDistance] = field(default_factory=list)
    at: tuple[float, float] | None = None
    open_at: str | None = None      # hollowed out: the face left open


@dataclass
class Line:
    number: int                     # 1-based line number in the plan text
    text: str                       # the line as written
    # kind: "part" | "feature" | "group" | "end" | "undo" | "undo to" | "done" | "unknown"
    kind: str = "unknown"
    name: str | None = None         # the part or group this line defines (or undo's target)
    in_group: str | None = None     # the group this line sits inside, if any
    shape: str | None = None        # one of the ten shapes, or None when a group is used
    profile: str | None = None      # bar only
    group: str | None = None        # the group placed by this line, instead of a shape
    sizes: list[Size] = field(default_factory=list)
    orientation: str = "standing"   # standing | lying along length | lying along depth
    pointing: str = "up"            # up | down | left | right | front | back
    flat: str | None = None         # wedge only: the second full face, when written
    placement: Placement | None = None
    options: list[Option] = field(default_factory=list)
    alignments: list[Alignment] = field(default_factory=list)
    repetition: Repetition | None = None
    copies: int | None = None       # how many parts this line makes (None: cannot be told)
    target: str | None = None       # feature lines: the part the feature is cut into
    face: str = "top"               # feature lines: the face, "top" or "inner left" ...
    feature: Feature | None = None
    rejections: list[Rejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # warnings; a note never rejects a line

    @property
    def accepted(self) -> bool:
        return not self.rejections

    def option(self, kind: str) -> Option | None:
        return next((option for option in self.options if option.kind == kind), None)


@dataclass
class Plan:
    lines: list[Line] = field(default_factory=list)
    problems: list[Rejection] = field(default_factory=list)   # about the plan, not one line

    @property
    def accepted(self) -> bool:
        return not self.problems and all(line.accepted for line in self.lines)
