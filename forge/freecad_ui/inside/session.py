"""What the runtime itself has to remember between requests. Runs inside FreeCAD.

Almost everything is read fresh from FreeCAD each time. The one thing FreeCAD
does not tell us is how far a sketcher drawing tool has got: the on-view fields
have no names, so we count which of the tool's numbers have been entered
(tools.py).
"""

from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui
from tools import TOOL_PHASES, Tool  # noqa: F401  (TOOL_PHASES is re-exported)

tool = Tool()


def document():
    return App.ActiveDocument


def sketch_in_edit():
    """The sketch object that is open for editing, or None."""
    gui_document = Gui.ActiveDocument
    provider = gui_document.getInEdit() if gui_document is not None else None
    if provider is None or provider.Object.TypeId != "Sketcher::SketchObject":
        return None
    return provider.Object


def active_body():
    """The body new features go into: the document's first PartDesign body."""
    doc = document()
    if doc is None:
        return None
    for obj in doc.Objects:
        if obj.TypeId == "PartDesign::Body":
            return obj
    return None
