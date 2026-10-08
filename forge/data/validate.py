"""Steps 2-3 of the data factory: execute every program in the sandbox and validate it.

A program is ACCEPTED only if it builds exactly one valid solid, its bounding box
is sane, and it exports to STEP. Everything else is REJECTED with a reason, and
the reasons are kept: the reject log is part of the deliverable.

Run:    uv run python -m forge.data.validate [--workers 7] [--limit-shards N] [--minutes M]
Input:  data/zero2cad/code/<shard>.parquet        (from forge.data.zero2cad_ingest)
Output: data/zero2cad/validated/<shard>.jsonl     (one line per program, accepted or not)

Safe to stop and re-run: a shard with an output file is skipped. The CAD kernel,
not the GPU, is the bottleneck, so this uses one sandbox per CPU worker.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyarrow.parquet as pq

from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.sandbox import Sandbox

IN_DIR = PROJECT_ROOT / "data" / "zero2cad" / "code"
OUT_DIR = PROJECT_ROOT / "data" / "zero2cad" / "validated"

TIMEOUT_SECONDS = 30.0
# "Sane" bounding box, in mm: nothing thinner than a hair, nothing bigger than a room.
MIN_EXTENT = 0.01
MAX_EXTENT = 10_000.0

_local = threading.local()
_stop = threading.Event()


def verdict(reply: dict) -> tuple[bool, str]:
    """Turn a sandbox reply into (accepted, reason)."""
    if reply["status"] != "ok":
        detail = reply.get("error_type")
        return False, f"{reply['status']}:{detail}" if detail else reply["status"]
    m = reply["measure"]
    if m["n_solids"] != 1:
        return False, f"solids:{m['n_solids']}"
    if not m["is_valid"]:
        return False, "kernel_invalid"
    if m["volume"] <= 0:
        return False, "no_volume"
    if min(m["bbox"]) < MIN_EXTENT or max(m["bbox"]) > MAX_EXTENT:
        return False, "bbox_out_of_range"
    if not reply.get("step_export_ok"):
        return False, "step_export_failed"
    return True, "accepted"


def validate_shard(shard: Path) -> Counter:
    if not hasattr(_local, "sandbox"):
        _local.sandbox = Sandbox(timeout=TIMEOUT_SECONDS)
    if _stop.is_set():
        return Counter()  # time budget used up: start no new shard (one in progress finishes)
    rows = pq.read_table(shard).to_pylist()
    reasons: Counter = Counter()
    lines = []
    for row in rows:
        reply = _local.sandbox.run(row["code"], check_export=True)
        accepted, reason = verdict(reply)
        reasons[reason] += 1
        lines.append(json.dumps({
            "uuid": row["uuid"],
            "source": row["source"],
            "source_shard": row["source_shard"],
            "accepted": accepted,
            "reason": reason,
            "seconds": reply.get("seconds"),
            "error": reply.get("error"),
            "error_line": reply.get("line"),
            "measure": reply.get("measure"),
        }))
    out_path = OUT_DIR / (shard.stem + ".jsonl")
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.rename(out_path)
    return reasons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--limit-shards", type=int, default=None)
    parser.add_argument("--minutes", type=float, default=None, help="start no new shard after this long")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    todo = [s for s in sorted(IN_DIR.glob("*.parquet"))
            if not (OUT_DIR / (s.stem + ".jsonl")).exists()]
    if args.limit_shards:
        todo = todo[: args.limit_shards]

    run_dir = start_run("zero2cad-validate", {
        "timeout_seconds": TIMEOUT_SECONDS, "min_extent_mm": MIN_EXTENT,
        "max_extent_mm": MAX_EXTENT, "workers": args.workers, "shards_todo": len(todo),
        "minutes": args.minutes, "accept_rule": "one valid solid, sane bbox, STEP export ok",
    })

    if args.minutes:
        threading.Timer(args.minutes * 60, _stop.set).start()

    started, totals, done = time.time(), Counter(), 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(validate_shard, s) for s in todo]
        for future in as_completed(futures):
            totals.update(future.result())
            done += 1
            n = sum(totals.values())
            if n and (done % 5 == 0 or done == len(todo)):
                elapsed = time.time() - started
                print(f"{done}/{len(todo)} shards | {n} programs | "
                      f"{totals['accepted'] / n:.1%} accepted | {n / elapsed:.1f} programs/s",
                      flush=True)
                log_metrics(run_dir, shards_done=done, programs=n, accepted=totals["accepted"],
                            programs_per_second=round(n / elapsed, 2), seconds=round(elapsed))

    n = sum(totals.values())
    print(f"\nthis run: {n} programs, {totals['accepted']} accepted")
    for reason, count in totals.most_common():
        print(f"  {reason:32s} {count}")
    log_metrics(run_dir, final=True, programs=n, reasons=dict(totals),
                seconds=round(time.time() - started))


if __name__ == "__main__":
    main()
