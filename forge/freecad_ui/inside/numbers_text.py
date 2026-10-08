"""How a number is typed into a field. Plain Python: no FreeCAD needed."""

from __future__ import annotations


def number_text(value) -> str:
    """A number as a person would type it: no exponent, no trailing zeros."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"not a number: {value!r}")  # noqa: TRY004 - callers catch ValueError
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{float(value):.10f}".rstrip("0").rstrip(".")
