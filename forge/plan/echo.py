"""Print a parsed line back in one fixed wording, so a person can see how it was read.

The echo of an accepted line is itself a valid line: parsing the echo gives the same
reading again (tests/test_plan_language.py checks this for every form in the tables).
"""

from __future__ import annotations

from forge.plan import grammar as g
from forge.plan.model import Alignment, Feature, Line, Option, Placement, Repetition, Size


def _number(value: float) -> str:
    return f"{value:g}"          # 420.0 -> "420", 12.5 -> "12.5"


def echo_size(size: Size) -> str:
    if size.source == "number":
        return _number(size.value)
    if size.source == "same as":
        return f"same as {size.part}'s {size.dimension}"
    if size.source == "share":
        of = "" if size.share == "double" else " of"
        return f"{size.share}{of} {size.part}'s {size.dimension}"
    return size.source           # "rest" or "default"


def echo_placement(placement: Placement) -> str:
    if placement.kind == g.BETWEEN:
        return "between " + " and ".join(placement.parts)
    if placement.kind == g.SPANS:
        (first, second), (start, end) = placement.parts, placement.anchors
        return f"spans from {first}'s {start} to {second}'s {end}"
    return " ".join([placement.kind, *placement.parts])


def echo_option(option: Option) -> str:
    if option.kind == "gap":
        return f"gap {echo_size(option.amount)}"
    if option.kind == "sunk":
        return f"sunk {echo_size(option.amount)} into {option.part}"
    return f"across {option.part}"


def echo_alignment(a: Alignment) -> str:
    if a.kind == "flush":
        # The short form says the face once: both parts' same-named faces.
        mine = "" if a.other_face.removeprefix("inner ") == a.face else f"{a.face} "
        return f"{mine}flush with {a.part}'s {a.other_face}"
    if a.kind == "face offset":
        return f"{a.face} {echo_size(a.amount)} {a.relation} {a.part}'s {a.other_face}"
    if a.kind == "inset":
        return f"inset {echo_size(a.amount)}"
    if a.kind == "offset":
        return f"offset {echo_size(a.amount)} to the {a.face}"
    if a.kind in ("top at height", "bottom at height"):
        return f"{a.kind} {echo_size(a.amount)}"
    if a.kind == "same axis":
        return f"on the same axis as {a.part}"
    return a.kind                # "down to ground"


def echo_repetition(r: Repetition) -> str:
    if r.kind == "each corner":
        return f"at each corner of {r.part}"
    if r.kind == "corners":
        return f"at the {r.side} corners of {r.part}"
    if r.kind == "corner":
        return f"at the {r.side} corner of {r.part}"
    if r.kind in ("evenly spaced", "spread"):
        return f"{r.count} {r.kind} along {r.direction}"
    if r.kind == "grid":
        return f"grid {r.count} by {r.count2}"
    if r.kind == "around":
        circle = f" on circle {echo_size(r.diameter)}" if r.diameter else ""
        return f"{r.count} around {r.part}{circle}{' facing outward' if r.outward else ''}"
    return f"mirrored {r.direction}"


def echo_feature(feature: Feature) -> str:
    pieces = [f"{slot} {_number(value)}" for slot, value in feature.slots.items()]
    pieces += [f"{echo_size(e.amount)} from {e.part}'s {e.face}" for e in feature.edges]
    if feature.at:
        pieces.append(f"at ({_number(feature.at[0])}, {_number(feature.at[1])})")
    if feature.open_at:
        pieces.append(f"open at {feature.open_at}")
    return " ".join([feature.name, ", ".join(pieces)]).strip()


def _echo_part(line: Line) -> str:
    if line.group:
        head = line.group
    else:
        profile = [line.profile.upper() if len(line.profile) == 1 else line.profile] \
            if line.profile else []
        sizes = " by ".join(echo_size(size) for size in line.sizes)
        head = " ".join([line.shape, *profile, sizes]).strip()
    if line.orientation != "standing":
        head += f" {line.orientation}"
    if line.pointing != "up" or line.flat:
        head += f" pointing {line.pointing}" + (f" flat {line.flat}" if line.flat else "")
    pieces = [head]
    if line.placement:
        pieces.append(echo_placement(line.placement))
    # Canonical order: the placement's options, then the alignments as written.
    pieces += [echo_option(option) for option in line.options]
    pieces += [echo_alignment(a) for a in line.alignments]
    if line.repetition:
        pieces.append(echo_repetition(line.repetition))
    return f"{line.name}: " + ", ".join(pieces)


def echo_line(line: Line) -> str:
    """The canonical wording of an accepted line; for a rejected line, why it was refused."""
    if not line.accepted:
        reasons = "; ".join(f'{r.reason} "{r.fragment}"' for r in line.rejections)
        return f"not understood ({reasons})"
    if line.kind == "part":
        return _echo_part(line)
    if line.kind == "feature":
        face = "" if line.face == "top" else f"'s {line.face}"
        return f"on {line.target}{face}: {echo_feature(line.feature)}"
    if line.kind == "group":
        return f"group {line.name}"
    if line.kind == "undo to":
        return f"undo to {line.name}"
    return line.kind             # "end", "undo", "done"
