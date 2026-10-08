"""Draw a sheet of random structures per kind, so a person can look before we scale up.

Each cell is one random structure: a shaded picture, its level, its variant and
its first prompt. Every structure is also run through the sandbox and checked;
a red border marks one that fails.

Run:    uv run python -m forge.generators.structures.contact_sheet [--kinds chair ...] [--count 20]
Output: data/contact_sheets/structures/<kind>.png
"""

from __future__ import annotations

import argparse
import random
import tempfile
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw

from forge.generators.structures import KINDS
from forge.generators.structures.check import check
from forge.generators.structures.prompts import prompts_for
from forge.generators.structures.render import picture
from forge.generators.structures.sampling import sample
from forge.runs import PROJECT_ROOT
from forge.sandbox import Sandbox

OUT_DIR = PROJECT_ROOT / "data" / "contact_sheets" / "structures"
CELL = 380
LABEL_HEIGHT = 76
COLUMNS = 5


def sheet(kind_name: str, count: int, seed: int, sandbox: Sandbox) -> tuple[Image.Image, int]:
    kind = KINDS[kind_name]
    rng = random.Random(f"sheet:{seed}:{kind_name}")
    rows = -(-count // COLUMNS)
    image = Image.new("RGB", (COLUMNS * CELL, rows * (CELL + LABEL_HEIGHT)), "white")
    draw = ImageDraw.Draw(image)
    failed = 0
    with tempfile.TemporaryDirectory() as folder:
        for i in range(count):
            structure = sample(kind, rng)
            x, y = (i % COLUMNS) * CELL, (i // COLUMNS) * (CELL + LABEL_HEIGHT)
            shot = Path(folder) / f"{i}.png"
            picture(structure, shot, CELL)
            image.paste(Image.open(shot).convert("RGB").resize((CELL, CELL)), (x, y))
            problems = check(structure, sandbox.run(structure.code, structure=True))
            if problems:
                failed += 1
                draw.rectangle([x, y, x + CELL - 1, y + CELL - 1], outline="red", width=4)
            prompt = prompts_for(kind, structure, 1)[0][0]["text"].replace("\n", " ")
            variant = " ".join(f"{k}={v}" for k, v in structure.choices.items())
            label = f"[{structure.level}] {len(structure.solids)} parts  {variant}"
            lines = textwrap.wrap(label, 62)[:2] + textwrap.wrap(prompt, 62)[:3]
            draw.text((x + 6, y + CELL + 2), "\n".join(lines[:5]), fill="black")
    return image, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--kinds", nargs="*", default=list(KINDS))
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with Sandbox(timeout=60) as sandbox:
        for name in args.kinds:
            image, failed = sheet(name, args.count, args.seed, sandbox)
            path = OUT_DIR / f"{name}.png"
            image.save(path)
            print(f"{name:8s} {path.relative_to(PROJECT_ROOT)}  failed checks: {failed}")


if __name__ == "__main__":
    main()
