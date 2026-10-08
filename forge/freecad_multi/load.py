"""Read structure sessions the way a model may: a whitelist, and a compact state.

    for example in examples(slices=("train",)):
        example.view     {"plan": [...], "state": {...}, "valid": [...]}     what the model sees
        example.label    [{"command": ..., "args": {...}}, ...]              the teacher's set
        example.session, example.t                                          bookkeeping only

    python -m forge.freecad_multi.load --sizes      measure the view on the recorded sessions

Same rules as forge/freecad/load.py (read that file's top first): nothing is copied, the
view is BUILT field by field from the lists below, and an unknown object type, shape,
feature or string is an error, not a leak. tests/test_freecad_multi_load.py proves it on
real records and on poisoned ones.

OBJECT-BLIND. Forge-S1 knows shapes, sizes, places and features. It must never learn what
a thing is. So the view never holds: the structure's kind ("stool"), the plan's id, file
or source, a part's name ("left leg"), a plan line or its number. A body is a NUMBER (1
for the first body made), which is also what `activate_body` takes. FreeCAD's object
names become opaque ids (`#7`: the object's place in the document). Every string must be
in VOCABULARY or be such an id.

NO HISTORY. As in the single-part reader: `undo_depth` becomes `can_undo`, a shape's
`fixed` list is sorted. A body's turn is shown as the three angles of `turn_body`,
worked out from the body's matrix by the same function the plan's turn is (FreeCAD's own
three angles for the same turn can be written in more than one way).

THE PLAN the model reads, one entry per item, in order:
    part      {"kind": "part", "body": n, "shape", "sizes": {...}, "at": [x, y, z],
               "turn": [yaw, pitch, roll], + "drawn" numbers (an outline, a bore),
               "features": [...] when the part comes with features}
    feature   {"kind": "feature", "body": n, "features": [{"feature", "side", ...numbers}]}
`turn` is the turn that is actually left to do. A mirrored copy's matrix is not a turn;
the recorder splits it (recipes.split) into "drawn as its mirror image" and a turn, and
only that turn is here, with the mirrored outline under `outline` where the shape has
one. A mirrored box or cylinder is its own mirror image: nothing but the turn is left.

THE COMPACT STATE. A structure snapshot lists every sketch and pad of every body: 7.9 KB
per step in the first recording. Here a body is ONE SHORT ENTRY when it is a complete
shape that nothing is being done to:

    {"body": 3, "is": "box", "length": 40.0, "depth": 20.0, "height": 10.0,
     "at": [0.0, 0.0, 5.0], "turn": [0.0, 0.0, 0.0]}                 (+ "features": [...])

and is shown IN DETAIL (every object, as the single-part reader shows them) when
    - it is not a complete, valid shape: a sketch is open or unfinished, a feature
      failed, an object is where no shape has one, a shape is not drawn centred;
    - or the open sketch or the selection points at one of its objects.
The body being worked on is therefore in detail whenever there is something to see in
it, and so is every body that is off plan in its STRUCTURE.

What decides between the two is the snapshot alone, never the plan. A body with a wrong
NUMBER (a 50 mm leg where the plan says 45) is a complete shape and gets the short entry,
with the wrong number in it. That is deliberate: "is this body what the plan asks for" is
exactly what the model has to work out, and a reader that answered it (short when right,
long when wrong) would hand the model the teacher's on-plan flag.

The same `view` must be used when a model drives FreeCAD.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from forge.freecad.catalogue import AXES, EDGE_RULES, FACE_RULES, PLANES, SKETCH_AXES
from forge.freecad.load import ITEM_FIELDS as BASE_ITEM_FIELDS
from forge.freecad.load import LABEL_SIDE_KEYS, _keys, _strings
from forge.freecad_multi.multi_catalogue import COMMANDS, SIDE_EDGE_RULES, SIDES, check_args
from forge.freecad_multi.recipes import _clean, yaw_pitch_roll
from forge.freecad_multi.shards import OUT_DIR, complete_shards, read_sessions

# --- the plan ----------------------------------------------------------------------------------

SHAPE_WORDS = ("box", "cylinder", "tube", "prism", "wedge", "cone", "sphere", "dome",
               "tapered box", "bar l", "bar t", "bar u", "bar i", "bar tube")
# Every size slot the resolver gives a part (forge/resolve/shapes.py), by shape.
SIZE_SLOTS = ("length", "depth", "height", "diameter", "outer diameter", "inner diameter",
              "wall", "sides", "across", "bottom diameter", "top diameter", "bottom length",
              "bottom depth", "top length", "top depth", "profile depth", "profile width",
              "thickness")
DRAWN_FIELDS = ("outline", "plane", "bore", "diameter", "y")
FEATURE_KINDS = ("hole", "hole_pair", "polar", "row", "blind_hole", "counterbore", "boss",
                 "boss_pair", "pad", "pocket", "pocket_pair", "slot", "corner_radius",
                 "top_chamfer", "top_fillet", "shell")
FEATURE_FIELDS = ("feature", "side", "edges", "offset", "at", "diameter", "through", "depth",
                  "counterbore_diameter", "counterbore_depth", "height", "length", "width",
                  "angle", "radius", "size", "wall")

# --- the state ---------------------------------------------------------------------------------

DIMENSIONS = ("length", "width", "diameter", "across_flats", "angle", "x", "y")
SHAPE_FIELDS = ("shape", "sides", "first", "points", *DIMENSIONS)      # and `fixed`, sorted
COMMON = ("type", "name", "valid")
# Object type -> the fields a model sees in a DETAILED body, besides COMMON.
ITEM_FIELDS: dict[str, tuple[str, ...]] = {
    "sketch": ("plane", "side", "offset", "dof", "closed", "used_by", "n_geometry",
               "n_constraints"),
    "pad": ("sketch", "length", "symmetric"),
    "pocket": ("sketch", "through_all", "depth"),
    "fillet": ("on", "edges", "rule", "side", "radius"),
    "chamfer": ("on", "edges", "rule", "side", "size"),
    "thickness": ("on", "faces", "rule", "side", "value"),
    "cone": ("bottom_diameter", "top_diameter", "height"),
    "sphere": ("diameter",),
    "dome": ("diameter", "height"),
    "tapered_box": ("bottom_length", "bottom_depth", "top_length", "top_depth", "height"),
    # Objects no structure recipe makes, but a wrong command can (the base commands stay
    # available): the single-part reader's own field lists.
    **{kind: BASE_ITEM_FIELDS[kind] for kind in ("hole", "revolution", "polar_pattern",
                                                 "linear_pattern", "mirror")},
}
PRIMITIVES = ("cone", "sphere", "dome", "tapered_box")
SELECTION_FIELDS = ("type", "name", "rule", "of", "count", "side")
SELECTION_TYPES = ("plane", "sketch", "edges", "face", "feature", "side")
LINK_FIELDS = ("name", "sketch", "on", "of", "used_by")
SKETCH_SHAPES = ("rectangle", "circle", "polygon", "slot", "outline")
STATE_SHAPES = ("box", "cylinder", "tube", "prism", "outline", *PRIMITIVES)
PART_KINDS = ("part", "feature")
# Every string a view may contain, besides opaque ids. Dictionary keys are field names
# fixed by the lists above; these are the VALUES.
VOCABULARY = frozenset({*COMMANDS, *SHAPE_WORDS, *FEATURE_KINDS, *SIDES, *SIDE_EDGE_RULES,
                        *PLANES, *AXES, *SKETCH_AXES, *EDGE_RULES, *FACE_RULES, *ITEM_FIELDS,
                        *SKETCH_SHAPES, *STATE_SHAPES, *DIMENSIONS, *SELECTION_TYPES, *PART_KINDS})


@dataclass(frozen=True)
class Example:
    view: dict          # the model's side: plan, state, valid
    label: list[dict]   # the teacher's set: [{"command", "args"}], sorted by command
    session: str        # bookkeeping: which session and step this is. Never model input.
    t: int


def _only(source: dict, fields: tuple[str, ...]) -> dict:
    return {field: source[field] for field in fields if field in source}


def _strict(source: dict, fields: tuple[str, ...], what: str) -> dict:
    """Like _only, but a key outside the list is an error: these dictionaries come from the
    resolver and the recipes, and a new slot must be looked at before a model sees it."""
    unknown = sorted(set(source) - set(fields))
    if unknown:
        raise ValueError(f"load.py does not know the {what} {unknown}")
    return dict(source)


# --- the plan the model reads ------------------------------------------------------------------

def _feature(feature: dict) -> dict:
    if feature["feature"] not in FEATURE_KINDS:
        raise ValueError(f"load.py does not know the feature {feature['feature']!r}")
    return _strict(feature, FEATURE_FIELDS, "feature fields")


def view_plan(plan: list[dict]) -> list[dict]:
    """The stored plan (play.plan_json) as the model reads it. Names, lines, the resolver's
    own matrix and everything else in a stored item stay behind."""
    seen = []
    for item in plan:
        entry: dict = {"kind": item["kind"], "body": item["body"]}
        if item["kind"] == "part":
            if item["shape"] not in SHAPE_WORDS:
                raise ValueError(f"load.py does not know the shape {item['shape']!r}")
            entry.update(shape=item["shape"], sizes=_strict(item["sizes"], SIZE_SLOTS, "sizes"),
                         at=list(item["at"]), turn=list(item["turn"]),
                         **_strict(item["drawn"], DRAWN_FIELDS, "drawn numbers"))
        elif item["kind"] != "feature":
            raise ValueError(f"load.py does not know the plan item kind {item['kind']!r}")
        if item["features"]:
            entry["features"] = [_feature(feature) for feature in item["features"]]
        seen.append(entry)
    return seen


# --- the state the model reads -----------------------------------------------------------------

def _shape(shape: dict) -> dict:
    if shape["shape"] not in SKETCH_SHAPES:
        raise ValueError(f"load.py does not know the sketch shape {shape['shape']!r}")
    return {**_only(shape, SHAPE_FIELDS), "fixed": sorted(shape["fixed"])}


def _linked(fields: dict, ids: dict[str, str]) -> dict:
    return {field: ids[value] if field in LINK_FIELDS and value is not None else value
            for field, value in fields.items()}


def _object(item: dict, ids: dict[str, str]) -> dict:
    """One object of a detailed body."""
    kind = item["type"]
    if kind not in ITEM_FIELDS:
        raise ValueError(f"load.py has no field list for an object of type {kind!r}")
    seen = _linked(_only(item, (*COMMON, *ITEM_FIELDS[kind])), ids)
    if kind == "sketch":
        seen["shapes"] = [_shape(shape) for shape in item["shapes"]]
    return seen


def _turn(placement: dict) -> list[float]:
    matrix = tuple(tuple(row) for row in placement["matrix"])
    return list(yaw_pitch_roll(matrix))


def _centred(shape: dict) -> bool:
    return abs(shape.get("x", 0.0)) < 1e-9 and abs(shape.get("y", 0.0)) < 1e-9


def _base_shape(objects: list[dict]) -> tuple[dict, int] | None:
    """(the short entry of the body's shape, how many objects it is made of), or None when
    the first objects are not a complete shape drawn the standard way."""
    if not objects or not objects[0]["valid"]:
        return None
    first = objects[0]
    if first["type"] in PRIMITIVES:
        return {"is": first["type"], **_only(first, ITEM_FIELDS[first["type"]])}, 1
    if first["type"] != "sketch" or len(objects) < 2:
        return None
    pad, shapes = objects[1], first["shapes"]
    if first.get("side") is not None or first["offset"] != 0 or first["dof"] != 0 \
            or not first["closed"] or pad["type"] != "pad" or not pad["valid"] \
            or not pad.get("symmetric") or pad["sketch"] != first["name"] or not shapes:
        return None
    kinds = [shape["shape"] for shape in shapes]
    made: dict | None = None
    if first["plane"] == "XY" and all(_centred(shape) for shape in shapes):
        if kinds == ["rectangle"]:
            made = {"is": "box", "length": shapes[0]["length"], "depth": shapes[0]["width"]}
        elif kinds == ["circle"]:
            made = {"is": "cylinder", "diameter": shapes[0]["diameter"]}
        elif kinds == ["circle", "circle"]:
            made = {"is": "tube", "outer diameter": shapes[0]["diameter"],
                    "inner diameter": shapes[1]["diameter"]}
    if made is None and kinds == ["polygon"] and first["plane"] == "XY" \
            and abs(shapes[0].get("x", 0.0)) < 1e-9:
        polygon = shapes[0]
        size = "diameter" if "diameter" in polygon["fixed"] else "across_flats"
        made = {"is": "prism", "sides": polygon["sides"], size: polygon[size],
                "angle": polygon["angle"], "y": polygon["y"]}
    if made is None and kinds == ["outline"]:
        made = {"is": "outline", "plane": first["plane"], "outline": shapes[0]["points"]}
    if made is None:
        return None
    return {**made, "height": pad["length"]}, 2


def _short_features(objects: list[dict]) -> list[dict] | None:
    """The features of a body after its shape, each as one short entry; None when they are
    not all complete, valid features built the standard way."""
    made, at = [], 0
    while at < len(objects):
        item = objects[at]
        if not item["valid"]:
            return None
        if item["type"] == "sketch":
            if at + 1 >= len(objects) or item.get("side") is None or item["dof"] != 0 \
                    or not item["closed"] or not item["shapes"]:
                return None
            use = objects[at + 1]
            if use["type"] not in ("pad", "pocket") or not use["valid"] \
                    or use["sketch"] != item["name"] or use.get("symmetric") \
                    or use.get("through_all"):
                return None
            amount = {"height": use["length"]} if use["type"] == "pad" else {"depth": use["depth"]}
            made.append({"is": use["type"], "side": item["side"], "offset": item["offset"],
                         "shapes": [{key: value for key, value in _shape(shape).items()
                                     if key not in ("first", "fixed")}
                                    for shape in item["shapes"]], **amount})
            at += 2
        elif item["type"] in ("fillet", "chamfer", "thickness"):
            if item.get("side") is None:
                return None
            made.append({"is": item["type"],
                         **_only(item, ("side", "rule", "radius", "size", "value"))})
            at += 1
        else:
            return None
    return made


def _short_body(objects: list[dict]) -> dict | None:
    """A body's one short entry (without number and place), or None if it needs detail."""
    base = _base_shape(objects)
    if base is None:
        return None
    entry, used = base
    features = _short_features(objects[used:])
    if features is None:
        return None
    return {**entry, "features": features} if features else entry


def view_state(snapshot: dict) -> dict:
    """The lean structure snapshot as a model sees it (see the top of this file)."""
    session, items = snapshot["session"], snapshot["items"]
    ids = {item["name"]: f"#{place}" for place, item in enumerate(items)}
    number = {item["name"]: item["index"] for item in items if item["type"] == "body"}
    owner = {item["name"]: item.get("body") for item in items if item["type"] != "body"}
    selection = session["selection"]
    # Bodies the open sketch or the selection points into: shown in detail.
    pointed = {owner.get(session["open_sketch"])}
    if selection is not None and selection["type"] != "plane":
        pointed |= {owner.get(selection.get(field)) for field in ("name", "of")}
    bodies = []
    for body in (item for item in items if item["type"] == "body"):
        objects = [item for item in items if item.get("body") == body["name"]]
        entry: dict = {"body": body["index"]}
        short = None if body["name"] in pointed or not body["valid"] else _short_body(objects)
        if short is not None:
            entry.update(short)
        else:
            entry.update(valid=body["valid"],
                         objects=[_object(item, ids) for item in objects])
        entry.update(at=[_clean(v) for v in body["placement"]["position"]],
                     turn=_turn(body["placement"]))
        bodies.append(entry)
    seen_selection = None
    if selection is not None:
        seen_selection = _only(selection, SELECTION_FIELDS)
        if seen_selection["type"] != "plane":       # a plane's name is "XY": a word, not an object
            seen_selection = _linked(seen_selection, ids)
    return {
        "document": session["document"], "finished": session["finished"],
        "can_undo": session["undo_depth"] > 0,
        "active": number.get(session["active_body"]),
        "open_sketch": ids.get(session["open_sketch"]),
        "selection": seen_selection,
        "bodies": bodies,
    }


# --- the checks every view passes ----------------------------------------------------------------

def check_strings(seen: dict) -> None:
    """Raise unless every string in the view is an opaque id or a word of VOCABULARY."""
    strange = sorted({text for text in _strings(seen) if text not in VOCABULARY
                      and not (text.startswith("#") and text[1:].isdigit())})
    if strange:
        raise ValueError(f"the view holds strings that are not in load.VOCABULARY: {strange}")


# No dictionary anywhere in a view may carry a label-side field name, or a field that says
# what the thing is or where it came from.
FORBIDDEN_KEYS = LABEL_SIDE_KEYS | {
    "name_of", "kind_of", "line", "lines", "plan_id", "where", "origin", "source", "split",
    "slice", "item", "cuts", "matrix", "centre", "license", "generator_version", "code_hash",
    "mix", "selection_hash", "placement", "index", "tip", "structure", "solid"}


def check_keys(seen: dict) -> None:
    leaked = sorted(_keys(seen) & FORBIDDEN_KEYS)
    if leaked:
        raise ValueError(f"the view carries fields a model must not see: {leaked}")


def view(plan: list[dict], snapshot: dict, valid: list[str]) -> dict:
    """What the model sees at one step: the plan, the state, the commands available."""
    unknown = [name for name in valid if name not in COMMANDS]
    if unknown:
        raise ValueError(f"the valid list names commands the catalogue does not have: {unknown}")
    seen = {"plan": view_plan(plan), "state": view_state(snapshot), "valid": list(valid)}
    check_strings(seen)
    check_keys(seen)
    return seen


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


def examples_of(header: dict, records: list[dict]) -> Iterator[Example]:
    """The examples of one session. The plan is the same object at every step."""
    plan = view_plan(header["plan"])
    for record in records:
        unknown = [name for name in record["valid"] if name not in COMMANDS]
        if unknown:
            raise ValueError(f"the valid list names unknown commands: {unknown}")
        seen = {"plan": plan, "state": view_state(record["snapshot"]),
                "valid": list(record["valid"])}
        check_strings({"state": seen["state"], "valid": seen["valid"]})
        check_keys(seen["state"])
        yield Example(seen, label(record["target"]), header["session"], record["t"])


def examples(slices: tuple[str, ...] | list[str], out_dir: Path = OUT_DIR,
             shards: list[int] | None = None) -> Iterator[Example]:
    """Every step of every usable session of these slices (optionally only some shards)."""
    for _, path in complete_shards(out_dir, tuple(slices)):
        if shards is not None and int(path.name[5:9]) not in shards:
            continue
        for header, records, end in read_sessions(path):
            if usable(end):
                check_strings({"plan": view_plan(header["plan"])})
                check_keys({"plan": view_plan(header["plan"])})
                yield from examples_of(header, records)


# --- how big is a view ---------------------------------------------------------------------------

def leaves(value: object) -> int:
    """How many numbers, words and true/false a view holds: its size in token-like items
    (a dictionary key is not counted; a model's tokenizer may spend one on it or none)."""
    if isinstance(value, dict):
        return sum(leaves(inner) for inner in value.values())
    if isinstance(value, list):
        return sum(leaves(inner) for inner in value)
    return 1


def sizes(out_dir: Path, slices: list[str], shards_per_slice: int) -> dict:
    """Measure the view on recorded sessions: bytes per step, and the largest view by the
    number of bodies in the plan."""
    total = {"steps": 0, "state_bytes": 0, "plan_bytes": 0, "valid_bytes": 0, "max_view_bytes": 0}
    largest: dict[str, dict] = {}
    for name in slices:
        for _, path in complete_shards(out_dir, (name,))[:shards_per_slice]:
            for header, records, end in read_sessions(path):
                if not usable(end):
                    continue
                plan = view_plan(header["plan"])
                plan_bytes = len(json.dumps(plan, separators=(",", ":")))
                bodies = header["parts"]
                group = ("1-10" if bodies <= 10 else "11-20" if bodies <= 20 else "21-30"
                         if bodies <= 30 else "over 30")
                for example in examples_of(header, records):
                    state = len(json.dumps(example.view["state"], separators=(",", ":")))
                    valid = len(json.dumps(example.view["valid"], separators=(",", ":")))
                    total["steps"] += 1
                    total["state_bytes"] += state
                    total["plan_bytes"] += plan_bytes
                    total["valid_bytes"] += valid
                    total["max_view_bytes"] = max(total["max_view_bytes"],
                                                  state + plan_bytes + valid)
                    best = largest.setdefault(group, {"items": 0})
                    items = leaves(example.view)
                    if items > best["items"]:
                        largest[group] = {
                            "items": items, "bodies": bodies,
                            "of_which_plan": leaves(plan),
                            "of_which_state": leaves(example.view["state"]),
                            "view_bytes": state + plan_bytes + valid,
                            "session": example.session, "t": example.t}
    steps = max(total["steps"], 1)
    return {"steps_measured": total["steps"],
            "state_bytes_per_step": round(total["state_bytes"] / steps, 1),
            "plan_bytes_per_step": round(total["plan_bytes"] / steps, 1),
            "valid_bytes_per_step": round(total["valid_bytes"] / steps, 1),
            "view_bytes_per_step": round(sum(total[key] for key in (
                "state_bytes", "plan_bytes", "valid_bytes")) / steps, 1),
            "largest_view_bytes": total["max_view_bytes"],
            "largest_view_by_bodies_in_plan": dict(sorted(largest.items()))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", default=str(OUT_DIR))
    parser.add_argument("--sizes", action="store_true")
    parser.add_argument("--slices", nargs="*", default=["train", "iid", "kinds", "combo", "long"])
    parser.add_argument("--shards-per-slice", type=int, default=8)
    args = parser.parse_args()
    found = sizes(Path(args.dir), args.slices, args.shards_per_slice)
    print(json.dumps(found, indent=1))
    (Path(args.dir) / "view_sizes.json").write_text(json.dumps(found, indent=1) + "\n")


if __name__ == "__main__":
    main()
