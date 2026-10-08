"""Build ONE copy of what a part line describes, in its place: the prototype.

The steps, in order:
  1. sizes: numbers, `same as`, shares; `default` by the rule table; `rest` stays open
  2. the turn (lying / pointing) and so the frame's sizes along length, depth, height
  3. pins from the placement, then from the alignments (placing.py)
  4. solve each axis (pins.py): this fixes the frame and gives `rest` its value
  5. the Body, or for a group the group's bodies moved as one

The repetition (repeating.py) then makes the other copies from this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.plan import grammar as g
from forge.plan.model import Line
from forge.resolve import shapes, spans
from forge.resolve import space as sp
from forge.resolve.bodies import Body, turn_matrix, unit_frame
from forge.resolve.defaults import fill_defaults
from forge.resolve.pins import solve
from forge.resolve.placing import Facts, align, fallback_size, inset_of, place
from forge.resolve.scene import Scene, World
from forge.resolve.shapes import DoesNotFit
from forge.resolve.space import TOL, Box


@dataclass
class Prototype:
    bodies: list[Body]
    facts: Facts
    inset: float = 0.0
    notes: list[str] = field(default_factory=list)


def read_sizes(line: Line, scene: Scene, notes: list[str]) -> dict[str, float | None]:
    """Every size as a number; a `rest` size is None until the frame is solved."""
    sizes: dict[str, float | None] = {}
    defaults = []
    for size in line.sizes:
        if size.source in ("rest", "default"):
            sizes[size.slot] = None
            if size.source == "default":
                defaults.append(size.slot)
        else:
            sizes[size.slot] = scene.value(size)
    if defaults:
        known = {slot: value for slot, value in sizes.items() if value is not None}
        notes += fill_defaults(line.shape, known, defaults, fallback_size(line, scene))
        sizes.update({slot: known[slot] for slot in defaults})
    return sizes


def _world_dims(matrix: sp.Mat, local: list) -> tuple[list, list[int]]:
    """The frame's sizes along length, depth, height, and which local side each one is."""
    source = [max(range(3), key=lambda j, k=k: abs(matrix[k][j])) for k in range(3)]
    return [local[j] for j in source], source


def _solved_box(line: Line, scene: Scene, facts: Facts, dims: list, shift: sp.Vec) -> Box:
    offsets = align(line, scene, facts, shift)
    ends = [solve(facts.pins[k], dims[k], k) for k in range(3)]
    return Box(tuple(e[0] + offsets[k] for k, e in enumerate(ends)),
               tuple(e[1] + offsets[k] for k, e in enumerate(ends)))


def first_of_group(line: Line, scene: Scene) -> Prototype:
    """The first part of a group: a shape and sizes only, standing where the group starts."""
    notes: list[str] = []
    sizes = read_sizes(line, scene, notes)
    if None in sizes.values():
        raise DoesNotFit("the first part of a group cannot have a `rest` size")
    matrix = turn_matrix(line.shape, line.orientation, line.pointing)
    body = Body(line.name, line.name, line.number, line.shape, line.profile, sizes, matrix,
                (0.0, 0.0, 0.0), line.pointing, line.flat)
    return Prototype([body], Facts(may_float=True), notes=notes)


def build(line: Line, scene: Scene, groups: dict[str, World]) -> Prototype:
    if line.placement.kind == g.SPANS:
        notes: list[str] = []
        sizes = read_sizes(line, scene, notes)
        return Prototype([spans.build(line, scene, sizes)], Facts(), notes=notes)
    if line.group:
        return _group(line, scene, groups[line.group])
    notes = []
    sizes = read_sizes(line, scene, notes)
    matrix = turn_matrix(line.shape, line.orientation, line.pointing)
    # A stand-in with every open size set to 1: enough to check the written sizes and to
    # find the axis, neither of which depends on a `rest` size.
    stand_in = shapes.describe(line.shape, line.profile,
                               {s: 1.0 if v is None else v for s, v in sizes.items()},
                               line.pointing, line.flat)
    own_axis = sp.apply(matrix, (0.0, 0.0, 1.0)) if stand_in.is_round else None
    shift = sp.apply(matrix, stand_in.axis_at)
    dims, source = _world_dims(matrix, shapes.frame_dims(line.shape, line.profile, sizes))

    facts = place(line, scene, dims, own_axis, shift)
    box = _solved_box(line, scene, facts, dims, shift)
    for slot, value in sizes.items():
        if value is None:           # give `rest` its value: the solved side of the frame
            local = shapes.rest_axis(line.shape, slot)
            sizes[slot] = box.size[source.index(local)]
            notes.append(f"rest {slot} = {sizes[slot]:g}")
    body = Body(line.name, line.name, line.number, line.shape, line.profile, sizes, matrix,
                box.mid, line.pointing, line.flat, may_overlap=frozenset(facts.sunk_into))
    made = body.frame
    off = max(abs(made.low[k] - box.low[k]) + abs(made.high[k] - box.high[k]) for k in range(3))
    if off > 1e-6:
        raise DoesNotFit("internal: the solved frame and the shape's frame disagree")
    return Prototype([body], facts, inset_of(line, scene), notes)


def _group(line: Line, scene: Scene, template: World) -> Prototype:
    """A group is placed like one part, using the frame around all its bodies (section 8)."""
    bodies = list(template.bodies.values())
    frame = unit_frame(bodies)
    dims = list(frame.size)
    facts = place(line, scene, dims, None, (0.0, 0.0, 0.0))
    box = _solved_box(line, scene, facts, dims, (0.0, 0.0, 0.0))
    move = sp.sub(box.low, frame.low)
    return Prototype([body.moved(sp.IDENTITY, move) for body in bodies], facts,
                     inset_of(line, scene))


def below_ground(bodies: list[Body]) -> bool:
    return any(body.frame.low[2] < -TOL for body in bodies)
