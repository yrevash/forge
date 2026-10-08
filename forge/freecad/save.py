"""Build one part in FreeCAD and save it as an editable .FCStd document.

    uv run python -m forge.freecad.save --part 648d8771353deeb3 [--out data/freecad/<id>.FCStd]

Open the file in FreeCAD yourself: the model tree shows every sketch and
feature, and every dimension is a named constraint ("length", "diameter") you
can double-click and change. This tool never opens a FreeCAD window.
"""

from __future__ import annotations

import argparse

from forge.freecad.build import against_stored, build_part
from forge.freecad.client import FreeCADClient
from forge.freecad.parts import find_row
from forge.runs import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--part", required=True)
    parser.add_argument("--out", help="where to write the file (default data/freecad/<id>.FCStd)")
    args = parser.parse_args()
    row = find_row(args.part)
    if row is None:
        raise SystemExit(f"no composed part with id {args.part}")
    out = args.out or PROJECT_ROOT / "data" / "freecad" / f"{row['id']}.FCStd"
    with FreeCADClient() as fc:
        result = build_part(fc, row["family"], row["params"])
        problems = result.problems or against_stored(result.snapshot["solid"], row["measured"])
        if problems:
            raise SystemExit(f"the build differs from the stored part: {problems}")
        path = fc.save(out)
        reopened = fc.measure_file(path)
    print(f"saved {path} ({path.stat().st_size} bytes, {result.commands} commands)")
    print(f"reopened and recomputed in FreeCAD: volume {reopened['volume']:.3f} mm3, "
          f"stored {row['measured']['volume']:.3f} mm3")


if __name__ == "__main__":
    main()
