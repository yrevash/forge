"""Draw a sheet of random parts from a family, so a person can look before we scale up.

No family is accepted on numbers alone: the playbook asks for a contact sheet of
random parts per family, looked at by a human, before the family enters training.

Run:    uv run python -m forge.data.contact_sheet [--families hex_nut ...] [--count 20]
Output: data/contact_sheets/<family>.png
"""

from __future__ import annotations

import argparse
import io
import random
import subprocess

from PIL import Image, ImageDraw

from forge.generators import FAMILIES
from forge.generators.base import check
from forge.runs import PROJECT_ROOT
from forge.sandbox import Sandbox

OUT_DIR = PROJECT_ROOT / "data" / "contact_sheets"
CELL = 320
LABEL_HEIGHT = 46
COLUMNS = 5


def svg_to_image(svg: str) -> Image.Image:
    png = subprocess.run(
        ["rsvg-convert", "-w", str(CELL), "-h", str(CELL), "-b", "white"],
        input=svg.encode(), capture_output=True, check=True,
    ).stdout
    # CadQuery's SVG export draws +Z pointing down the page; turn it the right way up.
    return Image.open(io.BytesIO(png)).convert("RGB").rotate(180)


def label(part) -> str:
    head = part.family
    if part.designation:
        head += "  " + " ".join(part.designation.values())
    values = "  ".join(f"{v:g}" for v in part.params.values())
    return f"{head}\n{values}"


def sheet(family_name: str, count: int, seed: int, sandbox: Sandbox) -> tuple[Image.Image, int]:
    family = FAMILIES[family_name]
    rng = random.Random(f"sheet:{seed}:{family_name}")
    parts = [family.sample(rng) for _ in range(count)]
    rows = -(-count // COLUMNS)
    image = Image.new("RGB", (COLUMNS * CELL, rows * (CELL + LABEL_HEIGHT)), "white")
    draw = ImageDraw.Draw(image)
    problems = 0
    for i, part in enumerate(parts):
        x, y = (i % COLUMNS) * CELL, (i // COLUMNS) * (CELL + LABEL_HEIGHT)
        reply = sandbox.run(part.code, svg=True)
        ok = reply["status"] == "ok" and not check(part, reply["measure"])
        if reply["status"] == "ok" and reply.get("svg"):
            image.paste(svg_to_image(reply["svg"]), (x, y))
        if not ok:
            problems += 1
            draw.rectangle([x, y, x + CELL - 1, y + CELL - 1], outline="red", width=4)
        draw.text((x + 6, y + CELL + 2), label(part)[:120], fill="black")
    return image, problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--families", nargs="*", default=sorted(FAMILIES))
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with Sandbox(timeout=30) as sandbox:
        for name in args.families:
            image, problems = sheet(name, args.count, args.seed, sandbox)
            path = OUT_DIR / f"{name}.png"
            image.save(path)
            print(f"{name:24s} {path.relative_to(PROJECT_ROOT)}  failed checks: {problems}")


if __name__ == "__main__":
    main()
