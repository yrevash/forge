"""Find FreeCAD's own Python interpreter.

FreeCAD ships a complete Python (3.11 in FreeCAD 1.1) with the `FreeCAD`, `Part`
and `Sketcher` modules built for it. Our code runs on Python 3.12 and cannot
import those, so it starts FreeCAD's interpreter as a separate process instead.

Set FORGE_FREECAD_PYTHON and FORGE_FREECAD_LIB to use a FreeCAD somewhere else.
"""

from __future__ import annotations

import os
from pathlib import Path

# (the interpreter, the folder that holds FreeCAD.so) for the usual install places.
_CANDIDATES = (
    ("/Applications/FreeCAD.app/Contents/Resources/bin/python",
     "/Applications/FreeCAD.app/Contents/Resources/lib"),
    ("/usr/lib/freecad/bin/python3", "/usr/lib/freecad/lib"),
)


def freecad_python() -> tuple[Path, Path] | None:
    """(interpreter, lib folder) of an installed FreeCAD, or None if there is none."""
    chosen = (os.environ.get("FORGE_FREECAD_PYTHON"), os.environ.get("FORGE_FREECAD_LIB"))
    for python, lib in ((chosen,) if all(chosen) else _CANDIDATES):
        if Path(python).exists() and Path(lib).exists():
            return Path(python), Path(lib)
    return None


def freecad_available() -> bool:
    return freecad_python() is not None
