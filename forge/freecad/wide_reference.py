"""The reference solid of a WIDE plan: a CadQuery program that follows the plan item by item.

Why a new reference. The composed generator (forge/generators/families/composed.py) keeps
every feature clear of every other, so its volume is closed-form arithmetic and its program
builds each feature against the bare base. A wide plan (wide_parts.py) lets features overlap,
puts edge treatments anywhere in the order and allows several of them, so there is no
arithmetic to compare with and the ORDER of the operations matters. The reference is
therefore built the way the plan reads: one operation per plan item, in plan order.

What "the same solid" then rests on. Two independent builds must agree:
    this file      CadQuery: one solid per feature, cut or joined, edge treatments by rule
    recipes.py     FreeCAD PartDesign: sketches, pads, pockets, holes, patterns, mirrors
Both sit on the same geometry kernel (OpenCASCADE) but share no modelling code. A plan is
kept only if the two give the same volume after EVERY item and the same bounding box at the
end (wide_parts.py, wide_prove.py); a recorded session ends with the same comparison.

What each plan item does here is what its recipe does in FreeCAD:
    cuts            are sketched on the base's top face (z = base height) and go DOWN; a hole
                    "through all" removes z = 0 .. base height, nothing above it
    boss, pad       stand on the floor: the base's top, or a shell's floor if the plan has one
    corner_radius   rounds every straight vertical edge the solid has at that moment
    top_chamfer,    treat every edge that lies flat at the very top of the solid at that
    top_fillet      moment (the rim of a hole in the top face included)
    shell           removes the flat faces at the very top and leaves walls, inwards
Cuts and joins are made without CadQuery's clean-up of the result (`clean=False`): that
clean-up sometimes never returns. The solid is cleaned once, right before an edge treatment,
which is the only place where merged faces matter.
    pairs           the feature and its mirror image across the YZ plane
The rules for "top" and "vertical" are forge/freecad/inside/rules.py, written again here in
CadQuery words (a test compares the two on real parts, tests/test_freecad_wide.py).

A stored program is plain CadQuery with named parameters, like every row of data/generated/.
"""

from __future__ import annotations

from forge.generators.base import num
from forge.generators.families import composed
from forge.system1.steps import FEATURES, STARTS, TREATMENTS, Step

# Smallest relative change of volume a plan item must make. An item that changes nothing
# (a pocket in thin air, a boss inside the material) is a plan nobody would write.
NO_CHANGE = 1e-9

# The helper functions every wide program starts with (plain text: they are part of the
# stored program, so that a row can be run by itself).
HELPERS = '''
FLAT = 1e-6     # mm; two heights closer than this are the same height


def _level(piece, z):
    box = piece.BoundingBox()
    return abs(box.zmin - z) < FLAT and abs(box.zmax - z) < FLAT


def top_edges(solid):
    """Every edge that lies flat at the very top of the solid."""
    top = solid.BoundingBox().zmax
    return [edge for edge in solid.Edges() if _level(edge, top)]


def vertical_edges(solid):
    """Every straight edge that runs parallel to Z."""
    found = []
    for edge in solid.Edges():
        if edge.geomType() == "LINE":
            a, b = edge.startPoint(), edge.endPoint()
            if abs(a.x - b.x) < FLAT and abs(a.y - b.y) < FLAT:
                found.append(edge)
    return found


def top_faces(solid):
    """The flat faces at the very top of the solid."""
    top = solid.BoundingBox().zmax
    return [face for face in solid.Faces() if face.geomType() == "PLANE" and _level(face, top)]


def treated(result, kind, value):
    """One edge treatment applied to the solid as it is now."""
    # Faces that lie in one plane are merged first, as FreeCAD's "refine" does after every
    # feature: otherwise the seam between two bosses of one height would count as an edge.
    solid = result.val().clean()
    if kind == "corner_radius":
        solid = solid.fillet(value, vertical_edges(solid))
    elif kind == "top_fillet":
        solid = solid.fillet(value, top_edges(solid))
    elif kind == "top_chamfer":
        solid = solid.chamfer(value, None, top_edges(solid))
    else:   # shell: walls grow inwards, sharp inside corners
        solid = solid.hollow(top_faces(solid), -value, kind="intersection")
    return cq.Workplane("XY").newObject([solid])
'''.strip()


def name_of(index: int, step: Step) -> str:
    """The parameter prefix of plan item `index` (0 is the base)."""
    return "base" if index == 0 else f"{step.kind}_{index}"


def step_params(index: int, step: Step) -> dict[str, float | int]:
    return {f"{name_of(index, step)}_{slot}": value for slot, value in step.slots.items()}


class Rejected(Exception):
    """A plan item the kernel cannot build into one valid solid, or that changes nothing."""


class Builder:
    """Builds a wide plan item by item with the CAD kernel, keeping the program text.

        builder = Builder(start_step)
        builder.add(step)        # raises Rejected and leaves the builder as it was
        builder.volumes          # the volume after every plan item so far
        builder.program()        # the whole thing as one CadQuery program

    `shell` is the plan item (index, step) of the plan's shell when it is known in advance:
    the recipes stand EVERY boss and pad of a plan with a shell on the shell's floor, also
    one that the plan lists before the shell, and the reference must do the same.
    """

    def __init__(self, start: Step, shell: tuple[int, Step] | None = None) -> None:
        import cadquery as cq  # slow to import; only processes that build need it

        if start.kind not in STARTS:
            raise ValueError("the first plan item must be a start")
        self.steps = [start]
        self.params: dict[str, float | int] = dict(step_params(0, start))
        self.base = composed.make_base(start.kind, {f"base_{slot}": value
                                                    for slot, value in start.slots.items()})
        self.body: list[str] = ["base = (", '    cq.Workplane("XY")',
                                *(f"    {call}" for call in self.base.build), ")",
                                "result = base"]
        self.space: dict = {"cq": cq}
        exec(HELPERS, self.space)  # noqa: S102 - our own generator text, not model output
        self._define(self.params)
        if shell is not None:
            self._shell(*shell)
            self._define(step_params(*shell))
        exec("\n".join(self.body), self.space)  # noqa: S102
        self.measures = [self._measure()]
        if not self.measures[0]["one_valid_solid"]:
            raise Rejected("the base is not one valid solid")

    @classmethod
    def of_plan(cls, plan: list[Step]) -> Builder:
        """Build a whole plan (raises Rejected at the first item that does not build)."""
        shells = [(index, step) for index, step in enumerate(plan) if step.kind == "shell"]
        builder = cls(plan[0], shells[0] if shells else None)
        for step in plan[1:]:
            builder.add(step)
        return builder

    @property
    def volumes(self) -> list[float]:
        return [measure["volume"] for measure in self.measures]

    def _define(self, params: dict) -> None:
        self.space.update(params)

    def _shell(self, index: int, step: Step) -> None:
        self.base.floor = f"{name_of(index, step)}_wall_thickness"
        self.base.shelled = True

    def _measure(self) -> dict:
        from forge.geometry import measure

        return measure(self.space["result"])

    def lines_of(self, index: int, step: Step) -> tuple[list[str], float | None]:
        """The program lines of one plan item (they update `result`), and the most volume the
        item can add (positive) or remove (negative); None for an edge treatment."""
        name = name_of(index, step)
        if step.kind in TREATMENTS:
            slot = TREATMENTS[step.kind][0]
            return [f'result = treated(result, "{step.kind}", {name}_{slot})'], None
        if step.kind not in FEATURES:
            raise ValueError(f"{step.kind} cannot follow a start")
        layout = "pair" if step.kind.endswith("_pair") else \
            {"polar": "polar", "row": "row"}.get(step.kind, "single")
        sizes = {slot: value for slot, value in step.slots.items()
                 if slot not in composed.PLACED_FIELDS[layout]}
        draft = composed.draft_of(step.kind, self.base, sizes)
        copies = 2 if layout == "pair" else step.slots.get("count", 1)
        solid = composed._solid(name, draft.calls)
        return ([*solid.split("\n"),
                 f"result = result.{'union' if draft.rise else 'cut'}({name}, clean=False)"],
                draft.volume * copies)

    def add(self, step: Step) -> None:
        index = len(self.steps)
        undo_shell = step.kind == "shell" and not self.base.shelled
        if undo_shell:
            self._shell(index, step)
        before = self.space["result"]
        self._define(step_params(index, step))
        try:
            lines, most = self.lines_of(index, step)
            exec("\n".join(lines), self.space)  # noqa: S102 - our own generator text
            measure = self._measure()
            volume = self.measures[-1]["volume"]
            change = measure["volume"] - volume
            if not measure["one_valid_solid"]:
                raise Rejected(f"{step.kind}: not one valid solid")
            if abs(change) <= NO_CHANGE * volume:
                raise Rejected(f"{step.kind}: it changes nothing")
            # The kernel sometimes returns a "valid" solid that is plainly wrong (a chamfer
            # that adds material, a cut that removes more than its tool). Arithmetic bounds
            # catch that: a treatment removes, a feature changes at most its own volume.
            if (most is None and change > 0) or \
                    (most is not None and (change * most < 0
                                           or abs(change) > abs(most) * (1 + 1e-6) + 1e-9)):
                raise Rejected(f"{step.kind}: the volume changed by more than the item can")
            if abs(measure["bbox_min"][2]) > 1e-6:
                raise Rejected(f"{step.kind}: the part no longer sits on the XY plane")
        except Exception as error:
            self.space["result"] = before
            for key in step_params(index, step):
                self.space.pop(key, None)
            if undo_shell:
                self.base.floor, self.base.shelled = "base_height", False
            if isinstance(error, Rejected):
                raise
            raise Rejected(f"{step.kind}: {type(error).__name__}") from None
        self.steps.append(step)
        self.params.update(step_params(index, step))
        self.body += lines
        self.measures.append(measure)

    def program(self) -> str:
        lines = ["import cadquery as cq", ""]
        lines += [f"{name} = {value if isinstance(value, int) else num6(value)}"
                  for name, value in self.params.items()]
        return "\n".join([*lines, "", "", HELPERS, "", "", *self.body, ""])


def num6(value: float) -> str:
    """A number as the plan holds it. Wide plans carry up to four decimals; `num` keeps four,
    and a value is written with more only if it has more (it never should)."""
    text = num(value)
    return text if float(text) == float(value) else repr(float(value))
