"""DEMO, written by hand: a robot dog built step by step from a plan.

Not model output and not training data. Here the list of steps is written by hand,
in the place of a PLANNER that knows what a robot dog is made of.
Plain code then builds each step and checks the result. It shows the split that
open-ended requests need: something that knows the world writes the plan, and
the step-by-step builder carries it out and verifies it.

Run:    uv run python -m forge.system1.demo_robot_dog [--open]
Output: data/system1/exports/robot_dog_demo/  (step files, robot_dog.step, build.gif)
"""

from __future__ import annotations

import argparse
import itertools
import subprocess

import cadquery as cq
from PIL import Image

from forge.runs import PROJECT_ROOT
from forge.system1.demo_chair import box, picture

OUT_DIR = PROJECT_ROOT / "data" / "system1" / "exports" / "robot_dog_demo"
PROMPT = "robot dog, body 400 long and 160 wide, back 380 high"

BODY_LENGTH, BODY_WIDTH, BACK_HEIGHT = 400.0, 160.0, 380.0
# Everything below is the planner's choice, not in the request.
BODY_HEIGHT = 120.0
LEG_X, LEG_Y = 150.0, BODY_WIDTH / 2 + 20.0     # legs sit just outside the body's sides
BODY_BOTTOM = BACK_HEIGHT - BODY_HEIGHT

GREY = cq.Color(0.72, 0.74, 0.78)
DARK = cq.Color(0.25, 0.27, 0.32)
ORANGE = cq.Color(0.95, 0.55, 0.15)
CORNERS = [(x, y) for x in (LEG_X, -LEG_X) for y in (LEG_Y, -LEG_Y)]


def plan() -> list[tuple[str, str, list[tuple[str, cq.Workplane]], cq.Color]]:
    return [
        ("body", f"body {BODY_LENGTH:g} long, {BODY_WIDTH:g} wide, top at {BACK_HEIGHT:g}",
         [("body", box(BODY_LENGTH, BODY_WIDTH, BODY_HEIGHT, (0, 0, BODY_BOTTOM)))], GREY),
        ("upper_legs", "an upper leg at each corner, against the side of the body",
         [(f"upper_leg_{i}", box(50, 40, 160, (x, y, 140))) for i, (x, y) in enumerate(CORNERS)],
         DARK),
        ("lower_legs", "a lower leg under each upper leg",
         [(f"lower_leg_{i}", box(40, 30, 120, (x, y, 20))) for i, (x, y) in enumerate(CORNERS)],
         GREY),
        ("feet", "a foot under each lower leg",
         [(f"foot_{i}", box(70, 50, 20, (x + 10, y, 0))) for i, (x, y) in enumerate(CORNERS)],
         DARK),
        ("head", "a neck at the front of the back, and a head on it",
         [("neck", box(60, 60, 70, (165, 0, BACK_HEIGHT))),
          ("head", box(140, 110, 90, (205, 0, BACK_HEIGHT + 70)))], GREY),
        ("ears", "two ears on the head",
         [(f"ear_{side}", box(15, 30, 40, (175, y, BACK_HEIGHT + 160)))
          for side, y in (("left", 35), ("right", -35))], ORANGE),
        ("tail", "a tail at the back",
         [("tail", box(20, 20, 110, (-185, 0, BACK_HEIGHT)))], ORANGE),
    ]


def check(parts: list[tuple[str, cq.Workplane]]) -> list[str]:
    """No two parts overlap, and every part touches another."""
    problems, touching = [], set()
    for (name_a, a), (name_b, b) in itertools.combinations(parts, 2):
        if a.val().intersect(b.val()).Volume() > 1e-6:
            problems.append(f"{name_a} and {name_b} overlap")
        if a.val().distance(b.val()) < 1e-6:
            touching.update({name_a, name_b})
    return problems + [f"{name} touches nothing" for name, _ in parts if name not in touching]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--open", action="store_true", help="open the result in FreeCAD")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f'request: "{PROMPT}"')
    built: list[tuple[str, cq.Workplane, cq.Color]] = []
    frames: list[Image.Image] = []
    assembly = cq.Assembly()
    for number, (name, says, parts, colour) in enumerate(plan(), start=1):
        built += [(part_name, part, colour) for part_name, part in parts]
        assembly = cq.Assembly()
        for part_name, part, part_colour in built:
            assembly.add(part, name=part_name, color=part_colour)
        path = OUT_DIR / f"step_{number:02d}_{name}.step"
        assembly.export(str(path))
        frames.append(picture(built, f"step {number}: {says}", path))
        print(f"step {number}: {says}  ({len(built)} parts so far)")

    problems = check([(n, p) for n, p, _ in built])
    print("checks:", "all passed (no overlaps, every part touches another)"
          if not problems else problems)
    assembly.export(str(OUT_DIR / "robot_dog.step"))
    frames[0].save(OUT_DIR / "build.gif", save_all=True,
                   append_images=frames[1:] + [frames[-1]] * 3, duration=1300, loop=0)
    frames[-1].save(OUT_DIR / "robot_dog.png")
    if args.open:
        subprocess.run(["open", "-a", "FreeCAD", str(OUT_DIR / "robot_dog.step")], check=False)


if __name__ == "__main__":
    main()
