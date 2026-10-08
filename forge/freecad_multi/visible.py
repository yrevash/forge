"""Make a saved .FCStd open with its parts VISIBLE and in view.

The problem. A FreeCAD file is a zip. `Document.xml` holds the objects; a second file,
`GuiDocument.xml`, holds what the WINDOW knows: which objects are shown, their colours,
where the camera is. A FreeCAD running in the background has no window, so it writes no
GuiDocument.xml, and a file saved that way opens with nothing to see.
(`FreeCADGui.setupWithoutGUI()` was tried: in FreeCAD 1.1.4's bundled Python it creates no
view objects and still writes no GuiDocument.xml.)

What this file does. It writes that second file itself and appends it to the zip:

  * one entry per object with a single property, `Visibility`. Shown: every body and each
    body's newest feature (its tip), which is how PartDesign shows a body. Hidden: sketches,
    older features, origins, base planes and axes. Everything not listed keeps FreeCAD's
    default (colours, line widths);
  * a camera looking at the whole structure from the front, right and above (FreeCAD's
    "isometric" direction), far enough back to see all of it.

The format was read off a file saved by FreeCAD's own window (the EngineBlock example
that ships with FreeCAD 1.1). Two details matter: GuiDocument.xml must come AFTER every
file Document.xml refers to (FreeCAD reads the zip front to back), and the camera is a
text in Open Inventor's notation.

Nothing here starts FreeCAD or opens a window.
"""

from __future__ import annotations

import math
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

# FreeCAD's isometric view as a quaternion (x, y, z, w): the turn that takes the camera's
# own axes (x right, y up, looking along -z) to the structure's.
ISOMETRIC = (0.424708, 0.17592, 0.339851, 0.820473)
MARGIN = 1.15           # the view is this much taller than the structure's diagonal

HEADER = ("<?xml version='1.0' encoding='utf-8'?>\n<!--\n FreeCAD Document, see "
          "https://www.freecad.org for more information...\n-->\n")


def _turned(quaternion: tuple[float, float, float, float], v: tuple[float, float, float]) -> tuple:
    """A vector turned by a quaternion: v + 2w(q x v) + 2 q x (q x v)."""
    x, y, z, w = quaternion
    cross = (y * v[2] - z * v[1], z * v[0] - x * v[2], x * v[1] - y * v[0])
    twice = (y * cross[2] - z * cross[1], z * cross[0] - x * cross[2], x * cross[1] - y * cross[0])
    return tuple(v[k] + 2 * w * cross[k] + 2 * twice[k] for k in range(3))


def camera(bbox: list[float]) -> str:
    """Open Inventor text for an orthographic camera that sees the whole bounding box."""
    centre = [(bbox[k] + bbox[k + 3]) / 2 for k in range(3)]
    diagonal = max(1.0, math.dist(bbox[:3], bbox[3:]))
    distance = 2 * diagonal
    towards_camera = _turned(ISOMETRIC, (0.0, 0.0, 1.0))       # the camera looks along -z
    position = [centre[k] + distance * towards_camera[k] for k in range(3)]
    x, y, z, w = ISOMETRIC
    angle = 2 * math.acos(w)
    axis = [value / math.sin(angle / 2) for value in (x, y, z)]
    return ("OrthographicCamera {\n  viewportMapping ADJUST_CAMERA\n"
            f"  position {position[0]:.6g} {position[1]:.6g} {position[2]:.6g}\n"
            f"  orientation {axis[0]:.8g} {axis[1]:.8g} {axis[2]:.8g}  {angle:.8g}\n"
            f"  nearDistance {distance - diagonal:.6g}\n  farDistance {distance + diagonal:.6g}\n"
            f"  aspectRatio 1\n  focalDistance {distance:.6g}\n"
            f"  height {MARGIN * diagonal:.6g}\n\n}}\n")


def gui_document(names: list[str], shown: set[str], bbox: list[float]) -> str:
    """The text of GuiDocument.xml: who is visible, and the camera."""
    lines = [HEADER + '<Document SchemaVersion="1">',
             f'    <ViewProviderData Count="{len(names)}">']
    for name in names:
        value = "true" if name in shown else "false"
        lines += [f'        <ViewProvider name={quoteattr(name)} expanded="0">',
                  '            <Properties Count="1" TransientCount="0">',
                  '                <Property name="Visibility" type="App::PropertyBool" status="1">',
                  f'                    <Bool value="{value}"/>',
                  "                </Property>",
                  "            </Properties>",
                  "        </ViewProvider>"]
    settings = quoteattr(camera(bbox)).replace("\n", "&#10;")
    lines += ["    </ViewProviderData>", f"    <Camera settings={settings}/>", "</Document>", ""]
    return "\n".join(lines)


def object_names(document_xml: str) -> list[str]:
    """Every object's name, from the list at the top of Document.xml's <Objects> section."""
    listed = document_xml[document_xml.index("<Objects "):document_xml.index("</Objects>")]
    return re.findall(r'<Object type="[^"]*" name="([^"]*)"', listed)


def make_visible(path: Path, snapshot: dict) -> list[str]:
    """Add GuiDocument.xml to a saved file. `snapshot`: the session's snapshot when it was
    saved (it says which objects are bodies and tips). Returns the names that are shown."""
    bodies = [item for item in snapshot["items"] if item["type"] == "body"]
    shown = {body["name"] for body in bodies} | {body["tip"] for body in bodies if body["tip"]}
    with zipfile.ZipFile(path) as saved:
        if "GuiDocument.xml" in saved.namelist():
            raise ValueError(f"{path} already has display settings")
        names = object_names(saved.read("Document.xml").decode())
    text = gui_document(names, shown, snapshot["structure"]["bbox"])
    # Appending puts the new entry last in the zip, after everything Document.xml refers to.
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as saved:
        saved.writestr("GuiDocument.xml", text)
    return sorted(shown)
