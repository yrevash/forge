"""Repetition (section 7): from the one placed prototype to every copy.

Every repetition is a list of rigid moves of the prototype:

    corners, evenly spaced, spread, grid   slides (no turn)
    N around X on circle D                 slides; turns about X's axis with `facing outward`
    N around X (beside X)                  always turns about X's axis
    mirrored                               the prototype plus its mirror image

A move is (turn, shift): world point p goes to turn * p + shift. Because a move acts on
whole bodies, the same code repeats a single part and a placed group.
"""

from __future__ import annotations

from forge.plan.model import Line
from forge.resolve import space as sp
from forge.resolve.bodies import Body, unit_frame
from forge.resolve.placing import Facts
from forge.resolve.scene import Scene
from forge.resolve.shapes import DoesNotFit
from forge.resolve.space import AXIS_OF_DIRECTION, FACE, TOL, Box, Mat, Vec

Move = tuple[Mat, Vec]
# Which two directions a grid's N and M run along, by the axis the touched face is square to.
GRID_AXES = {2: (0, 1), 1: (0, 2), 0: (1, 2)}


def _slide(axis_shifts: dict[int, float]) -> Move:
    return sp.IDENTITY, tuple(axis_shifts.get(k, 0.0) for k in range(3))


def _corner_moves(line: Line, scene: Scene, facts: Facts, frame: Box, inset: float) -> list[Move]:
    """Tuck the part into corners of X's face: its two outer faces flush with X's."""
    repetition = line.repetition
    target = scene.frame(repetition.part)
    in_face = [k for k in range(3) if k != facts.touch_axis]
    if repetition.kind == "each corner":
        corners = [{in_face[0]: a, in_face[1]: b} for a in (-1, 1) for b in (-1, 1)]
    else:
        fixed = dict(FACE[word] for word in repetition.side.split("-"))
        if not set(fixed) <= set(in_face):
            raise DoesNotFit(f"the touched face has no {repetition.side} corner")
        free = [k for k in in_face if k not in fixed]
        corners = [{**fixed, free[0]: s} for s in (-1, 1)] if free else [fixed]
    moves = []
    for corner in corners:
        shifts = {}
        for axis, side in corner.items():
            if side < 0:
                shifts[axis] = target.low[axis] + inset - frame.low[axis]
            else:
                shifts[axis] = target.high[axis] - inset - frame.high[axis]
        moves.append(_slide(shifts))
    return moves


def _row(face: Box, frame: Box, axis: int, count: int, spread: bool) -> list[float]:
    """The shift of each copy along one direction of the touched face."""
    room, size = face.size[axis], frame.size[axis]
    if spread:              # first and last flush with the two ends
        if count == 1:
            return [face.mid[axis] - frame.mid[axis]]
        step = (room - size) / (count - 1)
        if step < -TOL:
            raise DoesNotFit(f"the part is {size:g} long; the face is only {room:g}")
        return [face.low[axis] + i * step - frame.low[axis] for i in range(count)]
    gap = (room - count * size) / (count + 1)       # equal gaps, also before and after
    if gap < -TOL:
        raise DoesNotFit(f"{count} copies of {size:g} do not fit in {room:g}")
    return [face.low[axis] + gap + i * (size + gap) - frame.low[axis] for i in range(count)]


def _around_moves(line: Line, scene: Scene, facts: Facts, frame: Box) -> list[Move]:
    repetition = line.repetition
    point, direction = scene.axis(repetition.part)
    axis = max(range(3), key=lambda k: abs(direction[k]))
    if abs(abs(direction[axis]) - 1.0) > 1e-9:
        raise DoesNotFit(f"{repetition.part}'s axis is not square to the directions")
    start = sp.IDENTITY, (0.0, 0.0, 0.0)
    if repetition.diameter is not None:
        # The first copy's middle goes on the circle: at the front (upright axis), else on top.
        first_way = (0.0, -1.0, 0.0) if axis == 2 else (0.0, 0.0, 1.0)
        radius = scene.value(repetition.diameter) / 2
        want = sp.add(point, sp.scale(first_way, radius))
        shift = [want[k] - frame.mid[k] for k in range(3)]
        shift[axis] = 0.0                       # along the axis it stays where the line put it
        start = sp.IDENTITY, tuple(shift)
    middle = sp.add(frame.mid, start[1])
    moves = []
    for i in range(repetition.count):
        turn = sp.rotation(axis, 360.0 * i / repetition.count)
        # Turning about the axis through `point`: p -> turn * (p - point) + point.
        # `N around X` beside X: "the others are that copy TURNED about X's axis", always.
        # `N around X on circle D`: turned only with `facing outward`.
        if repetition.outward or repetition.diameter is None:
            shift = sp.add(sp.apply(turn, sp.sub(start[1], point)), point)
            moves.append((turn, shift))
        else:                                   # only the middle goes round; no turning
            target = sp.add(sp.apply(turn, sp.sub(middle, point)), point)
            moves.append((sp.IDENTITY, sp.sub(target, frame.mid)))
    return moves


def moves(line: Line, scene: Scene, facts: Facts, bodies: list[Body], inset: float) -> list[Move]:
    """Where every copy of the prototype goes. No repetition: one copy, not moved."""
    repetition = line.repetition
    stay: Move = (sp.IDENTITY, (0.0, 0.0, 0.0))
    if repetition is None:
        return [stay]
    frame = unit_frame(bodies)
    kind = repetition.kind
    if kind in ("each corner", "corners", "corner"):
        return _corner_moves(line, scene, facts, frame, inset)
    if kind in ("evenly spaced", "spread", "grid"):
        if facts.face is None:
            raise DoesNotFit("this placement touches no face to space copies across")
        if kind == "grid":
            first, second = GRID_AXES[facts.touch_axis]
            return [_slide({first: a, second: b})
                    for a in _row(facts.face, frame, first, repetition.count, False)
                    for b in _row(facts.face, frame, second, repetition.count2, False)]
        axis = AXIS_OF_DIRECTION[repetition.direction]
        if axis == facts.touch_axis:
            raise DoesNotFit(f"copies cannot be spaced along {repetition.direction} here")
        return [_slide({axis: s})
                for s in _row(facts.face, frame, axis, repetition.count, kind == "spread")]
    if kind == "around":
        return _around_moves(line, scene, facts, frame)
    # mirrored: a true mirror image across the middle of the part the placement names
    axis = 0 if repetition.direction == "left-right" else 1
    if facts.mirror_about is None:
        raise DoesNotFit("there is no part to mirror across")
    return [stay, (sp.mirror(axis), sp.scale(sp.unit(axis), 2 * facts.mirror_about[axis]))]
