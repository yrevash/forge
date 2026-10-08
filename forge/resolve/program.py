"""Write the CadQuery program that builds a list of bodies: the reference builder.

Each body becomes one named solid: its sizes as named parameters, the standing shape
built around (0, 0, 0), its features cut or added, then turned and moved to its place.
`result` is a cq.Assembly with one named entry per body.

This file only WRITES program text. The text is run by forge.sandbox, never here
(verify.py and judge.py do that), so the kernel's answer comes from code that shares
no arithmetic with the resolver's frames and volumes.
"""

from __future__ import annotations

import re

from forge.resolve import space as sp
from forge.resolve.bodies import Body, Cut

HEADER = "import cadquery as cq\n"

# Only written into programs that hold a wedge or a tapered box.
POLYHEDRON = '''

def polyhedron(points, faces):
    """A solid with flat faces: its corners, and which corners go round each face."""
    made = [cq.Face.makeFromWires(cq.Wire.makePolygon([cq.Vector(*points[i]) for i in face],
                                                      close=True)) for face in faces]
    solid = cq.Solid.makeSolid(cq.Shell.makeShell(made))
    if solid.Volume() < 0:          # the faces were listed facing inward: turn it inside out
        solid = cq.Shape.cast(solid.wrapped.Reversed())
    return cq.Workplane("XY").add(solid)
'''


def _n(value: float) -> str:
    """A number as short as it can be written without losing anything."""
    if abs(value) < 5e-13:
        return "0.0"
    return repr(round(value, 12))


def _vec(v: tuple) -> str:
    return "(" + ", ".join(_n(x) for x in v) + ")"


def variable(name: str) -> str:
    made = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return made if made and not made[0].isdigit() else f"part_{made}"


def _shared(bodies: list[Body]) -> dict[str, str]:
    """body name -> the prefix of its parameters. Copies with the same sizes share one set."""
    by_owner: dict[str, list[Body]] = {}
    for body in bodies:
        by_owner.setdefault(body.owner, []).append(body)
    prefixes = {}
    for owner, group in by_owner.items():
        def key(b: Body) -> tuple:
            return (b.shape, b.profile, tuple(b.sizes.items()),
                    tuple((c.kind, tuple(c.slots.items())) for c in b.cuts))
        same = all(key(b) == key(group[0]) for b in group)
        for body in group:
            prefixes[body.name] = variable(owner) if same else variable(body.name)
    return prefixes


def _clean_faces(corners: list, faces: list[list[int]]) -> list[list[int]]:
    """Drop repeated corners (a tapered box whose top is a point) and faces that vanish."""
    kept = []
    for face in faces:
        ring: list[int] = []
        for index in face:
            if all(sp.length(sp.sub(corners[index], corners[other])) > 1e-9 for other in ring):
                ring.append(index)
        if len(ring) >= 3:
            kept.append(ring)
    return kept


def _standing_shape(body: Body, p: str) -> str:
    """The expression for the standing shape, frame middle at (0, 0, 0)."""
    shape, standing = body.shape, body.standing
    work = 'cq.Workplane("XY")'
    if shape == "box":
        return f"{work}.box({p}_length, {p}_depth, {p}_height)"
    if shape == "cylinder":
        return f"{work}.cylinder({p}_height, {p}_diameter / 2)"
    if shape == "tube":
        return (f"{work}.circle({p}_outer_diameter / 2).circle({p}_inner_diameter / 2)"
                f".extrude({p}_height).translate((0, 0, -{p}_height / 2))")
    if shape == "bar" and body.profile == "tube":
        return (f"{work}.circle({p}_outer_diameter / 2).circle({p}_outer_diameter / 2 - {p}_wall)"
                f".extrude({p}_length).translate((0, 0, -{p}_length / 2))")
    if shape == "cone":
        if abs(body.sizes["bottom diameter"] - body.sizes["top diameter"]) < 1e-9:
            # Both ends the same: the kernel refuses such a "cone"; it is a cylinder.
            return f"{work}.cylinder({p}_height, {p}_bottom_diameter / 2)"
        return (f"{work}.add(cq.Solid.makeCone({p}_bottom_diameter / 2, {p}_top_diameter / 2, "
                f"{p}_height)).translate((0, 0, -{p}_height / 2))")
    if shape == "sphere":
        return f"{work}.sphere({p}_diameter / 2)"
    if shape == "dome":
        # The top `height` of a ball: the ball, kept only above the dome's flat base.
        d, h = body.sizes["diameter"], body.sizes["height"]
        ball = (d * d / 4 + h * h) / (2 * h)
        return (f"{work}.sphere({_n(ball)}).translate((0, 0, {p}_height / 2 - {_n(ball)}))"
                f".intersect({work}.box(2 * {p}_diameter, 2 * {p}_diameter, {p}_height))")
    if shape in ("prism", "bar"):
        run = f"{p}_height" if shape == "prism" else f"{p}_length"
        points = "[" + ", ".join(f"({_n(x)}, {_n(y)})" for x, y in standing.outline) + "]"
        return f"{work}.polyline({points}).close().extrude({run}).translate((0, 0, -{run} / 2))"
    # wedge, tapered box
    corners = "[" + ", ".join(_vec(c) for c in standing.corners) + "]"
    return f"polyhedron({corners}, {_clean_faces(standing.corners, standing.faces)})"


def _selector(normal: tuple) -> tuple[str, str]:
    """(the face selector, the letter of its axis) for a local direction square to the axes."""
    axis = max(range(3), key=lambda k: abs(normal[k]))
    return (">" if normal[axis] > 0 else "<") + "XYZ"[axis], "XYZ"[axis]


def _cut_lines(var: str, body: Body, cut: Cut, p: str) -> list[str]:
    """The statements that apply one feature to the standing shape."""
    slot = {name: f"{p}_{name.replace(' ', '_')}" for name in cut.slots}
    spots = "[" + ", ".join(f"({_n(u)}, {_n(v * cut.second_sign)})" for u, v in cut.spots) + "]"
    plane = (f"cq.Workplane(cq.Plane(origin={_vec(cut.origin)}, xDir={_vec(cut.first)}, "
             f"normal={_vec(cut.normal)})).pushPoints({spots})")
    deep = _n(-cut.through)
    kind = cut.kind

    def cut_away(sketch: str, depth: str) -> str:
        return f"{var} = {var}.cut({plane}.{sketch}.extrude({depth}))"

    def add_on(sketch: str, height: str) -> str:
        return f"{var} = {var}.union({plane}.{sketch}.extrude({height}))"

    if kind in ("hole", "hole_pair"):
        return [cut_away(f"circle({slot['diameter']} / 2)", deep)]
    if kind in ("polar", "row"):
        return [cut_away(f"circle({slot['hole diameter']} / 2)", deep)]
    if kind == "blind_hole":
        return [cut_away(f"circle({slot['diameter']} / 2)", f"-{slot['depth']}")]
    if kind == "counterbore":
        return [cut_away(f"circle({slot['hole diameter']} / 2)", deep),
                cut_away(f"circle({slot['diameter']} / 2)", f"-{slot['depth']}")]
    if kind in ("boss", "boss_pair"):
        return [add_on(f"circle({slot['diameter']} / 2)", slot["height"])]
    if kind == "pad":
        return [add_on(f"rect({slot['length']}, {slot['width']})", slot["height"])]
    if kind in ("pocket", "pocket_pair"):
        return [cut_away(f"rect({slot['length']}, {slot['width']})", f"-{slot['depth']}")]
    if kind == "slot":
        # The plane's own second direction may be the mirror of the face's: turn the other way.
        turn = f"{slot['angle']} * {_n(cut.second_sign)}"
        return [cut_away(f"slot2D({slot['length']}, {slot['width']}, {turn})",
                         f"-{slot['depth']}")]
    face, letter = _selector(cut.normal)
    if kind == "corner_radius":
        return [f'{var} = {var}.edges("|{letter}").fillet({slot["radius"]})']
    if kind == "top_chamfer":
        return [f'{var} = {var}.faces("{face}").edges().chamfer({slot["size"]})']
    if kind == "top_fillet":
        return [f'{var} = {var}.faces("{face}").edges().fillet({slot["radius"]})']
    # shell: take the inside away, leaving walls of `wall`; the open face loses its wall.
    wall = slot["wall"]
    if body.shape not in ("box", "cylinder") or body.cuts[0] is not cut:
        # Sloping, round or already rounded walls: let the kernel offset the surface inward.
        chosen = f'.faces("{_selector(cut.open_normal)[0]}")' if cut.open_normal else ""
        return [f"{var} = {var}{chosen}.shell(-{wall})"]
    opened = cut.open_normal or (0.0, 0.0, 0.0)
    dims = body.standing.dims
    inner = [f"{_n(dims[k])} - {wall}" + ("" if opened[k] else " * 2") for k in range(3)]
    shift = "(" + ", ".join(f"{_n(opened[k] / 2)} * {wall}" for k in range(3)) + ")"
    if body.shape == "cylinder":
        tool = f'cq.Workplane("XY").cylinder({inner[2]}, ({_n(dims[0])} - {wall} * 2) / 2)'
    else:
        tool = f'cq.Workplane("XY").box({", ".join(inner)})'
    return [f"{var} = {var}.cut({tool}.translate({shift}))"]


def _place_lines(var: str, body: Body) -> list[str]:
    """Mirror (for a mirrored copy), turn, then move to the body's place."""
    lines = []
    turn = body.matrix
    if sp.determinant(turn) < 0:
        lines.append(f'{var} = {var}.mirror("YZ")')
        turn = sp.multiply(turn, sp.mirror(0))      # what is left is a pure turn
    axis, degrees = sp.axis_and_angle(turn)
    if degrees:
        lines.append(f"{var} = {var}.rotate((0, 0, 0), {_vec(axis)}, {_n(degrees)})")
    lines.append(f"{var} = {var}.translate({_vec(body.centre)})")
    return lines


def _export_lines(var: str, body: Body, index: int, stage: int) -> list[str]:
    """Write the body as it is after `stage` features, in its place, for the build pictures."""
    staged = f"{var}_stage"
    return [f"{staged} = {var}", *_place_lines(staged, body),
            f'{staged}.val().exportBrep("body_{index}_{stage}.brep")']


def write_program(bodies: list[Body], comments: dict[str, str] | None = None,
                  export: bool = False) -> str:
    """The program for these bodies. `comments`: owner name -> the plan line that made it.

    With `export`, the program also writes the assembly as STEP and every body, after each
    of its features, as a BREP file into the working directory (build.py copies them out).
    """
    comments = comments or {}
    prefixes = _shared(bodies)
    needs_polyhedron = any(b.shape in ("wedge", "tapered box") for b in bodies)
    out = [HEADER + (POLYHEDRON if needs_polyhedron else "")]
    written: set[str] = set()
    commented: set[str] = set()
    names = {}
    for index, body in enumerate(bodies):
        var, p = variable(body.name), prefixes[body.name]
        if var in names.values():
            var = f"{var}_{index}"
        names[body.name] = var
        out.append("")
        if body.owner not in commented and body.owner in comments:
            out.append(f"# {comments[body.owner]}")
            commented.add(body.owner)
        if p not in written:            # the named parameters, once per set of equal copies
            written.add(p)
            for slot, value in body.sizes.items():
                out.append(f"{p}_{slot.replace(' ', '_')} = {_n(value)}")
            for number, cut in enumerate(body.cuts, start=1):
                for slot, value in cut.slots.items():
                    out.append(f"{p}_{cut.kind}_{number}_{slot.replace(' ', '_')} = {_n(value)}")
        out.append(f"{var} = {_standing_shape(body, p)}")
        if export:
            out += _export_lines(var, body, index, 0)
        for number, cut in enumerate(body.cuts, start=1):
            out += _cut_lines(var, body, cut, f"{p}_{cut.kind}_{number}")
            if export:
                out += _export_lines(var, body, index, number)
        out += _place_lines(var, body)
    out += ["", "result = cq.Assembly()"]
    out += [f"result.add({names[body.name]}, name={body.name!r})" for body in bodies]
    if export:
        out.append('result.export("assembly.step")')
    return "\n".join(out) + "\n"
