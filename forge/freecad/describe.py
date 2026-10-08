"""Say a snapshot in a few plain lines, for people (the model reads the dict itself)."""

from __future__ import annotations

from forge.freecad.catalogue import DIMENSIONS
from forge.freecad.valid import is_fixed


def _n(value: object) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def describe_shape(shape: dict) -> str:
    """'circle diameter=6 x=10 (y free)': given dimensions, then the ones still free."""
    names = [name for name in DIMENSIONS[shape["shape"]]
             if not (name == "diameter" and shape["shape"] == "polygon")]
    given = [f"{name}={_n(shape[name])}" for name in names if is_fixed(shape, name)]
    free = [name for name in names if not is_fixed(shape, name)]
    sides = f"({shape['sides']} sides) " if "sides" in shape else ""
    words = [shape["shape"], sides.strip(), *given, f"({', '.join(free)} free)" if free else ""]
    return " ".join(word for word in words if word)


def describe_item(item: dict) -> str:
    kind, name = item["type"], item["name"]
    mark = "" if item["valid"] else "  ** INVALID **"
    if kind == "body":
        return f"body {name}, tip {item['tip'] or '-'}{mark}"
    if kind == "sketch":
        drawn = "; ".join(describe_shape(shape) for shape in item["shapes"]) or "empty"
        state = "closed outline" if item["closed"] else "no closed outline"
        used = f", used by {item['used_by']}" if item["used_by"] else ""
        return (f"sketch {name} on {item['plane']} at {_n(item['offset'])}: {drawn} "
                f"[{item['dof']} degrees of freedom, {state}{used}]{mark}")
    skip = ("type", "name", "body", "valid")
    numbers = " ".join(f"{key}={_n(value)}" for key, value in item.items()
                       if key not in skip and value is not None)
    return f"{kind} {name}: {numbers}{mark}"


def describe_solid(solid: dict | None) -> str:
    if solid is None:
        return "solid: none yet"
    size = " x ".join(_n(round(v, 4)) for v in solid["size"])
    return (f"solid: {solid['volume']:.3f} mm3, {size} mm, {solid['solids']} solid(s), "
            f"{'valid' if solid['valid'] else 'NOT VALID'}")


def describe(snapshot: dict) -> list[str]:
    """Three lines: the newest item, the session, the solid."""
    session = snapshot["session"]
    items = snapshot["items"]
    selection = session["selection"]
    if selection is None:
        selected = "nothing"
    elif "rule" in selection:
        selected = f"{selection['count']} {selection['type']} ({selection['rule']})"
    else:
        selected = f"{selection['type']} {selection['name']}"
    return [
        f"{len(items)} items; newest: {describe_item(items[-1]) if items else '-'}",
        f"tip {session['tip'] or '-'} | open sketch {session['open_sketch'] or '-'} | "
        f"selected {selected} | can undo {session['undo_depth']}"
        + (" | FINISHED" if session["finished"] else ""),
        describe_solid(snapshot["solid"]),
    ]
