"""Resolve, build and draw plans.                                              [command]

    uv run python -m forge.resolve.build PLAN_FILE_OR_DIR --out DIR

For every plan (`.txt` or `.plan`, one plan per file) this prints the executor's reply
to each line, and when the plan is complete writes into DIR:

    NAME.step    the assembly, one named solid per part
    NAME.py      the reference CadQuery program that built it
    NAME.png     a shaded picture
    NAME.gif     the build, one frame per accepted line
    summary.json reader / resolver / build results for every plan

The program is run only in forge.sandbox. The sandbox may write only into its own
temporary folder, so the program writes the STEP and BREP files there and this
command copies them out before the sandbox is closed.

Options: `--fix-commas` applies comma_fix.py in memory (old Draft 1 plans);
`--partial` also builds what was accepted of a plan that is not complete;
`--workers N` (at most 3) builds several plans at once.
"""

from __future__ import annotations

import argparse
import json
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from forge.plan import parse_plan
from forge.resolve import render
from forge.resolve import space as sp
from forge.resolve.comma_fix import fix_commas
from forge.resolve.judge import KernelJudge
from forge.resolve.resolver import Resolution, resolve_plan
from forge.resolve.verify import verify
from forge.sandbox import Sandbox

MAX_WORKERS = 3


def plan_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.iterdir() if p.suffix in (".txt", ".plan"))


def reply_lines(resolution: Resolution) -> list[str]:
    """The line-by-line replies as text: number, reply, and how the line was read."""
    out = []
    for reply in resolution.replies:
        out.append(f"{reply.number:3d}  {reply.reply:<34}| {reply.echo}")
        out += [f"       - {note}" for note in reply.notes]
    return out


def _draw(resolution: Resolution, work_dir: Path, out: Path, name: str, title: str) -> None:
    """The picture and the animation, from the BREP files the reference program wrote."""
    import cadquery as cq

    final = resolution.bodies
    index_of = {body.name: index for index, body in enumerate(final)}
    owners = list(dict.fromkeys(body.owner for body in final))
    whole = sp.around([body.frame for body in final])
    loaded: dict[tuple[int, int], cq.Shape] = {}

    def shape(body_name: str, features: int) -> cq.Shape | None:
        key = (index_of[body_name], features)
        path = work_dir / f"body_{key[0]}_{key[1]}.brep"
        if key not in loaded and path.exists():
            loaded[key] = cq.Shape.importBrep(str(path))
        return loaded.get(key)

    def parts(bodies: list) -> list:
        drawn = []
        for body in bodies:
            if body.name in index_of and (made := shape(body.name, len(body.cuts))) is not None:
                drawn.append((body.name, made, render.colour(owners.index(body.owner))))
        return drawn

    texts = {reply.number: reply.text for reply in resolution.replies}
    frames, last = [], None
    for number, bodies in resolution.stages:
        state = [(body.name, len(body.cuts)) for body in bodies]
        if not bodies or state == last:
            continue                    # `group`, `end`, `done`: nothing new to show
        last = state
        frames.append(render.picture(parts(bodies), f"line {number}: {texts[number]}",
                                     out / f"{name}.frame.png", whole))
    (out / f"{name}.frame.png").unlink(missing_ok=True)
    render.animation(frames, out / f"{name}.gif")
    render.picture(parts(final), title, out / f"{name}.png", whole)


def build_one(path: Path, out: Path, fix: bool = False, partial: bool = False) -> dict:
    """Everything for one plan. Returns its row of the summary (and what to print)."""
    name = path.stem
    text = path.read_text()
    plan = parse_plan(fix_commas(text) if fix else text)
    row = {"plan": name, "reader_accepts": plan.accepted, "resolver_completes": False,
           "builds_and_passes": False, "parts": 0, "problems": [], "rejected": []}
    with Sandbox() as sandbox:
        resolution = resolve_plan(plan, KernelJudge(sandbox))
        row["resolver_completes"] = resolution.complete
        row["parts"] = len(resolution.bodies)
        row["rejected"] = [{"line": r.number, "reply": r.reply, "text": r.text, "notes": r.notes}
                           for r in resolution.replies if not r.built]
        row["pairs_by_arithmetic"] = resolution.pairs_by_arithmetic
        row["pairs_by_kernel"] = resolution.pairs_by_kernel
        row["print"] = [f"== {name}", *reply_lines(resolution)]
        if resolution.bodies and (resolution.complete or partial):
            comments = {r.text.split(":")[0].strip().lower(): r.text for r in resolution.replies}
            verdict = verify(resolution.bodies, resolution.touching, sandbox, comments,
                             export=True)
            row["problems"] = verdict.problems
            row["builds_and_passes"] = resolution.complete and verdict.passed
            row["print"].append("   build: " + ("every check passed" if verdict.passed
                                                else "; ".join(verdict.problems)))
            work_dir = Path(sandbox._work_dir)      # the only folder the sandbox may write in
            if (work_dir / "assembly.step").exists():
                out.mkdir(parents=True, exist_ok=True)
                shutil.copy(work_dir / "assembly.step", out / f"{name}.step")
                (out / f"{name}.py").write_text(verdict.program)
                status = "" if row["builds_and_passes"] else "  [NOT COMPLETE OR CHECKS FAILED]"
                _draw(resolution, work_dir, out, name, f"{name}: {len(resolution.bodies)} parts"
                      + status)
                for leftover in work_dir.glob("*.brep"):
                    leftover.unlink()
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve, build and draw plans.")
    parser.add_argument("path", type=Path, help="a plan file, or a folder of them")
    parser.add_argument("--out", type=Path, required=True, help="folder for the results")
    parser.add_argument("--fix-commas", action="store_true",
                        help="join comma-separated sizes with `by`, in memory only")
    parser.add_argument("--partial", action="store_true",
                        help="also build the accepted part of an incomplete plan")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    files = plan_files(args.path)
    args.out.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(args.workers, MAX_WORKERS, len(files) or 1))
    if workers == 1:
        rows = [build_one(path, args.out, args.fix_commas, args.partial) for path in files]
    else:
        with ProcessPoolExecutor(workers) as pool:
            rows = list(pool.map(build_one, files, [args.out] * len(files),
                                 [args.fix_commas] * len(files), [args.partial] * len(files)))
    for row in rows:
        print("\n".join(row.pop("print")))
    counts = {key: sum(1 for row in rows if row[key])
              for key in ("reader_accepts", "resolver_completes", "builds_and_passes")}
    print(f"\n{len(rows)} plans: reader accepts {counts['reader_accepts']}, resolver completes "
          f"{counts['resolver_completes']}, build and pass every check "
          f"{counts['builds_and_passes']}")
    (args.out / "summary.json").write_text(json.dumps({"counts": counts, "plans": rows}, indent=1))
    print(f"written to {args.out}/")


if __name__ == "__main__":
    main()
