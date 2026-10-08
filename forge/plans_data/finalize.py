"""Turn the raw shards into the data set: data/plans/<source>/<split>/shardNNN.jsonl.gz. [command]

    uv run python -m forge.plans_data.finalize

Reads data/plans/_raw/ in name order (so the result does not depend on which shard was
built first), drops a plan whose text already occurred in its source, adds the kernel
sample's verdict to the plans that were sampled, and writes

    data/plans/<source>/<split>/shardNNN.jsonl.gz     one record per plan
    data/plans/rejects/<stream>.jsonl                 everything that was not kept, with reasons
    data/plans/manifest.json                          counts, the coverage table, hashes

The coverage table counts cells twice: over every kept plan, and over the TRAIN split
alone. The minimums are checked against the train counts, because a form that is only in
a test split teaches the model nothing.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path

from forge.plans_data import config, table
from forge.plans_data.build import _VERSIONS, RAW
from forge.plans_data.kernel_check import KERNEL
from forge.runs import log_metrics, start_run
from forge.system1.splits import HELD_OUT_PAIRS

SOURCE_OF_STREAM = (("random-", "random"), ("single-", "random"), ("featured-", "random"),
                    ("topup-", "random"), ("topup2-", "random"), ("ktopup-", "random"), ("ktopup2-", "random"),
                    ("ktopup3-", "random"),
                    ("long-", "random"), ("negatives-", "negatives"),
                    ("structures-", "structures"), ("parts-", "parts"))


def source_of(stream: str) -> str:
    return next(source for prefix, source in SOURCE_OF_STREAM if stream.startswith(prefix))


class Writer:
    """Writes one split of one source as numbered shards and remembers their hashes.

    A shard is written to a temp file and renamed over the old one in one step, so a
    reader never sees half a file; a shard whose bytes did not change is left alone.
    """

    def __init__(self, folder: Path) -> None:
        self.folder, self.rows, self.number, self.file = folder, 0, 0, None
        self.files: dict[str, dict] = {}
        folder.mkdir(parents=True, exist_ok=True)

    def add(self, line: str) -> None:
        if self.file is None or self.rows == config.SHARD_ROWS:
            self.close()
            self.path = self.folder / f"shard{self.number:03d}.jsonl.gz"
            self.raw = open(f"{self.path}.tmp", "wb")           # noqa: SIM115 - closed in close()
            # mtime=0: the same records always give the same bytes
            self.file = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, compresslevel=5,
                                      mtime=0)
            self.number += 1
            self.rows = 0
        self.file.write(line.encode())
        self.rows += 1

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.raw.close()
            temp = Path(f"{self.path}.tmp")
            digest = hashlib.sha256(temp.read_bytes()).hexdigest()
            if self.path.exists() and hashlib.sha256(self.path.read_bytes()).hexdigest() == digest:
                temp.unlink()
            else:
                temp.replace(self.path)
            self.files[str(self.path.relative_to(config.OUT_DIR))] = {"rows": self.rows,
                                                                    "sha256": digest}
            self.file = None


def raw_order() -> list[Path]:
    """The raw shards in the order they are written out.

    Shards that an earlier finalize already wrote keep their order (the manifest remembers
    it); shards built since come after them, by name. So a plan that is already in
    `<source>/<split>/shardNNN` stays in that file at that row: new plans are only appended.
    """
    found = {path.stem: path for path in RAW.glob("*.json")}
    manifest_path = config.OUT_DIR / "manifest.json"
    earlier: list[str] = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        earlier = manifest.get("raw_order") or sorted(
            stem for stem, path in found.items()
            if path.stat().st_mtime <= manifest_path.stat().st_mtime)
    earlier = [stem for stem in earlier if stem in found]
    return [found[stem] for stem in earlier] + [found[stem] for stem in sorted(found)
                                               if stem not in set(earlier)]


def explained() -> dict[str, str]:
    """Kernel disagreements that were looked into and are not the plan's fault: id -> why."""
    path = Path(__file__).parent / "kernel_explained.json"
    return json.loads(path.read_text()) if path.exists() else {}


def kernel_results() -> dict[str, dict]:
    found = {}
    for path in sorted(KERNEL.glob("*.jsonl")) if KERNEL.exists() else []:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            found[row["id"]] = row
    return found


def held_out_report(train: Counter, everything: Counter, combo_plans: Counter) -> list[dict]:
    """For every held-out combination: plans holding it, and each half alone in training."""
    def total(counts: Counter, prefix: str, suffix: str = "") -> int:
        return sum(n for cell, n in counts.items() if cell.startswith(prefix) and cell.endswith(suffix))

    report = []
    for a, b in config.HELD_OUT_LINE_PAIRS:
        left, right = a.split(":")[1], b.split(":")[1]
        if a.startswith("place") and b.startswith("target"):
            pair = f"pair:place×target|{left}|{right}"
            alone = {a: total(train, f"pair:place×target|{left}|"),
                     b: total(train, "pair:place×target|", f"|{right}")}
        elif a.startswith("rep"):
            pair = f"pair:place×rep|{right}|{left}"
            alone = {a: total(train, "pair:place×rep|", f"|{left}"),
                     b: total(train, f"pair:place×rep|{right}|")}
        else:
            pair = f"pair:place×align|{right}|{left}"
            alone = {a: total(train, "pair:place×align|", f"|{left}"),
                     b: total(train, f"pair:place×align|{right}|")}
        report.append({"pair": f"{a} + {b}", "plans_held_out": combo_plans[f"{a} + {b}"],
                       "lines_with_both_anywhere": everything[pair],
                       "lines_with_both_in_train": train[pair], "lines_in_train_with_each": alone})
    for a, b in HELD_OUT_PAIRS:
        name = f"feature:{a} + feature:{b}"
        report.append({"pair": name, "plans_held_out": combo_plans[name]})
    return report


def main() -> None:
    started = time.time()
    run_dir = start_run("plans-finalize", {"versions": _VERSIONS, "raw": str(RAW)})
    rejects_dir = config.OUT_DIR / "rejects"
    shutil.rmtree(rejects_dir, ignore_errors=True)
    rejects_dir.mkdir(parents=True)

    kernel = kernel_results()
    writers: dict[tuple[str, str], Writer] = {}
    seen: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, Counter] = defaultdict(Counter)          # source -> split -> plans
    lines: dict[str, Counter] = defaultdict(Counter)
    cells_all: Counter = Counter()
    cells_train: Counter = Counter()
    cells_by_source: dict[str, Counter] = defaultdict(Counter)
    combo_plans: Counter = Counter()
    kernel_sample: dict[str, Counter] = defaultdict(Counter)
    kernel_problems: list[dict] = []
    generation: dict[str, Counter] = defaultdict(Counter)       # stream -> what happened
    by_kind: dict[str, Counter] = defaultdict(Counter)
    parts_by_kind: Counter = Counter()
    lengths: dict[str, Counter] = defaultdict(Counter)
    rejects: Counter = Counter()
    duplicates = 0

    summaries = raw_order()
    known = explained()
    for summary_path in summaries:
        summary = json.loads(summary_path.read_text())
        stream = summary["stream"]
        source = source_of(stream)
        generation[stream].update({k: v for k, v in summary["stats"].items()})
        generation[stream].update(shards=1, tried=summary["tried"], seconds=summary["seconds"],
                                  kernel_calls=summary.get("kernel_calls", 0))
        if source == "structures":
            by_kind[summary["kind"]].update(summary["stats"])
            by_kind[summary["kind"]].update({f"flag: {k}": v for k, v in summary["flags"].items()})
        if source == "parts":
            parts_by_kind.update(summary["by_kind"])
        reject_path = summary_path.with_suffix(".rejects.jsonl")
        if reject_path.exists():
            with open(rejects_dir / f"{stream}.jsonl", "a") as out:
                for line in reject_path.read_text().splitlines():
                    rejects[stream] += 1
                    out.write(json.dumps({"shard": summary["index"], **json.loads(line)}) + "\n")
        with gzip.open(summary_path.with_suffix(".jsonl.gz"), "rt") as f:
            for line in f:
                record = json.loads(line)
                if record["id"] in seen[source]:
                    duplicates += 1
                    continue
                seen[source].add(record["id"])
                split = record["split"]
                # Template wording written into generator code is labelled.
                # Every line here is a fixed form of the plan language filled in by code, and
                # the part names come from word lists or step names in the code.
                record["caption_model"] = "template"
                verdict = kernel.get(record["id"])
                if verdict is not None:
                    record["kernel"] = {"built": True, "checks": verdict["checks"],
                                        "problems": verdict["problems"]}
                line = json.dumps(record, separators=(",", ":")) + "\n"
                if "kernel" in record:
                    kernel_sample[source]["built on the kernel"] += 1
                    kernel_sample[source]["parts built"] += len(record["parts"])
                    if record["kernel"]["problems"] and record["id"] in known:
                        record["kernel"]["explained"] = known[record["id"]]
                        line = json.dumps(record, separators=(",", ":")) + "\n"
                        kernel_sample[source]["with a disagreement, explained"] += 1
                    elif record["kernel"]["problems"]:
                        kernel_sample[source]["with a disagreement"] += 1
                        kernel_problems.append({"id": record["id"], "source": source,
                                                "problems": record["kernel"]["problems"]})
                    for check in record["kernel"].get("checks", []):
                        kernel_sample[source][f"check: {check.split(' on line')[0]}"] += 1
                key = (source, split)
                if key not in writers:
                    writers[key] = Writer(config.OUT_DIR / source / split)
                writers[key].add(line)
                counts[source][split] += 1
                lines[source][split] += len(record["lines"])
                lengths[f"{source}/{split}"][len(record["lines"])] += 1
                cells_all.update(record["cells"])
                cells_by_source[source].update(record["cells"])
                if split == "train" and source != "negatives":
                    cells_train.update(record["cells"])
                if source == "negatives":
                    # the rejection replies are the point of this slice: they count for training
                    cells_train.update(c for c in record["cells"] if c.startswith("reply:")
                                       and c != "reply:built" and split == "train")
                for combination in record.get("held_out", []):
                    combo_plans[combination] += 1
        log_metrics(run_dir, shard=summary_path.stem)

    files = {}
    for writer in writers.values():
        writer.close()
        files.update(writer.files)
    # a shard file no finalize of today's data would write (there is none unless raw shards
    # were deleted) is removed last
    for source in config.SOURCES:
        for path in (config.OUT_DIR / source).glob("*/shard*.jsonl.gz"):
            if str(path.relative_to(config.OUT_DIR)) not in files:
                path.unlink()

    rows = table.rows(cells_train, cells_all)
    held = {cell: {"held_out_as": pair, "train": cells_train.get(cell, 0),
                   "all": cells_all.get(cell, 0)} for cell, pair in table.held_out_cells().items()}
    under = {cell: row for cell, row in rows.items() if row["train"] < row["floor"]}
    under_target = {cell: row for cell, row in rows.items() if row["train"] < row["target"]}
    extra = {cell: {"train": cells_train.get(cell, 0), "all": n}
             for cell, n in sorted(cells_all.items()) if cell not in rows}
    length_ranges = {key: {"min": min(c), "max": max(c), "plans": sum(c.values())}
                     for key, c in sorted(lengths.items())}
    manifest = {
        "data_version": config.DATA_VERSION, "language": config.LANGUAGE,
        "license": config.LICENSE, "written": time.strftime("%Y-%m-%d %H:%M:%S"),
        "versions": _VERSIONS,
        "plans": {source: dict(counts[source]) for source in config.SOURCES},
        "plans_total": sum(sum(c.values()) for c in counts.values()),
        "lines": {source: dict(lines[source]) for source in config.SOURCES},
        "plan_lengths": length_ranges,
        "duplicates_dropped": duplicates,
        "splits": {
            "iid_fraction": config.IID_FRACTION, "max_train_lines": config.MAX_TRAIN_LINES,
            "long_lines": list(config.LONG_LINES), "held_out_kinds": list(config.HELD_OUT_KINDS),
            "held_out_combinations": held_out_report(cells_train, cells_all, combo_plans),
        },
        "coverage": {
            "floors": {"arithmetic": config.MIN_ARITHMETIC, "kernel": config.MIN_KERNEL,
                       "pair_arithmetic": config.MIN_PAIR_ARITHMETIC,
                       "pair_kernel": config.MIN_PAIR_KERNEL, "reply": config.MIN_REPLY},
            "targets": {"arithmetic": config.TARGET_ARITHMETIC, "kernel": config.TARGET_KERNEL,
                        "pair_arithmetic": config.TARGET_PAIR_ARITHMETIC,
                        "pair_kernel": config.TARGET_PAIR_KERNEL, "reply": config.TARGET_REPLY},
            "cells_required": len(rows), "cells_under_floor": len(under),
            "cells_under_target": len(under_target),
            "by_family": table.summary(rows),
            "cells": rows, "under_floor": under, "under_target": sorted(under_target),
            "held_out_pair_cells": held, "cells_not_in_table": extra,
            "by_source": {source: dict(c) for source, c in cells_by_source.items()},
        },
        "kernel_sample": {source: dict(c) for source, c in kernel_sample.items()},
        "kernel_disagreements": kernel_problems[:200],
        "structures_by_kind": {kind: dict(c) for kind, c in sorted(by_kind.items())},
        "parts_by_feature_kind": dict(sorted(parts_by_kind.items())),
        "generation": {stream: dict(c) for stream, c in sorted(generation.items())},
        "rejects_logged": dict(rejects),
        "raw_order": [path.stem for path in summaries],
        "files": files,
        "files_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
    }
    temp = config.OUT_DIR / "manifest.json.tmp"
    temp.write_text(json.dumps(manifest, indent=1))
    temp.replace(config.OUT_DIR / "manifest.json")
    log_metrics(run_dir, final=True, plans=manifest["plans_total"],
                seconds=round(time.time() - started, 1))
    print(f"{manifest['plans_total']} plans written in {time.time() - started:.0f}s "
          f"({duplicates} duplicates dropped)")
    for source in config.SOURCES:
        print(f"  {source:11s} {dict(counts[source])}")
    print(f"  coverage in train: {len(rows) - len(under)} of {len(rows)} required cells at their "
          f"floor, {len(rows) - len(under_target)} at their target")
    print(f"  kernel sample: { {s: c['built on the kernel'] for s, c in kernel_sample.items()} }, "
          f"{len(kernel_problems)} disagreements")
    print(f"  manifest: {config.OUT_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
