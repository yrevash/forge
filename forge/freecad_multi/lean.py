"""The lean snapshot for structures: what a session record keeps of the runtime's snapshot.

Same idea as forge/freecad/lean.py: keep everything a decision can depend on, drop what
is bulky or derived. For a structure the bodies' solids are NOT dropped: each body's
bounding box in the structure is how the session shows where a part ended up.

    dropped                           kept in its place
    sketch `geometry`, `constraints`  `n_geometry`, `n_constraints`; the dimensions given so
                                      far are in `shapes[...]["fixed"]`, with their values
    solid `size`                      nothing: it is bbox max minus bbox min
    volumes' last digits              rounded to a millionth of a cubic millimetre (the
                                      kernel's last binary digits differ between two
                                      identical builds)
"""

from __future__ import annotations

from forge.freecad.lean import VOLUME_DIGITS, VOLUME_TOLERANCE, lean_solid


def _lean_item(item: dict) -> dict:
    if item["type"] == "body":
        return {**item, "solid": lean_solid(item["solid"])}
    if item["type"] == "sketch":
        kept = {key: value for key, value in item.items()
                if key not in ("geometry", "constraints")}
        kept["n_geometry"] = len(item["geometry"])
        kept["n_constraints"] = len(item["constraints"])
        return kept
    return item


def lean(snapshot: dict) -> dict:
    """The runtime's snapshot, cut down to what a record stores."""
    structure = snapshot["structure"]
    if structure is not None:
        structure = {key: value for key, value in structure.items() if key != "size"}
        structure["volume"] = round(structure["volume"], VOLUME_DIGITS)
        structure["overlapping"] = [[a, b, round(volume, VOLUME_DIGITS)]
                                    for a, b, volume in structure["overlapping"]]
    return {"items": [_lean_item(item) for item in snapshot["items"]],
            "session": snapshot["session"],
            "solid": lean_solid(snapshot["solid"]),
            "structure": structure}


def _volumes_out(snapshot: dict) -> tuple[dict, list[float]]:
    """A lean snapshot with every volume taken out, and those volumes."""
    items, volumes = [], []
    for item in snapshot["items"]:
        if item["type"] == "body" and item["solid"]:
            volumes.append(item["solid"]["volume"])
            item = {**item, "solid": {**item["solid"], "volume": None}}
        items.append(item)
    solid, structure = snapshot["solid"], snapshot["structure"]
    if solid:
        volumes.append(solid["volume"])
        solid = {**solid, "volume": None}
    if structure:
        volumes += [structure["volume"], *(pair[2] for pair in structure["overlapping"])]
        structure = {**structure, "volume": None,
                     "overlapping": [pair[:2] for pair in structure["overlapping"]]}
    return {"items": items, "session": snapshot["session"], "solid": solid,
            "structure": structure}, volumes


def same_lean(a: dict, b: dict) -> bool:
    """Are two lean snapshots the same state? Volumes may differ in their last digits."""
    (bare_a, volumes_a), (bare_b, volumes_b) = _volumes_out(a), _volumes_out(b)
    return bare_a == bare_b and len(volumes_a) == len(volumes_b) and all(
        abs(x - y) <= max(VOLUME_TOLERANCE * abs(x), 2 * 10.0 ** -VOLUME_DIGITS)
        for x, y in zip(volumes_a, volumes_b))
