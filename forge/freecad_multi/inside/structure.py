"""Measure the whole structure: every body where it sits, and which bodies touch. Runs inside FreeCAD.

A body's `Shape` is already in the structure's coordinates (FreeCAD applies the body's
placement to it), so each body's `solid` in the snapshot is its GLOBAL solid: the
bounding box says where the part is, not only how big it is.

Touching and overlapping use the same three numbers as the reference checker
(forge/assembly_geometry.py), so the two can be compared pair for pair:

    two bodies closer than 1e-6 mm                 touch
    two bodies sharing more than 1e-6 mm^3         overlap
    bounding boxes more than 1e-5 mm apart         neither; the kernel is not asked

Cost. Asking the kernel about a pair takes a millisecond or more, and a structure of
100 bodies has 4,950 pairs. So answers are remembered per pair and thrown away only
when one of the two bodies has changed (its `fingerprint` is different). A command
changes one body, so a snapshot asks about that body's neighbours and nobody else. And
two upright boxes are never sent to the kernel at all: a solid as large as its own
bounding box is that box, and two boxes are compared by arithmetic.
"""

from __future__ import annotations

import math

TOUCH_TOLERANCE = 1e-6      # mm
OVERLAP_TOLERANCE = 1e-6    # mm^3
BOX_MARGIN = 1e-5           # mm
DIGITS = 9


def _r(value: float) -> float:
    return round(float(value), DIGITS) + 0.0


def placement_item(body) -> dict:
    """Where the body's origin is and how the body is turned."""
    placement = body.Placement
    m = placement.Rotation.Matrix
    yaw, pitch, roll = placement.Rotation.toEuler()
    return {"position": [_r(placement.Base.x), _r(placement.Base.y), _r(placement.Base.z)],
            "turn": [round(yaw, 6) + 0.0, round(pitch, 6) + 0.0, round(roll, 6) + 0.0],
            # Rows of the turn as a matrix: structure direction = matrix * body direction.
            "matrix": [[_r(m.A11), _r(m.A12), _r(m.A13)], [_r(m.A21), _r(m.A22), _r(m.A23)],
                       [_r(m.A31), _r(m.A32), _r(m.A33)]]}


def fingerprint(item: dict) -> tuple | None:
    """Changes whenever the body's solid or its place changes. None: no solid."""
    solid = item["solid"]
    if solid is None:
        return None
    return (item["tip"], solid["volume"], tuple(solid["bbox"]),
            tuple(item["placement"]["position"]), tuple(map(tuple, item["placement"]["matrix"])))


def fills_its_box(solid: dict) -> bool:
    """True for an upright box: a solid as large as its own bounding box IS that box."""
    x, y, z = solid["size"]
    return solid["solids"] == 1 and abs(solid["volume"] - x * y * z) <= 1e-9 * solid["volume"]


def relation(shape_a, solid_a: dict, shape_b, solid_b: dict) -> tuple[bool, float]:
    """(do they touch, the volume they share) for two solids and their measurements."""
    box_a, box_b = solid_a["bbox"], solid_b["bbox"]
    depth = [min(box_a[k + 3], box_b[k + 3]) - max(box_a[k], box_b[k]) for k in range(3)]
    if min(depth) < -BOX_MARGIN:
        return False, 0.0
    if fills_its_box(solid_a) and fills_its_box(solid_b):
        # Two upright boxes (most furniture): plain arithmetic, no kernel. They share the
        # box where their extents run into each other; they touch when no gap is left.
        shared = depth[0] * depth[1] * depth[2] if min(depth) > BOX_MARGIN else 0.0
        gap = math.sqrt(sum(min(d, 0.0) ** 2 for d in depth))
        return gap < TOUCH_TOLERANCE, shared if shared > OVERLAP_TOLERANCE else 0.0
    shared = 0.0
    if min(depth) > BOX_MARGIN:
        shared = shape_a.common(shape_b).Volume
        if shared <= OVERLAP_TOLERANCE:
            shared = 0.0
    # Solids that share volume are in contact; the distance need not be asked for.
    return bool(shared) or shape_a.distToShape(shape_b)[0] < TOUCH_TOLERANCE, shared


class Relations:
    """Remembers, pair by pair, which bodies touch or overlap."""

    def __init__(self) -> None:
        self._prints: dict[str, tuple] = {}
        self._pairs: dict[tuple[str, str], tuple[bool, float]] = {}     # only pairs that touch

    def clear(self) -> None:
        self._prints.clear()
        self._pairs.clear()

    def update(self, bodies: list[tuple[dict, object]]) -> None:
        """`bodies`: (snapshot item, FreeCAD body) for every body, in order."""
        prints = {item["name"]: fingerprint(item) for item, _ in bodies}
        changed = {name for name, mark in prints.items() if self._prints.get(name) != mark}
        gone = set(self._prints) - set(prints)
        self._pairs = {pair: value for pair, value in self._pairs.items()
                       if not (set(pair) & (changed | gone))}
        solid = [(item, obj) for item, obj in bodies if item["solid"] is not None]
        for item, obj in solid:
            if item["name"] not in changed:
                continue
            for other, other_obj in solid:
                if other is item or (other["name"] in changed and other["name"] < item["name"]):
                    continue        # a pair of two changed bodies is asked about once
                touch, shared = relation(obj.Shape, item["solid"], other_obj.Shape,
                                         other["solid"])
                if touch or shared:
                    self._pairs[tuple(sorted((item["name"], other["name"])))] = (touch, shared)
        self._prints = prints

    def lists(self, index: dict[str, int]) -> tuple[list, list]:
        """(touching pairs, overlapping pairs with the shared volume), by body number."""
        touching, overlapping = [], []
        for (a, b), (touch, shared) in self._pairs.items():
            pair = sorted((index[a], index[b]))
            if touch:
                touching.append(pair)
            if shared:
                overlapping.append([*pair, shared])
        return sorted(touching), sorted(overlapping)


def structure_item(bodies: list[dict], touching: list, overlapping: list) -> dict | None:
    """The whole structure's measurement, from its bodies' snapshot items."""
    solids = [body["solid"] for body in bodies if body["solid"] is not None]
    if not solids:
        return {"bodies": len(bodies), "solids": 0, "volume": 0.0, "bbox": None, "size": None,
                "valid": False, "touching": [], "overlapping": []}
    low = [min(s["bbox"][k] for s in solids) for k in range(3)]
    high = [max(s["bbox"][k + 3] for s in solids) for k in range(3)]
    return {"bodies": len(bodies), "solids": sum(s["solids"] for s in solids),
            "volume": sum(s["volume"] for s in solids), "bbox": low + high,
            "size": [round(b - a, DIGITS) for a, b in zip(low, high)],
            "valid": len(solids) == len(bodies) and all(s["valid"] for s in solids),
            "touching": touching, "overlapping": overlapping}
