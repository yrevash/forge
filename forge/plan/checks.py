"""Rules about one whole line that need no geometry (the "static checks").

Every piece of the line has already been read by clauses.py. The functions here look at
the pieces TOGETHER and answer questions such as: the line says `rest` for a size, so
does it also say enough for that size to have a value? Is there a face to leave a `gap`
from? Each function returns a list of Rejections (empty when the rule holds).

`add_notes` writes warnings that do not reject.
"""

from __future__ import annotations

from forge.plan import grammar as g
from forge.plan.model import (
    OUT_OF_PLACE,
    REST_NOT_DEFINED,
    UNKNOWN_ALIGNMENT,
    UNKNOWN_ORIENTATION,
    UNKNOWN_PLACEMENT,
    Line,
    Rejection,
)

_GROUND = (g.ON_GROUND, g.ABOVE_GROUND)


def _kind(line: Line) -> str | None:
    return line.placement.kind if line.placement else None


def _plain(face: str) -> str:
    return face.removeprefix("inner ")


# --- turning: lying, pointing, flat ---------------------------------------------------------

def check_turning(line: Line, lying: str | None, pointing: str | None) -> list[Rejection]:
    """`lying`, `pointing` and `flat` each belong to certain shapes (section 4)."""
    problems = []
    if lying and lying != "standing" and line.shape not in g.LYING_SHAPES:
        problems.append(Rejection(UNKNOWN_ORIENTATION, lying,
                                  f"a {line.shape or 'group'} cannot lie; see section 4"))
    if pointing and line.shape not in g.POINTING_SHAPES:
        problems.append(Rejection(UNKNOWN_ORIENTATION, f"pointing {pointing}",
                                  f"a {line.shape or 'group'} cannot point; see section 4"))
    if line.flat:
        written = f"flat {line.flat}"
        if line.shape != "wedge":
            problems.append(Rejection(UNKNOWN_ORIENTATION, written, "only a wedge has `flat`"))
        elif g.DIRECTION_OF_FACE[line.flat] == g.DIRECTION_OF_FACE[g.FACE_POINTED_AT[line.pointing]]:
            problems.append(Rejection(UNKNOWN_ORIENTATION, written,
                                      "the flat face must be beside the way the wedge points"))
    return problems


# --- `rest` ---------------------------------------------------------------------------------

def direction_of_slot(line: Line, slot: str) -> str:
    """Which way a size runs once the part is turned (section 4: sizes are written standing)."""
    if line.shape == "wedge":
        return slot                      # a wedge's sizes are its frame as it sits
    if line.shape == "box":
        swapped = {"lying along length": "length", "lying along depth": "depth"}.get(
            line.orientation)
        if swapped is None:
            return slot
        return {"height": swapped, swapped: "height"}.get(slot, slot)
    # Every other shape has one size that may be `rest`: the one along its axis.
    if line.orientation != "standing":
        return line.orientation.rsplit(" ", 1)[1]
    return g.DIRECTION_OF_FACE[g.FACE_POINTED_AT[line.pointing]]


def fixed_faces(line: Line) -> set[str]:
    """The faces of the new part whose position the line states."""
    faces: set[str] = set()
    kind = _kind(line)
    if kind in g.FACE_PLACEMENTS:
        faces.add(g.FACE_PLACEMENTS[kind])
    if kind in (g.ON_GROUND, "inside"):
        faces.add("bottom")
    for alignment in line.alignments:
        if alignment.kind == "down to ground":
            faces.add("bottom")
        elif alignment.kind in ("flush", "face offset", "top at height", "bottom at height"):
            faces.add(alignment.face)
    return faces


def directions_rest_can_fill(line: Line) -> set[str]:
    """The directions in which the line fixes both ends, so a `rest` there has a value.

    "any" stands for the direction of `between`, which only geometry can tell.
    """
    faces = fixed_faces(line)
    directions = {d for d, (low, high) in g.FACE_PAIRS.items() if low in faces and high in faces}
    kind = _kind(line)
    if kind == g.BETWEEN:
        directions.add("any")
    if kind == "inside":                       # wall to wall
        directions |= {"length", "depth"}
    if line.option("across") and kind in g.FACE_PLACEMENTS:
        touching = g.DIRECTION_OF_FACE[g.FACE_PLACEMENTS[kind]]
        directions |= set(g.DIRECTIONS) - {touching}       # the frame of the whole set
    return directions


def check_rest(line: Line) -> list[Rejection]:
    rests = [size for size in line.sizes if size.source == "rest"]
    kind = _kind(line)
    if kind == g.SPANS and (len(rests) != 1 or line.shape not in g.SPANNING_SHAPES):
        shapes = ", ".join(sorted(g.SPANNING_SHAPES))
        return [Rejection(REST_NOT_DEFINED, "spans",
                          f"a spanning part is one of {shapes} with exactly one size "
                          "written `rest`: the size that runs from end to end")]
    if not rests:
        return []
    allowed = g.REST_SLOTS.get(line.shape, ())
    for size in rests:
        if size.slot not in allowed:
            may = " or ".join(allowed) or "no size"
            return [Rejection(REST_NOT_DEFINED, f"rest ({size.slot})",
                              f"on a {line.shape} only {may} may be `rest`")]
    if kind == g.SPANS:
        return []
    can_fill = directions_rest_can_fill(line)
    wide = kind == "inside" or line.option("across")       # two free directions at once
    if len(rests) > (2 if wide else 1):
        return [Rejection(REST_NOT_DEFINED, "rest", "too many sizes are `rest` on this line")]
    if not can_fill:
        return [Rejection(REST_NOT_DEFINED, "rest",
                          "rest needs both ends fixed: `between`, `spans`, `inside`, "
                          "`across`, or a placement and alignments that state both end "
                          "faces (`down to ground` states the bottom)")]
    problems = []
    for size in rests:
        direction = direction_of_slot(line, size.slot)
        if direction not in can_fill and "any" not in can_fill:
            fixed = ", ".join(sorted(can_fill))
            problems.append(Rejection(
                REST_NOT_DEFINED, f"rest ({size.slot})",
                f"this size runs along {direction}; the line fixes both ends only along {fixed}"))
    return problems


# --- the placement and its options ----------------------------------------------------------

def check_placement(line: Line) -> list[Rejection]:
    problems = []
    kind = _kind(line)
    at_height = any(a.kind in ("top at height", "bottom at height") for a in line.alignments)
    if kind == g.ABOVE_GROUND and not at_height:
        problems.append(Rejection(UNKNOWN_PLACEMENT, g.ABOVE_GROUND,
                                  "`above ground` needs `top at height H` or `bottom at height H`"))
    if kind == g.AROUND and line.shape != "tube":
        problems.append(Rejection(OUT_OF_PLACE, f"around {line.placement.parts[0]}",
                                  "only a tube can be placed around a part"))
    if kind == g.SPANS and (line.orientation != "standing" or line.repetition
                            or line.alignments or line.options):
        problems.append(Rejection(OUT_OF_PLACE, "spans",
                                  "a spanning part takes no turning, option, alignment or "
                                  "repetition: its two ends say everything"))
    gap, sunk, across = line.option("gap"), line.option("sunk"), line.option("across")
    target = line.placement.parts[0] if line.placement and line.placement.parts else None
    for option in (gap, sunk, across):
        if option and kind not in g.FACE_PLACEMENTS:
            problems.append(Rejection(OUT_OF_PLACE, option.kind,
                                      f"`{option.kind}` follows on top of, under, in front "
                                      "of, behind, left of or right of"))
        elif option and option.part is not None and option.part != target:
            problems.append(Rejection(OUT_OF_PLACE, f"{option.kind} ... {option.part}",
                                      f"`{option.kind}` names the part the placement names"))
    if gap and sunk:
        problems.append(Rejection(OUT_OF_PLACE, "sunk", "a part cannot have a gap and be sunk"))
    if len(line.options) != len({option.kind for option in line.options}):
        problems.append(Rejection(OUT_OF_PLACE, line.options[-1].kind, "written twice"))
    return problems


# --- alignments -----------------------------------------------------------------------------

def check_alignments(line: Line) -> list[Rejection]:
    problems = []
    flush = [a for a in line.alignments if a.kind == "flush"]
    for alignment in line.alignments:
        if alignment.kind in ("flush", "face offset"):
            mine = g.DIRECTION_OF_FACE[alignment.face]
            theirs = g.DIRECTION_OF_FACE[_plain(alignment.other_face)]
            written = f"{alignment.face} ... {alignment.other_face}"
            if mine != theirs:
                problems.append(Rejection(UNKNOWN_ALIGNMENT, written,
                                          f"a {alignment.face} face cannot line up with a "
                                          f"{alignment.other_face} face: different directions"))
            elif alignment.relation in ("above", "below") and mine != "height":
                problems.append(Rejection(UNKNOWN_ALIGNMENT, f"{alignment.relation} ({written})",
                                          "above and below are for top and bottom faces; "
                                          "use inside or beyond"))
        if alignment.kind == "inset":
            corners = line.repetition and line.repetition.kind in ("each corner", "corners",
                                                                   "corner")
            if not flush and not corners:
                problems.append(Rejection(OUT_OF_PLACE, "inset",
                                          "inset moves a part in from a face it is flush with "
                                          "or a corner it sits in; this line has neither"))
    return problems


# --- repetition -----------------------------------------------------------------------------

def check_repetition(line: Line) -> list[Rejection]:
    repetition, kind = line.repetition, _kind(line)
    if repetition is None or kind is None:
        return []
    problems = []
    if repetition.kind == "mirrored" and kind in (*_GROUND, g.AROUND):
        problems.append(Rejection(OUT_OF_PLACE, f"mirrored {repetition.direction}",
                                  f"with `{kind}` there is no part to mirror across"))
    if repetition.kind in ("evenly spaced", "spread") and kind in g.FACE_PLACEMENTS:
        touching = g.DIRECTION_OF_FACE[g.FACE_PLACEMENTS[kind]]
        if repetition.direction == touching:
            problems.append(Rejection(OUT_OF_PLACE, f"along {repetition.direction}",
                                      f"`{kind}` stacks parts along {touching}; copies are "
                                      "spaced along one of the other two directions"))
    if repetition.kind == "around":
        side = kind in ("in front of", "behind", "left of", "right of")
        if side and repetition.diameter:
            problems.append(Rejection(OUT_OF_PLACE, "on circle",
                                      f"with `{kind}` the copies touch the part, so no circle "
                                      "is written"))
        if not side and not repetition.diameter:
            problems.append(Rejection(OUT_OF_PLACE, "around",
                                      "write `on circle D`: the diameter the copies sit on"))
    return problems


# --- feature lines --------------------------------------------------------------------------

def check_feature(line: Line) -> list[Rejection]:
    feature = line.feature
    problems = []
    if feature.name in g.TOP_ONLY_FEATURES and line.face != "top":
        problems.append(Rejection(OUT_OF_PLACE, feature.name,
                                  f"{feature.name} is written `on {line.target}:` with no face"))
    directions = []
    for edge in feature.edges:
        direction = g.DIRECTION_OF_FACE[edge.face]
        if edge.part != line.target:
            problems.append(Rejection(OUT_OF_PLACE, f"from {edge.part}",
                                      "a feature is measured from the edges of its own part"))
        elif direction == g.DIRECTION_OF_FACE[_plain(line.face)] or direction in directions:
            problems.append(Rejection(OUT_OF_PLACE, f"from {edge.part}'s {edge.face}",
                                      f"the {line.face} face has no {edge.face} edge, or two "
                                      "distances run the same way"))
        directions.append(direction)
    return problems


# --- warnings -------------------------------------------------------------------------------

def add_notes(line: Line) -> None:
    """Warnings: the line is in the language, but a person should look at it."""
    kinds = {a.kind: a for a in line.alignments}
    height = next((size for size in line.sizes if direction_of_slot(line, size.slot) == "height"
                   and size.slot in g.REST_SLOTS.get(line.shape, ())), None)
    numeric_height = height is not None and height.source == "number"
    if _kind(line) == g.ON_GROUND:
        bottom, top = kinds.get("bottom at height"), kinds.get("top at height")
        if bottom and bottom.amount.value != 0:
            line.notes.append("`on ground` puts the bottom at height 0, but the line also "
                              "says `bottom at height`; use `above ground` for a raised part")
        # Plain arithmetic, no geometry: bottom at 0 plus the height gives the top.
        if top and numeric_height and top.amount.value not in (None, height.value):
            line.notes.append(
                f"`on ground` with height {height.value:g} puts the top at {height.value:g}, "
                f"but the line says top at height {top.amount.value:g}; use `above ground` "
                "for a raised part")
    if "down to ground" in kinds and numeric_height:
        line.notes.append("`down to ground` stretches the part to the ground, so its height "
                          "is written `rest`; a number is written instead")
