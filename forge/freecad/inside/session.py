"""One FreeCAD session: the document, what is selected, and how commands change them.

Runs inside FreeCAD. `Session.run(command, args)` is the only door: it checks
that the command is available, carries it out, and returns the new snapshot.

    status "ok"        the command was carried out
    status "rejected"  not available now, bad arguments, or nothing fits the rule
    status "error"     FreeCAD itself refused (an exception)
After "rejected" and "error" the session is exactly as it was before.

Note that "ok" means "FreeCAD did what was asked", not "the result is a good
part". A fillet with a radius too large for the solid is carried out and leaves
a feature marked `valid: false`, just as FreeCAD's own window would show it with
a red mark. The remedy is `undo`.

How undo works. The headless FreeCAD has no selection and no "open sketch";
those live here, in `self.meta`. The document itself is undone by FreeCAD:
every command that edits the document runs inside one TRANSACTION, and
`doc.undo()` rolls back one transaction. So an undo step is a pair: (what
`meta` was before the command, whether a transaction was used).

One limit. Without its window, FreeCAD keeps only the last 20 transactions and
offers no way to raise that (the setting is read by the window code only). So
the session also keeps `history`, the commands that are in effect. When an undo
reaches further back than FreeCAD remembers, the session rebuilds the document
by running the history again without its last command. That is slower, and
gives the same result.

Exact undo (`exact_undo`, off unless the worker is told to switch it on). FreeCAD's
undo takes an object away again, but it does not take back what COMPUTING that
object did to the shapes it was built from. Found on 6 Oct 2026: a pad of a sketch
with two rectangles drawn exactly on top of each other is carried out, and its
boolean operation raises the tolerance of a vertex of the solid underneath from
0.0000001 mm to 24 mm, in place. After `undo` the pad is gone and the snapshot is
what it was, but the solid still carries that tolerance, and a mirror made later
fails ("Null shape") every time. With `exact_undo`, undoing a feature, an edge
treatment or a pattern is followed by `_refresh`: every object is worked out again
from its own numbers, so nothing a removed feature did can be left behind.
"""

from __future__ import annotations

import copy

import catalogue
import features
import FreeCAD
import rules
import shapes
import snapshot as snap
import valid

FREECAD_UNDO_LIMIT = 20     # transactions a headless FreeCAD document remembers
# Command groups whose objects run a boolean operation on the solid when they are worked
# out. Only those can change an earlier shape in place, so only their undo needs `_refresh`.
REFRESH_AFTER_UNDO = ("feature", "dressup", "pattern")


class Rejected(Exception):
    """The command cannot be carried out here; nothing was changed."""


def _fresh_meta() -> dict:
    return {"active_body": None, "open_sketch": None, "selection": None, "finished": False,
            "shapes": {},       # sketch name -> the shapes drawn in it, in order
            "rules": {}}        # fillet / chamfer / thickness name -> the rule that chose its edges


class Session:
    def __init__(self) -> None:
        self.doc = None
        self.meta = _fresh_meta()
        self.undo_stack: list[tuple[dict, bool]] = []
        self.history: list[tuple[str, dict]] = []   # the commands in effect, oldest first
        self._remembered = 0                    # transactions FreeCAD can still undo
        self._floor = 0                         # undo steps that were forgotten (see forget_undo)
        self._cache: dict[str, dict] = {}       # snapshot items that are still up to date
        self.exact_undo = False                 # see "Exact undo" above and `_refresh`
        self.refreshes = 0                      # how many times `_refresh` ran (for the proofs)
        self.snapshot = copy.deepcopy(valid.EMPTY_SNAPSHOT)

    # --- the door --------------------------------------------------------------------------

    def run(self, command: str, args: dict) -> dict:
        reason = (catalogue.check_args(command, args) or valid.why_not(self.snapshot, command)
                  or self._bad_numbers(command, args))
        if reason:
            return self._reply("rejected", reason)
        if command == "new_document":
            self._new_document()
            return self._reply("ok")
        if command == "undo":
            self._undo()
            return self._reply("ok")

        before = copy.deepcopy(self.meta)
        in_transaction = catalogue.COMMANDS[command]["changes_document"]
        if in_transaction:
            self.doc.openTransaction(command)
        try:
            getattr(self, f"_{command}")(**args)
            if in_transaction:
                self.doc.recompute()
                self.doc.commitTransaction()
                FreeCAD.closeActiveTransaction(False)   # see the note in the except branch
        except Exception as error:                      # noqa: BLE001 - FreeCAD raises many kinds
            if in_transaction:
                self.doc.abortTransaction()             # FreeCAD takes back the half-done edit
                # If the command failed before it changed anything, the document never
                # started its transaction, and FreeCAD keeps the NAME waiting: the next
                # change made outside a transaction (the solve in `_undo`) would then be
                # recorded as an undo step we do not count. Closing it here prevents that.
                FreeCAD.closeActiveTransaction(True)
                self.doc.recompute()
            self.meta = before
            self._cache.clear()
            soft = isinstance(error, (Rejected, shapes.ShapeError))
            return self._reply("rejected" if soft else "error", str(error))

        self.undo_stack.append((before, in_transaction))
        self.history.append((command, dict(args)))
        if in_transaction:
            self._remembered = min(self._remembered + 1, FREECAD_UNDO_LIMIT)
        if catalogue.COMMANDS[command]["group"] == "sketch":
            self._cache.pop(self.meta["open_sketch"] or "", None)   # only that sketch changed
        elif in_transaction:
            self._cache.clear()
            self.meta["selection"] = None               # the solid changed: start afresh
        self._check_undo_stack()
        return self._reply("ok")

    @staticmethod
    def _bad_numbers(command: str, args: dict) -> str | None:
        """Numbers that can be refused without asking FreeCAD at all."""
        if command == "sketch_polygon" and args["sides"] < 3:
            return "a polygon needs 3 sides or more"
        if command == "hole_counterbore" and args["counterbore_diameter"] <= args["diameter"]:
            return "the counterbore must be wider than its hole"
        return None

    def _reply(self, status: str, reason: str | None = None) -> dict:
        self.snapshot = self._take_snapshot()
        reply = {"status": status, "snapshot": self.snapshot,
                 "valid": valid.valid_commands(self.snapshot)}
        if reason:
            reply["reason"] = reason
        return reply

    def _check_undo_stack(self) -> None:
        """FreeCAD must remember exactly as many transactions as we think, or undo would drift."""
        if self.doc.UndoCount != self._remembered:
            raise RuntimeError(f"undo stacks disagree: FreeCAD {self.doc.UndoCount}, "
                               f"ours {self._remembered}")

    # --- document, undo ----------------------------------------------------------------------

    def _new_document(self) -> None:
        if self.doc is not None:
            FreeCAD.closeDocument(self.doc.Name)
        self.doc = FreeCAD.newDocument("Forge")
        self.doc.UndoMode = 1                           # 1 = record transactions
        self.meta = _fresh_meta()
        self.undo_stack.clear()
        self.history.clear()
        self._remembered = 0
        self._floor = 0
        self._cache.clear()

    def _undo(self) -> None:
        if self.undo_stack[-1][1] and self._remembered == 0:
            self._rebuild(self.history[:-1])    # further back than FreeCAD remembers
            return
        before, in_transaction = self.undo_stack.pop()
        command, _ = self.history.pop()
        if in_transaction:
            self.doc.undo()
            self.doc.recompute()
            self._remembered -= 1
            if self.exact_undo and catalogue.COMMANDS[command]["group"] in REFRESH_AFTER_UNDO:
                self._refresh()
        self.meta = before
        self._cache.clear()
        sketch = self._open_sketch()
        if sketch is not None:
            sketch.solve()      # refresh the solver's count of degrees of freedom
        self._check_undo_stack()

    def _refresh(self) -> None:
        """Work every object out again from its own numbers (sketch, then pad, then ...).

        `recompute` alone skips objects FreeCAD believes are up to date; `touch` marks them
        all as changed first. The shapes that come out are new ones, so a tolerance that a
        removed feature raised in an old shape is gone. No property changes, so this is not
        an undo step and the snapshot stays what it was (a volume may differ in its last
        binary digits, as after any recompute).
        """
        for obj in self.doc.Objects:
            obj.touch()
        self.doc.recompute()
        self.refreshes += 1

    def _rebuild(self, commands: list[tuple[str, dict]]) -> None:
        """Start the document again and run these commands, as if they were typed afresh."""
        floor = self._floor
        self._new_document()
        self.snapshot = self._take_snapshot()
        for command, args in commands:
            reply = self.run(command, args)
            if reply["status"] != "ok":
                raise RuntimeError(f"rebuilding failed at {command}: {reply.get('reason')}")
        self._floor = floor                     # what could not be undone before still cannot
        self.snapshot = self._take_snapshot()

    def _new_body(self) -> None:
        body = self.doc.addObject("PartDesign::Body", "Body")
        self.meta["active_body"] = body.Name

    # --- looking things up -------------------------------------------------------------------

    def _body(self):
        return self.doc.getObject(self.meta["active_body"])

    def _open_sketch(self):
        name = self.meta["open_sketch"]
        return self.doc.getObject(name) if name and self.doc else None

    def _selected(self):
        """The FreeCAD object the selection names (a sketch or a feature)."""
        return self.doc.getObject(self.meta["selection"]["name"])

    # --- selecting ---------------------------------------------------------------------------

    def _select_plane(self, plane: str) -> None:
        self.meta["selection"] = {"type": "plane", "name": plane}

    def _select_by_rule(self, kind: str, rule: str) -> None:
        tip = self._body().Tip
        finder = rules.edges if kind == "edges" else rules.faces
        names = finder(tip.Shape, rule)
        if not names:
            raise Rejected(f"no {kind} fit the rule '{rule}'")
        # `names` are today's FreeCAD numbers. They are used by the very next command and
        # never shown in the snapshot; the snapshot shows the rule and how many it found.
        self.meta["selection"] = {"type": kind, "rule": rule, "of": tip.Name, "names": names}

    def _select_face(self, rule: str) -> None:
        self._select_by_rule("face", rule)

    def _select_edges(self, rule: str) -> None:
        self._select_by_rule("edges", rule)

    def _select_tip(self) -> None:
        self.meta["selection"] = {"type": "feature", "name": self._body().Tip.Name}

    def _select_sketch(self) -> None:
        self.meta["selection"] = {"type": "sketch",
                                  "name": valid.unused_sketch(self.snapshot)["name"]}

    def _clear_selection(self) -> None:
        self.meta["selection"] = None

    # --- sketching ---------------------------------------------------------------------------

    def _new_sketch(self, offset: float) -> None:
        sketch = features.new_sketch(self._body(), self.meta["selection"]["name"], offset)
        self.meta["shapes"][sketch.Name] = []
        self.meta["open_sketch"] = sketch.Name
        self.meta["selection"] = None

    def _edit_sketch(self) -> None:
        self.meta["open_sketch"] = self.meta["selection"]["name"]
        self.meta["selection"] = None

    def _draw(self, kind: str, sides: int | None = None) -> None:
        sketch = self._open_sketch()
        self.meta["shapes"][sketch.Name].append(shapes.draw(sketch, kind, sides))

    def _sketch_rectangle(self) -> None:
        self._draw("rectangle")

    def _sketch_circle(self) -> None:
        self._draw("circle")

    def _sketch_polygon(self, sides: int) -> None:
        self._draw("polygon", sides)

    def _sketch_slot(self) -> None:
        self._draw("slot")

    def _constrain(self, dimension: str, value: float) -> None:
        sketch = self._open_sketch()
        drawn = self.meta["shapes"][sketch.Name]
        # Constraint names must be unique in a sketch: "diameter", then "diameter_2", ...
        name = dimension if len(drawn) == 1 else f"{dimension}_{len(drawn)}"
        shapes.constrain(sketch, drawn[-1], dimension, value, name)

    def _constrain_length(self, value: float) -> None:
        self._constrain("length", value)

    def _constrain_width(self, value: float) -> None:
        self._constrain("width", value)

    def _constrain_diameter(self, value: float) -> None:
        self._constrain("diameter", value)

    def _constrain_across_flats(self, value: float) -> None:
        self._constrain("across_flats", value)

    def _constrain_angle(self, value: float) -> None:
        self._constrain("angle", value)

    def _constrain_x(self, value: float) -> None:
        self._constrain("x", value)

    def _constrain_y(self, value: float) -> None:
        self._constrain("y", value)

    def _leave_sketch(self) -> None:
        # As in FreeCAD's window, the sketch you just left stays selected, ready to pad.
        self.meta["selection"] = {"type": "sketch", "name": self.meta["open_sketch"]}
        self.meta["open_sketch"] = None

    # --- features ----------------------------------------------------------------------------

    def _pad(self, length: float) -> None:
        features.pad(self._body(), self._selected(), length)

    def _pocket(self, depth: float) -> None:
        features.pocket(self._body(), self._selected(), depth)

    def _pocket_through_all(self) -> None:
        features.pocket(self._body(), self._selected(), None)

    def _revolution(self, angle: float, axis: str) -> None:
        features.revolution(self._body(), self._selected(), angle, axis)

    def _hole_through(self, diameter: float) -> None:
        features.hole(self._body(), self._selected(), diameter)

    def _hole_blind(self, diameter: float, depth: float) -> None:
        features.hole(self._body(), self._selected(), diameter, depth=depth)

    def _hole_counterbore(self, diameter: float, counterbore_diameter: float,
                          counterbore_depth: float) -> None:
        features.hole(self._body(), self._selected(), diameter,
                      counterbore=(counterbore_diameter, counterbore_depth))

    def _dressup(self, kind: str, value: float) -> None:
        selection = self.meta["selection"]
        feature = features.dressup(self._body(), kind, self.doc.getObject(selection["of"]),
                                   selection["names"], value)
        # FreeCAD stores edge numbers, which say nothing to a reader. Remember the rule that
        # chose them, so the snapshot can say "the vertical edges" and not only "4 edges".
        self.meta["rules"][feature.Name] = selection["rule"]

    def _fillet(self, radius: float) -> None:
        self._dressup("fillet", radius)

    def _chamfer(self, size: float) -> None:
        self._dressup("chamfer", size)

    def _thickness(self, value: float) -> None:
        self._dressup("thickness", value)

    def _polar_pattern(self, count: int, axis: str) -> None:
        features.polar_pattern(self._body(), self._selected(), count, axis)

    def _linear_pattern(self, count: int, spacing: float, direction: str) -> None:
        features.linear_pattern(self._body(), self._selected(), count, spacing, direction)

    def _mirror(self, plane: str) -> None:
        features.mirror(self._body(), self._selected(), plane)

    def _done(self) -> None:
        self.meta["finished"] = True

    # --- the snapshot ------------------------------------------------------------------------

    def _item(self, obj) -> dict | None:
        """The snapshot entry for one document object (None for origins, planes and axes)."""
        if obj.TypeId == "PartDesign::Body":
            return {"type": "body", "name": obj.Name,
                    "tip": obj.Tip.Name if obj.Tip else None,
                    "active": obj.Name == self.meta["active_body"],
                    "solid": snap.measure(obj.Shape), "valid": snap.is_valid(obj)}
        if obj.TypeId == "Sketcher::SketchObject":
            return snap.sketch_item(obj, copy.deepcopy(self.meta["shapes"].get(obj.Name, [])))
        if obj.TypeId in snap.FEATURE_TYPES:
            item = snap.feature_item(obj)
            if item["type"] in ("fillet", "chamfer", "thickness"):
                item["rule"] = self.meta["rules"].get(obj.Name)
            return item
        return None

    def _take_snapshot(self) -> dict:
        if self.doc is None:
            return copy.deepcopy(valid.EMPTY_SNAPSHOT)
        items = []
        for obj in self.doc.Objects:
            if obj.Name not in self._cache:
                self._cache[obj.Name] = self._item(obj)
            if self._cache[obj.Name] is not None:
                items.append(self._cache[obj.Name])
        active = None
        for item in items:      # cached entries may predate a change of active body
            if item["type"] == "body":
                item["active"] = item["name"] == self.meta["active_body"]
                active = item if item["active"] else active
        selection = self.meta["selection"]
        if selection and "names" in selection:
            selection = {"type": selection["type"], "rule": selection["rule"],
                         "of": selection["of"], "count": len(selection["names"])}
        return {
            "items": items,
            "session": {"document": True, "active_body": self.meta["active_body"],
                        "tip": active["tip"] if active else None,
                        "open_sketch": self.meta["open_sketch"], "selection": selection,
                        "undo_depth": len(self.undo_stack) - self._floor,
                        "finished": self.meta["finished"]},
            "solid": active["solid"] if active else None,
        }

    # --- services for tools and tests (not commands the model issues) -------------------------

    def reset(self) -> dict:
        """Back to the very start: no document at all. Sessions use it between parts."""
        if self.doc is not None:
            FreeCAD.closeDocument(self.doc.Name)
        self.doc = None
        self.meta = _fresh_meta()
        self.undo_stack.clear()
        self.history.clear()
        self._remembered = self._floor = 0
        self._cache.clear()
        return self._reply("ok")

    def forget_undo(self) -> dict:
        """As if the document had just been opened from a file: nothing done so far can be
        undone. The commands stay in `history`, so a later rebuild still works."""
        self._floor = len(self.undo_stack)
        return self._reply("ok")

    def rebuild(self) -> dict:
        """Start the document again and run the commands in effect, as a fresh session would.

        The state a snapshot shows is unchanged; anything it does not show (see "Exact
        undo") is gone. For proofs, and for a harness whose build is stuck.
        """
        if self.doc is not None:
            self._rebuild(list(self.history))
        return self._reply("ok")

    def tolerances(self) -> dict[str, float]:
        """Object name -> the largest tolerance in its shape (mm). Shapes made by
        these commands have 1e-7, now and then up to 1e-6; millimetres are damage that the
        snapshot does not show."""
        found = {}
        for obj in self.doc.Objects if self.doc else []:
            shape = getattr(obj, "Shape", None)
            if shape is not None and not shape.isNull():
                found[obj.Name] = shape.getTolerance(1)
        return found

    def save(self, path: str) -> None:
        self.doc.saveAs(path)

    def objects(self) -> list[list[str]]:
        """Every object in the document as [name, FreeCAD type], origins and planes included.

        The snapshot leaves the origins out; this list leaves nothing out, so a test can
        show that an undo left no stray object behind.
        """
        return [[obj.Name, obj.TypeId] for obj in self.doc.Objects]

    def measure_file(self, path: str) -> dict | None:
        """Open a saved .FCStd file, recompute it, and measure its first body."""
        document = FreeCAD.openDocument(path)
        try:
            document.recompute()
            bodies = [obj for obj in document.Objects if obj.TypeId == "PartDesign::Body"]
            return snap.measure(bodies[0].Shape) if bodies else None
        finally:
            FreeCAD.closeDocument(document.Name)

    def probe(self, points: list[list[float]]) -> list[bool]:
        """For each point: is it inside the active body's solid (surface included)?"""
        shape = self._body().Shape
        return [bool(shape.isInside(FreeCAD.Vector(*point), 1e-7, True)) for point in points]
