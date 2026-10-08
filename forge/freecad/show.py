"""Print the command-by-command build of one part in FreeCAD, in plain words.

    uv run python -m forge.freecad.show --part 648d8771353deeb3
    uv run python -m forge.freecad.show --part 648d8771353deeb3 --save data/freecad/part.FCStd
    uv run python -m forge.freecad.show --part 648d8771353deeb3 --valid   (also list valid commands)

For each command: the command and its arguments, FreeCAD's answer, then the
snapshot in three lines (the newest item, the session, the measured solid).
`--save` writes the finished document as an .FCStd file you can open and edit
in FreeCAD. This tool never opens a FreeCAD window.
"""

from __future__ import annotations

import argparse

from forge.freecad.build import against_stored, build_part
from forge.freecad.client import FreeCADClient
from forge.freecad.describe import describe
from forge.freecad.parts import find_row
from forge.freecad.recipes import Cmd
from forge.system1.steps import Step


def show(part_id: str, save: str | None = None, list_valid: bool = False) -> int:
    row = find_row(part_id)
    if row is None:
        print(f"no composed part with id {part_id}")
        return 2
    number = 0
    current: list[Step | None] = [None]

    def on_command(step: Step, command: Cmd, reply: dict) -> None:
        nonlocal number
        if step is not current[0]:
            current[0] = step
            slots = " ".join(f"{name}={value:g}" for name, value in step.slots.items())
            print(f"\nSTEP {step.kind}  {slots}")
        number += 1
        args = " ".join(f"{name}={value}" for name, value in command.args.items())
        print(f"  [{number}] {command.name} {args}".rstrip() + f"  -> {reply['status']}"
              + (f" ({reply['reason']})" if "reason" in reply else ""))
        for line in describe(reply["snapshot"]):
            print(f"        {line}")
        if list_valid:
            print(f"        valid next: {', '.join(reply['valid'])}")

    print(f"part {row['id']}  {row['family']}{'  (long)' if row['long'] else ''}")
    with FreeCADClient() as fc:
        result = build_part(fc, row["family"], row["params"], on_command=on_command)
        problems = result.problems or against_stored(result.snapshot["solid"], row["measured"])
        print(f"\n{number} commands. Stored measurement: volume {row['measured']['volume']:.3f} "
              f"mm3, size {row['measured']['bbox']}.")
        print("FreeCAD built the same solid." if not problems else f"DIFFERENT: {problems}")
        if save:
            print(f"saved {fc.save(save)}")
    return 1 if problems else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--part", required=True, help="the part's id (first field of its row)")
    parser.add_argument("--save", help="also save the finished document to this .FCStd path")
    parser.add_argument("--valid", action="store_true",
                        help="after each command, list the commands that are valid next")
    args = parser.parse_args()
    raise SystemExit(show(args.part, args.save, args.valid))


if __name__ == "__main__":
    main()
