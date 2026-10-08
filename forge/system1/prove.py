"""Proof that the engine is right.

For a composed part, applying its steps one at a time and building the result
must give the same solid as the part's verified one-go program. Four checks:

On EVERY part (plain Python, a few seconds):
  1. round trip    params -> steps -> params gives the same parameters back;
  2. no false "no" the rule check accepts every step of every real part;
  3. same program  `build` of the final state is, character for character, the
                   stored program.
On a SAMPLE of parts, on the CAD kernel, through the sandbox:
  4. same solid    the built state measures the same as the stored part: bounding
                   box within 0.001 mm, volume within one part in a million, the
                   same geometry fingerprint, and it passes the generator's own
                   arithmetic check.

Check 3 already implies check 4 (same text, same solid). Check 4 is run anyway:
it is the claim in the contract, and it would catch a stored row that no longer
matches what the kernel builds today.

Run:    uv run python -m forge.system1.prove [--kernel-parts 4000] [--workers 7]
Exit code 0 only if nothing failed.
"""

from __future__ import annotations

import argparse
import heapq
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from forge.generators.base import BBOX_TOLERANCE_MM, VOLUME_RELATIVE_TOLERANCE
from forge.generators.base import check as arithmetic_check
from forge.runs import log_metrics, start_run
from forge.sandbox import Sandbox
from forge.system1 import engine
from forge.system1.parts import BASES, LONG_DIR, read_parts
from forge.system1.splits import unit_hash
from forge.system1.steps import params_of, steps_of

_local = threading.local()


def final_state(part: dict, stats: Counter) -> engine.State:
    """Apply a part's steps in order to an empty state, counting anything that goes wrong."""
    steps = steps_of(part["family"], part["params"])
    family, params = params_of(steps)
    same_types = all(type(a) is type(b) for a, b in
                     zip(params.values(), part["params"].values(), strict=True))
    if (family, params, list(params)) != (part["family"], part["params"], list(part["params"])) \
            or not same_types:
        stats["round_trip_failures"] += 1
    state = engine.State([])
    for step in steps:
        stats["steps"] += 1
        if engine.apply(state, step) != "ok":
            stats["false_rejections"] += 1
    return state


def same_solid(part: dict) -> list[str]:
    """Build a part step by step on the kernel; list every way it differs from the stored one."""
    if not hasattr(_local, "sandbox"):
        _local.sandbox = Sandbox(timeout=30)
    state = final_state(part, Counter())
    reply = _local.sandbox.run(engine.build(state))
    if reply["status"] != "ok":
        return [f"{reply['status']}: {reply.get('error')}"]
    got, want = reply["measure"], part["measured"]
    problems = arithmetic_check(engine.expected(state), got)
    problems += [f"bbox {axis}: {a:.5f} against stored {b:.5f}"
                 for axis, a, b in zip("XYZ", got["bbox"], want["bbox"], strict=True)
                 if abs(a - b) > BBOX_TOLERANCE_MM]
    if abs(got["volume"] - want["volume"]) > VOLUME_RELATIVE_TOLERANCE * want["volume"]:
        problems.append(f"volume {got['volume']:.6f} against stored {want['volume']:.6f}")
    if got["fingerprint"] != part["geom_fingerprint"]:
        problems.append(f"fingerprint {got['fingerprint']} against stored {part['geom_fingerprint']}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--kernel-parts", type=int, default=4000,
                        help="how many parts to build on the kernel, shared evenly between "
                             "the input files")
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    run_dir = start_run("system1-prove-engine", vars(args))

    stats: Counter = Counter()
    # The kernel sample: from each input file, the parts whose hash of (seed, id) is
    # smallest. Each file keeps a small heap with its largest kept hash on top, ready to be
    # pushed out, so the files are read once and never held in memory.
    files = len(BASES) + sum((LONG_DIR / f"composed_{base}.jsonl").exists() for base in BASES)
    per_file = -(-args.kernel_parts // files)     # rounded up
    chosen: dict[tuple[str, bool], list] = {}
    started = time.time()
    for part in read_parts(full=True):
        stats["parts"] += 1
        state = final_state(part, stats)
        if engine.build(state) != part["code"]:
            stats["program_differs"] += 1
        heap = chosen.setdefault((part["family"], part["long"]), [])
        item = (-unit_hash(f"{args.seed}:{part['id']}"), part["id"], part)
        if len(heap) < per_file:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    sample = [part for heap in chosen.values() for _, _, part in sorted(heap, key=lambda i: i[1])]
    print(f"all {stats['parts']} parts, {stats['steps']} steps ({time.time() - started:.1f}s):")
    print(f"  round-trip failures  {stats['round_trip_failures']}")
    print(f"  false rejections     {stats['false_rejections']}")
    print(f"  program text differs {stats['program_differs']}")

    started = time.time()
    by_file: Counter = Counter()
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for part, problems in zip(sample, pool.map(same_solid, sample), strict=True):
            by_file[(part["family"], "long" if part["long"] else "1-5", not problems)] += 1
            if problems:
                failures.append((part["id"], problems))
    print(f"kernel: {len(sample)} parts built step by step ({time.time() - started:.1f}s), "
          f"{len(failures)} differ from the stored solid")
    for (family, length, ok), count in sorted(by_file.items()):
        print(f"  {family:18s} {length:5s} {'same solid' if ok else 'DIFFERENT'} {count}")
    for part_id, problems in failures[:20]:
        print(f"  {part_id}: {problems}")

    bad = (stats["round_trip_failures"] + stats["false_rejections"] + stats["program_differs"]
           + len(failures))
    log_metrics(run_dir, final=True, **stats, kernel_parts=len(sample),
                kernel_failures=len(failures))
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
