"""Turn verified parts into a dataset: prompts, prompt checks, splits, leakage check.

Run:    uv run python -m forge.data.build_dataset --config configs/dataset_v1.yaml
Input:  data/generated/<family>.jsonl                 (from forge.data.generate)
Output: data/dataset/<version>/parts.parquet          one row per part
        data/dataset/<version>/<split>.parquet        one row per (prompt, program) pair
        data/dataset/<version>/manifest.json          counts, file hashes, config
Optional input:  data/mistakes/<family>.jsonl         (from forge.data.mistakes)
Optional output: data/dataset/<version>/<split>_repair.parquet
                 one row per (prompt, wrong program, feedback) -> correct program

Order matters and is fixed:
0. if the config has `max_parts_per_family`, families with more parts than their
   cap are cut down to a fixed subset first, so every later step sees the same
   parts. Without that key nothing is cut;
1. splits are assigned on the PART (held-out families, held-out sizes, a held-out
   band of sizes, then a hash of the geometry), so every prompt of a part lands
   in the same split;
2. a shape or program that is in any test split is removed from train and val;
3. every part gets its prompts, and a prompt is kept only if every number in it
   belongs to the part (and, for full prompts, no dimension is missing);
4. if repair data exists for a part, its repairs are written to the SAME split as
   the part. With no data/mistakes/ the build is exactly as before.

The files are read and written in a stream, so the dataset can be far larger
than memory.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from fnmatch import fnmatchcase
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from forge.data.mistakes import REPAIR_PAIR_FIELDS, repair_pairs, selection_point
from forge.generators.captions import caption_problems, spec_captions
from forge.generators.prompts import natural_prompts, prompt_problems
from forge.runs import PROJECT_ROOT, log_metrics, start_run

IN_DIR = PROJECT_ROOT / "data" / "generated"
MISTAKES_DIR = PROJECT_ROOT / "data" / "mistakes"
TEST_SPLITS = ("test_id", "test_ood_family", "test_ood_params")
SPLITS = ("train", "val", *TEST_SPLITS)
BATCH = 20_000

PAIR_SCHEMA = pa.schema([(name, pa.string()) for name in (
    "id", "part_id", "prompt", "code", "family", "params", "designation",
    # Provenance required on every training row:
    "source", "license", "generator_version", "caption_style", "caption_variant",
    "caption_model", "geom_fingerprint", "split")])
PART_SCHEMA = pa.schema([(name, pa.string()) for name in (
    "id", "family", "source", "license", "generator_version", "dimension_table", "designation",
    "params", "code", "measured", "geom_fingerprint", "split")])
REPAIR_SCHEMA = pa.schema([(name, pa.string()) for name in REPAIR_PAIR_FIELDS])
# How many prompts one repair is paired with, unless the config says otherwise.
# A standard part has dozens of prompts; without a cap its repairs would swamp the rest.
DEFAULT_PROMPTS_PER_REPAIR = 2


def unit_hash(text: str) -> float:
    """A stable number in [0, 1) from a string. Same text, same number, every run."""
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16) / 16**12


def code_key(code: str) -> bytes:
    return hashlib.blake2b(code.encode(), digest_size=10).digest()


def first_dimension(params: dict) -> float:
    """The first length among the parameters. Counts (whole numbers such as a
    number of sides or holes) are skipped: a band of a count would hold out
    whole categories instead of a range of sizes."""
    return next(v for v in params.values() if isinstance(v, float))


def read_rows() -> Iterator[dict]:
    for path in sorted(IN_DIR.glob("*.jsonl")):
        if path.name.endswith(".failures.jsonl"):
            continue
        with path.open() as f:
            for line in f:
                yield json.loads(line)


def cap_for(family: str, caps: dict) -> int | None:
    """The most parts `family` may keep: its own entry, else the first pattern
    that matches (`composed_*`), else `default`. None means no cap."""
    if family in caps:
        return caps[family]
    for pattern, cap in caps.items():
        if pattern != "default" and fnmatchcase(family, pattern):
            return cap
    return caps.get("default")


def cap_point(row: dict, seed: int) -> tuple[float, str]:
    """Where a part falls in its family's keep-order: a hash of its id and the seed.
    `forge.data.mistakes` samples parts in the same order, so repairs are made for
    parts this cap keeps."""
    return selection_point(row["id"], seed)


class FamilyCaps:
    """Which parts survive `max_parts_per_family`.

    A family over its cap keeps the parts with the smallest `cap_point`, so the
    choice depends only on the part ids and the seed, never on file order.
    Parts with a designation (sizes from a standards table) are always kept and
    do not count towards the cap: a standard has only so many sizes.
    """

    def __init__(self, rows: Iterable[dict], caps: dict, seed: int) -> None:
        self.seed = seed
        self.caps: dict[str, int | None] = {}
        self.available: Counter = Counter()
        self.kept: Counter = Counter()
        # Per family, the `cap` smallest points seen so far (a heap of negatives
        # keeps the largest of them on top, ready to be pushed out).
        smallest: dict[str, list] = defaultdict(list)
        for row in rows:
            family = row["family"]
            self.available[family] += 1
            if family not in self.caps:
                self.caps[family] = cap_for(family, caps)
            cap = self.caps[family]
            if cap is None or row["designation"]:
                continue
            point, part_id = cap_point(row, seed)
            item = (-point, part_id)
            if len(smallest[family]) < cap:
                heapq.heappush(smallest[family], item)
            elif cap > 0 and item > smallest[family][0]:
                heapq.heapreplace(smallest[family], item)
        self.chosen = {family: {part_id for _, part_id in heap}
                       for family, heap in smallest.items()}

    def keeps(self, row: dict) -> bool:
        if self.caps.get(row["family"]) is None or row["designation"]:
            return True
        return row["id"] in self.chosen.get(row["family"], ())

    def filter(self, rows: Iterable[dict]) -> Iterator[dict]:
        for row in rows:
            if self.keeps(row):
                yield row

    def report(self, kept: Counter) -> dict:
        return {family: {"available": self.available[family], "kept": kept[family],
                         "cap": self.caps[family]}
                for family in sorted(self.available)}


def band_limits(rows: Iterable[dict], band: list[float]) -> dict[str, tuple[float, float]]:
    """Per family: the held-out range of its first dimension."""
    values = defaultdict(list)
    for row in rows:
        if not row["designation"]:
            values[row["family"]].append(first_dimension(row["params"]))
    limits = {}
    for family, numbers in values.items():
        numbers.sort()
        low = numbers[int(band[0] * (len(numbers) - 1))]
        high = numbers[int(band[1] * (len(numbers) - 1))]
        limits[family] = (low, high)
    return limits


def assign_split(row: dict, config: dict, bands: dict) -> str:
    if row["family"] in config["holdout_families"]:
        return "test_ood_family"
    designation = row["designation"]
    if designation and designation["size"] in config["holdout_sizes"]:
        return "test_ood_params"
    if not designation and row["family"] in bands:
        low, high = bands[row["family"]]
        if low <= first_dimension(row["params"]) <= high and low < high:
            return "test_ood_params"
    point = unit_hash(f"{config['seed']}:{row['geom_fingerprint']}")
    train, val = config["fractions"]["train"], config["fractions"]["val"]
    return "train" if point < train else "val" if point < train + val else "test_id"


def prompts_for(row: dict, config: dict, stats: Counter) -> list[dict]:
    """All verified prompts of one part: the canonical spec ones plus natural wording."""
    params, designation = row["params"], row["designation"]
    kept = []
    for caption in spec_captions(row["family"], params, designation):
        if caption_problems(caption["text"], params, designation):
            stats["prompts_rejected"] += 1
        else:
            kept.append(caption)
    wanted = config.get("prompts", {}).get("standard" if designation else "free", {})
    for prompt in natural_prompts(row["family"], params, designation, row["id"], wanted):
        complete = prompt["style"] != "designation"
        if prompt_problems(prompt["text"], params, designation, complete):
            stats["prompts_rejected"] += 1
        else:
            kept.append(prompt)
    return kept


class Repairs:
    """Repair rows from data/mistakes/, looked up by part, one family in memory at a time.

    Only families with a `.done` marker are read: `forge.data.mistakes` writes
    the marker last, so a file without one may be half-written or out of date.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.families = {marker.stem for marker in directory.glob("*.done")
                         if (directory / f"{marker.stem}.jsonl").exists()}
        self._family: str | None = None
        self._by_part: dict[str, list[dict]] = {}

    def __bool__(self) -> bool:
        return bool(self.families)

    def of(self, row: dict) -> list[dict]:
        family = row["family"]
        if family not in self.families:
            return []
        if family != self._family:
            self._family, self._by_part = family, defaultdict(list)
            with (self.directory / f"{family}.jsonl").open() as f:
                for line in f:
                    repair = json.loads(line)
                    self._by_part[repair["part_id"]].append(repair)
        return self._by_part.get(row["id"], [])


def repair_rows(row: dict, repairs: list[dict], prompts: list[dict], split: str, config: dict,
                test_programs: set[bytes], stats: Counter) -> list[dict]:
    """The repair pairs of one part, all in the part's own split.

    Three safeguards:
    - a repair made from another version of the part (other generator version,
      or its correct program is not the part's program) is dropped as stale;
    - in train and val, a repair whose WRONG program is itself the program of a
      test part is dropped, like any other program shared with a test split;
    - each repair is paired with at most `repairs.prompts_per_repair` prompts,
      chosen by a hash so the same build always picks the same ones.
    Repairs the feedback cannot see produce no pair (see `repair_pairs`).
    """
    cap = (config.get("repairs") or {}).get("prompts_per_repair", DEFAULT_PROMPTS_PER_REPAIR)
    rows = []
    for repair in repairs:
        stats["repairs_in"] += 1
        if (repair["generator_version"] != row["generator_version"]
                or repair["correct_code"] != row["code"]):
            stats["repairs_dropped_stale"] += 1
            continue
        if split in ("train", "val") and code_key(repair["wrong_code"]) in test_programs:
            stats["repairs_dropped_for_leakage"] += 1
            continue
        pairs = repair_pairs(repair, prompts, split)
        if not pairs:
            stats["repairs_without_pair"] += 1
            continue
        pairs.sort(key=lambda pair: unit_hash(f"{config['seed']}:{pair['id']}"))
        rows += pairs[:cap]
        stats[f"repairs_{split}"] += 1
        stats[f"repair_pairs_{split}"] += len(pairs[:cap])
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Writer:
    """Buffers rows and writes them to a parquet file in batches."""

    def __init__(self, path: Path, schema: pa.Schema) -> None:
        self.schema = schema
        self.writer = pq.ParquetWriter(path, schema, compression="zstd")
        self.rows: list[dict] = []

    def add(self, row: dict) -> None:
        self.rows.append(row)
        if len(self.rows) >= BATCH:
            self.flush()

    def flush(self) -> None:
        if self.rows:
            self.writer.write_table(pa.Table.from_pylist(self.rows, schema=self.schema))
            self.rows = []

    def close(self) -> None:
        self.flush()
        self.writer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default="configs/dataset_v1.yaml")
    args = parser.parse_args()
    config = yaml.safe_load((PROJECT_ROOT / args.config).read_text())
    out_dir = PROJECT_ROOT / "data" / "dataset" / config["version"]
    out_dir.mkdir(parents=True, exist_ok=True)
    run_dir = start_run(f"build-dataset-{config['version']}", config)
    stats: Counter = Counter()
    # Repair data is optional. Files from an earlier build are removed first, so
    # a build without repair data never leaves old repair files in the manifest.
    for stale in out_dir.glob("*_repair.parquet"):
        stale.unlink()
    repairs = Repairs(MISTAKES_DIR)
    repair_stats: Counter = Counter()
    repair_groups: Counter = Counter()
    mistake_versions: set[str] = set()

    # Pass 0 (only with `max_parts_per_family`): choose which parts of the big
    # families stay. Every later pass reads through the same filter.
    caps = None
    if config.get("max_parts_per_family"):
        caps = FamilyCaps(read_rows(), config["max_parts_per_family"], config["seed"])

    def rows_in() -> Iterator[dict]:
        return caps.filter(read_rows()) if caps else read_rows()

    selected: Counter = Counter()

    # Pass 1: the held-out size band of each family.
    bands = band_limits(rows_in(), config["holdout_band"])

    # Pass 2: which shapes and programs are in a test split.
    test_shapes: set[str] = set()
    test_programs: set[bytes] = set()
    for row in rows_in():
        if assign_split(row, config, bands) in TEST_SPLITS:
            test_shapes.add(row["geom_fingerprint"])
            test_programs.add(code_key(row["code"]))

    # Pass 3: write everything.
    parts_writer = Writer(out_dir / "parts.parquet", PART_SCHEMA)
    pair_writers = {s: Writer(out_dir / f"{s}.parquet", PAIR_SCHEMA) for s in SPLITS}
    repair_writers = ({s: Writer(out_dir / f"{s}_repair.parquet", REPAIR_SCHEMA) for s in SPLITS}
                      if repairs else {})
    parts_count: dict[str, Counter] = {s: Counter() for s in SPLITS}
    pairs_by_style: Counter = Counter()
    versions: set[str] = set()

    for row in rows_in():
        stats["parts_in"] += 1
        selected[row["family"]] += 1
        split = assign_split(row, config, bands)
        if split in ("train", "val") and (row["geom_fingerprint"] in test_shapes
                                          or code_key(row["code"]) in test_programs):
            stats["parts_dropped_for_leakage"] += 1
            continue
        prompts = prompts_for(row, config, stats)
        if not prompts:
            stats["parts_without_prompt"] += 1
            continue
        versions.add(row["generator_version"])
        params = json.dumps(row["params"])
        designation = json.dumps(row["designation"])
        parts_writer.add({
            "id": row["id"], "family": row["family"], "source": row["source"],
            "license": row["license"], "generator_version": row["generator_version"],
            "dimension_table": json.dumps(row["dimension_table"]), "designation": designation,
            "params": params, "code": row["code"], "measured": json.dumps(row["measured"]),
            "geom_fingerprint": row["geom_fingerprint"], "split": split,
        })
        parts_count[split][row["family"]] += 1
        for prompt in prompts:
            pair_writers[split].add({
                "id": hashlib.sha1(
                    f"{row['id']}:{prompt['variant']}".encode()).hexdigest()[:16],
                "part_id": row["id"], "prompt": prompt["text"], "code": row["code"],
                "family": row["family"], "params": params, "designation": designation,
                "source": row["source"], "license": row["license"],
                "generator_version": row["generator_version"],
                "caption_style": prompt["style"], "caption_variant": prompt["variant"],
                "caption_model": prompt["model"],
                "geom_fingerprint": row["geom_fingerprint"], "split": split,
            })
            stats[f"pairs_{split}"] += 1
            pairs_by_style[prompt["style"]] += 1
        if repairs:
            for pair in repair_rows(row, repairs.of(row), prompts, split, config,
                                    test_programs, repair_stats):
                repair_writers[split].add(pair)
                repair_groups[pair["mistake_group"]] += 1
                mistake_versions.add(pair["mistake_version"])

    parts_writer.close()
    for writer in (*pair_writers.values(), *repair_writers.values()):
        writer.close()
    for split in SPLITS:
        stats[f"parts_{split}"] = sum(parts_count[split].values())

    generators = PROJECT_ROOT / "forge" / "generators"
    manifest = {
        "version": config["version"],
        "config": config,
        "generator_versions": sorted(versions),
        "prompt_code_sha256": {name: sha256(generators / name)
                               for name in ("captions.py", "prompts.py")},
        "stats": dict(stats),
        "pairs_by_style": dict(pairs_by_style),
        "parts_by_split_and_family": {s: dict(sorted(c.items()))
                                      for s, c in parts_count.items()},
        "holdout_bands": {f: list(b) for f, b in sorted(bands.items())},
        "files": {p.name: sha256(p) for p in sorted(out_dir.glob("*.parquet"))},
    }
    if caps:
        # Per family: parts generated, parts that passed the cap, and the cap.
        manifest["max_parts_per_family"] = caps.report(selected)
    if repairs:
        manifest["repairs"] = {
            "prompts_per_repair": (config.get("repairs") or {}).get(
                "prompts_per_repair", DEFAULT_PROMPTS_PER_REPAIR),
            "families_with_repair_data": sorted(repairs.families),
            "mistake_versions": sorted(mistake_versions),
            "stats": dict(repair_stats),
            "pairs_by_mistake_group": dict(sorted(repair_groups.items())),
        }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    log_metrics(run_dir, final=True, **stats, **repair_stats)

    kept = sum(stats[f"parts_{s}"] for s in SPLITS)
    print(f"dataset {config['version']}: {kept} parts, "
          f"{sum(stats[f'pairs_{s}'] for s in SPLITS)} pairs")
    for split in SPLITS:
        print(f"  {split:16s} {stats[f'parts_{split}']:8d} parts {stats[f'pairs_{split}']:9d} pairs")
    print(f"  pairs by style: {dict(pairs_by_style)}")
    print(f"  prompts rejected: {stats['prompts_rejected']}, "
          f"parts dropped for leakage: {stats['parts_dropped_for_leakage']}")
    if caps:
        cut = {f: c for f, c in caps.report(selected).items() if c["kept"] < c["available"]}
        print(f"  max_parts_per_family: {len(cut)} families cut down, "
              f"{sum(c['available'] - c['kept'] for c in cut.values())} parts left out")
    if repairs:
        print(f"  repair data for {len(repairs.families)} families: "
              f"{repair_stats['repairs_in']} repairs read, "
              f"{sum(repair_stats[f'repair_pairs_{s}'] for s in SPLITS)} repair pairs written")
        for split in SPLITS:
            print(f"  {split:16s} {repair_stats[f'repairs_{split}']:8d} repairs "
                  f"{repair_stats[f'repair_pairs_{split}']:9d} repair pairs")
        print(f"  repairs dropped: {repair_stats['repairs_dropped_stale']} stale, "
              f"{repair_stats['repairs_dropped_for_leakage']} for leakage, "
              f"{repair_stats['repairs_without_pair']} the feedback cannot see")
        print(f"  repair pairs by mistake group: {dict(sorted(repair_groups.items()))}")


if __name__ == "__main__":
    main()
