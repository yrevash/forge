"""The lean snapshot: what a session record keeps of the runtime's snapshot.

The runtime's snapshot (inside/snapshot.py) lists every line, arc and constraint
of every sketch. That is right for a person opening the document, and far too
much to store once per step: a part with six features carries about 150
constraints, and they repeat in every record of the session.

The teacher, the model and the audit all read the LEAN snapshot instead. It
keeps everything a decision can depend on and drops what is derived or bulky:

    dropped                         kept in its place
    sketch `geometry` (every line)  `n_geometry`, the number of geometry elements
    sketch `constraints`            `n_constraints`; the dimensions given so far are in
                                    `shapes[...]["fixed"]`, with their values
    body `solid`                    nothing: it is the top-level `solid` of the active body
    solid `size`                    nothing: it is bbox max minus bbox min
    solid `volume` last digits      rounded to a millionth of a cubic millimetre

The volume is rounded because FreeCAD's kernel adds up a solid's faces in an
order that depends on where they sit in memory, so the last binary digits of a
volume can differ between two identical builds (measured in undo_proof.py).
Rounding makes a rerun of the same session write the same bytes almost always;
the audit still compares volumes with a tolerance.
"""

from __future__ import annotations

VOLUME_DIGITS = 6
VOLUME_TOLERANCE = 1e-9     # relative; two builds of the same solid agree far better than this


def lean_solid(solid: dict | None) -> dict | None:
    if solid is None:
        return None
    return {"volume": round(solid["volume"], VOLUME_DIGITS), "bbox": solid["bbox"],
            "solids": solid["solids"], "valid": solid["valid"]}


def _lean_item(item: dict) -> dict:
    if item["type"] == "body":
        return {key: value for key, value in item.items() if key != "solid"}
    if item["type"] == "sketch":
        kept = {key: value for key, value in item.items()
                if key not in ("geometry", "constraints")}
        kept["n_geometry"] = len(item["geometry"])
        kept["n_constraints"] = len(item["constraints"])
        return kept
    return item


def lean(snapshot: dict) -> dict:
    """The runtime's snapshot, cut down to what a record stores."""
    return {"items": [_lean_item(item) for item in snapshot["items"]],
            "session": snapshot["session"],
            "solid": lean_solid(snapshot["solid"])}


def same_lean(a: dict, b: dict) -> bool:
    """Are two lean snapshots the same state? Volumes may differ in their last digits."""
    if a["items"] != b["items"] or a["session"] != b["session"]:
        return False
    solid_a, solid_b = a["solid"], b["solid"]
    if solid_a is None or solid_b is None:
        return solid_a is solid_b
    rest_a = {key: value for key, value in solid_a.items() if key != "volume"}
    rest_b = {key: value for key, value in solid_b.items() if key != "volume"}
    close = abs(solid_a["volume"] - solid_b["volume"]) <= max(
        VOLUME_TOLERANCE * abs(solid_a["volume"]), 2 * 10.0 ** -VOLUME_DIGITS)
    return rest_a == rest_b and close
