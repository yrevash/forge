"""Compare what the kernel measured with what arithmetic says a structure must be.

The expected numbers come from the structure's parts as plain numbers
(solids.py); the measured numbers come from running the generated program in
the sandbox with `structure=True`. They are produced by different code, so
agreement is evidence that the program builds the intended structure.

Checked, with the tolerances single parts use (forge/generators/base.py):
- the program ran and every part is exactly one solid;
- the number of parts and their names;
- every part's own volume and bounding box;
- the overall bounding box, and that it stands on z = 0 centred on the Z axis;
- the total volume (the sum of the parts);
- no two parts overlap;
- every part touches another, and all of them form one group;
- every joint arithmetic predicts is a touch the kernel found.
"""

from __future__ import annotations

from forge.generators.base import BBOX_TOLERANCE_MM, VOLUME_RELATIVE_TOLERANCE
from forge.generators.structures import solids as arithmetic
from forge.generators.structures.draft import Structure

MAX_LISTED = 6   # a broken structure can have dozens of problems of one sort; show a few


def check(structure: Structure, reply: dict) -> list[str]:
    """Every problem found. An empty list means the structure is verified."""
    if reply.get("status") != "ok":
        return [f"{reply.get('status')}: {reply.get('error')}"]
    measure = reply["measure"]
    measured = measure.get("structure")
    if measured is None:
        return ["the sandbox was not asked for structure measurements"]
    wanted = structure.solids
    problems: list[str] = []

    names, got_names = [s.name for s in wanted], [p["name"] for p in measured["parts"]]
    if measured["n_parts"] != len(wanted):
        problems.append(f"part count: measured {measured['n_parts']}, expected {len(wanted)}")
    if got_names != names:
        missing = sorted(set(names) - set(got_names))
        extra = sorted(set(got_names) - set(names))
        problems.append(f"part names differ: missing {missing[:MAX_LISTED]}, "
                        f"unexpected {extra[:MAX_LISTED]}")

    by_name = {p["name"]: p for p in measured["parts"]}
    part_problems = []
    for solid in wanted:
        got = by_name.get(solid.name)
        if got is None:
            continue
        if got["n_solids"] != 1:
            part_problems.append(f"{solid.name} is {got['n_solids']} solids, expected 1")
        if abs(got["volume"] - solid.volume) > VOLUME_RELATIVE_TOLERANCE * solid.volume:
            part_problems.append(f"{solid.name} volume: measured {got['volume']:.4f}, "
                                 f"expected {solid.volume:.4f}")
        for axis in range(3):
            if (abs(got["bbox_min"][axis] - solid.low[axis]) > BBOX_TOLERANCE_MM
                    or abs(got["bbox_max"][axis] - solid.high[axis]) > BBOX_TOLERANCE_MM):
                part_problems.append(
                    f"{solid.name} {'XYZ'[axis]}: measured {got['bbox_min'][axis]:.4f} to "
                    f"{got['bbox_max'][axis]:.4f}, expected {solid.low[axis]:.4f} to "
                    f"{solid.high[axis]:.4f}")
    problems += part_problems[:MAX_LISTED]

    for axis, got, want in zip("XYZ", measure["bbox"], structure.expected_bbox, strict=True):
        if abs(got - want) > BBOX_TOLERANCE_MM:
            problems.append(f"overall {axis}: measured {got:.5f}, expected {want:.5f}")
    for axis, got, want in zip("XYZ", measure["bbox_min"],
                               (-structure.expected_bbox[0] / 2, -structure.expected_bbox[1] / 2,
                                0.0), strict=True):
        if abs(got - want) > BBOX_TOLERANCE_MM:
            problems.append(f"not centred on the Z axis standing on z = 0: {axis} starts at "
                            f"{got:.5f}, expected {want:.5f}")
    want_volume = structure.expected_volume
    if abs(measure["volume"] - want_volume) > VOLUME_RELATIVE_TOLERANCE * want_volume:
        problems.append(f"total volume: measured {measure['volume']:.6f}, "
                        f"expected {want_volume:.6f}")

    problems += [f"{a} and {b} overlap by {volume:.3f} mm^3"
                 for a, b, volume in measured["overlaps"][:MAX_LISTED]]
    problems += [f"{name} touches nothing" for name in measured["untouched"][:MAX_LISTED]]
    if measured["n_groups"] != 1:
        problems.append(f"the parts form {measured['n_groups']} separate groups, expected 1")

    # Arithmetic says which parts are joined; the kernel must have found each of those.
    found = {frozenset(pair) for pair in measured["touching"]}
    unseen = [f"{a} and {b}" for (a, b), how in arithmetic.contacts(wanted).items()
              if how in ("face", "line") and frozenset((a, b)) not in found]
    if unseen:
        problems.append(f"joints the kernel did not find: {unseen[:MAX_LISTED]}")
    return problems
