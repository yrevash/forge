"""Does the CAD kernel agree with the resolver, on thousands of random plans?   [command]

    uv run python -m forge.resolve.property_test --plans 3000 --workers 3

For every random plan (random_plans.py) the resolver's verdicts are checked against a
kernel build in forge.sandbox:

  built            The structure after the last line is built by the reference program.
                   The kernel must find: every part's frame and volume as the resolver
                   states them, no overlap that was not declared, and exactly the touching
                   pairs the resolver recorded (so every part the resolver called joined
                   is joined, and nothing it called apart touches).
  overlaps X       The state before the line plus the rejected parts is built. The kernel
                   must find shared volume between the two bodies the resolver named.
  touches nothing  Same build. The kernel must find the rejected part touching no earlier
                   part, not overlapping one, and not reaching the ground.

A verdict is counted as "arithmetic" when no pair of that line needed the kernel
(contact.py decided all of them), else as "kernel". Disagreements on arithmetic
verdicts are resolver bugs; the kernel agreeing with itself proves little.

Results go to runs/resolve-property/summary.json.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from forge.plan import parse_plan
from forge.resolve.judge import KernelJudge
from forge.resolve.program import write_program
from forge.resolve.random_plans import random_plan
from forge.resolve.resolver import TOUCHES_NOTHING, Reply, resolve_plan
from forge.resolve.verify import BBOX_TOLERANCE_MM, compare
from forge.runs import PROJECT_ROOT
from forge.sandbox import Sandbox

OUT = PROJECT_ROOT / "runs" / "resolve-property"
MAX_WORKERS = 3


def _check_rejection(reply: Reply, sandbox: Sandbox) -> str | None:
    """None when the kernel agrees with this rejection, else what it found instead."""
    attempt = reply.attempt
    built = sandbox.run(write_program(attempt.before + attempt.tried), structure=True)
    if built.get("status") != "ok":
        return f"kernel build failed: {built.get('error')}"
    measured = built["measure"]["structure"]
    overlaps = {frozenset((a, b)) for a, b, _ in measured["overlaps"]}
    if reply.reply != TOUCHES_NOTHING:
        return None if frozenset(attempt.against) in overlaps else \
            f"resolver: {attempt.against} overlap; kernel found no shared volume"
    floating, earlier = set(attempt.floating), {body.name for body in attempt.before}
    for pair in [frozenset(p) for p in measured["touching"]] + list(overlaps):
        if pair & floating and pair & earlier:
            return f"resolver: {sorted(floating)} floats; kernel: {sorted(pair)} touch"
    lows = {part["name"]: part["bbox_min"][2] for part in measured["parts"]}
    if any(abs(lows[name]) <= BBOX_TOLERANCE_MM for name in floating):
        return f"resolver: {sorted(floating)} floats; kernel: it reaches the ground"
    return None


def check_plan(seed: int, sandbox: Sandbox) -> dict:
    """Resolve one random plan and ask the kernel about every verdict."""
    text = random_plan(seed)
    resolution = resolve_plan(parse_plan(text), KernelJudge(sandbox))
    counts: Counter = Counter()
    disagreements = []
    for reply in resolution.replies:
        how = "kernel" if reply.by_kernel else "arithmetic"
        kind = reply.reply.removeprefix("rejected: ")
        for start in ("overlaps", "unknown part"):          # drop the part's name
            kind = start if kind.startswith(start) else kind
        if kind == "built" and not reply.made:
            kind = "control line accepted (undo, done)"
        checked = kind in ("built", "overlaps", "touches nothing")
        counts[f"{kind}, decided by {how}" if checked else kind] += 1
        if reply.attempt is not None and (reply.attempt.against or reply.attempt.floating):
            problem = _check_rejection(reply, sandbox)
            if problem:
                disagreements.append({"seed": seed, "line": reply.text, "by": how,
                                      "reply": reply.reply, "kernel": problem})
    if resolution.bodies:
        built = sandbox.run(write_program(resolution.bodies), structure=True)
        problems = compare(resolution.bodies, resolution.touching, built)
        # A random plan need not be one body on the ground: `done` rejects those, and
        # that reply is not what this test is about.
        problems = [p for p in problems if "separate groups" not in p and "touches nothing" not in p
                    and "reaches the ground" not in p]
        counts["final structures built by the kernel"] += 1
        counts["parts in final structures"] += len(resolution.bodies)
        counts["touching pairs compared"] += len(resolution.touching)
        if problems:
            plain = all(body.kind != "other" for body in resolution.bodies)
            disagreements.append({"seed": seed, "line": "(final structure)",
                                  "by": "arithmetic" if plain else "kernel",
                                  "reply": "built", "kernel": "; ".join(problems[:4])})
    return {"counts": dict(counts), "disagreements": disagreements,
            "lines": len(resolution.replies),
            "not_understood": sum(1 for r in resolution.replies if "not understood" in r.reply)}


def check_range(seeds: list[int]) -> list[dict]:
    with Sandbox() as sandbox:
        return [check_plan(seed, sandbox) for seed in seeds]


def run(plans: int, workers: int, first_seed: int = 0) -> dict:
    seeds = list(range(first_seed, first_seed + plans))
    chunks = [seeds[i::workers] for i in range(workers)]
    if workers == 1:
        rows = check_range(seeds)
    else:
        with ProcessPoolExecutor(workers) as pool:
            rows = [row for chunk in pool.map(check_range, chunks) for row in chunk]
    counts: Counter = Counter()
    for row in rows:
        counts.update(row["counts"])
    return {"plans": plans, "first_seed": first_seed,
            "lines": sum(row["lines"] for row in rows),
            "lines_not_understood_by_the_reader": sum(row["not_understood"] for row in rows),
            "counts": dict(sorted(counts.items())),
            "disagreements": [d for row in rows for d in row["disagreements"]]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Kernel against resolver on random plans.")
    parser.add_argument("--plans", type=int, default=3000)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--first-seed", type=int, default=0)
    args = parser.parse_args()
    summary = run(args.plans, max(1, min(args.workers, MAX_WORKERS)), args.first_seed)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"{summary['plans']} random plans, {summary['lines']} lines "
          f"({summary['lines_not_understood_by_the_reader']} not understood by the reader)")
    for key, value in summary["counts"].items():
        print(f"  {value:7d}  {key}")
    by = Counter(d["by"] for d in summary["disagreements"])
    print(f"disagreements: {len(summary['disagreements'])} "
          f"(arithmetic verdicts: {by['arithmetic']}, kernel verdicts: {by['kernel']})")
    for d in summary["disagreements"][:20]:
        print(f"  seed {d['seed']} [{d['by']}] {d['reply']} | {d['line']} | {d['kernel']}")
    print(f"written to {OUT.relative_to(PROJECT_ROOT)}/summary.json")


if __name__ == "__main__":
    main()
