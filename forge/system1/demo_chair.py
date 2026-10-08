"""DEMO, written by hand: a chair built step by step, the way a structure session would go.

This is not model output and not training data. It shows what the System 1
model is meant to do once structures exist: each step adds one part
or a group of parts in a stated place, and the result is checked like our
single parts are: right overall size, nothing overlapping, parts touching.

Run:    uv run python -m forge.system1.demo_chair [--open]
Output: data/system1/exports/chair_demo/
          step_NN_<name>.step   the chair so far, after each step
          chair.step            the finished assembly, every part named
          build.gif             shaded pictures of each step
"""

from __future__ import annotations

import argparse
import itertools
import subprocess

import cadquery as cq
from cadquery import vis
from PIL import Image, ImageDraw

from forge.runs import PROJECT_ROOT

OUT_DIR = PROJECT_ROOT / "data" / "system1" / "exports" / "chair_demo"

# What the person asked for.
PROMPT = "dining chair, seat 420 by 420, seat height 450, back height 900"
SEAT, SEAT_HEIGHT, BACK_HEIGHT = 420.0, 450.0, 900.0

# Sizes the sentence does not give. In the real system these come from default
# rules in code and are reported back to the user.
SEAT_THICKNESS, LEG, RAIL_THICKNESS, APRON_HEIGHT = 30.0, 40.0, 20.0, 60.0
TOP_RAIL_HEIGHT, LOW_RAIL_HEIGHT, SLAT_WIDTH, SLAT_THICKNESS = 80.0, 40.0, 40.0, 12.0

EDGE = SEAT / 2 - LEG / 2            # leg centre, measured from the middle of the seat
BETWEEN_LEGS = SEAT - 2 * LEG        # clear span between two legs
UNDER_SEAT = SEAT_HEIGHT - SEAT_THICKNESS

WOOD = cq.Color(0.80, 0.62, 0.40)
DARK = cq.Color(0.55, 0.38, 0.22)


def box(x: float, y: float, z: float, at: tuple[float, float, float]) -> cq.Workplane:
    """A block of size x, y, z whose bottom-centre sits at `at`."""
    return cq.Workplane("XY").box(x, y, z, centered=(True, True, False)).translate(at)


def steps() -> list[tuple[str, str, list[tuple[str, cq.Workplane]], cq.Color]]:
    """(short name, what the step says, the parts it adds, colour)."""
    low_rail_bottom = SEAT_HEIGHT + 110
    top_rail_bottom = BACK_HEIGHT - 20 - TOP_RAIL_HEIGHT
    slat_bottom = low_rail_bottom + LOW_RAIL_HEIGHT
    return [
        ("seat", f"seat {SEAT:g} x {SEAT:g}, top at {SEAT_HEIGHT:g}",
         [("seat", box(SEAT, SEAT, SEAT_THICKNESS, (0, 0, UNDER_SEAT)))], WOOD),
        ("front_legs", "a leg under each front corner",
         [(f"front_leg_{side}", box(LEG, LEG, UNDER_SEAT, (x, -EDGE, 0)))
          for side, x in (("left", -EDGE), ("right", EDGE))], DARK),
        ("back_posts", f"a post at each back corner, up to {BACK_HEIGHT:g}",
         [(f"back_post_{side}", box(LEG, LEG, UNDER_SEAT, (x, EDGE, 0)))
          for side, x in (("left", -EDGE), ("right", EDGE))]
         + [(f"back_post_{side}_upper",
             box(LEG, LEG, BACK_HEIGHT - SEAT_HEIGHT, (x, EDGE, SEAT_HEIGHT)))
            for side, x in (("left", -EDGE), ("right", EDGE))], DARK),
        ("aprons", "a rail between each pair of legs, under the seat",
         [(f"apron_{name}", box(BETWEEN_LEGS, RAIL_THICKNESS, APRON_HEIGHT,
                                (0, y, UNDER_SEAT - APRON_HEIGHT)))
          for name, y in (("front", -EDGE), ("back", EDGE))]
         + [(f"apron_{name}", box(RAIL_THICKNESS, BETWEEN_LEGS, APRON_HEIGHT,
                                  (x, 0, UNDER_SEAT - APRON_HEIGHT)))
            for name, x in (("left", -EDGE), ("right", EDGE))], WOOD),
        ("back_rails", "a top rail and a lower rail between the back posts",
         [("top_rail", box(BETWEEN_LEGS, RAIL_THICKNESS, TOP_RAIL_HEIGHT,
                           (0, EDGE, top_rail_bottom))),
          ("lower_rail", box(BETWEEN_LEGS, RAIL_THICKNESS, LOW_RAIL_HEIGHT,
                             (0, EDGE, low_rail_bottom)))], WOOD),
        ("slats", "three slats between the two rails",
         [(f"slat_{i + 1}", box(SLAT_WIDTH, SLAT_THICKNESS, top_rail_bottom - slat_bottom,
                                (x, EDGE, slat_bottom)))
          for i, x in enumerate((-100.0, 0.0, 100.0))], DARK),
    ]


def check(parts: list[tuple[str, cq.Workplane]]) -> list[str]:
    """The structure checks. An empty list means the chair passes."""
    problems = []
    whole = cq.Compound.makeCompound([p.val() for _, p in parts]).BoundingBox()
    for axis, got, want in zip("XYZ", (whole.xlen, whole.ylen, whole.zlen),
                               (SEAT, SEAT, BACK_HEIGHT), strict=True):
        if abs(got - want) > 1e-3:
            problems.append(f"overall {axis} is {got:g}, expected {want:g}")
    touching = set()
    for (name_a, a), (name_b, b) in itertools.combinations(parts, 2):
        common = a.val().intersect(b.val())
        if common.Volume() > 1e-6:
            problems.append(f"{name_a} and {name_b} overlap by {common.Volume():.2f} mm^3")
        if a.val().distance(b.val()) < 1e-6:
            touching.update({name_a, name_b})
    problems += [f"{name} touches nothing" for name, _ in parts if name not in touching]
    return problems


def picture(parts: list[tuple[str, cq.Workplane, cq.Color]], text: str, path) -> Image.Image:
    assembly = cq.Assembly()
    for name, part, colour in parts:
        assembly.add(part, name=name, color=colour)
    shot = str(path.with_suffix(".png"))
    vis.show(assembly, screenshot=shot, interact=False, edges=True, trihedron=False,
             width=640, height=640, gradient=False, roll=0, elevation=0, azimuth=0,
             position=(1500, -1900, 1500), focus=(0, 0, 430), viewup=(0, 0, 1))
    frame = Image.new("RGB", (640, 700), "white")
    frame.paste(Image.open(shot).convert("RGB").resize((640, 640)), (0, 0))
    ImageDraw.Draw(frame).text((12, 656), text, fill="black")
    return frame


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--open", action="store_true", help="open the finished chair in FreeCAD")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f'request: "{PROMPT}"')
    built: list[tuple[str, cq.Workplane, cq.Color]] = []
    frames = []
    for number, (name, says, parts, colour) in enumerate(steps(), start=1):
        built += [(part_name, part, colour) for part_name, part in parts]
        so_far = cq.Assembly()
        for part_name, part, part_colour in built:
            so_far.add(part, name=part_name, color=part_colour)
        path = OUT_DIR / f"step_{number:02d}_{name}.step"
        so_far.save(str(path))
        frames.append(picture(built, f"step {number}: {says}", path))
        print(f"step {number}: {says}  ({len(parts)} parts, {len(built)} so far)")

    problems = check([(n, p) for n, p, _ in built])
    print("checks:", "all passed (overall size, no overlaps, every part touches another)"
          if not problems else problems)
    so_far.save(str(OUT_DIR / "chair.step"))
    frames[0].save(OUT_DIR / "build.gif", save_all=True,
                   append_images=frames[1:] + [frames[-1]] * 3, duration=1300, loop=0)
    frames[-1].save(OUT_DIR / "chair.png")
    print(f"written to {OUT_DIR.relative_to(PROJECT_ROOT)}/")
    if args.open:
        subprocess.run(["open", "-a", "FreeCAD", str(OUT_DIR / "chair.step")], check=False)


if __name__ == "__main__":
    main()
