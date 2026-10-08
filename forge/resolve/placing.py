"""Turn a line's placement (section 5) and alignments (section 6) into pins.

`place` reads the one placement and returns Facts: the first pins, plus what the
repetition and the checks need to know about how the part meets its target.
`align` adds the pins of the alignments and returns the sum of the `offset` moves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.plan import grammar as g
from forge.plan.model import Line
from forge.resolve import space as sp
from forge.resolve.defaults import FALLBACK_MM
from forge.resolve.pins import Pin
from forge.resolve.scene import Scene
from forge.resolve.shapes import DoesNotFit
from forge.resolve.space import FACE, TOL, Box, Vec

# face placement -> (axis, the side of X the new part ends up on)
SIDE_OF = {"on top of": (2, 1), "under": (2, -1), "in front of": (1, -1), "behind": (1, 1),
           "left of": (0, -1), "right of": (0, 1)}
THROUGH_CUTS = ("hole", "counterbore", "hole_pair", "polar", "row")


@dataclass
class Facts:
    pins: dict[int, list[Pin]] = field(default_factory=lambda: {0: [], 1: [], 2: []})
    touch_axis: int = 2                 # the touched face is square to this axis
    face: Box | None = None             # the face the placement touches (repetitions use it)
    mirror_about: Vec | None = None     # the middle a `mirrored` copy is mirrored across
    may_float: bool = False             # allowed to touch nothing until `done`
    sunk_into: list[str] = field(default_factory=list)   # bodies it may overlap
    also_at: list[Vec] = field(default_factory=list)     # `through` several holes: more copies


def fallback_size(line: Line, scene: Scene) -> float:
    """The size a `default` falls back to when nothing on the line gives a scale."""
    placement = line.placement
    if placement is None or not placement.parts:
        return FALLBACK_MM
    frame = scene.frame(placement.parts[0])
    axis = SIDE_OF.get(placement.kind, (2, 1))[0]
    return min(frame.size[k] for k in range(3) if k != axis)


def _axis_index(direction: Vec) -> int:
    axis = max(range(3), key=lambda k: abs(direction[k]))
    if abs(abs(direction[axis]) - 1.0) > 1e-9:
        raise DoesNotFit("the axis is not square to length, depth or height")
    return axis


def _centred(facts: Facts, box: Box, skip: int, stretch: bool = False) -> None:
    """Weak pins: centred on `box` in the two directions other than `skip`."""
    for k in range(3):
        if k == skip:
            continue
        facts.pins[k].append(Pin(0, box.mid[k], strong=False))
        if stretch:         # a `rest` size here reaches from one side of the box to the other
            facts.pins[k] += [Pin(-1, box.low[k], strong=False), Pin(1, box.high[k], strong=False)]


def _holes(scene: Scene, name: str) -> list[list[tuple[Vec, Vec]]]:
    """The holes of X, oldest first. Each entry is what ONE line made: a list of
    (a point on the hole's axis, its direction). A tube's own hole comes first."""
    body = scene.body(name)
    if body is None:
        raise DoesNotFit(f"{name} is several parts; `through` needs one part with a hole")
    found = []
    if body.inner is not None and body.hollow is None:
        found.append([body.axis])                            # a tube's own hole
    for cut in body.cuts:
        if cut.kind in THROUGH_CUTS:
            second = sp.scale(sp.cross(cut.normal, cut.first), cut.second_sign)
            direction = sp.apply(body.matrix, cut.normal)
            spots = [sp.add(cut.origin, sp.add(sp.scale(cut.first, u), sp.scale(second, v)))
                     for u, v in cut.spots]
            found.append([(body.to_world(spot), direction) for spot in spots])
    return found


def _through(facts: Facts, scene: Scene, name: str, own_axis: Vec | None, shift: Vec) -> None:
    holes = _holes(scene, name)
    if own_axis is not None:    # a round part goes through a hole that runs the same way
        holes = [made for made in holes if abs(abs(sp.dot(made[0][1], own_axis)) - 1.0) < 1e-9]
    if not holes:
        raise DoesNotFit(f"{name} has no hole running the way this part runs")
    # The most recent hole line wins; a line that made several holes gives several copies.
    chosen = holes[-1]
    point, direction = chosen[0]
    axis = _axis_index(direction)
    frame = scene.frame(name)
    for k in range(3):
        if k == axis:
            facts.pins[k].append(Pin(0, frame.mid[k], strong=False))    # centred along the hole
        else:
            facts.pins[k].append(Pin(0, point[k] - shift[k]))
    facts.touch_axis, facts.mirror_about, facts.may_float = axis, frame.mid, True
    facts.also_at = [sp.sub(other[0], point) for other in chosen[1:]]


def _between(facts: Facts, scene: Scene, parts: list[str], dims: list) -> None:
    if len(parts) == 1:         # `between X`: X is a set of exactly two copies
        copies = scene.world.items[parts[0]].copies
        if len(copies) != 2:
            raise DoesNotFit(f"{parts[0]} is not a set of two")
        a, b = (sp.around([scene.world.bodies[n].frame for n in names]) for names in copies)
    else:
        a, b = scene.frame(parts[0]), scene.frame(parts[1])
    gaps = [max(a.low[k] - b.high[k], b.low[k] - a.high[k]) for k in range(3)]
    apart = [k for k in range(3) if gaps[k] > TOL]
    unknown = [k for k in range(3) if dims[k] is None]
    if not apart:
        raise DoesNotFit("the two parts are not apart: there is nothing between them")
    if unknown:
        if unknown[0] not in apart:
            raise DoesNotFit("`rest` runs along a direction in which the two parts are not apart")
        axis = unknown[0]
    else:
        axis = max(apart, key=lambda k: gaps[k])
        if abs(dims[axis] - gaps[axis]) > TOL:
            raise DoesNotFit(f"the gap is {gaps[axis]:g}, the part is {dims[axis]:g}; write `rest`")
    low_part, high_part = (a, b) if a.high[axis] <= b.low[axis] + TOL else (b, a)
    facts.pins[axis] += [Pin(-1, low_part.high[axis]), Pin(1, high_part.low[axis])]
    # Centred on the piece of the two facing faces that both parts share.
    low = [max(a.low[k], b.low[k]) for k in range(3)]
    high = [min(a.high[k], b.high[k]) for k in range(3)]
    low[axis], high[axis] = low_part.high[axis], high_part.low[axis]
    if any(high[k] - low[k] < -TOL for k in range(3)):
        raise DoesNotFit("the two parts do not face each other")
    shared = Box(tuple(low), tuple(high))
    _centred(facts, shared, skip=axis)
    facts.touch_axis, facts.face, facts.mirror_about = axis, shared, shared.mid


def place(line: Line, scene: Scene, dims: list, own_axis: Vec | None, shift: Vec) -> Facts:
    """The pins the placement gives. `dims`: the new frame's sizes (None where `rest`);
    `own_axis`: the new part's round axis as it sits, if it has one; `shift`: from the
    middle of its frame to that axis (not zero only for an odd prism)."""
    facts = Facts()
    kind, parts = line.placement.kind, line.placement.parts
    if kind in (g.ON_GROUND, g.ABOVE_GROUND):
        if not scene.world.has_ground:
            raise DoesNotFit("a group is not on the ground until it is placed")
        if kind == g.ON_GROUND:
            facts.pins[2].append(Pin(-1, 0.0, hard=True))
        facts.pins[0].append(Pin(0, 0.0, strong=False))
        facts.pins[1].append(Pin(0, 0.0, strong=False))
        facts.may_float = kind == g.ABOVE_GROUND
    elif kind in SIDE_OF:
        axis, side = SIDE_OF[kind]
        target = scene.frame(parts[0])
        gap, sunk = line.option("gap"), line.option("sunk")
        move = scene.value(gap.amount) if gap else 0.0       # a gap moves it away from X ...
        move -= scene.value(sunk.amount) if sunk else 0.0    # ... `sunk` moves it into X
        at = target.high[axis] + move if side > 0 else target.low[axis] - move
        facts.pins[axis].append(Pin(-side, at))
        _centred(facts, target, skip=axis, stretch=line.option("across") is not None)
        facts.touch_axis, facts.face, facts.mirror_about = axis, target, target.mid
        facts.may_float = gap is not None
        if sunk:
            facts.sunk_into = [body.name for body in scene.bodies(parts[0])]
    elif kind == "inside":
        inner = scene.inner(parts[0], ("bottom",))
        facts.pins[2].append(Pin(-1, inner.low[2]))
        body = scene.body(parts[0])
        walls = all(body.inner_exact(face) for face in ("left", "right", "front", "back"))
        if not walls and (dims[0] is None or dims[1] is None):
            scene.inner(parts[0], ("left", "right", "front", "back"))    # raises: not exact
        _centred(facts, inner, skip=2, stretch=walls)
        # Copies can only be spaced across an inside whose walls are known exactly.
        facts.face, facts.mirror_about = (inner if walls else None), inner.mid
    elif kind == "through":
        _through(facts, scene, parts[0], own_axis, shift)
    elif kind == g.AROUND:
        point, direction = scene.axis(parts[0])
        axis = _axis_index(direction)
        if own_axis is None or abs(abs(sp.dot(own_axis, direction)) - 1.0) > 1e-9:
            raise DoesNotFit(f"the tube does not run along {parts[0]}'s axis; turn it")
        frame = scene.frame(parts[0])
        for k in range(3):
            facts.pins[k].append(Pin(0, frame.mid[k], strong=False) if k == axis
                                 else Pin(0, point[k] - shift[k]))
        facts.touch_axis, facts.mirror_about = axis, frame.mid
    elif kind == g.BETWEEN:
        _between(facts, scene, parts, dims)
    else:
        raise DoesNotFit(f"placement {kind} is handled elsewhere")
    return facts


def inset_of(line: Line, scene: Scene) -> float:
    amounts = [scene.value(a.amount) for a in line.alignments if a.kind == "inset"]
    return amounts[-1] if amounts else 0.0


def align(line: Line, scene: Scene, facts: Facts, shift: Vec) -> Vec:
    """Add the alignments' pins to `facts`; return the total of the `offset` moves."""
    inset = inset_of(line, scene)
    offsets = [0.0, 0.0, 0.0]
    for a in line.alignments:
        if a.kind in ("flush", "face offset"):
            axis, side = FACE[a.face]
            at, inward = scene.face_of(a.part, a.other_face)
            if a.kind == "flush":
                # `inset` moves a part in from a face it is flush with: same-named faces only.
                if a.other_face.removeprefix("inner ") == a.face:
                    at += inward * inset
            else:
                amount = scene.value(a.amount)
                at += {"above": amount, "below": -amount, "inside": inward * amount,
                       "beyond": -inward * amount}[a.relation]
            facts.pins[axis].append(Pin(side, at))
        elif a.kind in ("top at height", "bottom at height"):
            facts.pins[2].append(Pin(1 if a.face == "top" else -1, scene.value(a.amount)))
        elif a.kind == "down to ground":
            if not scene.world.has_ground:
                raise DoesNotFit("a group is not on the ground until it is placed")
            facts.pins[2].append(Pin(-1, 0.0, hard=True))
        elif a.kind == "same axis":
            point, direction = scene.axis(a.part)
            along = _axis_index(direction)
            for k in range(3):
                if k != along:
                    facts.pins[k].append(Pin(0, point[k] - shift[k]))
        elif a.kind == "offset":
            axis, side = FACE[a.face]
            offsets[axis] += side * scene.value(a.amount)
    return tuple(offsets)
