"""Read ONE piece of a line: a size, a placement, an option, an alignment, a repetition.

Each reader is given text that has already been through grammar.clean (lower case,
single spaces, straight apostrophes). It returns the parsed object, returns None when
the text is simply not that kind of piece, or raises Reject with a reason.

Part names are returned exactly as written. Whether a name exists is the parser's job
(parser.py), because that depends on the lines before this one. Whether a piece makes
sense on its line (a gap needs a face to leave it from) is the job of checks.py.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from forge.plan import grammar as g
from forge.plan.model import (
    UNKNOWN_PLACEMENT,
    UNKNOWN_SIZE_FORM,
    Alignment,
    Option,
    Placement,
    Reject,
    Repetition,
    Size,
)

_DIMENSION = "(" + "|".join(g.DIMENSIONS) + ")"
_DIRECTION = "(" + "|".join(g.DIRECTIONS) + ")"
_RELATION = "(" + "|".join(g.FACE_RELATIONS) + ")"
# A place on a part, as text: "top", "top-front edge", "top-front-left corner".
_ANCHOR = r"([a-z]+(?:[- ][a-z]+)*?(?: edge| corner)?)"


def full(pattern: str, text: str) -> re.Match[str] | None:
    return re.fullmatch(pattern, text)


def read_number(text: str) -> float | None:
    """'40', '12.5', '40mm', '40 mm' -> the number; anything else -> None."""
    match = full(g.NUM, text)
    return float(match.group(1)) if match else None


# --- section 4: one size --------------------------------------------------------------------

def _read_reference(text: str, slot: str) -> Size | None:
    """`same as X's length`, `half of X's depth`, `double X's height`."""
    if m := full(rf"same as {g.OWNER} {_DIMENSION}", text):
        return Size(slot, "same as", part=m.group(1), dimension=m.group(2))
    m = (full(rf"(half|third|quarter) of {g.OWNER} {_DIMENSION}", text)
         or full(rf"(double) {g.OWNER} {_DIMENSION}", text))
    # "double of X's length" is not a form: the word after `double` is the part's name.
    if m and not m.group(2).startswith("of "):
        return Size(slot, "share", share=m.group(1), part=m.group(2), dimension=m.group(3))
    return None


def read_size(text: str, slot: str) -> Size:
    """One size of a shape, in one of the five forms of the section 4 table."""
    if slot in g.COUNT_SLOTS:       # a prism's number of sides: a whole number, nothing else
        if full(g.INT, text) and int(text) >= 3:
            return Size(slot, "number", value=float(text))
        raise Reject(UNKNOWN_SIZE_FORM, text, f"{slot} must be a whole number, 3 or more")
    if text in ("rest", "default"):
        return Size(slot, text)
    number = read_number(text)
    if number is not None:
        if number == 0 and slot not in g.ZERO_ALLOWED_SLOTS:
            raise Reject(UNKNOWN_SIZE_FORM, text, "a size must be greater than zero")
        return Size(slot, "number", value=number)
    reference = _read_reference(text, slot)
    if reference is None:
        raise Reject(UNKNOWN_SIZE_FORM, text)
    return reference


def read_amount(text: str) -> Size:
    """A distance that is not a shape's size: a number (zero allowed) or a size reference."""
    number = read_number(text)
    if number is not None:
        return Size("amount", "number", value=number)
    reference = _read_reference(text, "amount")
    if reference is None:
        raise Reject(UNKNOWN_SIZE_FORM, text, "write a number, or a size of an earlier part")
    return reference


def looks_like_a_size(text: str) -> bool:
    """True for a piece that is a bare size: the planner put a comma between sizes."""
    if text in ("rest", "default"):
        return True
    try:
        read_amount(text)
    except Reject:
        return False
    return True


# --- places on a part -----------------------------------------------------------------------

def read_anchor(text: str) -> str | None:
    """A face ("top"), an edge ("top-front edge") or a corner ("top-front-left corner")."""
    kind = {" edge": 2, " corner": 3}
    for ending, count in kind.items():
        if text.endswith(ending):
            words = g.face_words(text[: -len(ending)])
            return "-".join(words) + ending if words and len(words) == count else None
    return text if text in g.FACES else None


# --- section 5: placement -------------------------------------------------------------------

def read_placement(text: str, knows: Callable[[str], bool]) -> Placement | None:
    """`knows(name)` is only used to cut "between A and B" when a name itself contains "and"."""
    if text in (g.ON_GROUND, g.ABOVE_GROUND):
        return Placement(text)
    for words in g.ONE_PART_PLACEMENTS:
        if text.startswith(words + " "):
            return Placement(words, [text[len(words) + 1:]])
    if text.startswith(g.BETWEEN + " "):
        pair = text[len(g.BETWEEN) + 1:]
        cuts = [m.start() for m in re.finditer(" and ", pair)]
        halves = [(pair[:cut], pair[cut + 5:]) for cut in cuts]
        for first, second in halves:
            if knows(first) and knows(second):
                return Placement(g.BETWEEN, [first, second])
        # No "and", or a name that itself contains "and": one name, a set of two copies.
        if knows(pair) or not halves:
            return Placement(g.BETWEEN, [pair])
        return Placement(g.BETWEEN, list(halves[0]))
    if m := full(rf"spans from {g.OWNER} {_ANCHOR} to {g.OWNER} {_ANCHOR}", text):
        anchors = [read_anchor(m.group(2)), read_anchor(m.group(4))]
        if None in anchors:
            raise Reject(UNKNOWN_PLACEMENT, text,
                         "an end is a face, an edge (top-front edge) or a corner "
                         "(top-front-left corner)")
        return Placement(g.SPANS, [m.group(1), m.group(3)], anchors)
    return None


def read_option(text: str) -> Option | None:
    """The three pieces that change how the placement touches."""
    if m := full(r"gap (.+)", text):
        return Option("gap", amount=read_amount(m.group(1)))
    if m := full(r"sunk (.+?) into (.+)", text):
        return Option("sunk", amount=read_amount(m.group(1)), part=m.group(2))
    if m := full(r"across (.+)", text):
        return Option("across", part=m.group(1))
    return None


# --- section 6: alignment -------------------------------------------------------------------

def _same_named(face: str) -> str:
    """The new part's face that `flush with X's inner left` lines up: left."""
    return face.removeprefix("inner ")


def read_alignment(text: str) -> Alignment | None:
    if text == "down to ground":
        return Alignment("down to ground")
    if m := full(rf"flush with {g.OWNER} {g.OTHER_FACE}", text):
        return Alignment("flush", face=_same_named(m.group(2)), part=m.group(1),
                         other_face=m.group(2))
    if m := full(rf"{g.FACE} flush with {g.OWNER} {g.OTHER_FACE}", text):
        return Alignment("flush", face=m.group(1), part=m.group(2), other_face=m.group(3))
    if m := full(rf"{g.FACE} (.+?) {_RELATION} {g.OWNER} {g.OTHER_FACE}", text):
        return Alignment("face offset", face=m.group(1), amount=read_amount(m.group(2)),
                         relation=m.group(3), part=m.group(4), other_face=m.group(5))
    if m := full(r"inset (.+)", text):
        return Alignment("inset", amount=read_amount(m.group(1)))
    if m := full(rf"offset (.+) to the {g.FACE}", text):
        return Alignment("offset", amount=read_amount(m.group(1)), face=m.group(2))
    if m := full(r"(top|bottom) at height (.+)", text):
        return Alignment(f"{m.group(1)} at height", face=m.group(1),
                         amount=read_amount(m.group(2)))
    if m := full(r"on the same axis as (.+)", text):
        return Alignment("same axis", part=m.group(1))
    return None


# --- section 7: repetition ------------------------------------------------------------------

def read_repetition(text: str) -> Repetition | None:
    repetition = _read_repetition(text)
    if repetition is None:
        return None
    counts = (repetition.count, repetition.count2)
    # A count of zero copies is not a repetition.
    return repetition if all(count is None or count >= 1 for count in counts) else None


def _read_repetition(text: str) -> Repetition | None:
    if m := full(r"at each corner of (.+)", text):
        return Repetition("each corner", part=m.group(1))
    sides = "(" + "|".join(g.CORNER_SIDES) + ")"
    if m := full(rf"at the {sides} corners of (.+)", text):
        return Repetition("corners", side=m.group(1), part=m.group(2))
    if m := full(r"at the ([a-z]+[- ][a-z]+) corner of (.+)", text):
        words = g.face_words(m.group(1))
        if words and "top" not in words and "bottom" not in words:
            return Repetition("corner", side="-".join(words), part=m.group(2))
    if m := full(rf"{g.INT} (evenly spaced|spread) along {_DIRECTION}", text):
        return Repetition(m.group(2), count=int(m.group(1)), direction=m.group(3))
    if m := full(rf"grid {g.INT} ?{g.BY} ?{g.INT}", text):
        return Repetition("grid", count=int(m.group(1)), count2=int(m.group(2)))
    if m := full(rf"{g.INT} around (.+?)(?: on circle (.+?))?( facing outward)?", text):
        circle = read_amount(m.group(3)) if m.group(3) else None
        return Repetition("around", count=int(m.group(1)), part=m.group(2), diameter=circle,
                          outward=bool(m.group(4)))
    if m := full(r"mirrored (left|front)[- ](right|back)", text):
        direction = f"{m.group(1)}-{m.group(2)}"
        if direction in g.MIRRORS:
            return Repetition("mirrored", direction=direction)
    return None


# --- a piece that matched nothing: which kind was the planner trying to write? ----------------

def guess_kind(text: str) -> str | None:
    """'placement', 'alignment', 'repetition', 'orientation' or None, by the first word or two.

    Only the opening words of the forms in sections 4 to 7 are used. This decides which
    "unknown ..." reason a piece gets; it never makes a piece accepted.
    """
    words = text.split()
    first = words[0] if words else ""
    second = words[1] if len(words) > 1 else ""
    if text.startswith("on the same axis"):
        return "alignment"
    if first in ("standing", "lying", "pointing", "flat"):
        return "orientation"
    if first in ("left", "right"):
        return "placement" if second == "of" else "alignment"
    if first in ("on", "above", "under", "in", "behind", "inside", "between", "through",
                 "around", "spans", "gap", "sunk", "across"):
        return "placement"
    if first in ("flush", "inset", "offset", "down", *g.FACES):
        return "alignment"
    if first in ("at", "grid", "mirrored") or first.isdigit():
        return "repetition"
    return None
