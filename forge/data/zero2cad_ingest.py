"""Step 1 of the data factory: fetch Zero-to-CAD's CadQuery programs.

The full dataset is about 339 GB because every row carries 8 renders, an STL and
a STEP file. We need only the program text, so this reads just those columns out
of each remote parquet shard (a parquet file can be read column by column over
HTTP). That is roughly 1 GB in total.

Only the TRAIN split is touched. The dataset's own validation and test splits
are never downloaded (test sets are never read).

Run:    uv run python -m forge.data.zero2cad_ingest [--limit-shards N] [--workers 16]
Output: data/zero2cad/code/<shard>.parquet   (safe to stop and re-run; done shards are skipped)
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from huggingface_hub import HfApi, HfFileSystem

from forge.runs import PROJECT_ROOT, log_metrics, start_run

DATASET = "ADSKAILab/Zero-To-CAD-1m"
SOURCE = "zero2cad-1m"
LICENSE = "apache-2.0"
COLUMNS = ["uuid", "cadquery_file", "num_faces", "cadquery_ops_json", "cadquery_ops_count"]
OUT_DIR = PROJECT_ROOT / "data" / "zero2cad" / "code"


def _op_names(ops_json: str | bytes | None) -> list[str]:
    """Keep just the operation names (extrude, fillet, ...) for the histogram."""
    if not ops_json:
        return []
    try:
        return sorted({op.get("op_name", "") for op in json.loads(ops_json)} - {""})
    except (ValueError, AttributeError):
        return []


def _as_text(value: str | bytes) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def fetch_shard(fs: HfFileSystem, remote_path: str, revision: str) -> int:
    out_path = OUT_DIR / Path(remote_path).name
    if out_path.exists():
        return 0
    with fs.open(remote_path, "rb", revision=revision) as f:
        table = pq.ParquetFile(f).read(columns=COLUMNS)
    rows = table.to_pylist()
    out = pa.table({
        "uuid": [r["uuid"] for r in rows],
        "code": [_as_text(r["cadquery_file"]) for r in rows],
        "num_faces": [r["num_faces"] for r in rows],
        "ops_count": [r["cadquery_ops_count"] for r in rows],
        "ops": [_op_names(r["cadquery_ops_json"]) for r in rows],
        # Provenance travels with every row.
        "source": [SOURCE] * len(rows),
        "license": [LICENSE] * len(rows),
        "source_revision": [revision] * len(rows),
        "source_shard": [Path(remote_path).name] * len(rows),
    })
    tmp = out_path.with_suffix(".tmp")
    pq.write_table(out, tmp, compression="zstd")
    tmp.rename(out_path)  # rename last, so a half-written shard is never mistaken for done
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit-shards", type=int, default=None, help="fetch only the first N")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    revision = HfApi().dataset_info(DATASET).sha  # pin the exact dataset version we read
    fs = HfFileSystem()
    shards = sorted(fs.glob(f"datasets/{DATASET}/data/train/*.parquet", revision=revision))
    if args.limit_shards:
        shards = shards[: args.limit_shards]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = start_run("zero2cad-ingest", {
        "dataset": DATASET, "revision": revision, "license": LICENSE, "split": "train",
        "columns": COLUMNS, "shards": len(shards), "workers": args.workers,
    })

    started, new_rows, done, failed = time.time(), 0, 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_shard, fs, s, revision): s for s in shards}
        for future in as_completed(futures):
            done += 1
            try:
                new_rows += future.result()
            except Exception as exc:  # noqa: BLE001 - log and keep going; re-run retries it
                failed += 1
                print(f"FAILED {futures[future]}: {exc}", flush=True)
            if done % 50 == 0 or done == len(shards):
                elapsed = time.time() - started
                print(f"{done}/{len(shards)} shards, {new_rows} new rows, {failed} failed, "
                      f"{elapsed:.0f}s", flush=True)
                log_metrics(run_dir, shards_done=done, shards_total=len(shards),
                            new_rows=new_rows, failed=failed, seconds=round(elapsed))

    total_rows = sum(pq.ParquetFile(p).metadata.num_rows for p in OUT_DIR.glob("*.parquet"))
    print(f"done: {len(list(OUT_DIR.glob('*.parquet')))} shards on disk, {total_rows} rows, "
          f"{failed} failed (re-run to retry)")
    log_metrics(run_dir, final=True, rows_on_disk=total_rows, failed=failed)


if __name__ == "__main__":
    main()
