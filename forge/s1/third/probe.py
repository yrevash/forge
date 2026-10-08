"""Hand-written probe plans, built by a model: an ANECDOTE, never a measurement.

    uv run python -m forge.s1.third.probe --checkpoint <file> --arguments model \
        data/s1/demo_stool/plan.json data/s1/demo_plate_flange/plan.json

A probe plan is a plan.json written by a person to try one thing (a 420 mm leg on a 30 mm
seat). It is never trained on and decides nothing. For each plan:
    1. the scripted teacher builds it once, to get the solid to compare with;
    2. the model builds it from an empty document (teacher-free with --arguments model);
    3. the result says: done or not, the solid, and the model's first mistakes.
One FreeCAD process, no window.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from forge.freecad.client import FreeCADClient
from forge.freecad.lean import lean
from forge.freecad.teacher import script_of, teacher
from forge.runs import start_run
from forge.s1.third.drive import episode, load_any_model, plan_of


def reference(fc: FreeCADClient, part: dict) -> dict:
    """The solid the teacher's own commands build for this plan: {"bbox", "volume"}."""
    plan = plan_of(part)
    script, reply = script_of(plan), fc.reset()
    for _ in range(2000):
        advice = teacher(plan, lean(reply["snapshot"]), script)
        target = advice.targets[0]
        reply = fc.command(target.command, **target.args)
        if target.command == "done":
            break
    solid = reply["snapshot"]["solid"]
    return {"bbox": solid["size"], "volume": solid["volume"]}      # stored rows hold the extents


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("plans", nargs="+")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--arguments", choices=("model", "teacher"), required=True)
    args = parser.parse_args()
    run_dir = start_run("s1-probes", vars(args))
    model, third = load_any_model(args.checkpoint)
    fc = FreeCADClient(exact_undo=True)
    fc.start()
    results = []
    for file in args.plans:
        probe = json.loads(Path(file).read_text())
        part = {"id": Path(file).parent.name, "plan": probe["plan"]}
        part["measured"] = reference(fc, part)
        result = episode(fc, model, third, part, 0, 0.0, args.arguments, "probe")
        results.append({"plan": file, "name": probe["name"], "teacher_solid": part["measured"],
                        "arguments": args.arguments, **result})
        print(f"{part['id']}: built right {result['success']}, said done "
              f"{result['said_done']}, steps {result['steps']} (clean length "
              f"{result['clean_length']}), own mistakes {result['command_mistakes']} command / "
              f"{result['argument_mistakes']} argument; {result['problems']}", flush=True)
    fc.close()
    (run_dir / "probes.json").write_text(json.dumps(results, indent=1) + "\n")
    print(f"run folder: {run_dir}")


if __name__ == "__main__":
    main()
