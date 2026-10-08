"""Write a part out as one STEP file per build step, so a person can open them in FreeCAD.

This is the check you can do with your own eyes: pick a part, replay its steps
through the engine, and after every step save what exists so far as a real CAD
file. Opening them in order shows the part being built.

Run:    uv run python -m forge.system1.export_steps [--long] [--seed 3] [--open]
Output: data/system1/exports/<part id>/step_NN_<kind>.step  (+ steps.txt)

The programs run here are written by our own engine from a verified part, not by
a model, so they are run directly; anything a model writes still goes through
forge.sandbox.
"""

from __future__ import annotations

import argparse
import random
import subprocess

import cadquery as cq

from forge.runs import PROJECT_ROOT
from forge.system1.engine import State, apply, build
from forge.system1.parts import read_parts
from forge.system1.steps import steps_of

OUT_DIR = PROJECT_ROOT / "data" / "system1" / "exports"


def export_part(part: dict) -> list:
    """Replay the part's steps; after each one save the solid built so far."""
    out = OUT_DIR / part["id"]
    out.mkdir(parents=True, exist_ok=True)
    state = State(mentions=[])
    files, lines = [], []
    for number, step in enumerate(steps_of(part["family"], part["params"]), start=1):
        outcome = apply(state, step)
        if outcome != "ok":
            raise RuntimeError(f"step {number} ({step.kind}) was {outcome}")
        namespace: dict = {}
        exec(build(state), namespace)  # noqa: S102 - our own engine's program, see module docstring
        path = out / f"step_{number:02d}_{step.kind}.step"
        cq.exporters.export(namespace["result"], str(path))
        solid = namespace["result"].val()
        sizes = ", ".join(f"{name} {value:g}" for name, value in step.slots.items())
        lines.append(f"step {number:2d}  {step.kind:14s} {sizes}   -> volume {solid.Volume():.1f} mm^3")
        files.append(path)
    (out / "steps.txt").write_text("\n".join(lines) + "\n")
    print(f"part {part['id']} ({part['family']})")
    print("\n".join(lines))
    print(f"written to {out.relative_to(PROJECT_ROOT)}/")
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--long", action="store_true", help="pick one of the 6-to-12-item parts")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--open", action="store_true", help="open the last step in FreeCAD")
    args = parser.parse_args()

    parts = [p for p in read_parts(full=True, limit_per_file=400, include_long=args.long)
             if bool(p.get("long")) == args.long]
    part = random.Random(args.seed).choice(parts)
    files = export_part(part)
    if args.open:
        subprocess.run(["open", "-a", "FreeCAD", str(files[-1])], check=False)


if __name__ == "__main__":
    main()
