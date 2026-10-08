"""Small random plans from the vocabulary, for the property test (property_test.py).

These are TEST INPUTS, made by this code from a seed. They are not training data and
are never written into a dataset. The generator knows no geometry: it writes lines
that are in the language and lets the resolver say `built` or `rejected`. It is tuned
so that a good share of lines collide or float (a second part on the same face, an
offset along the touching direction), because the test needs rejections as much as
acceptances.

Most parts are boxes and cylinders, standing or lying, because those are the ones
whose overlap and contact the resolver decides by arithmetic; the other shapes are
mixed in so the kernel path is exercised too.
"""

from __future__ import annotations

import random

SIZES = (10, 15, 20, 30, 40, 50, 60, 80, 100, 120)
SMALL = (5, 10, 15, 20, 30)
FACE_PLACEMENTS = ("on top of", "under", "in front of", "behind", "left of", "right of")
SIDE_FACES = {"on top of": ("left", "right", "front", "back"),
              "under": ("left", "right", "front", "back"),
              "in front of": ("left", "right", "top", "bottom"),
              "behind": ("left", "right", "top", "bottom"),
              "left of": ("front", "back", "top", "bottom"),
              "right of": ("front", "back", "top", "bottom")}
FACE_DIRECTIONS = {"on top of": ("length", "depth"), "under": ("length", "depth"),
                   "in front of": ("length", "height"), "behind": ("length", "height"),
                   "left of": ("depth", "height"), "right of": ("depth", "height")}


def _shape(rng: random.Random) -> str:
    """A shape with its sizes and turning words."""
    def s() -> int:
        return rng.choice(SIZES)

    roll = rng.random()
    if roll < 0.55:
        lying = rng.choice(["", "", "", " lying along length", " lying along depth"])
        return f"box {s()} by {s()} by {s()}{lying}"
    if roll < 0.85:
        lying = rng.choice(["", "", " lying along length", " lying along depth"])
        return f"cylinder {s()} by {s()}{lying}"
    other = rng.choice(["cone", "sphere", "dome", "wedge", "tube", "prism", "tapered box"])
    if other == "cone":
        return f"cone {s()} by {rng.choice((0, 10, 20, 60))} by {s()}"
    if other == "sphere":
        return f"sphere {s()}"
    if other == "dome":
        wide = s()
        return f"dome {wide * 2} by {rng.choice((wide // 2, wide))}"
    if other == "wedge":
        way = rng.choice(["", " pointing left", " pointing right", " pointing front",
                          " pointing back", " pointing down"])
        return f"wedge {s()} by {s()} by {s()}{way}"
    if other == "tube":
        wide = s()
        return f"tube {wide + 20} by {wide} by {s()}"
    if other == "prism":
        return f"prism {rng.choice((3, 5, 6, 8))} by {s()} by {s()}"
    return f"tapered box {s() + 40} by {s() + 40} by {rng.choice((0, 20, 40))} by 20 by {s()}"


def _alignments(rng: random.Random, placement: str, target: str, names: list[str]) -> list[str]:
    pieces = []
    other = rng.choice(names)
    for _ in range(rng.choice((0, 0, 1, 1, 2))):
        roll = rng.random()
        side = rng.choice(SIDE_FACES[placement])
        if roll < 0.40:
            pieces.append(f"flush with {target}'s {side}")
            if rng.random() < 0.3:
                pieces.append(f"inset {rng.choice(SMALL)}")
        elif roll < 0.60:
            relation = (rng.choice(("above", "below")) if side in ("top", "bottom")
                        else rng.choice(("inside", "beyond")))
            pieces.append(f"{side} {rng.choice(SMALL)} {relation} {other}'s {side}")
        elif roll < 0.90:
            # Any of the six ways: along the touching direction this makes it float or overlap.
            way = rng.choice(("left", "right", "front", "back", "top", "bottom"))
            pieces.append(f"offset {rng.choice(SMALL)} to the {way}")
        else:
            pieces.append(f"on the same axis as {other}")
    return pieces


def _repetition(rng: random.Random, placement: str, target: str) -> str | None:
    roll = rng.random()
    first, second = FACE_DIRECTIONS[placement]
    flat = placement in ("on top of", "under")
    if roll < 0.70:
        return None
    if roll < 0.76:
        return f"at each corner of {target}"
    if roll < 0.80:
        return f"at the {rng.choice(('front', 'back', 'left', 'right'))} corners of {target}"
    if roll < 0.86:
        return f"{rng.randint(2, 4)} evenly spaced along {rng.choice((first, second))}"
    if roll < 0.90:
        return f"{rng.randint(2, 3)} spread along {rng.choice((first, second))}"
    if roll < 0.93:
        return f"grid {rng.randint(1, 3)} by {rng.randint(1, 2)}"
    if roll < 0.97:
        return f"mirrored {rng.choice(('left-right', 'front-back'))}"
    if flat:
        outward = rng.choice(("", " facing outward"))
        return f"{rng.randint(2, 6)} around {target} on circle {rng.choice(SIZES)}{outward}"
    return f"{rng.randint(2, 4)} around {target}"


def random_plan(seed: int) -> str:
    """One plan of four to eleven lines, ending with `done`.

    It starts with a slab held 150 up on a post, so that parts placed beside or under it
    have room: on a slab lying on the ground most of them would poke below the ground
    and be refused before overlap or contact is ever looked at.
    """
    rng = random.Random(seed)
    wide = (80, 100, 120, 200)
    slab = (f"p0: box {rng.choice(wide)} by {rng.choice(wide)} by {rng.choice((10, 20, 40))}, "
            "above ground, bottom at height 150")
    lines = [slab, "p1: box 20 by 20 by rest, under p0, down to ground"]
    names = ["p0", "p1"]
    for index in range(2, rng.randint(4, 10)):
        name = f"p{index}"
        roll = rng.random()
        if roll < 0.04 and len(names) > 2:
            lines.append("undo")
            names.pop()
            continue
        # Half the time the slab: a part placed on a rejected part is only `unknown part`.
        target = "p0" if rng.random() < 0.5 else rng.choice(names)
        if roll < 0.10:
            lines.append(f"{name}: {_shape(rng)}, on ground, offset {rng.choice(SIZES) * 2} to the "
                         f"{rng.choice(('left', 'right', 'front', 'back'))}")
        elif roll < 0.17:
            first, second = rng.sample(names, 2)
            slot = rng.choice(("rest by 20 by 20", "20 by rest by 20", "20 by 20 by rest"))
            lines.append(f"{name}: box {slot}, between {first} and {second}")
        elif roll < 0.22:
            lines.append(f"{name}: box 20 by 20 by rest, under {target}, down to ground, "
                         f"flush with {target}'s {rng.choice(('left', 'right', 'front', 'back'))}")
        else:
            placement = rng.choice(FACE_PLACEMENTS)
            pieces = [f"{name}: {_shape(rng)}", f"{placement} {target}"]
            option = rng.random()
            if option < 0.05:
                pieces.append(f"gap {rng.choice(SMALL)}")
            elif option < 0.10:
                pieces.append(f"sunk {rng.choice((2, 5))} into {target}")
            pieces += _alignments(rng, placement, target, names)
            repetition = _repetition(rng, placement, target)
            if repetition:
                pieces.append(repetition)
            lines.append(", ".join(pieces))
        names.append(name)
    return "\n".join([*lines, "done"]) + "\n"
