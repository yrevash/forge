"""What one stored plan of data/plans looks like TO THE EXECUTOR, read off its stored parts.

    facts = plan_facts(record, where)       # record: one line of data/plans/<source>/<split>/

Forge-S1 is object-blind: it never learns that a plan is a "stool" or which words placed
a part. It sees bodies: a shape, sizes, a position, a turn, and features on a side. So
everything the session splits are decided on is computed here from exactly that:

    bodies         how many bodies the plan makes
    shapes         the shape words ("box", "bar l", "tapered box" ...)
    cells          the combinations the executor sees (see CELLS below)
    geometry       a hash of the parts themselves (shape, sizes, frame, turn, features)
                   WITHOUT names or lines: two plans with the same hash ask the executor
                   for the same commands, whatever they are called
    late_feature   a feature written after a later part was made, so the executor must go
                   back with `activate_body`
    stale / overlap   the check for the fault of 6 Oct 2026 (see `check_overlaps`)

CELLS. Three families, each a pair of things the executor reads in one body:
    st:<shape>|<turn>       a shape and how it is turned (yaw, pitch, roll of `turn_body`)
    fs:<feature>|<side>     a feature kind and the side it is built on (`select_side`)
    sf:<shape>|<feature>    a shape and a feature kind on it
A session split that holds out a CELL holds out something the executor can see; the
plan-language combinations of data/plans (`behind` with a prism as target) leave no
trace in a body and are invisible to it.

Nothing here reads a session. Nothing here is model input.
"""

from __future__ import annotations

import hashlib
import re

from forge.freecad_multi.recipes import MIRROR, SIDE_OF, split, yaw_pitch_roll
from forge.resolve import contact
from forge.resolve import space as sp
from forge.resolve.bodies import Body

OVERLAP_MARGIN = 1e-4       # mm; stored numbers are rounded to 1e-6, so ask for a clear overlap
GROUP_LINE = re.compile(r"^\s*group\s")


def shape_word(part: dict) -> str:
    return f"bar {part['profile']}" if part["shape"] == "bar" else part["shape"]


def _matrix(part: dict) -> sp.Mat:
    return tuple(tuple(float(v) for v in row) for row in part["turn"])


def turn_word(part: dict) -> str:
    """The turn the executor is asked for, as `turn_body`'s three angles; "odd" when the
    part is not turned by quarter turns."""
    turn, _ = split(_matrix(part))
    if not sp.is_square(turn):
        return "odd"
    return ",".join(f"{angle:g}" for angle in yaw_pitch_roll(turn))


def side_word(part: dict, feature: dict) -> str:
    """The side a feature is built on, as the executor is told (`select_side`): the face as
    the body sits, or for a hollowing the face left open. A body turned by an odd angle
    gets its features before it is turned, so its sides are its own (recipes.py). "none"
    for a face that is square to nothing, or a hollowing with no open face: no command
    builds those (recipes.Unsupported)."""
    turn, mirrored = split(_matrix(part))
    sits = _matrix(part) if sp.is_square(turn) else (MIRROR if mirrored else sp.IDENTITY)
    normal = feature.get("open") if feature["kind"] == "shell" else feature["normal"]
    if normal is None:
        return "none"
    direction = sp.apply(sits, tuple(normal))
    axis = max(range(3), key=lambda k: abs(direction[k]))
    if abs(abs(direction[axis]) - 1.0) > 1e-6:
        return "none"
    return SIDE_OF[(axis, 1 if direction[axis] > 0 else -1)]


def cells_of(parts: list[dict]) -> set[str]:
    cells = set()
    for part in parts:
        shape = shape_word(part)
        cells.add(f"st:{shape}|{turn_word(part)}")
        for feature in part.get("features", []):
            cells.add(f"fs:{feature['kind']}|{side_word(part, feature)}")
            cells.add(f"sf:{shape}|{feature['kind']}")
    return cells


def _rounded(values: list[float], digits: int = 3) -> tuple:
    return tuple(round(float(v), digits) + 0.0 for v in values)      # + 0.0: -0.0 becomes 0.0


def part_key(part: dict) -> tuple:
    """One part as the executor is asked to make it: no name, no line, no owner."""
    features = tuple(sorted(
        (f["kind"], tuple(sorted((slot, round(v, 3)) for slot, v in f["slots"].items())),
         _rounded(f["origin"]), _rounded(f["normal"]), _rounded(f["first"]),
         tuple(_rounded(spot) for spot in f["spots"]), _rounded(f.get("open", [])))
        for f in part.get("features", [])))
    return (shape_word(part), tuple(sorted((slot, round(v, 3)) for slot, v in part["sizes"].items())),
            _rounded(part["low"]), _rounded(part["high"]),
            tuple(_rounded(row) for row in part["turn"]),
            part.get("pointing"), part.get("flat"), features)


def geometry_key(parts: list[dict]) -> str:
    """A hash of the plan's parts as a SET of bodies (the order they are made in left out,
    which is the stricter choice: the same bodies in another order count as the same)."""
    listed = sorted(repr(part_key(part)) for part in parts)
    return hashlib.sha256("\n".join(listed).encode()).hexdigest()[:20]


# --- the overlap check ---------------------------------------------------------------------------

def stored_bodies(parts: list[dict]) -> list[Body]:
    """The stored parts as resolver Bodies, features left out (enough for boxes and cylinders)."""
    return [Body(part["name"], part["owner"], part["line"], part["shape"], part.get("profile"),
                 {slot: float(v) for slot, v in part["sizes"].items()}, _matrix(part),
                 tuple(float(v) for v in part["centre"]), part.get("pointing", "up"),
                 part.get("flat"), [], frozenset(part.get("may_overlap", [])))
            for part in parts]


def undeclared_overlaps(bodies: list[Body], margin: float = 0.0) -> list[tuple[str, str]]:
    """Every pair of boxes and cylinders that shares volume without `sunk` (or a span's
    end) allowing it. Arithmetic only (forge/resolve/contact.py); a pair with another
    shape in it is not judged here (FreeCAD judges it at the end of every session)."""
    found = []
    for i, a in enumerate(bodies):
        for b in bodies[i + 1:]:
            if b.name in a.may_overlap or a.name in b.may_overlap:
                continue
            if a.kind == "other" or b.kind == "other":
                continue
            if min(a.frame.depth_into(b.frame)) <= margin:
                continue                    # the frames only touch, or miss each other
            shrunk = a if not margin else _shrunk(a, margin)
            if contact.relation(shrunk, b) == contact.OVERLAP:
                found.append((a.name, b.name))
    return found


def _shrunk(body: Body, margin: float) -> Body:
    """The same body, every size smaller by `margin`: an overlap that survives is real and
    not the last digit of a stored number."""
    sizes = {slot: value - margin for slot, value in body.sizes.items()}
    return Body(body.name, body.owner, body.line, body.shape, body.profile, sizes, body.matrix,
                body.centre, body.pointing, body.flat, [], body.may_overlap)


def has_group(lines: list[str]) -> bool:
    return any(GROUP_LINE.match(line) for line in lines)


def late_feature(parts: list[dict]) -> bool:
    """Is some feature built on a body that is not the newest one at that moment? Then the
    executor has to come back to it (`activate_body`): a feature written after a later
    part, or a feature on every copy of a set."""
    order = sorted(range(len(parts)), key=lambda k: (parts[k]["line"], k))
    for k in order:
        for feature in parts[k].get("features", []):
            if feature["line"] <= parts[k]["line"]:
                continue                    # it came with the part (a copy of a group)
            made_before = [j for j in order if parts[j]["line"] < feature["line"]]
            if made_before[-1] != k:
                return True
    return False


def plan_facts(record: dict, where: str) -> dict:
    """The index entry of one complete plan (see the top of this file)."""
    parts = record["parts"]
    provenance = record.get("provenance", {})
    features = sorted({f"{f['kind']}|{side_word(part, f)}"
                       for part in parts for f in part.get("features", [])})
    return {
        "id": record["id"], "source": record["source"], "plans_split": record["split"],
        "where": where, "kind": record.get("kind") or provenance.get("kind"),
        "stream": provenance.get("stream"), "tier": provenance.get("tier"),
        "lines": len(record["lines"]), "bodies": len(parts),
        "shapes": sorted({shape_word(part) for part in parts}),
        "features": features, "cells": sorted(cells_of(parts)),
        "late_feature": late_feature(parts), "group": has_group(record["lines"]),
        "sunk": any("sunk" in line for line in record["lines"]),
        "geometry": geometry_key(parts),
        "suspect": bool(undeclared_overlaps(stored_bodies(parts), OVERLAP_MARGIN)),
        "buildable": not any(feature.endswith("|none") for feature in features),
    }
