"""The ten shapes of PLAN_LANGUAGE section 4 as numbers: frame, outline and volume.

Every shape is described STANDING, with the middle of its frame at (0, 0, 0). Turning it
(lying, pointing) and moving it into place is done by bodies.py with a matrix and a
point. Three things are worked out here for a shape and its sizes:

    dims     the frame as it stands: (length, depth, height)
    hull     a few points, circles and ball pieces whose outline IS the shape's outline,
             so the frame after any turn can be computed exactly (bodies.py)
    volume   by the textbook formula, to compare with what the CAD kernel measures

`DoesNotFit` is raised for sizes that cannot make the shape (a tube whose hole is wider
than the tube). The resolver turns it into the reply `rejected: does not fit`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from forge.resolve.space import TOL, Vec

Point2 = tuple[float, float]


class DoesNotFit(Exception):
    """A size or position is impossible. The text says why, for a person."""


@dataclass
class Standing:
    dims: Vec
    hull: list[tuple]                   # ("point", p) | ("circle", centre, normal, radius)
    #                                     | ("ball", centre, radius)
    #                                     | ("cap", ball centre, normal, ball radius, height)
    volume: float
    is_round: bool = False              # has an axis of its own (its local z)
    diameter: float | None = None       # what `same as X's diameter` copies
    axis_at: Vec = (0.0, 0.0, 0.0)      # where that axis crosses, from the frame's middle
    outline: list[Point2] = field(default_factory=list)      # prism and bar: the profile
    corners: list[Vec] = field(default_factory=list)         # wedge and tapered box
    faces: list[list[int]] = field(default_factory=list)     # ... and their flat faces
    flat_faces: frozenset[Vec] = frozenset()   # local directions whose frame face is all solid
    planes: frozenset[Vec] = frozenset()       # local directions that end in SOME flat face


_UP: Vec = (0.0, 0.0, 1.0)
_ALL_SIX = frozenset({(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0),
                      (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)})
_ENDS = frozenset({(0.0, 0.0, 1.0), (0.0, 0.0, -1.0)})


def _positive(sizes: dict[str, float], zero_ok: tuple[str, ...] = ()) -> None:
    for slot, value in sizes.items():
        if value < -TOL or (value <= TOL and slot not in zero_ok):
            raise DoesNotFit(f"{slot} would be {value:g}")


def _box_points(x: float, y: float, z: float) -> list[tuple]:
    return [("point", (sx * x / 2, sy * y / 2, sz * z / 2))
            for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]


def _two_circles(bottom_radius: float, top_radius: float, height: float) -> list[tuple]:
    return [("circle", (0.0, 0.0, -height / 2), _UP, bottom_radius),
            ("circle", (0.0, 0.0, height / 2), _UP, top_radius)]


def _prism(sizes: dict[str, float]) -> Standing:
    n, across, height = int(sizes["sides"]), sizes["across"], sizes["height"]
    # apothem: middle to a flat side; corner: middle to a corner.
    # Even number of sides: across = flat to flat = 2 apothems.
    # Odd: across = flat to the opposite corner = apothem + corner radius.
    stretch = 1.0 / math.cos(math.pi / n)
    apothem = across / 2 if n % 2 == 0 else across / (1.0 + stretch)
    corner = apothem * stretch
    # One flat side faces the front (-y): its two corners sit either side of -90 degrees.
    angles = [math.radians(-90.0 + 180.0 / n + 360.0 * k / n) for k in range(n)]
    points = [(corner * math.cos(a), corner * math.sin(a)) for a in angles]
    low_y, high_y = min(p[1] for p in points), max(p[1] for p in points)
    shift = (low_y + high_y) / 2            # not zero for an odd prism
    outline = [(x, y - shift) for x, y in points]
    width = 2 * max(x for x, _ in outline)
    hull = [("point", (x, y, z)) for x, y in outline for z in (-height / 2, height / 2)]
    return Standing((width, high_y - low_y, height), hull,
                    n * apothem * apothem * math.tan(math.pi / n) * height, is_round=True,
                    axis_at=(0.0, -shift, 0.0), outline=outline, flat_faces=_ENDS, planes=_ENDS)


def _bar_outline(profile: str, w: float, d: float, t: float) -> tuple[list[Point2], float]:
    """The profile seen from the end (x: profile width, y: profile depth) and its area."""
    l, r, f, b = -w / 2, w / 2, -d / 2, d / 2        # left, right, front, back
    if profile == "l":       # corner at the back left
        need = t < w and t < d
        points = [(l, f), (l + t, f), (l + t, b - t), (r, b - t), (r, b), (l, b)]
        area = t * (w + d - t)
    elif profile == "t":     # the bar across the back, the stem running to the front
        need = t < w and t < d
        points = [(-t / 2, f), (t / 2, f), (t / 2, b - t), (r, b - t), (r, b), (l, b),
                  (l, b - t), (-t / 2, b - t)]
        area = w * t + t * (d - t)
    elif profile == "u":     # opens to the front
        need = 2 * t < w and t < d
        points = [(l, f), (l + t, f), (l + t, b - t), (r - t, b - t), (r - t, f), (r, f),
                  (r, b), (l, b)]
        area = w * t + 2 * t * (d - t)
    else:                    # "i": a bar at the front and one at the back, joined by a web
        need = t < w and 2 * t < d
        points = [(l, f), (r, f), (r, f + t), (t / 2, f + t), (t / 2, b - t), (r, b - t),
                  (r, b), (l, b), (l, b - t), (-t / 2, b - t), (-t / 2, f + t), (l, f + t)]
        area = 2 * w * t + t * (d - 2 * t)
    if not need:
        raise DoesNotFit(f"thickness {t:g} is too large for a {w:g} by {d:g} profile")
    return points, area


def _wedge(sizes: dict[str, float], pointing: str, flat: str) -> Standing:
    """A box cut in half along a slope. The sizes are its frame as it sits (never turned)."""
    from forge.plan.grammar import FACE_POINTED_AT
    from forge.resolve.space import FACE

    half = (sizes["length"] / 2, sizes["depth"] / 2, sizes["height"] / 2)
    p_axis, p_side = FACE[FACE_POINTED_AT[pointing]]
    f_axis, f_side = FACE[flat]
    w_axis = 3 - p_axis - f_axis

    def corner(p: int, f: int, w: int) -> Vec:
        point = [0.0, 0.0, 0.0]
        point[p_axis], point[f_axis], point[w_axis] = p * half[p_axis], f * half[f_axis], \
            w * half[w_axis]
        return tuple(point)

    # The triangle: the sharp edge (pointing side, flat side), then along the flat face to
    # the whole face opposite the pointing side, then up that face to its far edge.
    triangle = [(p_side, f_side), (-p_side, f_side), (-p_side, -f_side)]
    corners = [corner(p, f, w) for w in (-1, 1) for p, f in triangle]
    faces = [[0, 1, 2], [3, 4, 5], [0, 1, 4, 3], [1, 2, 5, 4], [0, 2, 5, 3]]
    opposite = tuple(-p_side if k == p_axis else 0.0 for k in range(3))
    flat_dir = tuple(float(f_side) if k == f_axis else 0.0 for k in range(3))
    # The two triangular ends are flat too, but only half of their frame face is solid,
    # so they are not listed as flat faces (a boss there could hang in the air).
    return Standing(tuple(2 * h for h in half), [("point", c) for c in corners],
                    sizes["length"] * sizes["depth"] * sizes["height"] / 2,
                    corners=corners, faces=faces, flat_faces=frozenset({opposite, flat_dir}),
                    planes=frozenset({opposite, flat_dir}))


def _tapered_box(sizes: dict[str, float]) -> Standing:
    bl, bd, tl, td, h = (sizes[s] for s in ("bottom length", "bottom depth", "top length",
                                            "top depth", "height"))
    ring = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    corners = [(sx * bl / 2, sy * bd / 2, -h / 2) for sx, sy in ring] \
        + [(sx * tl / 2, sy * td / 2, h / 2) for sx, sy in ring]
    faces = [[3, 2, 1, 0], [4, 5, 6, 7]] + [[k, (k + 1) % 4, 4 + (k + 1) % 4, 4 + k]
                                            for k in range(4)]
    middle = ((bl + tl) / 2) * ((bd + td) / 2)
    # Prismatoid rule: exact for a solid with flat sides between two parallel ends.
    volume = h / 6 * (bl * bd + tl * td + 4 * middle)
    flat = {(0.0, 0.0, -1.0)} if bl >= tl and bd >= td else set()
    if tl >= bl and td >= bd:
        flat.add((0.0, 0.0, 1.0))
    ends = {(0.0, 0.0, -1.0)} | ({(0.0, 0.0, 1.0)} if tl > TOL and td > TOL else set())
    return Standing((max(bl, tl), max(bd, td), h), [("point", c) for c in corners], volume,
                    corners=corners, faces=faces, flat_faces=frozenset(flat),
                    planes=frozenset(ends))


def describe(shape: str, profile: str | None, sizes: dict[str, float], pointing: str = "up",
             flat: str | None = None) -> Standing:
    """The standing shape for these sizes. Raises DoesNotFit when they cannot make it."""
    zero_ok = ("top diameter", "top length", "top depth")
    _positive(sizes, zero_ok)
    if shape == "box":
        x, y, z = sizes["length"], sizes["depth"], sizes["height"]
        return Standing((x, y, z), _box_points(x, y, z), x * y * z, flat_faces=_ALL_SIX,
                        planes=_ALL_SIX)
    if shape == "cylinder":
        d, h = sizes["diameter"], sizes["height"]
        return Standing((d, d, h), _two_circles(d / 2, d / 2, h), math.pi * d * d / 4 * h,
                        is_round=True, diameter=d, flat_faces=_ENDS, planes=_ENDS)
    if shape == "tube" or (shape == "bar" and profile == "tube"):
        if shape == "tube":
            outer, inner, h = sizes["outer diameter"], sizes["inner diameter"], sizes["height"]
        else:
            outer, h = sizes["outer diameter"], sizes["length"]
            inner = outer - 2 * sizes["wall"]
        if inner <= TOL or inner >= outer - TOL:
            raise DoesNotFit(f"a tube {outer:g} wide cannot have a hole {inner:g} wide")
        return Standing((outer, outer, h), _two_circles(outer / 2, outer / 2, h),
                        math.pi * (outer * outer - inner * inner) / 4 * h, is_round=True,
                        diameter=outer, planes=_ENDS)
    if shape == "cone":
        low, top, h = sizes["bottom diameter"], sizes["top diameter"], sizes["height"]
        wide = max(low, top)
        flat_ends = {(0.0, 0.0, -1.0)} if low >= top else {(0.0, 0.0, 1.0)}
        ends = {(0.0, 0.0, -1.0)} if low > TOL else set()
        if top > TOL:
            ends.add((0.0, 0.0, 1.0))
        return Standing((wide, wide, h), _two_circles(low / 2, top / 2, h),
                        math.pi * h / 12 * (low * low + low * top + top * top), is_round=True,
                        diameter=wide, flat_faces=frozenset(flat_ends), planes=frozenset(ends))
    if shape == "sphere":
        d = sizes["diameter"]
        return Standing((d, d, d), [("ball", (0.0, 0.0, 0.0), d / 2)], math.pi * d ** 3 / 6,
                        is_round=True, diameter=d)
    if shape == "dome":
        d, h = sizes["diameter"], sizes["height"]
        if h > d / 2 + TOL:
            raise DoesNotFit(f"a dome {d:g} wide is at most {d / 2:g} high; {h:g} written")
        ball = (d * d / 4 + h * h) / (2 * h)        # radius of the ball the dome is cut from
        hull = [("circle", (0.0, 0.0, -h / 2), _UP, d / 2),
                ("cap", (0.0, 0.0, h / 2 - ball), _UP, ball, h)]
        return Standing((d, d, h), hull, math.pi * h * h * (3 * ball - h) / 3, is_round=True,
                        diameter=d, flat_faces=frozenset({(0.0, 0.0, -1.0)}),
                        planes=frozenset({(0.0, 0.0, -1.0)}))
    if shape == "prism":
        return _prism(sizes)
    if shape == "wedge":
        from forge.plan.grammar import WEDGE_DEFAULT_FLAT

        return _wedge(sizes, pointing, flat or WEDGE_DEFAULT_FLAT[pointing])
    if shape == "tapered box":
        return _tapered_box(sizes)
    if shape == "bar":
        w, d, t, run = (sizes[s] for s in ("profile width", "profile depth", "thickness",
                                           "length"))
        outline, area = _bar_outline(profile, w, d, t)
        hull = [("point", (x, y, z)) for x, y in outline for z in (-run / 2, run / 2)]
        return Standing((w, d, run), hull, area * run, outline=outline, planes=_ENDS)
    raise DoesNotFit(f"unknown shape {shape}")


def frame_dims(shape: str, profile: str | None, sizes: dict[str, float | None]) -> list:
    """The standing frame for sizes of which some are not known yet (None stays None).

    Only the sizes that may be written `rest` can be unknown, and each of those is one
    whole side of the frame, so the others can be worked out without it.
    """
    get = sizes.get
    if shape in ("box", "wedge"):
        return [get("length"), get("depth"), get("height")]
    if shape == "bar":
        if profile == "tube":
            return [get("outer diameter"), get("outer diameter"), get("length")]
        return [get("profile width"), get("profile depth"), get("length")]
    if shape == "tapered box":
        return [max(get("bottom length"), get("top length")),
                max(get("bottom depth"), get("top depth")), get("height")]
    if shape == "prism":
        known = dict(sizes, height=1.0)
        x, y, _ = _prism(known).dims
        return [x, y, get("height")]
    if shape == "sphere":
        return [get("diameter")] * 3
    wide = {"cylinder": get("diameter"), "tube": get("outer diameter"), "dome": get("diameter"),
            "cone": max(get("bottom diameter") or 0.0, get("top diameter") or 0.0)}[shape]
    return [wide, wide, get("height")]


# Which side of the standing frame each `rest`-able size is (grammar.REST_SLOTS).
REST_AXIS = {"length": 0, "depth": 1, "height": 2}


def rest_axis(shape: str, slot: str) -> int:
    """The local axis a size that may be `rest` runs along."""
    if shape in ("box", "wedge"):
        return REST_AXIS[slot]
    return 2            # a height, or a bar's length: always the standing shape's z
