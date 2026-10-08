"""Probe points: is every feature WHERE it should be, not only the right size?

Volume and bounding box cannot tell a hole at x = +10 from a hole at x = -10, or
a slot along X from the same slot along Y. So for every feature we also pick a
few points whose answer is known ("the middle of this hole is empty", "just
outside its rim there is material") and ask FreeCAD whether each one is inside
the solid. The positions come from the reference generator's own `spots_of`, so
this checks FreeCAD against our CadQuery engine, not against itself.

Features keep at least 2 mm (MIN_WALL) from each other and from every edge, so
a point 0.2 mm outside a feature is always in plain base material (or, for a
boss, in plain air).
"""

from __future__ import annotations

from forge.freecad.recipes import Context, context_of
from forge.generators.families import composed
from forge.system1.engine import layout_of
from forge.system1.steps import FEATURES, Step

NUDGE = 0.2     # mm; how far a probe sits from the surface it tests
Probe = tuple[list[float], bool, str]   # (point, should it be inside the solid?, what it tests)


def _one(kind: str, s: dict, x: float, y: float, c: Context) -> list[Probe]:
    top = c.height
    out: list[Probe] = []

    def add(px: float, py: float, pz: float, inside: bool, what: str) -> None:
        out.append(([px, py, pz], inside, f"{kind} at ({x:g}, {y:g}): {what}"))

    if kind in ("hole", "polar", "row"):
        radius = s["diameter" if kind == "hole" else "hole_diameter"] / 2
        add(x, y, NUDGE, False, "open at the bottom")
        add(x, y, top - NUDGE, False, "open at the top")
        add(x + radius + NUDGE, y, top / 2, True, "material outside the rim")
    elif kind == "blind_hole":
        add(x, y, top - s["depth"] + NUDGE, False, "empty above the floor")
        add(x, y, top - s["depth"] - NUDGE, True, "floor below the depth")
        add(x + s["diameter"] / 2 + NUDGE, y, top - NUDGE, True, "material outside the rim")
    elif kind == "counterbore":
        ring = (s["hole_diameter"] + s["diameter"]) / 4     # between the hole and the recess
        add(x, y, NUDGE, False, "hole open at the bottom")
        add(x + ring, y, top - s["depth"] + NUDGE, False, "recess empty")
        add(x + ring, y, top - s["depth"] - NUDGE, True, "shoulder under the recess")
        add(x + s["diameter"] / 2 + NUDGE, y, top - NUDGE, True, "material outside the recess")
    elif kind == "boss":
        z = c.floor + s["height"]
        add(x, y, z - NUDGE, True, "solid up to its height")
        add(x, y, z + NUDGE, False, "nothing above it")
        add(x + s["diameter"] / 2 + NUDGE, y, z - NUDGE, False, "nothing beside it")
    elif kind in ("pad", "pocket"):
        hx, hy = s["length"] / 2, s["width"] / 2
        raised = kind == "pad"
        z = c.floor + s["height"] - NUDGE if raised else top - s["depth"] + NUDGE
        for sx in (-1, 1):
            for sy in (-1, 1):
                add(x + sx * (hx - NUDGE), y + sy * (hy - NUDGE), z, raised, "corner")
            add(x + sx * (hx + NUDGE), y, z, not raised, "just past its length")
        add(x, y, z + (2 if raised else -2) * NUDGE, not raised, "its height or depth")
    elif kind == "slot":
        along = (1.0, 0.0) if s["angle"] == 0 else (0.0, 1.0)
        across = (along[1], along[0])
        z = top - s["depth"] + NUDGE
        for sign in (-1, 1):
            reach = sign * (s["length"] / 2 - NUDGE)
            add(x + along[0] * reach, y + along[1] * reach, z, False, "round end")
            past = sign * (s["length"] / 2 + NUDGE)
            add(x + along[0] * past, y + along[1] * past, z, True, "just past its end")
            side = sign * (s["width"] / 2 + NUDGE)
            add(x + across[0] * side, y + across[1] * side, z, True, "just past its width")
        add(x, y, z - 2 * NUDGE, True, "floor below the depth")
    return out


def probes_of(steps: list[Step]) -> list[Probe]:
    """Every probe for a part's features, in build order."""
    context = context_of(steps)
    probes: list[Probe] = []
    for step in steps:
        if step.kind not in FEATURES:
            continue
        spots = composed.spots_of(layout_of(step.kind), step.slots, step.slots)
        for x, y in spots:
            probes += _one(step.kind.removesuffix("_pair"), step.slots, x, y, context)
    return probes


def failed(probes: list[Probe], inside: list[bool]) -> list[str]:
    """What each wrong probe was testing."""
    return [f"probe {what}: expected {'inside' if want else 'outside'} at "
            f"({', '.join(f'{v:g}' for v in point)})"
            for (point, want, what), got in zip(probes, inside, strict=True) if got != want]

