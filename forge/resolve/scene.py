"""What exists so far (World), and how one line looks at it (Scene).

A plan name stands for an Item: one copy, or several (a set, section 7). Each copy is
one body, or the bodies of a placed group. A Scene answers the questions a line asks
about earlier parts: where is X's frame, its axis, its inside, how big is it.

The rule for sets (section 7, "a set in an alignment or a size"): a set means the frame
around all its copies, EXCEPT when the line makes one new part per copy of that very
set; then each new part sees its own copy. `own` holds that choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.plan.model import Size
from forge.resolve import space as sp
from forge.resolve.bodies import Body
from forge.resolve.shapes import DoesNotFit
from forge.resolve.space import AXIS_OF_DIRECTION, Box, Vec

SHARES = {"half": 0.5, "third": 1.0 / 3.0, "quarter": 0.25, "double": 2.0}


@dataclass
class Item:
    name: str
    copies: list[list[str]]             # per copy, the names of its bodies
    is_group: bool = False


@dataclass
class World:
    bodies: dict[str, Body] = field(default_factory=dict)       # in the order they were built
    items: dict[str, Item] = field(default_factory=dict)
    touching: set[frozenset[str]] = field(default_factory=set)  # pairs of body names
    has_ground: bool = True             # False inside a group: it is not placed yet

    def bodies_of(self, name: str, copy: int | None = None) -> list[Body]:
        item = self.items[name]
        copies = item.copies if copy is None else [item.copies[copy]]
        return [self.bodies[body] for names in copies for body in names]


@dataclass
class Scene:
    world: World
    own: dict[str, int] = field(default_factory=dict)   # set name -> this new part's copy

    def bodies(self, name: str) -> list[Body]:
        return self.world.bodies_of(name, self.own.get(name))

    def frame(self, name: str) -> Box:
        return sp.around([body.frame for body in self.bodies(name)])

    def body(self, name: str) -> Body | None:
        """The one body the name means here, or None when it means several."""
        found = self.bodies(name)
        return found[0] if len(found) == 1 else None

    def axis(self, name: str) -> tuple[Vec, Vec]:
        body = self.body(name)
        if body is not None:
            return body.axis
        return self.frame(name).mid, (0.0, 0.0, 1.0)

    def inner(self, name: str, faces: tuple[str, ...] = ()) -> Box:
        """The frame of X's inside. `faces`: the inner faces the caller is going to use;
        each must be known exactly (bodies.Body.inner_exact), or the line does not fit."""
        body = self.body(name)
        if body is None or body.inner is None:
            raise DoesNotFit(f"{name} has no inside here")
        for face in faces:
            if not body.inner_exact(face):
                raise DoesNotFit(f"{name}'s inner {face} is a sloping or round wall: its place "
                                 "is not resolved exactly, so it cannot be used")
        return body.inner

    def face_of(self, name: str, face: str) -> tuple[float, float]:
        """(where the face is along its axis, which way is towards X's middle from it)."""
        plain = face.removeprefix("inner ")
        box = self.inner(name, (plain,)) if face.startswith("inner ") else self.frame(name)
        _, side = sp.FACE[plain]
        return box.face(plain), -float(side)

    def value(self, size: Size) -> float:
        """A number, or a size copied from an earlier part (section 4)."""
        if size.source == "number":
            return float(size.value)
        factor = 1.0 if size.source == "same as" else SHARES[size.share]
        if size.dimension == "diameter":
            diameters = {body.standing.diameter for body in self.bodies(size.part)}
            if len(diameters) != 1 or None in diameters:
                raise DoesNotFit(f"{size.part} has no single diameter")
            return factor * diameters.pop()
        return factor * self.frame(size.part).size[AXIS_OF_DIRECTION[size.dimension]]
