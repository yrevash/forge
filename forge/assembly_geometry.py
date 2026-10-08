"""Measure a result made of several parts: which part is which, do any overlap, do they touch?

`forge.geometry.measure` answers "is this one valid solid, and how big is it".
A structure is many solids on purpose, so three more questions need
the kernel's answer:

- what does EACH part measure (volume, bounding box)?
- do any two parts share volume? They must not.
- which parts touch, and do the touching parts form ONE group? A part that
  touches nothing is floating in the air.

`measure(result, structure=True)` adds this file's answer under the key
"structure". Nothing here is used for single parts.
"""

from __future__ import annotations

import itertools

import cadquery as cq

# Two parts closer than this are touching (mm).
TOUCH_TOLERANCE = 1e-6
# A shared volume above this is an overlap (mm^3).
OVERLAP_TOLERANCE = 1e-6
# Bounding boxes carry a little kernel tolerance; pairs whose boxes are further
# apart than this cannot touch, and pairs whose boxes run into each other by
# less than this on some axis cannot share volume worth the name.
BOX_MARGIN = 1e-5


def named_parts(obj: object) -> list[tuple[str, cq.Shape]]:
    """The parts of a result, with their names.

    For an Assembly: one part per top-level entry, in the order added, already
    moved to where the assembly places it. For anything else: one per solid.
    """
    if isinstance(obj, cq.Assembly):
        parts = [(child.name, child.toCompound()) for child in obj.children]
        if obj.obj is not None:                       # a shape stored on the root itself
            parts.insert(0, (obj.name, obj.toCompound() if not obj.children
                             else cq.Assembly(obj.obj, loc=obj.loc).toCompound()))
        return parts
    from forge.geometry import to_shape

    return [(f"solid_{i}", solid) for i, solid in enumerate(to_shape(obj).Solids())]


def _groups(count: int, joined: list[tuple[int, int]]) -> int:
    """How many separate groups the parts fall into, given which pairs touch."""
    group = list(range(count))

    def find(i: int) -> int:
        while group[i] != i:
            i = group[i]
        return i

    for a, b in joined:
        group[find(a)] = find(b)
    return len({find(i) for i in range(count)})


def measure_parts(obj: object) -> dict:
    """Per-part measurements, overlapping pairs, touching pairs and the number of groups."""
    parts = named_parts(obj)
    boxes = [shape.BoundingBox() for _, shape in parts]
    lows = [(b.xmin, b.ymin, b.zmin) for b in boxes]
    highs = [(b.xmax, b.ymax, b.zmax) for b in boxes]

    overlaps: list[list] = []
    touching: list[tuple[int, int]] = []
    for i, j in itertools.combinations(range(len(parts)), 2):
        # How far the two bounding boxes run into each other on each axis (negative: a gap).
        depth = [min(highs[i][k], highs[j][k]) - max(lows[i][k], lows[j][k]) for k in range(3)]
        if min(depth) < -BOX_MARGIN:
            continue                                   # too far apart to touch or overlap
        a, b = parts[i][1], parts[j][1]
        if min(depth) > BOX_MARGIN:
            shared = a.intersect(b).Volume()
            if shared > OVERLAP_TOLERANCE:
                overlaps.append([parts[i][0], parts[j][0], shared])
        if a.distance(b) < TOUCH_TOLERANCE:
            touching.append((i, j))

    touched = {index for pair in touching for index in pair}
    return {
        "n_parts": len(parts),
        "parts": [{"name": name, "n_solids": len(shape.Solids()), "volume": shape.Volume(),
                   "bbox_min": list(lows[i]), "bbox_max": list(highs[i])}
                  for i, (name, shape) in enumerate(parts)],
        "overlaps": overlaps,
        "touching": [[parts[i][0], parts[j][0]] for i, j in touching],
        "untouched": [name for i, (name, _) in enumerate(parts) if i not in touched],
        "n_groups": _groups(len(parts), touching),
    }
