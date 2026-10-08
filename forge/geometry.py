"""Measure a CadQuery result: is it one valid solid, and what are its dimensions?

Every part that enters the dataset is measured by this one function, whether it
came from our generators or from an outside dataset. The measurements are what
the dimension checks, the leakage fingerprint and the data card are built from.
"""

from __future__ import annotations

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder

# Rounding used for the geometry fingerprint. Coarse on purpose: two programs
# that build the same shape must get the same fingerprint despite float noise.
FINGERPRINT_DECIMALS = 2


class NotASolid(ValueError):
    """The program ran but did not leave a usable solid in `result`."""


def to_shape(obj: object) -> cq.Shape:
    """Turn whatever a program left in `result` into a single cq.Shape."""
    if isinstance(obj, cq.Workplane):
        vals = [v for v in obj.vals() if isinstance(v, cq.Shape)]
        if not vals:
            raise NotASolid("Workplane holds no shapes")
        return vals[0] if len(vals) == 1 else cq.Compound.makeCompound(vals)
    if isinstance(obj, cq.Assembly):
        return obj.toCompound()
    if isinstance(obj, cq.Shape):
        return obj
    raise NotASolid(f"result is a {type(obj).__name__}, not a CadQuery object")


def cylinder_diameters(shape: cq.Shape) -> dict[str, int]:
    """Count cylindrical faces by diameter (mm, 3 decimals).

    Holes, bores and round outer walls all show up here. A through hole is
    usually one cylindrical face, sometimes two halves, so use the diameters
    as "which round features exist" and treat counts with care.
    """
    counts: dict[str, int] = {}
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            key = f"{2.0 * surface.Cylinder().Radius():.3f}"
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: float(kv[0])))


def measure(obj: object, structure: bool = False) -> dict:
    """Measure a result. Raises NotASolid if there is nothing solid to measure.

    `one_valid_solid` is the acceptance test used everywhere: exactly one
    solid, the kernel calls it valid, and it has positive volume.

    With `structure`, the answer also holds a "structure" entry for results made
    of several parts: each part's own measurements, which pairs overlap, which
    touch, and how many separate groups they form (forge/assembly_geometry.py).
    Every other entry is the same with or without it.
    """
    shape = to_shape(obj)
    solids = shape.Solids()
    if not solids:
        raise NotASolid("result contains no solid")

    bb = shape.BoundingBox()
    volume = sum(s.Volume() for s in solids)
    area = sum(s.Area() for s in solids)
    is_valid = bool(shape.isValid())
    n_faces = len(shape.Faces())

    extents = [bb.xlen, bb.ylen, bb.zlen]
    r = FINGERPRINT_DECIMALS
    fingerprint = (
        f"v{round(volume, r)}|a{round(area, r)}"
        f"|b{'x'.join(str(round(e, r)) for e in sorted(extents))}|f{n_faces}"
    )

    measured = {
        "one_valid_solid": len(solids) == 1 and is_valid and volume > 0,
        "n_solids": len(solids),
        "is_valid": is_valid,
        "volume": volume,
        "area": area,
        "bbox": extents,
        "bbox_min": [bb.xmin, bb.ymin, bb.zmin],
        "n_faces": n_faces,
        "n_edges": len(shape.Edges()),
        "cylinders": cylinder_diameters(shape),
        "fingerprint": fingerprint,
    }
    if structure:
        from forge.assembly_geometry import measure_parts

        measured["structure"] = measure_parts(obj)
    return measured
