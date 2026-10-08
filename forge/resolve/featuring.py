"""Features (section 9): work out where on a body a feature goes, as a Cut.

A feature line names a face of a part AS IT SITS ("deck's front"). Here that face is
found on the body's frame, the feature's middle is placed on it (centred, edge
distances, or `at (x, y)`), unwritten slots get their default, and the whole thing is
turned into the body's own standing coordinates so it travels with the body.

Only what can be decided by arithmetic is checked here (the feature lies inside its
face, a blind hole is shallower than the part). Whether the result is still one sound
solid is the kernel's call (resolver.py asks it on every feature line).
"""

from __future__ import annotations

import math

from forge.plan import grammar as g
from forge.plan.model import Feature, Line
from forge.resolve import space as sp
from forge.resolve.bodies import Body, Cut
from forge.resolve.defaults import fill_feature_slots
from forge.resolve.scene import Scene
from forge.resolve.shapes import DoesNotFit
from forge.resolve.space import FACE, TOL

# The first and second direction of a face, by the axis it is square to (section 9).
FACE_DIRECTIONS = {2: (0, 1), 1: (0, 2), 0: (1, 2)}
ADDED = ("boss", "boss_pair", "pad")
PAIRS = ("hole_pair", "boss_pair", "pocket_pair")
NEEDS_DEPTH = ("blind_hole", "counterbore", "pocket", "pocket_pair", "slot")


def _half_footprint(kind: str, slots: dict[str, float]) -> tuple[float, float]:
    """Half the feature's size along the face's first and second direction."""
    if kind in ("pad", "pocket", "pocket_pair"):
        return slots["length"] / 2, slots["width"] / 2
    if kind == "slot":
        run, wide = slots["length"], slots["width"]
        turn = math.radians(slots["angle"])
        reach = max(0.0, run - wide) / 2
        return abs(math.cos(turn)) * reach + wide / 2, abs(math.sin(turn)) * reach + wide / 2
    if kind in ("polar", "row"):
        return slots["hole diameter"] / 2, slots["hole diameter"] / 2
    if kind in ("corner_radius", "top_chamfer", "top_fillet", "shell"):
        return 0.0, 0.0
    return slots["diameter"] / 2, slots["diameter"] / 2


def _spots(kind: str, slots: dict[str, float], u: float, v: float) -> list[tuple[float, float]]:
    """Where each instance sits on the face, from the face's middle."""
    if kind in PAIRS:
        if abs(u) <= TOL:
            raise DoesNotFit("a pair needs a position off the middle: its twin is its mirror image")
        return [(u, v), (-u, v)]
    if kind == "polar":
        count, radius = int(slots["count"]), slots["circle diameter"] / 2
        turns = [2 * math.pi * i / count for i in range(count)]
        return [(radius * math.cos(turn), radius * math.sin(turn)) for turn in turns]
    if kind == "row":
        count, step = int(slots["count"]), slots["spacing"]
        return [(u + (i - (count - 1) / 2) * step, v) for i in range(count)]
    return [(u, v)]


def _position(feature: Feature, scene: Scene, low: list[float], high: list[float],
              first: int, second: int) -> tuple[float, float]:
    """The feature's middle, from the middle of the face."""
    if feature.position == "at":
        return feature.at
    at = {first: 0.0, second: 0.0}
    for edge in feature.edges:
        axis, side = FACE[edge.face]
        if axis not in at:
            raise DoesNotFit(f"the face has no {edge.face} edge")
        amount = scene.value(edge.amount)
        middle = (low[axis] + high[axis]) / 2
        at[axis] = (low[axis] + amount if side < 0 else high[axis] - amount) - middle
    return at[first], at[second]


def make_cut(line: Line, scene: Scene, body: Body, notes: list[str]) -> Cut:
    """The Cut for this feature line on this body. Raises DoesNotFit."""
    feature = line.feature
    kind = feature.step_kind
    if not sp.is_square(body.matrix):
        raise DoesNotFit(f"{body.name} is turned by an odd angle; its faces are not the frame's")
    inner = line.face.startswith("inner ")
    axis, side = FACE[line.face.removeprefix("inner ")]
    frame = body.core           # the shape's own faces, not counting bosses already on it
    if inner:
        room = body.inner
        if room is None:
            raise DoesNotFit(f"{body.name} has no inside")
        if not body.inner_exact(line.face.removeprefix("inner ")):
            raise DoesNotFit(f"{body.name}'s {line.face} is a sloping or round wall: its place "
                             "is not resolved exactly")
        plane = room.face(line.face.removeprefix("inner "))
        under = abs(plane - frame.face(line.face.removeprefix("inner ")))   # the wall
        normal = sp.unit(axis, -float(side))        # an inner face looks into the hollow
        low, high = list(room.low), list(room.high)
    else:
        plane, under = frame.face(line.face), frame.size[axis]
        normal = sp.unit(axis, float(side))
        low, high = list(frame.low), list(frame.high)
    if under <= TOL:
        raise DoesNotFit("that side is open: there is no wall to put a feature on")
    first, second = FACE_DIRECTIONS[axis]
    sides = (high[first] - low[first], high[second] - low[second])

    slots = dict(feature.slots)
    _, slot_words, _ = g.FEATURES[feature.name]
    notes += fill_feature_slots(feature.name, slot_words, slots, sides[0], sides[1], under)

    u, v = _position(feature, scene, low, high, first, second)
    spots = _spots(kind, slots, u, v)
    half = _half_footprint(kind, slots)
    for spot_u, spot_v in spots:
        if (abs(spot_u) + half[0] > sides[0] / 2 + TOL
                or abs(spot_v) + half[1] > sides[1] / 2 + TOL):
            raise DoesNotFit(f"the {feature.name} does not lie inside the {line.face} face")
    # A depth equal to the material is allowed: a pocket right through is the language's
    # only way to cut a rectangular opening.
    if kind in NEEDS_DEPTH and slots["depth"] > under + TOL:
        raise DoesNotFit(f"depth {slots['depth']:g} is more than the {under:g} of material")
    if kind == "counterbore" and slots["diameter"] <= slots["hole diameter"] + TOL:
        raise DoesNotFit("the wider part of a counterbored hole must be wider than the hole")

    normal_local = _clean(body.direction_to_local(normal))
    open_normal = None
    if kind in ADDED and not inner and normal_local not in body.standing.flat_faces:
        raise DoesNotFit(f"{body.name}'s {line.face} is not a flat face; a {feature.name} "
                         "would hang in the air")
    if kind == "corner_radius" and (body.shape != "box"
                                    or 2 * slots["radius"] >= min(sides) - TOL):
        raise DoesNotFit("rounded corners need a box whose face is wider than twice the radius")
    if kind in ("top_chamfer", "top_fillet"):
        size = slots["size" if kind == "top_chamfer" else "radius"]
        if 2 * size >= min(sides) - TOL or size >= under - TOL:
            raise DoesNotFit(f"{size:g} is too large for the edge round this face")
    if kind == "shell":
        open_normal = _shell_opening(body, feature, slots["wall"])

    middle = [(low[k] + high[k]) / 2 for k in range(3)]
    middle[axis] = plane
    first_local = _clean(body.direction_to_local(sp.unit(first)))
    second_local = _clean(body.direction_to_local(sp.unit(second)))
    handed = sp.dot(sp.cross(normal_local, first_local), second_local)
    return Cut(kind, feature.name, slots, _clean(body.to_local(tuple(middle))), normal_local,
               first_local, 1.0 if handed > 0 else -1.0, under, spots, open_normal, line.number)


EDGE_TREATMENTS = ("corner_radius", "top_chamfer", "top_fillet")
SHELLED = ("box", "cylinder", "cone", "dome", "sphere", "tapered box", "prism")


def _shell_opening(body: Body, feature: Feature, wall: float) -> sp.Vec | None:
    """Check a `hollowed out` and return the open face as a direction of the standing shape."""
    # Rounded or chamfered edges may come first; anything else (a hole, a boss) may not.
    if body.shape not in SHELLED or any(cut.kind not in EDGE_TREATMENTS for cut in body.cuts):
        raise DoesNotFit("`hollowed out` is built for a " + ", ".join(SHELLED)
                         + " with no features yet other than rounded or chamfered edges")
    if 2 * wall >= min(body.standing.dims) - TOL:
        raise DoesNotFit(f"walls of {wall:g} leave no inside")
    if feature.open_at is None:
        return None
    axis, side = FACE[feature.open_at]
    opened = _clean(body.direction_to_local(sp.unit(axis, float(side))))
    if opened not in body.standing.planes:
        raise DoesNotFit(f"{body.name} has no flat {feature.open_at} face to leave open")
    return opened


def _clean(v: sp.Vec) -> sp.Vec:
    """Round away float dust (and -0.0), so directions compare equal to (0, 0, 1)."""
    return tuple(0.0 if abs(x) < 1e-9 else round(x, 9) for x in v)
