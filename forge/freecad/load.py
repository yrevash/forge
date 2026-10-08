"""Read session files the way a model may: a whitelist, not a filter.

    for example in examples(slices=("train_v2",)):
        example.view     {"plan": [...], "snapshot": {...}, "valid": [...]}   what the model sees
        example.label    [{"command": ..., "args": {...}}, ...]               the teacher's set
        example.session, example.t                                           bookkeeping only

A session record holds much more than a model may see. `progress` says whether
the state is on plan. `executed` and `reply` say what happened next. The header's
`noise_level`, `start.kind` and `clean_length` say how the session was made. A
target's `item` and `sources` say which plan item is being worked on and where
each number comes from. Any of these in the input and a model can score well
without reading the plan.

So this file never copies a record. `view` BUILDS the model's side field by
field from the lists below; a field that is not listed here cannot get through,
whatever a later recorder adds to the files. That holds at every depth: a plan
item keeps only the slots of its step kind (steps.SLOTS), a label only the
arguments of its command (the catalogue), the valid list only command names. An
object type, a step kind or a command that is not known raises an error instead
of slipping through. tests/test_freecad_load.py proves it
by poisoning every level of a record.

Two history traces are in the lean snapshot and are removed here (measured on
four shards, 23,788 distinct sketch states: nothing else in the snapshot differs
between two visits of the same visible state):
  - the ORDER of a shape's `fixed` list is the order the dimensions were given in;
    here the list is sorted;
  - `session.undo_depth` counts every command in effect, stray selections included;
    here it becomes `can_undo` (true or false), which is all the undo button shows
    and all the teacher uses.
Kept, because they are a function of what is on the screen: object names, a
sketch's `dof`, `closed`, `n_geometry` and `n_constraints`, the values of dimensions
that are not fixed yet, a shape's `first`.

Nothing object-revealing reaches the model either:
  - OBJECT NAMES become opaque ids. FreeCAD names objects by type and count ("Pad",
    "Sketch002", "Mirrored001"); a later generator or a user may name them by meaning
    ("left_bracket_hole"). Here every object is `#0`, `#1`, ... by its place in the
    document, and every field that points at an object (`tip`, `sketch`, `on`, `of`,
    `used_by`, `body`, `open_sketch`, `active_body`, a selection's `name` and `of`)
    carries that id. The type is still there, in `type`. No command takes a name, so
    nothing is lost. Checked on the files: the names in them are FreeCAD's automatic
    ones and nothing else (see the README), so today this hides the counter only.
  - EVERY STRING in the view must come from a closed vocabulary (VOCABULARY: command
    names, step kinds, slot names, object types, planes, axes, rules, shape and
    dimension names, selection types) or be an opaque id. Anything else is an error,
    not a leak. A part's family ("composed_block"), its id, its source and the
    generator's captions are in the header and never in the view.

The same `view` must be used when a model drives FreeCAD, so that it sees there
what it saw in training.

Only sessions that ended with `done` on the stored solid are read. The label
keeps the command and its arguments; a model that only picks the command (code
fills in the numbers) uses the names.

Two additions for the third model, both off unless asked for, so that
every view made before them is byte for byte what it was:
  - `example.sources` (LABEL side, parallel to `example.label`): for each accepted
    command the plan item it works on and where each argument comes from
    ("slot:length", "const", "base:height", "floor", or one of two fixed pieces of
    arithmetic). The model is trained to NAME that source; it is never an input.
    `view` does not take it as an argument, so it cannot get into a view.
  - `view(..., history=[...])`: the last commands that were carried out in this
    session and FreeCAD's reply to each ("ok", "rejected", ...). That is session
    state a person at the screen also has (the undo history). It is read from the
    records BEFORE the step, never from the step's own `executed` or `reply`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from forge.freecad.catalogue import (
    AXES,
    COMMANDS,
    EDGE_RULES,
    FACE_RULES,
    PLANES,
    SKETCH_AXES,
    check_args,
)
from forge.freecad.shards import OUT_DIR, complete_shards, read_sessions
from forge.system1.steps import SLOTS

DIMENSIONS = ("length", "width", "diameter", "across_flats", "angle", "x", "y")
SHAPE_FIELDS = ("shape", "sides", "first", *DIMENSIONS)        # and `fixed`, sorted
COMMON = ("type", "name", "body", "valid")
# Object type -> the fields a model sees, besides COMMON.
ITEM_FIELDS: dict[str, tuple[str, ...]] = {
    "body": ("tip", "active"),
    "sketch": ("plane", "offset", "dof", "closed", "used_by", "n_geometry", "n_constraints"),
    "pad": ("sketch", "length"),
    "pocket": ("sketch", "through_all", "depth"),
    "hole": ("sketch", "diameter", "through_all", "depth", "counterbore_diameter",
             "counterbore_depth"),
    "revolution": ("sketch", "angle", "axis"),
    "fillet": ("on", "edges", "rule", "radius"),
    "chamfer": ("on", "edges", "rule", "size"),
    "thickness": ("on", "faces", "rule", "value"),
    "polar_pattern": ("of", "count", "angle", "axis"),
    "linear_pattern": ("of", "count", "spacing", "direction"),
    "mirror": ("of", "plane"),
}
SESSION_FIELDS = ("document", "active_body", "tip", "open_sketch", "finished")
SELECTION_FIELDS = ("type", "name", "rule", "of", "count")
SOLID_FIELDS = ("volume", "bbox", "solids", "valid")
# Fields whose value is the name of an object of the document: they get an opaque id.
LINK_FIELDS = ("name", "body", "tip", "sketch", "on", "of", "used_by", "active_body", "open_sketch")
SHAPES = ("rectangle", "circle", "polygon", "slot")
SELECTION_TYPES = ("plane", "sketch", "edges", "face", "feature")
# Every string a view may contain, besides opaque ids and the names of its own fields.
VOCABULARY = frozenset({*COMMANDS, *SLOTS, *(slot for slots in SLOTS.values() for slot in slots),
                        *ITEM_FIELDS, *PLANES, *AXES, *SKETCH_AXES, *EDGE_RULES, *FACE_RULES,
                        *SHAPES, *DIMENSIONS, *SELECTION_TYPES})
# FreeCAD's possible answers to a command (the worker's reply `status`).
STATUSES = ("ok", "rejected", "error", "timeout", "crash")
# Where an argument of a teacher's command can come from (forge/freecad/recipes.py).
DERIVED_SOURCES = ("derived:circle_diameter / 2", "derived:-(count - 1) * spacing / 2")
SOURCES = frozenset({"const", "base:height", "floor", *DERIVED_SOURCES,
                     *(f"slot:{slot}" for slots in SLOTS.values() for slot in slots)})


@dataclass(frozen=True)
class Example:
    view: dict          # the model's side: plan, snapshot, valid
    label: list[dict]   # the teacher's set: [{"command", "args"}], sorted by command
    session: str        # bookkeeping: which session and step this is. Never model input.
    t: int
    # LABEL side, parallel to `label`: [{"item": plan index or None, "sources": {arg: source}}]
    sources: tuple[dict, ...] = ()


def _only(source: dict, fields: tuple[str, ...]) -> dict:
    return {field: source[field] for field in fields if field in source}


def _shape(shape: dict) -> dict:
    return {**_only(shape, SHAPE_FIELDS), "fixed": sorted(shape["fixed"])}


def opaque_ids(items: list[dict]) -> dict[str, str]:
    """Object name -> "#<its place in the document>". The model never sees the names."""
    return {item["name"]: f"#{place}" for place, item in enumerate(items)}


def _linked(fields: dict, ids: dict[str, str]) -> dict:
    """The same fields, with every object name replaced by its opaque id."""
    return {field: ids[value] if field in LINK_FIELDS and value is not None else value
            for field, value in fields.items()}


def _item(item: dict, ids: dict[str, str]) -> dict:
    kind = item["type"]
    if kind not in ITEM_FIELDS:
        raise ValueError(f"load.py has no field list for an object of type {kind!r}")
    seen = _linked(_only(item, (*COMMON, *ITEM_FIELDS[kind])), ids)
    if kind == "sketch":
        seen["shapes"] = [_shape(shape) for shape in item["shapes"]]
    return seen


def _selection(selection: dict | None, ids: dict[str, str]) -> dict | None:
    if selection is None:
        return None
    seen = _only(selection, SELECTION_FIELDS)
    if seen["type"] != "plane":         # a plane's name is "XY": a word, not an object
        seen = _linked(seen, ids)
    return seen


def _strings(value: object) -> list[str]:
    """Every string VALUE inside a view (dictionary keys are field names, fixed above)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for inner in value.values() for text in _strings(inner)]
    if isinstance(value, list):
        return [text for inner in value for text in _strings(inner)]
    return []


def check_strings(seen: dict) -> None:
    """Raise unless every string in the view is an opaque id or a word of VOCABULARY."""
    strange = sorted({text for text in _strings(seen) if text not in VOCABULARY
                      and not (text.startswith("#") and text[1:].isdigit())})
    if strange:
        raise ValueError(f"the view holds strings that are not in load.VOCABULARY: {strange}")


# Field names of the label side of a record. The whitelists above pick fields by name at
# the top of each object; a whitelisted field whose VALUE is itself a dictionary would be
# passed through whole. So no dictionary anywhere in a view may carry one of these names.
LABEL_SIDE_KEYS = frozenset({
    "noise", "noise_level", "on_plan", "progress", "reply", "executed", "target", "targets",
    "sources", "clean_length", "start", "t", "end", "problems", "undo_depth", "seed", "session_id",
})


def _keys(value: object) -> set[str]:
    """Every dictionary key at every depth of a view."""
    if isinstance(value, dict):
        return set(value) | {key for inner in value.values() for key in _keys(inner)}
    if isinstance(value, list):
        return {key for inner in value for key in _keys(inner)}
    return set()


def check_keys(seen: dict) -> None:
    """Raise if a label-side field name occurs anywhere inside the view, at any depth."""
    leaked = sorted(_keys(seen) & LABEL_SIDE_KEYS)
    if leaked:
        raise ValueError(f"the view carries label-side fields: {leaked}")


def view_snapshot(snapshot: dict) -> dict:
    """The lean snapshot as a model sees it (see the top of this file)."""
    session, solid = snapshot["session"], snapshot["solid"]
    ids = opaque_ids(snapshot["items"])
    return {
        "items": [_item(item, ids) for item in snapshot["items"]],
        "session": {**_linked(_only(session, SESSION_FIELDS), ids),
                    "selection": _selection(session["selection"], ids),
                    "can_undo": session["undo_depth"] > 0},
        "solid": None if solid is None else _only(solid, SOLID_FIELDS),
    }


def view(plan: list[dict], snapshot: dict, valid: list[str],
         history: list[tuple[str, str]] | None = None) -> dict:
    """What the model sees at one step: the plan, the session, the commands available.

    `history` (third model only): the commands carried out just before this step, oldest
    first, each as (command name, FreeCAD's reply status). None = no such part in the view."""
    unknown = [name for name in valid if name not in COMMANDS]
    if unknown:
        raise ValueError(f"the valid list names commands the catalogue does not have: {unknown}")
    seen = {"plan": [{"kind": item["kind"], "slots": _only(item["slots"], SLOTS[item["kind"]])}
                     for item in plan],
            "snapshot": view_snapshot(snapshot),
            "valid": list(valid)}
    check_strings(seen)
    if history is not None:
        for name, status in history:
            if name not in COMMANDS or status not in STATUSES:
                raise ValueError(f"not a command and a reply status: {(name, status)}")
        seen["history"] = [{"command": name, "status": status} for name, status in history]
    check_keys(seen)
    return seen


def history_before(records: list[dict], t: int, length: int) -> list[tuple[str, str]]:
    """The last `length` commands carried out BEFORE step t of a recorded session, oldest
    first. Step t's own `executed` and `reply` are what happens next: never read here."""
    return [(record["executed"]["command"], record["reply"]["status"])
            for record in records[max(0, t - length):t]]


def sources(targets: list[dict]) -> tuple[dict, ...]:
    """LABEL side: for each accepted command, in the order of `label`, the plan item it
    works on and the source of each argument. Only the strings of SOURCES pass."""
    made = []
    for target in sorted(targets, key=lambda target: target["command"]):
        names = tuple(COMMANDS[target["command"]]["args"])
        if "sources" not in target:         # a recording older than the sources field
            made.append({"item": None, "sources": None})
            continue
        found = _only(target["sources"], names)
        strange = sorted(set(found.values()) - SOURCES)
        if strange or set(found) != set(names):
            raise ValueError(f"{target['command']}: argument sources {strange or found} "
                             f"are not the ones load.SOURCES lists for {names}")
        item = target.get("item")
        if item is not None and (isinstance(item, bool) or not isinstance(item, int)):
            raise ValueError(f"a target's plan item must be a whole number or None: {item!r}")
        made.append({"item": item, "sources": found})
    return tuple(made)


def label(targets: list[dict]) -> list[dict]:
    """The teacher's set as a label: commands and arguments, nothing about the plan."""
    made = sorted(({"command": target["command"],
                    "args": _only(target["args"], tuple(COMMANDS[target["command"]]["args"]))}
                   for target in targets), key=lambda target: target["command"])
    for target in made:                 # numbers and catalogue words only
        problem = check_args(target["command"], target["args"])
        if problem:
            raise ValueError(f"a label is not a command of the catalogue: {problem}")
    return made


def usable(end: dict) -> bool:
    """Is this a session a model may learn from or be scored on?"""
    return end["end"] == "done" and not end["problems"]


def examples_of(header: dict, records: list[dict], history: int = 0) -> Iterator[Example]:
    """The examples of one session. `history` > 0 adds that many previous commands to the
    view (third model); 0 leaves the view exactly as the first two models saw it."""
    for record in records:
        before = history_before(records, record["t"], history) if history else None
        yield Example(view(header["plan"], record["snapshot"], record["valid"], before),
                      label(record["target"]), header["session"], record["t"],
                      sources(record["target"]))


def examples(slices: tuple[str, ...] | list[str], out_dir: Path = OUT_DIR,
             shards: list[int] | None = None) -> Iterator[Example]:
    """Every step of every usable session of these slices (optionally only some shards)."""
    for _, path in complete_shards(out_dir, tuple(slices)):
        if shards is not None and int(path.name[5:8]) not in shards:
            continue
        for header, records, end in read_sessions(path):
            if usable(end):
                yield from examples_of(header, records)
