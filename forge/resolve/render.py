"""Shaded pictures of a built structure, and a build animation.

The drawing follows forge/system1/demo_chair.py (`picture`): a cq.Assembly shown with
cadquery.vis and saved as a screenshot, with one line of text under it. Three changes:
the camera is fitted to the structure (a coin and a castle both fill the frame), each
plan line gets its own colour, and only the solids' real edges are drawn.

Nothing here runs program text. The shapes are the BREP files the sandboxed reference
program wrote (build.py copies them out of the sandbox's folder).
"""

from __future__ import annotations

import math
from pathlib import Path

import cadquery as cq
from cadquery import vis
from PIL import Image, ImageDraw

from forge.resolve.space import Box

SIZE = 640
VIEW = (0.62, -0.78, 0.50)          # from the front, a little to the right and above
VIEW_ANGLE_DEGREES = 30.0           # VTK's default camera opening
# One colour per plan line, in turn: wood tones and greys that stay apart when shaded.
PALETTE = [(0.80, 0.62, 0.40), (0.55, 0.38, 0.22), (0.72, 0.74, 0.78), (0.30, 0.33, 0.40),
           (0.95, 0.55, 0.15), (0.45, 0.62, 0.55), (0.85, 0.80, 0.60), (0.60, 0.45, 0.55)]


def colour(index: int) -> cq.Color:
    return cq.Color(*PALETTE[index % len(PALETTE)])


def camera(frame: Box) -> tuple[tuple, tuple]:
    """(position, focus) so the whole frame is in view from the VIEW direction."""
    focus = frame.mid
    norm = math.sqrt(sum(v * v for v in VIEW))
    view = tuple(v / norm for v in VIEW)
    spread = math.tan(math.radians(VIEW_ANGLE_DEGREES / 2))
    # Each corner of the frame must fall inside the camera's cone: the further a corner is
    # from the line of sight, the further back the camera has to stand.
    distance = 1e-6
    for x in (frame.low[0], frame.high[0]):
        for y in (frame.low[1], frame.high[1]):
            for z in (frame.low[2], frame.high[2]):
                rel = (x - focus[0], y - focus[1], z - focus[2])
                towards = sum(rel[k] * view[k] for k in range(3))
                aside = math.sqrt(max(0.0, sum(r * r for r in rel) - towards * towards))
                distance = max(distance, towards + aside / spread)
    position = tuple(focus[k] + view[k] * distance * 1.12 for k in range(3))
    return position, focus


def picture(parts: list[tuple[str, cq.Shape, cq.Color]], text: str, path: Path,
            frame: Box) -> Image.Image:
    """Draw the parts, write `path` (a PNG), and return the image."""
    assembly = cq.Assembly()
    for name, shape, part_colour in parts:
        assembly.add(shape, name=name, color=part_colour)
    # `edges=True` would draw every triangle of the display mesh (a cylinder turns black),
    # so the real edges of the solids are drawn as a second, black object instead.
    outline = cq.Assembly()
    outline.add(cq.Compound.makeCompound([edge for _, shape, _ in parts for edge in shape.Edges()]),
                name="edges", color=cq.Color(0.0, 0.0, 0.0))
    position, focus = camera(frame)
    shot = str(path.with_suffix(".shot.png"))
    vis.show(assembly, outline, screenshot=shot, interact=False, edges=False, trihedron=False,
             width=SIZE, height=SIZE, gradient=False, roll=0, elevation=0, azimuth=0,
             position=position, focus=focus, viewup=(0, 0, 1))
    image = Image.new("RGB", (SIZE, SIZE + 60), "white")
    image.paste(Image.open(shot).convert("RGB").resize((SIZE, SIZE)), (0, 0))
    Path(shot).unlink()
    ImageDraw.Draw(image).text((12, SIZE + 12), text[:100], fill="black")
    image.save(path)
    return image


def animation(frames: list[Image.Image], path: Path) -> None:
    """A GIF: one frame per accepted line, the last one held a little longer."""
    if frames:
        frames[0].save(path, save_all=True, append_images=frames[1:] + [frames[-1]] * 3,
                       duration=900, loop=0)
