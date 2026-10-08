"""Build a sample of plans on the CAD kernel and compare with the resolver.        [command]

    uv run python -m forge.plans_data.kernel_check structures --rate 0.03 --workers 2
    uv run python -m forge.plans_data.kernel_check parts --rate 0.05 --solid-rate 0.02 --workers 2
    uv run python -m forge.plans_data.kernel_check random --rate 0.02 --workers 2 --until 05:30

A plan is in the sample when a hash of its id is below `--rate`, so the sample is the
same on every run. For each sampled plan the text is read and resolved again from
scratch, then `forge.resolve.verify` runs the resolver's reference program in the sandbox
and compares: part count and names, one solid per part, every frame, every stated
volume, the overall frame, no undeclared overlap, every part joined, and the touching
pairs. Single parts are also compared with the STORED measurements of the generator's own
part (volume, area, faces, edges), and a sub-sample by symmetric difference with the
stored program's solid. Rejected lines of the negative slice are checked the way
forge.resolve.property_test does it: the kernel must find the overlap, or the float.

(Kernel-tier random plans are not sampled here: every one of them was built on the kernel
when it was generated, and its record carries the result.)

Results: data/plans/_kernel/<raw shard>.jsonl, one line per checked plan.
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from forge.plans_data import config
from forge.plans_data.build import RAW, past
from forge.runs import log_metrics, start_run

KERNEL = config.OUT_DIR / "_kernel"
PREFIX = {"random": ("random-arithmetic", "long-arithmetic", "single-arithmetic",
                     "topup-arithmetic", "topup2-arithmetic"), "negatives": ("negatives-arithmetic",),
          "structures": ("structures-",), "parts": ("parts-",)}


def sampled(record_id: str, rate: float, salt: str = "kernel") -> bool:
    return config.unit_hash(f"{salt}:{record_id}") < rate


def _stored_parts(stem: str) -> dict[str, dict]:
    """The generator's rows for one raw shard of parts, by part id."""
    from forge.plans_data.build import PER_SHARD, part_files
    stream, _, index = stem.rpartition("-")
    path = next(p for s, p, _ in part_files() if s == stream)
    low = int(index) * PER_SHARD["parts"]
    rows = {}
    with open(path) as f:
        for number, line in enumerate(f):
            if number >= low + PER_SHARD["parts"]:
                break
            if number >= low:
                row = json.loads(line)
                rows[row["id"]] = row
    return rows


def check_shard(job: tuple) -> dict:
    path, source, rate, solid_rate = job
    stem = Path(path).name.removesuffix(".jsonl.gz")
    out = KERNEL / f"{stem}.jsonl"
    if out.exists():
        return {"shard": stem, "skipped": True}
    from forge.plans_data import parts as part_checks
    from forge.plans_data.parts import TrustStored
    from forge.plans_data.record import NoKernel, solution_of
    from forge.resolve.property_test import _check_rejection
    from forge.resolve.verify import verify
    from forge.sandbox import Sandbox

    started = time.time()
    rows, stored = [], None
    with Sandbox(timeout=300) as sandbox, gzip.open(path, "rt") as f:
        for line in f:
            record = json.loads(line)
            if not sampled(record["id"], rate):
                continue
            text = "\n".join(record["lines"]) + "\n"
            judge = TrustStored() if source == "parts" else NoKernel()
            _, resolution, solved = solution_of(text, judge)
            problems, checks = [], ["verify"]
            if solved["parts"] != record["parts"]:
                problems.append("the resolver no longer gives the stored parts")
            if source == "parts":
                stored = stored or _stored_parts(stem)
                part = stored[record["provenance"]["part_id"]]
                problems += part_checks.check_measurements(part, resolution, sandbox)
                checks.append("stored measurements")
                if sampled(record["id"], solid_rate, "solid"):
                    problems += part_checks.check_same_solid(
                        part, resolution, record["provenance"]["turned_quarter"], sandbox)
                    checks.append("same solid")
            elif resolution.bodies:
                found = verify(resolution.bodies, resolution.touching, sandbox).problems
                if source == "negatives":   # `done` was refused or never reached: not one body
                    found = [p for p in found if "separate groups" not in p
                             and "touches nothing" not in p and "reaches the ground" not in p]
                problems += found
            if source == "negatives":
                for reply in resolution.replies:
                    if reply.attempt is not None and (reply.attempt.against or reply.attempt.floating):
                        checks.append(f"rejection on line {reply.number}")
                        said = _check_rejection(reply, sandbox)
                        if said:
                            problems.append(f"line {reply.number}: {said}")
            rows.append({"id": record["id"], "source": record["source"], "checks": checks,
                         "parts": len(resolution.bodies), "passed": not problems,
                         "problems": problems[:8]})
    KERNEL.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".tmp").write_text("".join(json.dumps(row) + "\n" for row in rows))
    out.with_suffix(".tmp").rename(out)
    return {"shard": stem, "checked": len(rows), "failed": sum(not r["passed"] for r in rows),
            "seconds": round(time.time() - started, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Kernel sample of the plans data set.")
    parser.add_argument("source", choices=tuple(PREFIX))
    parser.add_argument("--rate", type=float, default=0.02)
    parser.add_argument("--solid-rate", type=float, default=0.3,
                        help="parts: the share of the checked parts also compared solid to solid")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--until")
    args = parser.parse_args()
    # In a fixed shuffled order, so that a run stopped by `--until` has checked a random
    # set of shards, not just the first ones by name.
    paths = sorted((str(p) for p in RAW.glob("*.jsonl.gz")
                    if p.name.startswith(PREFIX[args.source])),
                   key=lambda path: config.unit_hash(Path(path).name))
    jobs = [(path, args.source, args.rate, args.solid_rate) for path in paths]
    run_dir = start_run(f"plans-kernel-{args.source}", {**vars(args), "shards": len(jobs)})
    checked = failed = 0
    with ProcessPoolExecutor(min(2, args.workers)) as pool:        # never more than 2 kernels
        pending, running = iter(jobs), set()
        while True:
            while len(running) < min(2, args.workers) and not past(args.until):
                job = next(pending, None)
                if job is None:
                    break
                running.add(pool.submit(check_shard, job))
            if not running:
                break
            finished, running = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                result = future.result()
                checked += result.get("checked", 0)
                failed += result.get("failed", 0)
                log_metrics(run_dir, **result)
                print(f"[{time.strftime('%H:%M:%S')}] {result}", flush=True)
    print(f"{args.source}: {checked} plans built on the kernel, {failed} with a disagreement")


if __name__ == "__main__":
    main()
