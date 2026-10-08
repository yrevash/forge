"""The coverage table: which pieces of the plan language a plan uses, and how often.

A CELL is one thing the executor must have seen: `shape:cone|pointing down`,
`place:behind`, `align:flush|left`, `rep:grid|top-bottom face`, `feat:slot|front`.
`cells_of(lines, replies)` reads the cells off the READER's structured lines (and the
resolver's replies: only a line that was `built` counts, a rejected line counts only as
its reply). Nothing here looks at geometry, so the audit can recompute every plan's cells
from the stored record alone.

`universe()` is the table defined up front: every required cell, whether plans that hit
it need the CAD kernel, and its minimum. PAIR cells (`pair:...`) are the important
pairings; the ones the language cannot make are listed in `impossible_pair` with the rule.
"""

from __future__ import annotations

from forge.plan import checks
from forge.plan import grammar as g
from forge.plan.model import Line
from forge.plans_data import config

FACE_CLASS = {"on top of": "top-bottom face", "under": "top-bottom face",
              "in front of": "front-back face", "behind": "front-back face",
              "left of": "left-right face", "right of": "left-right face"}
FACE_PLACEMENTS = tuple(g.FACE_PLACEMENTS)
ARITHMETIC_SHAPES = ("box", "cylinder")
KERNEL_SHAPES = ("tube", "cone", "sphere", "dome", "prism", "wedge", "tapered box", "bar")
ALL_SHAPES = ARITHMETIC_SHAPES + KERNEL_SHAPES
WEDGE_FLATS = {"left": ("bottom", "top", "front", "back"), "right": ("bottom", "top", "front", "back"),
               "front": ("bottom", "top", "left", "right"), "back": ("bottom", "top", "left", "right"),
               "up": ("back", "front", "left", "right"), "down": ("back", "front", "left", "right")}
PLACE_KINDS = ("point", "edge", "corner")
ALIGN_KINDS = ("flush", "flush-opposite", "face offset", "inset", "offset", "at height",
               "down to ground", "same axis")
REP_KINDS = ("each corner", "corners", "corner", "evenly spaced", "spread", "grid", "around",
             "mirrored")
REPLIES = ("built", "overlaps", "touches nothing", "does not fit", "unknown part",
           "not understood", "not joined to the ground")
POSITIONED = tuple(name for name, (_, _, positioned) in g.FEATURES.items() if positioned)
INNER_FEATURES = ("hole", "blind hole", "boss", "pocket")


# --- reading the cells off one line ---------------------------------------------------------

def shape_word(line: Line) -> str:
    if line.group:
        return "group"
    return f"bar {line.profile.upper() if len(line.profile) == 1 else line.profile}" \
        if line.shape == "bar" else line.shape


def turn_word(line: Line) -> str:
    if line.shape == "wedge":
        return f"pointing {line.pointing} flat {line.flat or g.WEDGE_DEFAULT_FLAT[line.pointing]}"
    if line.shape in g.POINTING_SHAPES:
        return f"pointing {line.pointing}"
    return line.orientation


def _place_kind(anchor: str) -> str:
    return "corner" if anchor.endswith("corner") else "edge" if anchor.endswith("edge") else "face"


def _amount_cell(size) -> str:
    return f"amount:{size.source}"


def reply_word(reply: str) -> str:
    word = reply.removeprefix("rejected: ")
    for start in ("overlaps", "unknown part"):
        if word.startswith(start):
            return start
    return word


class Known:
    """What earlier lines defined, as the reader saw it: name -> (shape word, copies)."""

    def __init__(self) -> None:
        self.parts: dict[str, tuple[str, int | None]] = {}
        self.features: dict[str, list[str]] = {}       # part -> the step kinds cut into it

    def shape(self, name: str) -> str:
        return self.parts.get(name, ("unknown", 1))[0]

    def is_set(self, name: str) -> bool:
        return (self.parts.get(name, ("unknown", 1))[1] or 1) > 1


def _part_cells(line: Line, known: Known) -> list[str]:
    cells = []
    shape = shape_word(line)
    if line.group:
        cells.append("group:placed")
    else:
        cells.append(f"shape:{shape}|{turn_word(line)}")
    plain = line.shape or "group"

    # sizes
    rests = [size for size in line.sizes if size.source == "rest"]
    for size in line.sizes:
        if size.source == "number":
            cells.append("size:number")
        elif size.source == "same as":
            cells.append(f"size:same as|{size.dimension}")
        elif size.source == "share":
            cells.append(f"size:share|{size.share}")
        elif size.source == "default":
            cells.append(f"size:default|{plain}")
        if size.part and known.is_set(size.part):
            cells.append("set:in a size")

    placement = line.placement
    if placement is None:
        return cells + ["group:first part"]
    kind = placement.kind
    place = kind
    if kind == g.ABOVE_GROUND:
        heights = [a.kind for a in line.alignments if a.kind.endswith("at height")]
        place = f"above ground|{heights[-1] if heights else 'no height'}"
    elif kind == g.BETWEEN:
        way = checks.direction_of_slot(line, rests[0].slot) if rests else "number"
        place = f"between|{way}"
    elif kind in ("through", g.AROUND):
        slot = "length" if line.shape == "bar" else "height"
        place = f"{kind}|{checks.direction_of_slot(line, slot)}"
    elif kind == g.SPANS:
        place = "spans|" + "-".join(_place_kind(a) for a in placement.anchors)
    cells.append(f"place:{place}")
    cells.append(f"pair:shape×place|{plain}|{kind}")
    for name in placement.parts:
        cells.append(f"pair:place×target|{kind}|{known.shape(name)}")
    sets = [name for name in placement.parts if known.is_set(name)]
    across = line.option("across")
    if kind == g.BETWEEN and len(placement.parts) == 1:
        cells.append("set:between X")
    elif kind == g.BETWEEN and len(sets) == 2:
        cells.append("set:between set and set")
    elif kind == g.BETWEEN and len(sets) == 1:
        cells.append("set:between set and part")
    elif sets and not across and kind != g.SPANS:
        cells.append(f"set:one per copy|{kind}")
        if line.repetition:
            cells.append("set:repetition on one per copy")
    if line.group:
        cells.append(f"group:placed|{kind}")
        if line.repetition:
            cells.append("group:placed with repetition")
        if any(a.kind == "down to ground" for a in line.alignments):
            cells.append("group:down to ground")

    for rest in rests:
        if kind == g.BETWEEN:
            where = "between"
        elif kind == g.SPANS:
            where = "spans"
        elif kind == "inside" and checks.direction_of_slot(line, rest.slot) != "height":
            where = "inside"
        elif across and checks.direction_of_slot(line, rest.slot) != g.DIRECTION_OF_FACE[
                g.FACE_PLACEMENTS[kind]]:
            where = "across"
        elif any(a.kind == "down to ground" for a in line.alignments):
            where = "down to ground"
        else:
            where = "two faces"
        cells.append(f"size:rest|{where}")

    for option in line.options:
        cells.append(f"option:{option.kind}|{kind}")
        if option.amount is not None:
            cells.append(_amount_cell(option.amount))

    has_flush = any(a.kind == "flush" for a in line.alignments)
    for a in line.alignments:
        inner = bool(a.other_face) and a.other_face.startswith("inner ")
        if a.kind == "flush":
            same = a.other_face.removeprefix("inner ") == a.face
            word = "flush" if same else "flush-opposite"
            cells.append(f"align-inner:flush|{a.face}" if inner else f"align:{word}|{a.face}")
            pair = word
        elif a.kind == "face offset":
            cells.append(f"align-inner:{a.relation}|{a.face}" if inner
                         else f"align:{a.relation}|{a.face}")
            pair = "face offset"
        elif a.kind == "inset":
            cells.append("align:inset|" + ("flush" if has_flush else "corner"))
            pair = "inset"
        elif a.kind == "offset":
            cells.append(f"align:offset|{a.face}")
            pair = "offset"
        elif a.kind in ("top at height", "bottom at height"):
            cells.append(f"align:{a.kind}")
            pair = "at height"
        else:
            cells.append(f"align:{a.kind}")
            pair = a.kind
        cells.append(f"pair:place×align|{kind}|{pair}")
        if a.amount is not None:
            cells.append(_amount_cell(a.amount))
        if a.part and known.is_set(a.part):
            cells.append("set:in an alignment")

    r = line.repetition
    if r is not None:
        face = FACE_CLASS.get(kind, "no face")
        if r.kind == "each corner":
            cell = f"each corner|{face}"
        elif r.kind in ("corners", "corner"):
            cell = f"{r.kind}|{r.side}"
        elif r.kind in ("evenly spaced", "spread"):
            cell = f"{r.kind}|{r.direction}"
        elif r.kind == "grid":
            cell = f"grid|{face}"
        elif r.kind == "around":
            cell = ("around on circle" if r.diameter else "around beside") \
                + (" facing outward" if r.outward else "")
            if r.diameter:
                cells.append(_amount_cell(r.diameter))
        else:
            cell = f"mirrored|{r.direction}"
        cells.append(f"rep:{cell}")
        cells.append(f"pair:place×rep|{kind}|{r.kind}")
        if r.part and r.part not in placement.parts:
            cells.append("set:repetition names another part")
    return cells


def _feature_cells(line: Line, known: Known) -> list[str]:
    feature = line.feature
    cells = [f"feat:{feature.name}|{line.face}", (f"pair:feat×shape|{feature.name}|"
             f"{known.shape(line.target)}")]
    _, slot_words, positioned = g.FEATURES[feature.name]
    cells.append("featslots:all written" if len(feature.slots) == len(slot_words)
                 else "featslots:some by rule")
    if positioned:
        how = feature.position if feature.position != "edges" else f"edges {len(feature.edges)}"
        cells.append(f"featpos:{how}")
    if feature.name == g.HOLLOWING_FEATURE:
        cells.append(f"hollow:open at {feature.open_at}" if feature.open_at else "hollow:closed")
    if known.is_set(line.target):
        cells.append("set:feature on a set")
    if line.in_group:
        cells.append("group:feature inside")
    return cells


def cells_of(lines: list[Line], replies: list[str]) -> list[str]:
    """Every cell a plan hits, with repeats, in line order. `replies`: one reply per line."""
    known = Known()
    cells: list[str] = []
    for line, reply in zip(lines, replies, strict=True):
        cells.append(f"reply:{reply_word(reply)}")
        if reply != "built":
            continue
        if line.kind == "part":
            cells += _part_cells(line, known)
            known.parts[line.name] = (line.shape or "group", line.copies)
        elif line.kind == "feature":
            cells += _feature_cells(line, known)
            known.features.setdefault(line.target, []).append(line.feature.step_kind)
        elif line.kind == "group":
            cells.append("group:declared")
        elif line.kind in ("undo", "undo to", "done"):
            cells.append(f"control:{line.kind}")
    return cells


def feature_kinds_by_part(lines: list[Line], replies: list[str]) -> dict[str, list[str]]:
    """part name -> the step kinds of the features built on it (for the split)."""
    found: dict[str, list[str]] = {}
    for line, reply in zip(lines, replies, strict=True):
        if reply == "built" and line.kind == "feature":
            found.setdefault(line.target, []).append(line.feature.step_kind)
    return found


def line_cell_sets(lines: list[Line], replies: list[str]) -> list[set[str]]:
    """Per built part line, the simple tags the held-out line pairs are written in:
    `place:KIND`, `target:SHAPE`, `rep:KIND`, `align:KIND`, `shape:SHAPE`."""
    known = Known()
    found = []
    for line, reply in zip(lines, replies, strict=True):
        if reply != "built" or line.kind != "part":
            continue
        tags = {f"shape:{line.shape or 'group'}"}
        if line.placement:
            tags.add(f"place:{line.placement.kind}")
            tags |= {f"target:{known.shape(name)}" for name in line.placement.parts}
        if line.repetition:
            tags.add(f"rep:{line.repetition.kind}")
        tags |= {f"align:{a.kind}" for a in line.alignments}
        found.append(tags)
        known.parts[line.name] = (line.shape or "group", line.copies)
    return found


# --- the table, defined up front ------------------------------------------------------------

def _shape_cells() -> dict[str, bool]:
    """shape cell -> does it need the kernel."""
    cells = {}
    for shape in ("box", "cylinder", "tube", "prism"):
        for turn in g.ORIENTATIONS:
            cells[f"shape:{shape}|{turn}"] = shape not in ARITHMETIC_SHAPES
    for profile in ("L", "T", "U", "I", "tube"):
        for turn in g.ORIENTATIONS:
            cells[f"shape:bar {profile}|{turn}"] = True
    cells["shape:sphere|standing"] = True
    for shape in ("cone", "dome", "tapered box"):
        for way in g.POINTINGS:
            cells[f"shape:{shape}|pointing {way}"] = True
    for way, flats in WEDGE_FLATS.items():
        for flat in flats:
            cells[f"shape:wedge|pointing {way} flat {flat}"] = True
    return cells


def impossible_pair(cell: str) -> str | None:
    """Why a pair cell cannot be made in the language as it stands, or None if it can."""
    _, family, *rest = cell.replace("pair:", "pair|").split("|")
    if family == "place×rep":
        kind, rep = rest
        side = kind in ("in front of", "behind", "left of", "right of")
        if rep == "corner" and side:
            return "a single corner is named front-left ...: both words must lie in the touched face"
    if family == "place×align":
        kind, align = rest
        if align == "down to ground" and kind == "on top of":
            return "on top of X fixes the bottom; down to ground would fix it again"
    return None


def universe() -> dict[str, dict]:
    """Every required cell: {cell: {"kernel": bool, "minimum": int}}."""
    table: dict[str, dict] = {}

    def add(cell: str, kernel: bool = False, minimum: int | None = None) -> None:
        if minimum is None:
            pair = cell.startswith("pair:")
            minimum = ((config.MIN_PAIR_KERNEL if kernel else config.MIN_PAIR_ARITHMETIC) if pair
                       else (config.MIN_KERNEL if kernel else config.MIN_ARITHMETIC))
        table[cell] = {"kernel": kernel, "minimum": minimum}

    for cell, kernel in _shape_cells().items():
        add(cell, kernel)
    # placements
    for cell in ("on ground", "above ground|top at height", "above ground|bottom at height",
                 *FACE_PLACEMENTS, "between|length", "between|depth", "between|height",
                 "between|number"):
        add(f"place:{cell}")
    add("place:inside", True)
    for kind in ("through", "around"):
        for way in g.DIRECTIONS:
            add(f"place:{kind}|{way}", True)
    for a in ("face", "edge", "corner"):
        for b in ("face", "edge", "corner"):
            add(f"place:spans|{a}-{b}", True)
    for option in ("gap", "sunk", "across"):
        for kind in FACE_PLACEMENTS:
            add(f"option:{option}|{kind}")
    # alignments
    for face in g.FACES:
        add(f"align:flush|{face}")
        add(f"align:flush-opposite|{face}")
        add(f"align:offset|{face}")
    for relation in ("above", "below"):
        for face in ("top", "bottom"):
            add(f"align:{relation}|{face}")
    for relation in ("inside", "beyond"):
        for face in ("left", "right", "front", "back"):
            add(f"align:{relation}|{face}")
    for cell in ("inset|flush", "inset|corner", "top at height", "bottom at height",
                 "down to ground", "same axis"):
        add(f"align:{cell}")
    for cell in ("flush|bottom", "inside|left", "inside|right", "inside|front", "inside|back",
                 "above|bottom"):
        add(f"align-inner:{cell}", True)
    for source in ("number", "same as", "share"):
        add(f"amount:{source}")
    # repetitions
    for face in ("top-bottom face", "front-back face", "left-right face"):
        add(f"rep:each corner|{face}")
        add(f"rep:grid|{face}")
    for side in g.CORNER_SIDES:
        add(f"rep:corners|{side}")
    for side in ("front-left", "front-right", "back-left", "back-right"):
        add(f"rep:corner|{side}")
    for kind in ("evenly spaced", "spread"):
        for way in g.DIRECTIONS:
            add(f"rep:{kind}|{way}")
    for cell in ("around on circle", "around on circle facing outward", "around beside",
                 "around beside facing outward", "mirrored|left-right", "mirrored|front-back"):
        add(f"rep:{cell}")
    # sets and groups
    for kind in FACE_PLACEMENTS:
        add(f"set:one per copy|{kind}")
        add(f"group:placed|{kind}")
    for cell in ("between X", "between set and set", "between set and part", "in an alignment",
                 "in a size", "repetition on one per copy", "repetition names another part"):
        add(f"set:{cell}")
    add("set:feature on a set", True)
    for cell in ("declared", "first part", "placed", "placed with repetition", "down to ground"):
        add(f"group:{cell}")
    add("group:feature inside", True)
    # sizes
    add("size:number")
    for dimension in g.DIMENSIONS:
        add(f"size:same as|{dimension}")
    for share in g.SHARES:
        add(f"size:share|{share}")
    for where in ("between", "down to ground", "two faces", "across"):
        add(f"size:rest|{where}")
    add("size:rest|spans", True)
    add("size:rest|inside", True)
    for shape in g.SHAPES:
        add(f"size:default|{shape}", shape not in ARITHMETIC_SHAPES)
    add("size:default|bar", True)
    # features
    for name in g.FEATURES:
        faces = ("top",) if name in g.TOP_ONLY_FEATURES else g.FACES
        for face in faces:
            add(f"feat:{name}|{face}", True)
    for name in INNER_FEATURES:
        add(f"feat:{name}|inner bottom", True)
    for wall in ("left", "right", "front", "back"):
        add(f"feat:hole|inner {wall}", True)
    for cell in ("centred", "edges 1", "edges 2", "at"):
        add(f"featpos:{cell}", True)
    for cell in ("all written", "some by rule"):
        add(f"featslots:{cell}", True)
    for face in g.FACES:
        add(f"hollow:open at {face}", True)
    add("hollow:closed", True)
    for cell in ("undo", "undo to", "done"):
        add(f"control:{cell}")
    for reply in REPLIES:
        add(f"reply:{reply}", minimum=config.MIN_REPLY)

    # the important pairings
    def pair(cell: str, kernel: bool) -> None:
        if impossible_pair(cell) is None:
            add(cell, kernel)

    for kind in FACE_PLACEMENTS:
        for shape in ALL_SHAPES:
            pair(f"pair:place×target|{kind}|{shape}", shape in KERNEL_SHAPES)
            pair(f"pair:shape×place|{shape}|{kind}", shape in KERNEL_SHAPES)
        pair(f"pair:place×target|{kind}|group", False)
        for align in ALIGN_KINDS:
            pair(f"pair:place×align|{kind}|{align}", False)
        for rep in REP_KINDS:
            pair(f"pair:place×rep|{kind}|{rep}", False)
    for shape in ARITHMETIC_SHAPES:
        pair(f"pair:shape×place|{shape}|on ground", False)
        pair(f"pair:shape×place|{shape}|between", False)
        pair(f"pair:place×target|between|{shape}", False)
    for align in ("flush", "face offset", "offset", "at height"):
        pair(f"pair:place×align|between|{align}", False)
        pair(f"pair:place×align|on ground|{align}", False)
    return table


def tally(counts: dict[str, int]) -> dict:
    """The coverage table with what was reached, and the cells under their minimum."""
    table = universe()
    rows = {cell: {**spec, "count": counts.get(cell, 0)} for cell, spec in table.items()}
    under = {cell: row for cell, row in rows.items() if row["count"] < row["minimum"]}
    extra = {cell: count for cell, count in counts.items() if cell not in table}
    return {"cells": rows, "under_minimum": under, "not_in_table": extra}
