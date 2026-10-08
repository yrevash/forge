"""Read a feature line: `on X's FACE: FEATURE SLOT N, SLOT N, POSITION` (section 9).

    on plate: hole diameter 8
    on plate's front: pocket length 40, width 20, depth 5, 30 from plate's left
    on body: hollowed out wall 3, open at top
    on plate: hole diameter 6, at (20, -15)

The slot words are the slot names of forge/system1/steps.py with a space for the
underscore (and `wall` for wall_thickness). A slot that is not written is left to a
default. Where the feature sits on the face is one of three forms: nothing (centred),
distances from the face's edges, or `at (x, y)` measured from the middle of the face.
"""

from __future__ import annotations

import re

from forge.plan import grammar as g
from forge.plan.clauses import full, read_amount
from forge.plan.model import (
    OUT_OF_PLACE,
    UNKNOWN_FEATURE,
    UNKNOWN_SIZE_FORM,
    EdgeDistance,
    Feature,
    Reject,
)

_SIGNED = r"(-?\d+(?:\.\d+)?)(?: ?mm)?"
_AT = re.compile(rf"(?:^|, ?)at \({_SIGNED}, ?{_SIGNED}\)$")


def read_head(head: str) -> tuple[str, str]:
    """'plate' -> (plate, top); "plate's front" -> (plate, front); also "inner left"."""
    if m := full(rf"{g.OWNER} {g.OTHER_FACE}", head):
        return m.group(1), m.group(2)
    return head, "top"


def read_feature(text: str) -> Feature:
    # Longest name first, so "blind hole" is not read as an unknown feature before "hole".
    for name in sorted(g.FEATURES, key=len, reverse=True):
        if text == name or text.startswith(name + " "):
            break
    else:
        raise Reject(UNKNOWN_FEATURE, re.split(r" \d", text)[0].split(",")[0])
    step_kind, slot_words, may_be_positioned = g.FEATURES[name]
    feature = Feature(name, step_kind)
    remainder = text[len(name):].strip()

    # `at (x, y)` holds a comma of its own, so it is taken off before the pieces are cut.
    if at := _AT.search(remainder):
        feature.position, feature.at = "at", (float(at.group(1)), float(at.group(2)))
        remainder = remainder[: at.start()]

    written = ["at"] if feature.at else []      # the position forms written so far
    for piece in (p.strip() for p in remainder.split(",")) if remainder else ():
        if piece == "centred":
            _one_position(written, piece, "centred")
        elif m := full(rf"open at {g.FACE}", piece):
            if name != g.HOLLOWING_FEATURE or feature.open_at:
                raise Reject(OUT_OF_PLACE, piece, "only `hollowed out` has one open face")
            feature.open_at = m.group(1)
        elif m := full(rf"(.+) from {g.OWNER} {g.FACE}", piece):
            _one_position(written, piece, "edges")
            feature.position = "edges"
            feature.edges.append(EdgeDistance(read_amount(m.group(1)), m.group(2), m.group(3)))
        else:
            _read_slot(feature, piece, slot_words)
    if feature.position != "centred" and not may_be_positioned:
        raise Reject(OUT_OF_PLACE, name, f"{name} cannot be moved about on its face")
    return feature


def _one_position(written: list[str], piece: str, form: str) -> None:
    """A feature says where it is in ONE way; two edge distances count as one way."""
    mixed = bool(written) and (form != "edges" or written[0] != "edges")
    if mixed or len(written) == 2:
        raise Reject(OUT_OF_PLACE, piece, "the position is written once: centred, one or two "
                                          "edge distances, or at (x, y)")
    written.append(form)


def _read_slot(feature: Feature, piece: str, slot_words: tuple[str, ...]) -> None:
    match = full(rf"(.+) {_SIGNED}", piece)
    if not match:
        raise Reject(UNKNOWN_SIZE_FORM, piece, "a feature slot is written SLOT NUMBER")
    word, value = match.group(1), float(match.group(2))
    if word not in slot_words or word in feature.slots:
        known = ", ".join(slot_words)
        raise Reject(UNKNOWN_FEATURE, piece, f"{feature.name} has these slots, once each: {known}")
    if word in g.FEATURE_COUNT_SLOTS:
        fine = value == int(value) and value >= 1
    else:
        fine = value > 0 or word in g.FEATURE_ANY_NUMBER_SLOTS
    if not fine:
        raise Reject(UNKNOWN_SIZE_FORM, piece, "this slot needs a number greater than zero")
    feature.slots[word] = value
