"""Build a structure in FreeCAD and save it as an editable .FCStd file a person can open.

    uv run python -m forge.freecad_multi.save --plan forge/plan/examples/chair.txt --out chair.FCStd
    uv run python -m forge.freecad_multi.save --row shelf --out shelf.FCStd      (first shelf row)
    uv run python -m forge.freecad_multi.save --examples       (the five under data/freecad_multi/examples)

The file holds the real history: every body with its sketches (dimensions are named
constraints you can double-click), pads and features, placed where the plan puts it. The
bodies carry the plan's names. `visible.py` adds the display settings a background
FreeCAD cannot write, so the parts are shown and in view when the file is opened.

This tool never opens a FreeCAD window. It reopens the saved file in the background
worker and measures every body again, to show the file is complete.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from forge.freecad_multi import sources
from forge.freecad_multi.build import build_structure
from forge.freecad_multi.client import MultiClient
from forge.freecad_multi.visible import make_visible
from forge.resolve import resolve_text
from forge.resolve.bodies import Body
from forge.runs import PROJECT_ROOT

EXAMPLES = PROJECT_ROOT / "data" / "freecad_multi" / "examples"
# name -> (how to get it, where from). Our choice of five; all are in the proof as well.
FIVE = {
    "chair": ("plan", "forge/plan/examples/chair.txt"),
    "table": ("plan", "data/coverage/tuning_plans_draft2/60_table.plan"),
    "shelf": ("row", "shelf"),
    "robot_dog": ("plan", "forge/resolve/examples/robot_dog_fixed.txt"),
    "mechanical_fan": ("plan", "data/coverage/tuning_plans_draft2/39_mechanical_fan.plan"),
}


def bodies_of(kind: str, where: str) -> tuple[list[Body], set | None]:
    if kind == "row":
        return sources.row_bodies(next(sources.structure_rows(where, 1))), None
    resolution = resolve_text((PROJECT_ROOT / where).read_text())
    if not resolution.complete:
        raise SystemExit(f"{where} does not resolve to a complete structure")
    return resolution.bodies, resolution.touching


def save_structure(client: MultiClient, bodies: list[Body], touching: set | None,
                   out: Path) -> dict:
    """Build, check, name, save, make visible, reopen. Returns a small report."""
    result = build_structure(client, bodies, touching)
    if not result.ok:
        raise SystemExit(f"the build differs from the reference: {result.problems[:5]}")
    parts = [item for item in result.items if item.kind == "part"]
    client.label_bodies([item.name for item in parts])
    path = client.save(out)
    shown = make_visible(path, result.snapshot)
    reopened = client.measure_file(path)
    before = [item["solid"]["volume"] for item in result.snapshot["items"]
              if item["type"] == "body"]
    same = len(reopened) == len(before) and all(
        abs(got["volume"] - want) <= 1e-9 * want for got, want in zip(reopened, before))
    return {"path": path, "bodies": len(parts), "commands": result.commands,
            "shown": len(shown), "reopened_same": same,
            "size": result.snapshot["structure"]["size"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--plan", help="a plan file")
    parser.add_argument("--row", help="a structure kind: its first row in data/structures")
    parser.add_argument("--examples", action="store_true", help="save the five example files")
    parser.add_argument("--out", help="where to write the file")
    args = parser.parse_args()
    if args.examples:
        jobs = [(kind, where, EXAMPLES / f"{name}.FCStd") for name, (kind, where) in FIVE.items()]
    elif (args.plan or args.row) and args.out:
        jobs = [("plan", args.plan, Path(args.out)) if args.plan
                else ("row", args.row, Path(args.out))]
    else:
        parser.error("give --examples, or --plan / --row together with --out")
    with MultiClient() as client:
        for kind, where, out in jobs:
            report = save_structure(client, *bodies_of(kind, where), out)
            size = " x ".join(f"{value:g}" for value in report["size"])
            print(f"saved {report['path']}  ({report['bodies']} bodies, {report['commands']} "
                  f"commands, {size} mm; {report['shown']} objects shown; reopened in the "
                  f"background and measured the same: {report['reopened_same']})")


if __name__ == "__main__":
    main()
