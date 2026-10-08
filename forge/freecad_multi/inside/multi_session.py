"""The session for structures: the base Session plus bodies that are placed. Runs inside FreeCAD.

`MultiSession` IS the single-part session (forge/freecad/inside/session.py, unchanged):
it inherits `run`, the transactions, undo and the rebuild beyond FreeCAD's 20 steps. It adds

    handlers   one `_name` method per command of ../multi_catalogue.py
    a side     three base handlers learn to work from `select_side`: new_sketch, thickness,
               and the features made from a sketch (so they grow out of / cut into the face)
    snapshot   every body gets its number and its placement; primitives and sketches on
               sides are described; the whole structure is measured (structure.py)

Undo across bodies needs nothing new. `move_body`, `turn_body` and the primitives each run
in one FreeCAD transaction like every other command, and which body is active lives in
`meta`, which the base session already saves before every command and restores on undo.

Speed. The base session describes every object again after every command. With 100
bodies that is 100 solids measured per command, so this class keeps the entries of the
bodies a command cannot have touched: a command only ever changes the active body.
After an undo, an error or a new document nothing is kept.
"""

from __future__ import annotations

import contextlib
import copy
import math

import features
import FreeCAD
import multi_valid
import part_shapes
import sides
import snapshot as snap
import structure
from session import Rejected, Session


def _measure_or_void(shape, _measure=snap.measure) -> dict | None:
    """The base measurement, for shapes it cannot measure too.

    A feature with a number far too large (a wall of 570 mm in a 190 mm part) can leave a
    shape that has solids and no extent. The base `measure` asks for its exact bounding
    box and the kernel raises "Bnd_Box is void", so the command got no reply at all (found
    6 Oct 2026 in a recorded session). Such a shape is reported as what it is: not valid.
    """
    try:
        return _measure(shape)
    except RuntimeError:
        return {"volume": 0.0, "bbox": [0.0] * 6, "size": [0.0] * 3,
                "solids": len(shape.Solids), "valid": False}


snap.measure = _measure_or_void     # in this worker process only; the base file is unchanged

BODY = "PartDesign::Body"


class MultiSession(Session):
    def __init__(self) -> None:
        super().__init__()
        self.snapshot = copy.deepcopy(multi_valid.EMPTY_SNAPSHOT)
        self._kept: dict[str, dict | None] = {}     # object name -> its snapshot entry
        self._touched: set[str | None] = set()      # bodies whose entries may be out of date
        self._all_stale = True
        self._relations = structure.Relations()

    # --- what a command may have changed -------------------------------------------------------

    def run(self, command: str, args: dict) -> dict:
        self._touched.add(self.meta["active_body"])     # the body active BEFORE the command
        return super().run(command, args)

    def _new_document(self) -> None:
        super()._new_document()
        self.meta["sides"] = {}         # sketch or dress-up name -> the side it was made on
        self._all_stale = True

    def _undo(self) -> None:
        super()._undo()
        self._all_stale = True

    def _refresh(self) -> None:
        """Exact undo (base session, "Exact undo") for a structure: work out again only the
        objects of the body the undone feature was in. A feature's boolean operation can
        only have changed shapes of its own body, and working out every body of a
        30-body structure after every undo would cost far more than the undo."""
        name = self.meta.get("active_body")
        body = self.doc.getObject(name) if name else None
        if body is None:
            super()._refresh()
            return
        for obj in body.Group:
            obj.touch()
        self.doc.recompute()
        self.refreshes += 1

    def reset(self) -> dict:
        self._all_stale = True
        return super().reset()

    # --- bodies --------------------------------------------------------------------------------

    def _bodies(self) -> list:
        return [obj for obj in self.doc.Objects if obj.TypeId == BODY]

    def _activate_body(self, index: int) -> None:
        bodies = self._bodies()
        if index > len(bodies):
            raise Rejected(f"there are only {len(bodies)} bodies")
        if bodies[index - 1].Name == self.meta["active_body"]:
            raise Rejected(f"body {index} is already the active one")
        self.meta["active_body"] = bodies[index - 1].Name
        self.meta["selection"] = None

    def _move_body(self, x: float, y: float, z: float) -> None:
        body = self._body()
        body.Placement = FreeCAD.Placement(FreeCAD.Vector(x, y, z), body.Placement.Rotation)

    def _turn_body(self, yaw: float, pitch: float, roll: float) -> None:
        body = self._body()
        body.Placement = FreeCAD.Placement(body.Placement.Base, FreeCAD.Rotation(yaw, pitch, roll))

    # --- drawing a part ------------------------------------------------------------------------

    def _sketch_outline(self, points: list) -> None:
        sketch = self._open_sketch()
        self.meta["shapes"][sketch.Name].append(part_shapes.outline(sketch, points))

    def _pad_symmetric(self, length: float) -> None:
        part_shapes.pad_symmetric(self._body(), self._selected(), length)

    def _add_cone(self, bottom_diameter: float, top_diameter: float, height: float) -> None:
        if bottom_diameter <= 0 and top_diameter <= 0:
            raise Rejected("a cone needs one end wider than zero")
        part_shapes.cone(self._body(), bottom_diameter, top_diameter, height)

    def _add_sphere(self, diameter: float) -> None:
        part_shapes.sphere(self._body(), diameter)

    def _add_dome(self, diameter: float, height: float) -> None:
        if height > diameter / 2 + 1e-9:
            raise Rejected("a dome is at most half its diameter high")
        part_shapes.dome(self._body(), diameter, height)

    def _add_tapered_box(self, bottom_length: float, bottom_depth: float, top_length: float,
                         top_depth: float, height: float) -> None:
        part_shapes.tapered_box(self._body(), bottom_length, bottom_depth, top_length, top_depth,
                                height)

    # --- sides ---------------------------------------------------------------------------------

    def _on_side(self) -> str | None:
        selection = self.meta["selection"]
        return selection["side"] if selection and selection["type"] == "side" else None

    def _select_side(self, side: str) -> None:
        try:
            sides.frame(self._body(), side)
        except sides.NotSquare as error:
            raise Rejected(str(error)) from None
        self.meta["selection"] = {"type": "side", "side": side}

    def _select_side_edges(self, side: str, rule: str) -> None:
        tip = self._body().Tip
        try:
            names = sides.edge_names(self._body(), tip.Shape, side, rule)
        except sides.NotSquare as error:
            raise Rejected(str(error)) from None
        if not names:
            raise Rejected(f"no edges are {rule} the {side}")
        self.meta["selection"] = {"type": "edges", "rule": rule, "side": side, "of": tip.Name,
                                  "names": names}

    def _new_sketch(self, offset: float) -> None:
        side = self._on_side()
        if side is None:
            super()._new_sketch(offset)
            return
        body = self._body()
        sketch, flipped = sides.new_sketch(body, features.origin_feature(body, "XY_Plane"), side,
                                           offset)
        self.meta["shapes"][sketch.Name] = []
        self.meta["sides"][sketch.Name] = {"side": side, "offset": float(offset),
                                           "flipped": flipped}
        self.meta["open_sketch"] = sketch.Name
        self.meta["selection"] = None

    def _thickness(self, value: float) -> None:
        side = self._on_side()
        if side is None:
            super()._thickness(value)
            return
        body = self._body()
        tip = body.Tip
        names = sides.face_names(body, tip.Shape, side)
        if not names:
            raise Rejected(f"the body has no flat face at its {side}")
        if part_shapes.is_dome(tip):
            feature = part_shapes.hollow_dome(body, tip, value)     # Thickness fails on a dome
        else:
            feature = features.dressup(body, "thickness", tip, names, value)
            # "Arc" is how FreeCAD's kernel closes gaps between offset faces. Walls that grow
            # inwards leave no gaps on a convex part, so the solid is the same as with the
            # base session's "Intersection"; but on a part whose edges were rounded first,
            # "Intersection" fails and "Arc" works (measured).
            feature.Join = "Arc"
        self.meta["sides"][feature.Name] = {"side": side}

    def _dressup(self, kind: str, value: float) -> None:
        side = self.meta["selection"].get("side")
        super()._dressup(kind, value)
        if side is not None:
            self.meta["sides"][self._body().Tip.Name] = {"side": side}

    def _outward(self, sketch_name: str) -> None:
        """A feature just made from a sketch that looks into the body must work the other way."""
        if self.meta["sides"].get(sketch_name, {}).get("flipped"):
            self._body().Tip.Reversed = True

    def _pad(self, length: float) -> None:
        sketch = self.meta["selection"]["name"]
        super()._pad(length)
        self._outward(sketch)

    def _pocket(self, depth: float) -> None:
        sketch = self.meta["selection"]["name"]
        super()._pocket(depth)
        self._outward(sketch)

    def _pocket_through_all(self) -> None:
        sketch = self.meta["selection"]["name"]
        super()._pocket_through_all()
        self._outward(sketch)

    # --- the snapshot --------------------------------------------------------------------------

    def _item(self, obj) -> dict | None:
        if obj.TypeId in part_shapes.PRIMITIVE_TYPES:
            item = part_shapes.primitive_item(obj, snap.body_of(obj), snap.is_valid(obj))
            if item["type"] == "thickness":
                item["side"] = self.meta.get("sides", {}).get(obj.Name, {}).get("side")
            return item
        item = super()._item(obj)
        if item is None:
            return None
        on_side = self.meta.get("sides", {}).get(obj.Name)
        if item["type"] == "sketch":
            # The base entry gives a polygon's size as `across_flats` only. An odd prism is
            # sized by `constrain_diameter` (the circle through its corners), so say that
            # number too: across flats = diameter x cos(180 / sides). Without it a reader
            # sees "diameter is fixed" and no diameter (found 6 Oct 2026: the teacher
            # stopped with a KeyError on the first three-sided prism).
            for shape in item["shapes"]:
                if shape["shape"] == "polygon" and "diameter" not in shape:
                    shape["diameter"] = shape["across_flats"] / math.cos(math.pi / shape["sides"])
        if item["type"] == "body":
            item["placement"] = structure.placement_item(obj)
        elif item["type"] == "sketch" and on_side:
            # The base entry reads plane and offset off the attachment, which for a sketch on
            # a side is the XY plane with a turned offset. Say what was asked for instead.
            item.update(plane=None, side=on_side["side"], offset=on_side["offset"])
        elif item["type"] == "sketch":
            item["side"] = None
        elif item["type"] == "pad":
            item["symmetric"] = obj.SideType == "Symmetric"
        elif on_side:
            item["side"] = on_side["side"]
        return item

    def _forget_stale(self) -> None:
        touched = self._touched | {self.meta["active_body"]}
        if self._all_stale:
            self._kept.clear()
        else:
            for name, item in list(self._kept.items()):
                owner = None if item is None else item.get("body", item["name"])
                if item is not None and (owner in touched or owner is None):
                    del self._kept[name]
        self._touched, self._all_stale = set(), False

    def _take_snapshot(self) -> dict:
        if self.doc is None:
            self._relations.clear()
            return copy.deepcopy(multi_valid.EMPTY_SNAPSHOT)
        self._forget_stale()
        items, bodies, active = [], [], None
        for obj in self.doc.Objects:
            if obj.Name not in self._kept:
                self._kept[obj.Name] = self._item(obj)
            item = self._kept[obj.Name]
            if item is None:
                continue
            items.append(item)
            if item["type"] == "body":
                bodies.append((item, obj))
                item["index"] = len(bodies)
                item["active"] = item["name"] == self.meta["active_body"]
                active = item if item["active"] else active
        self._relations.update(bodies)
        index = {item["name"]: item["index"] for item, _ in bodies}
        selection = self.meta["selection"]
        if selection and "names" in selection:
            selection = {key: value for key, value in selection.items() if key != "names"}
            selection["count"] = len(self.meta["selection"]["names"])
        return {
            "items": items,
            "session": {"document": True, "active_body": self.meta["active_body"],
                        "tip": active["tip"] if active else None,
                        "open_sketch": self.meta["open_sketch"], "selection": selection,
                        "undo_depth": len(self.undo_stack) - self._floor,
                        "finished": self.meta["finished"]},
            "solid": active["solid"] if active else None,
            "structure": structure.structure_item([item for item, _ in bodies],
                                                  *self._relations.lists(index)),
        }

    # --- services (not commands) ---------------------------------------------------------------

    def label_bodies(self, labels: list[str]) -> None:
        """Make the document ready for a person: name the bodies, mark what should be shown.

        The bodies get the plan's names ("seat", not "Body001"). Every object also carries a
        `Visibility` flag in the document itself; set it as FreeCAD's window would have:
        each body and its newest feature shown, sketches, older features and origins hidden.
        (The window's own display settings are written by ../visible.py.)
        """
        bodies = self._bodies()
        for body, label in zip(bodies, labels):
            body.Label = label
        shown = {body.Name for body in bodies} | {body.Tip.Name for body in bodies if body.Tip}
        for obj in self.doc.Objects:
            with contextlib.suppress(Exception):    # a flag FreeCAD will not let us set
                obj.Visibility = obj.Name in shown
        self.doc.recompute()

    def measure_file(self, path: str) -> list[dict | None]:
        """Open a saved .FCStd file, recompute it, and measure every body in it."""
        document = FreeCAD.openDocument(path)
        try:
            document.recompute()
            return [dict(snap.measure(obj.Shape) or {}, label=obj.Label, visible=None)
                    for obj in document.Objects if obj.TypeId == BODY]
        finally:
            FreeCAD.closeDocument(document.Name)

    def against_files(self, paths: list[str | None]) -> list[dict | None]:
        """Compare each body with a reference solid stored as a BREP file (None: skip).

        The answer per body is the volume the two solids SHARE, next to each one's own
        volume. Two solids are the same solid exactly when all three numbers are equal;
        equal volumes alone would not notice a wedge that points the wrong way.
        """
        import Part

        answers = []
        for body, path in zip(self._bodies(), paths):
            if path is None:
                answers.append(None)
                continue
            reference = Part.Shape()
            reference.read(path)
            answers.append({"shared": body.Shape.common(reference).Volume,
                            "volume": body.Shape.Volume, "reference": reference.Volume})
        return answers
