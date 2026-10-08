"""Choose the plans that get a structure session, and the split each one is in.

Run:    uv run python -m forge.freecad_multi.selection            scan data/plans, write everything
        uv run python -m forge.freecad_multi.selection --stats    only print what the scan found

Output (data/freecad_multi/selection/):
    index.jsonl.gz              one line per candidate plan: plan_facts.py's entry and its `s1_split`
    excluded_plans.json         the plans kept out, each with its reason (see below)
    <slice>/partNNNN.jsonl.gz   the plans of one session slice in RECORDING ORDER (shuffled by
                                a hash of the plan id), PLANS_PER_PART to a file. sessions.py
                                records one shard per file. `train_rare/` lists the train plans
                                with a feature or another shape once more (in_rare_pass).
    selection.json              the counts, the held-out cells, a hash of every file

Why a copy of the plans. data/plans is sorted by source and kind. Recording it in that
order stopped the first structure recording after 9 of 15 kinds. A shuffled order means
reading plans from every shard of data/plans for every shard of sessions; writing each
slice's plans once, in recording order, makes a session shard read one small file. The
copy is checked: the audit reads the plan again from data/plans itself.

THE SPLITS (decided on what the executor sees, plan_facts.py)

    long     more than MAX_BODIES bodies. Never in train; a hash-chosen sample is recorded
             as a test of length.
    kinds    a structure of a held-out kind (data/plans' own `kinds` split: stool, shelf,
             standoffs), at most MAX_BODIES bodies.
    combo    holds a HELD-OUT CELL (HELD_OUT_CELLS): a shape with a turn, a feature kind on
             a side, a shape with a feature kind. Each half of every held-out cell stays
             in train in other combinations.
    iid      data/plans' own `iid` plans that are none of the above.
    train    data/plans' own `train` plans that are none of the above.
    (unused) data/plans' `combo` and `long` plans that hold no held-out cell and have at
             most MAX_BODIES bodies: test plans by data/plans' rule, so never train, and
             nothing an object-blind executor would be tested on. Not recorded.

Then, by geometry (plan_facts.geometry_key: the parts without their names):
    a test plan whose parts equal a train plan's is dropped (8 stools were tables);
    of several test plans with the same parts, and of several train plans with the same
    parts, one is kept (the smallest id).

KEPT OUT (excluded_plans.json)
    undeclared_overlap   two parts share volume and neither is `sunk` into the other, by
                         the fixed check (forge/resolve/resolver.py `_named`, 6 Oct 2026)
    stale_declaration    the plan is right, but its stored record in data/plans says the
                         part may overlap "fm15" where the fixed resolver says "ch20 fm15".
                         The plan is sound; its stored record is not what the resolver
                         gives now, and a session is only made from a plan whose stored
                         record can be trusted. Re-finalizing data/plans brings them back.
    does_not_resolve     the fixed resolver no longer builds the plan as stored
    not_buildable        a feature no command builds yet: a hollowing with no open face, a
                         feature on a face that is square to nothing (recipes.Unsupported)

THE RECORDING ORDER is a weighted shuffle by a hash of the plan id (`weights`,
`order_key`), so a recording that stops part way holds a fair sample of every kind, and
most of the rare plans.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import multiprocessing
import time
from collections import Counter
from pathlib import Path

from forge.freecad_multi import plan_facts as facts
from forge.resolve import resolve_text
from forge.resolve.judge import KernelJudge
from forge.runs import PROJECT_ROOT

PLANS = PROJECT_ROOT / "data" / "plans"
OUT_DIR = PROJECT_ROOT / "data" / "freecad_multi" / "selection"
SOURCES = ("random", "structures")
PLANS_SPLITS = ("train", "iid", "combo", "long", "kinds")
MAX_BODIES = 30             # the cap for S1; more bodies is the `long` test
LONG_MAX_BODIES = 60        # recorded long sessions stop here (the listing has all of them)
LONG_RECORDED = 400         # how many long plans are recorded
PLANS_PER_PART = 25
PLAIN_SHAPES = {"box", "cylinder"}
STORED_TOLERANCE = 2e-6

# The held-out cells (plan_facts.CELLS). Chosen on 6 Oct 2026 from the counts of the scan
# (`--stats`), before any session of this recording existed: cells common enough to give a
# test of a few hundred plans and rare enough that train keeps almost everything, each
# half present in train in other cells. See README, "Splits".
HELD_OUT_CELLS: tuple[str, ...] = (
    # a shape with a turn
    "st:cylinder|180,0,-90", "st:box|0,-90,0", "st:cone|0,0,180", "st:bar l|0,90,0",
    "st:tube|0,0,-90",
    # a feature kind on a side
    "fs:hole|front", "fs:pocket|bottom", "fs:slot|left",
    # a shape with a feature kind
    "sf:tube|hole", "sf:box|polar",
)
# The recording order is a weighted shuffle (order_key). A multi-part plan with another
# shape than box or cylinder, or with a feature, is this many times as likely to come
# early as a plain one: there are about 2,000 such train plans among 125,000, and a
# recording that stops part way must still hold most of them.
RARE_WEIGHT = 8.0


def _records(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            yield json.loads(line)


def wanted(record: dict) -> bool:
    """A candidate: a complete plan of two parts or more, or a single part that the
    single-part sessions cannot show (another shape than box or cylinder, or a feature on
    a side)."""
    if not record.get("complete") or record.get("negative") or not record.get("parts"):
        return False
    parts = record["parts"]
    if len(parts) >= 2:
        return True
    return facts.shape_word(parts[0]) not in PLAIN_SHAPES or bool(parts[0].get("features"))


def scan_file(path: str) -> list[dict]:
    where = str(Path(path).relative_to(PROJECT_ROOT))
    found = []
    for record in _records(Path(path)):
        if wanted(record):
            entry = facts.plan_facts(record, where)
            if entry["suspect"] or (entry["group"] and entry["sunk"]):
                entry["check"] = recheck(record)
            found.append(entry)
    return found


_judge: KernelJudge | None = None


def recheck(record: dict) -> str:
    """Resolve the plan again with the fixed resolver. Returns "ok" or the reason it is
    kept out (see the top of this file)."""
    global _judge
    if _judge is None:
        _judge = KernelJudge()
    try:
        solved = resolve_text("\n".join(record["lines"]) + "\n", _judge)
    except Exception as error:      # noqa: BLE001 - whatever stops it, the plan is not usable
        return f"does_not_resolve ({type(error).__name__})"
    stored = record["parts"]
    if not solved.complete or [b.name for b in solved.bodies] != [p["name"] for p in stored]:
        return "does_not_resolve"
    for body, part in zip(solved.bodies, stored, strict=True):
        frame = body.frame
        if any(abs(a - b) > STORED_TOLERANCE
               for a, b in zip((*frame.low, *frame.high), (*part["low"], *part["high"]))):
            return "does_not_resolve"
    if facts.undeclared_overlaps(solved.bodies):
        return "undeclared_overlap"
    if any(sorted(body.may_overlap) != part.get("may_overlap", [])
           for body, part in zip(solved.bodies, stored, strict=True)):
        return "stale_declaration"
    return "ok"


def scan(workers: int) -> list[dict]:
    files = [str(path) for source in SOURCES for split in PLANS_SPLITS
             for path in sorted((PLANS / source / split).glob("shard*.jsonl.gz"))]
    entries: list[dict] = []
    with multiprocessing.Pool(workers) as pool:
        for found in pool.imap(scan_file, files):
            entries += found
    return entries


# --- the splits --------------------------------------------------------------------------------

def first_split(entry: dict) -> str:
    """The split before the geometry rules (see the top of this file)."""
    if entry.get("check", "ok") != "ok":
        return "excluded"
    if not entry["buildable"]:
        entry["check"] = "not_buildable"
        return "excluded"
    if entry["bodies"] > MAX_BODIES:
        return "long"
    if entry["plans_split"] == "kinds":
        return "kinds"
    if set(entry["cells"]) & set(HELD_OUT_CELLS):
        return "combo"
    if entry["plans_split"] in ("train", "iid"):
        return entry["plans_split"]
    return "unused"


def assign(entries: list[dict]) -> Counter:
    """Set every entry's `s1_split`. Returns what the geometry rules dropped."""
    dropped: Counter = Counter()
    for entry in entries:
        entry["s1_split"] = first_split(entry)
    entries.sort(key=lambda entry: entry["id"])
    train_geometry = set()
    for entry in entries:                       # one train plan per geometry
        if entry["s1_split"] == "train":
            if entry["geometry"] in train_geometry:
                entry["s1_split"] = "dropped"
                entry["dropped"] = "same parts as another train plan"
                dropped["train: same parts as another train plan"] += 1
            train_geometry.add(entry["geometry"])
    seen: set[str] = set()
    for split in ("kinds", "combo", "long", "iid"):
        for entry in entries:
            if entry["s1_split"] != split:
                continue
            if entry["geometry"] in train_geometry:
                reason = "same parts as a train plan"
            elif entry["geometry"] in seen:
                reason = "same parts as another test plan"
            else:
                seen.add(entry["geometry"])
                continue
            entry["s1_split"], entry["dropped"] = "dropped", reason
            dropped[f"{split}: {reason}"
                    + (f" ({entry['kind']})" if split == "kinds" else "")] += 1
    return dropped


def is_rare(entry: dict) -> bool:
    return entry["bodies"] >= 2 and (bool(entry["features"])
                                     or bool(set(entry["shapes"]) - PLAIN_SHAPES))


def weights(entries: list[dict]) -> dict[str, float]:
    """plan id -> how strongly the plan is pulled to the front of its slice.

    train, iid, long   RARE_WEIGHT for a rare plan (is_rare), 1 otherwise
    kinds              1 / (plans of its kind): the three held-out kinds come equally often
    combo              1 / (plans holding its rarest held-out cell): every cell comes about
                       equally often, although some are in thousands of plans and some in 40
    """
    per_kind = Counter(e["kind"] for e in entries if e["s1_split"] == "kinds")
    per_cell = Counter(cell for e in entries if e["s1_split"] == "combo"
                       for cell in set(e["cells"]) & set(HELD_OUT_CELLS))
    found = {}
    for entry in entries:
        if entry["s1_split"] == "kinds":
            found[entry["id"]] = 1.0 / per_kind[entry["kind"]]
        elif entry["s1_split"] == "combo":
            found[entry["id"]] = 1.0 / min(per_cell[cell] for cell in entry["cells"]
                                           if cell in HELD_OUT_CELLS)
        else:
            found[entry["id"]] = RARE_WEIGHT if is_rare(entry) else 1.0
    return found


def order_key(plan_id: str, weight: float = 1.0) -> float:
    """The recording order: a weighted shuffle by a hash of the plan id. Among plans of
    one weight it is a plain shuffle, so every stretch of a slice is a fair sample of
    them; a plan of weight w is w times as likely to come before a plan of weight 1."""
    digest = hashlib.sha256(f"record-order:{plan_id}".encode()).hexdigest()
    uniform = (int(digest[:13], 16) + 0.5) / 16 ** 13          # strictly between 0 and 1
    return -math.log(uniform) / weight


# --- writing -----------------------------------------------------------------------------------

def in_rare_pass(entry: dict) -> bool:
    """Is this train plan recorded again in the `train_rare` passes? Multi-part plans with
    a feature or another shape (about 1,100) and single parts with a feature on a side
    (about 900): what a structure needs most and data/plans holds least of."""
    return entry["s1_split"] == "train" and (is_rare(entry) or bool(entry["features"]))


def slice_of(entry: dict) -> str | None:
    """The selection folder an entry's first session is recorded from, or None."""
    split = entry["s1_split"]
    if split in ("train", "iid", "kinds", "combo"):
        return split
    if split == "long" and entry["bodies"] <= LONG_MAX_BODIES:
        return "long"
    return None


def write_parts(entries: list[dict], out_dir: Path) -> dict[str, int]:
    """Write every slice's plans in recording order. Returns plans per slice."""
    chosen: dict[str, list[dict]] = {}
    for entry in entries:
        name = slice_of(entry)
        if name:
            chosen.setdefault(name, []).append(entry)
    # The rare train plans once more, as a list of their own (shards.SLICES records it
    # with other seeds: other mistakes, another start, another order of the free steps).
    chosen["train_rare"] = [entry for entry in entries if in_rare_pass(entry)]
    weight = weights(entries)
    for name, listed in chosen.items():
        listed.sort(key=lambda entry: order_key(entry["id"], weight[entry["id"]]))
    if "long" in chosen:
        chosen["long"] = chosen["long"][:LONG_RECORDED]
    by_file: dict[str, dict[str, list[tuple[str, int]]]] = {}   # file -> id -> [(folder, place)]
    for name, listed in chosen.items():
        for place, entry in enumerate(listed):
            by_file.setdefault(entry["where"], {}).setdefault(entry["id"], []).append((name, place))
    rows: dict[str, dict[int, dict]] = {name: {} for name in chosen}
    for where, ids in sorted(by_file.items()):
        for record in _records(PROJECT_ROOT / where):
            if record["id"] in ids:
                for name, place in ids[record["id"]]:
                    rows[name][place] = {key: record[key] for key in (
                        "id", "source", "split", "lines", "parts", "license", "generator_version")
                    } | {"kind": record.get("kind") or record.get("provenance", {}).get("kind"),
                         "where": where, "s1_split": "train" if name == "train_rare" else name}
    for name, listed in chosen.items():
        folder = out_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("part*.jsonl.gz"):
            old.unlink()
        for number, at in enumerate(range(0, len(listed), PLANS_PER_PART)):
            path = folder / f"part{number:04d}.jsonl.gz"
            with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                                       mtime=0) as f:
                for place in range(at, min(at + PLANS_PER_PART, len(listed))):
                    f.write((json.dumps(rows[name][place], separators=(",", ":")) + "\n").encode())
    return {name: len(listed) for name, listed in chosen.items()}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(entries: list[dict]) -> dict:
    """Counts for the report and for choosing HELD_OUT_CELLS."""
    counts: dict[str, Counter] = {name: Counter() for name in (
        "candidates", "check", "s1_split", "cells_train_iid", "kinds", "bodies")}
    for entry in entries:
        key = f"{entry['source']}/{entry['plans_split']}"
        counts["candidates"][key] += 1
        counts["check"][f"{key}: {entry.get('check', 'not rechecked')}"] += 1
        if "s1_split" in entry:
            counts["s1_split"][entry["s1_split"]] += 1
            if entry["s1_split"] in ("train", "iid", "kinds", "combo"):
                counts["kinds"][f"{entry['s1_split']}: {entry['kind'] or entry['source']}"] += 1
        if entry["plans_split"] in ("train", "iid") and entry["bodies"] <= MAX_BODIES:
            for cell in entry["cells"]:
                counts["cells_train_iid"][cell] += 1
        counts["bodies"]["1" if entry["bodies"] == 1 else "2-10" if entry["bodies"] <= 10
                         else "11-20" if entry["bodies"] <= 20 else "21-30"
                         if entry["bodies"] <= 30 else "31-60" if entry["bodies"] <= 60
                         else "over 60"] += 1
    return {name: dict(sorted(counter.items())) for name, counter in counts.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--stats", action="store_true", help="scan and print; write nothing")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()
    out_dir = Path(args.out)
    started = time.time()
    entries = scan(args.workers)
    dropped = assign(entries)
    found = stats(entries)
    if args.stats:
        print(json.dumps(found, indent=1))
        print(json.dumps(dict(dropped), indent=1))
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_dir / "index.jsonl.gz", "wt", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    excluded = [{"id": e["id"], "source": e["source"], "plans_split": e["plans_split"],
                 "where": e["where"], "reason": e["check"]}
                for e in entries if e["s1_split"] == "excluded"]
    by_reason = Counter(f"{e['source']}/{e['plans_split']}: {e['reason']}" for e in excluded)
    (out_dir / "excluded_plans.json").write_text(json.dumps(
        {"what": "plans of data/plans that get no structure session (selection.py)",
         "count": len(excluded), "by_source_split_reason": dict(sorted(by_reason.items())),
         "plans": excluded}, indent=1) + "\n")
    long_listing = [{"id": e["id"], "source": e["source"], "bodies": e["bodies"],
                     "where": e["where"]} for e in entries if e["s1_split"] == "long"]
    (out_dir / "long_plans.json").write_text(json.dumps(
        {"what": f"plans with more than {MAX_BODIES} bodies: never in train",
         "count": len(long_listing), "plans": long_listing}, indent=1) + "\n")
    written = write_parts(entries, out_dir)
    files = {str(path.relative_to(out_dir)): _sha(path)
             for path in sorted(out_dir.glob("*/part*.jsonl.gz"))}
    summary = {
        "max_bodies": MAX_BODIES, "long_max_bodies": LONG_MAX_BODIES, "rare_weight": RARE_WEIGHT,
        "plans_per_part": PLANS_PER_PART, "held_out_cells": list(HELD_OUT_CELLS),
        "plans_recorded_per_slice": written, "dropped_by_geometry": dict(sorted(dropped.items())),
        "excluded": dict(sorted(by_reason.items())), **found,
        "files": files,
        "selection_hash": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:12],
    }
    (out_dir / "selection.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(f"{len(entries)} candidates in {time.time() - started:.0f}s; recorded per slice: "
          f"{written}; excluded {len(excluded)}: {dict(by_reason)}")
    print(f"dropped by geometry: {dict(dropped)}")


if __name__ == "__main__":
    main()
