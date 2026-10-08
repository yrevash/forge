"""Print how a structure is built in FreeCAD, item by item and command by command.

    uv run python -m forge.freecad_multi.show --plan forge/plan/examples/chair.txt
    uv run python -m forge.freecad_multi.show --row stool [--snapshot] [--teacher]

Without options nothing is started: the recipe is printed from the resolved plan alone
(`{ ... }` marks commands that may come in any order). `--snapshot` builds the structure
in a background FreeCAD and prints the final snapshot, bodies and structure only.
`--teacher` lets the teacher drive instead of the script and prints what it said.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forge.freecad_multi import sources
from forge.freecad_multi.build import run_script
from forge.freecad_multi.client import MultiClient
from forge.freecad_multi.lean import lean
from forge.freecad_multi.recipes import AnyOrder, Cmd, script_of, structure_items
from forge.freecad_multi.teacher import teacher
from forge.resolve import resolve_text


def _words(command: Cmd) -> str:
    args = " ".join(f"{name}={value}" for name, value in command.args.items())
    return f"{command.name} {args}".strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--plan", help="a plan file")
    parser.add_argument("--row", help="a structure kind: its first row in data/structures")
    parser.add_argument("--snapshot", action="store_true", help="build it, print the snapshot")
    parser.add_argument("--teacher", action="store_true", help="let the teacher drive")
    args = parser.parse_args()
    if args.plan:
        bodies = resolve_text(Path(args.plan).read_text()).bodies
    elif args.row:
        bodies = sources.row_bodies(next(sources.structure_rows(args.row, 1)))
    else:
        parser.error("give --plan or --row")
    items = structure_items(bodies)
    script = script_of(items)
    shown = None
    for index, entry in script:
        if index != shown and index is not None:
            item = items[index]
            where = f"{item.shape}, {item.placement}" if item.kind == "part" else "feature"
            print(f"\nitem {index}: {item.name}  (body {item.body}; {where})")
            shown = index
        text = ("{ " + ", ".join(_words(c) for c in entry.commands) + " }"
                if isinstance(entry, AnyOrder) else _words(entry))
        print(f"    {text}")
    if not (args.snapshot or args.teacher):
        return
    with MultiClient() as fc:
        if args.teacher:
            reply = fc.reset()
            while True:
                advice = teacher(items, lean(reply["snapshot"]), script)
                if not advice.targets:
                    break
                target = advice.targets[0]
                print(f"teacher: {target.command} {target.args}   ({advice.why})")
                reply = fc.command(target.command, **target.args)
            snapshot = reply["snapshot"]
        else:
            snapshot = run_script(fc, items).snapshot
    small = lean(snapshot)
    print("\nbodies:")
    for item in small["items"]:
        if item["type"] == "body":
            print("  " + json.dumps(item))
    print("structure:\n  " + json.dumps(small["structure"]))


if __name__ == "__main__":
    main()
