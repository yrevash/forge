"""Build the plans data set, one shard at a time.                                   [command]

    uv run python -m forge.plans_data.build random --tier arithmetic --shards 0-39 --workers 2
    uv run python -m forge.plans_data.build random --tier kernel --shards 0-199 --workers 2 --until 04:45
    uv run python -m forge.plans_data.build single --shards 0-14          (one part per plan)
    uv run python -m forge.plans_data.build featured --shards 0-99 --until 04:55   (one part, many faces)
    uv run python -m forge.plans_data.build negatives --shards 0-9 --workers 2
    uv run python -m forge.plans_data.build long --shards 0-3 --workers 2
    uv run python -m forge.plans_data.build structures --shards 0-4 --workers 1
    uv run python -m forge.plans_data.build structure-rows --workers 2   (the rows of data/structures/)
    uv run python -m forge.plans_data.build parts --workers 1
    uv run python -m forge.plans_data.finalize          (splits into data/plans/<source>/<split>/)

A SHARD is the unit of work: a fixed list of seeds (random plans), a seeded draw of
structures of one kind, or a fixed slice of one file of composed parts. A shard's records
depend on nothing but its own name, so the build is deterministic, and a shard that is
already written is skipped, so it is resumable. Raw shards go to data/plans/_raw/:

    <stream>-<index>.jsonl.gz     the records (every split mixed; `split` is already set)
    <stream>-<index>.json         counts, coverage cells, what was thrown away and why
    <stream>-<index>.rejects.jsonl  plans / structures / parts that were not kept, with the reason

`--until HH:MM` stops handing out new shards at that time (the kernel was shared with
other jobs on the night this was built, so the kernel tier ran to a deadline, not a count).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from forge.plans_data import config, fast, splits
from forge.plans_data.record import NoKernel, make_record
from forge.runs import log_metrics, start_run

RAW = config.OUT_DIR / "_raw"
# plans tried per shard: sized so that one shard takes minutes, not hours
PER_SHARD = {"single-arithmetic": 2000, "featured-kernel": 60, "topup-arithmetic": 1000,
             "topup2-arithmetic": 1000, "ktopup-kernel": 30, "ktopup2-kernel": 30, "ktopup3-kernel": 30, "random-arithmetic": 1000, "random-kernel": 40, "negatives-arithmetic": 1000,
             "negatives-kernel": 40, "long-arithmetic": 300, "long-kernel": 4,
             "structures": 400, "parts": 2000}
if os.environ.get("FORGE_PLANS_PER"):        # tests: tiny shards
    PER_SHARD = {stream: int(os.environ["FORGE_PLANS_PER"]) for stream in PER_SHARD}
KERNEL_TIMEOUT = 120.0


# The files a record's content depends on, per source. Hashed ONCE, when this module is
# loaded, so a record's version is the code that was in memory when it was made (the
# audit, the viewer and this command are not hashed: changing them changes no record).
SHARED_FILES = ("session.py", "record.py", "coverage.py", "splits.py", "fast.py")
SOURCE_FILES = {"random": "random_gen.py", "structures": "structures.py", "parts": "parts.py",
                "featured": "featured.py", "topup": "topup.py", "topup2": "topup2.py",
                "ktopup": "ktopup.py", "ktopup2": "ktopup2.py",
                "ktopup3": "ktopup3.py"}


def _hash(names: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()[:12]


_VERSIONS = {source: _hash((name, *SHARED_FILES)) for source, name in SOURCE_FILES.items()}
_VERSIONS.update(reader=config.package_version("plan"), resolver=config.package_version("resolve"))


def versions(source: str = "random") -> dict:
    """{"plans_data": the generator version of this source, "reader": ..., "resolver": ...}."""
    return {"plans_data": _VERSIONS[source], "reader": _VERSIONS["reader"],
            "resolver": _VERSIONS["resolver"]}


def _write(stream: str, index: int, records: list[dict], summary: dict, rejects: list[dict]) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    stem = RAW / f"{stream}-{index:04d}"
    with gzip.open(f"{stem}.jsonl.gz.tmp", "wt") as f:
        for record in records:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    Path(f"{stem}.jsonl.gz.tmp").rename(f"{stem}.jsonl.gz")
    if rejects:
        with open(f"{stem}.rejects.jsonl", "w") as f:
            f.writelines(json.dumps(reject) + "\n" for reject in rejects)
    cells: Counter = Counter()
    for record in records:
        cells.update(record["cells"])
    summary.update(stream=stream, index=index, records=len(records),
                   splits=dict(Counter(r["split"] for r in records)), cells=dict(cells),
                   lines=sum(len(r["lines"]) for r in records))
    Path(f"{stem}.json").write_text(json.dumps(summary))      # written last: the shard is done


def is_done(stream: str, index: int) -> bool:
    return (RAW / f"{stream}-{index:04d}.json").exists()


# --- random plans (and the negative slice, and long plans) -----------------------------------

def random_shard(job: tuple) -> dict:
    what, tier, index = job
    stream = f"{what}-{tier}"
    if is_done(stream, index):
        return {"stream": stream, "index": index, "skipped": True}
    from forge.plans_data.random_gen import (
        GENERATOR,
        Balance,
        Maker,
        ScreenedJudge,
        all_shape_cells,
    )
    from forge.resolve.property_test import _check_rejection
    from forge.resolve.verify import verify
    from forge.sandbox import Sandbox

    fast.share_bodies()
    started = time.time()
    sandbox = Sandbox(timeout=KERNEL_TIMEOUT) if tier == "kernel" else None
    judge = ScreenedJudge(sandbox) if sandbox else NoKernel()
    per = PER_SHARD[stream]
    ver = versions(what if what in ("featured", "topup", "topup2", "ktopup", "ktopup2", "ktopup3")
                   else "random")
    balance = Balance()
    records, rejects, stats, seen = [], [], Counter(), set()
    try:
        for seed in range(index * per, (index + 1) * per):
            if what == "featured":
                from forge.plans_data.featured import FeaturedMaker
                maker = FeaturedMaker(f"{what}:{seed}", tier, judge, balance=balance)
            elif what == "ktopup3":
                from forge.plans_data.ktopup3 import KernelTopUp3
                maker = KernelTopUp3(f"{what}:{seed}", tier, judge, balance=balance)
            elif what == "ktopup2":
                from forge.plans_data.ktopup2 import KernelTopUp2
                maker = KernelTopUp2(f"{what}:{seed}", tier, judge, balance=balance)
            elif what == "ktopup":
                from forge.plans_data.ktopup import KernelTopUp
                maker = KernelTopUp(f"{what}:{seed}", tier, judge, balance=balance)
            elif what == "topup2":
                from forge.plans_data.topup2 import TopUp2Maker
                maker = TopUp2Maker(f"{what}:{seed}", tier, judge, balance=balance)
            elif what == "topup":
                from forge.plans_data.topup import TopUpMaker, focus_moves
                TopUpMaker.focus = TopUpMaker.focus or focus_moves()
                maker = TopUpMaker(f"{what}:{seed}", tier, judge, balance=balance)
            else:
                maker = Maker(f"{what}:{seed}", tier, judge, negative=what == "negatives",
                              long=what == "long", balance=balance)
            try:
                if what == "single":        # one part: walk through every shape and turn
                    cells = all_shape_cells()
                    made = maker.make_single(cells[seed % len(cells)])
                else:
                    made = maker.make()
            except Exception as error:      # noqa: BLE001 - one bad seed must not stop a shard
                stats["generator error"] += 1
                rejects.append({"seed": seed, "reason": f"generator error: {error!r}"[:300]})
                continue
            stats.update({f"line: {k}": v for k, v in maker.stats.items()})
            if made is None:
                stats["plan thrown away"] += 1
                reason = next((k for k in reversed(maker.stats) if k not in (
                    "offered", "overlaps", "touches nothing", "does not fit", "not understood",
                    "left something floating")), "ran out of tries")
                rejects.append({"seed": seed, "reason": reason, "lines": maker.text[:40]})
                continue
            kernel = None
            if sandbox is not None:
                # Every kernel-tier plan is built on the kernel and compared (resolve.verify).
                bodies = made.resolution.bodies
                problems = []
                if bodies:
                    verdict = verify(bodies, made.resolution.touching, sandbox)
                    problems = verdict.problems
                    if what == "negatives":     # a plan that `done` refused need not be joined
                        problems = [p for p in problems if "separate groups" not in p
                                    and "touches nothing" not in p and "reaches the ground" not in p]
                wrong = []
                for reply in made.resolution.replies:
                    if reply.attempt is not None and (reply.attempt.against or reply.attempt.floating):
                        said = _check_rejection(reply, sandbox)
                        if said:
                            wrong.append(f"line {reply.number}: {said}")
                kernel = {"built": bool(bodies), "problems": problems + wrong}
                if problems or wrong:
                    stats["kernel disagreed"] += 1
                    rejects.append({"seed": seed, "reason": "kernel disagreed",
                                    "problems": (problems + wrong)[:6], "lines": maker.text})
                    continue
            record = make_record("negatives" if what == "negatives" else "random", made.text,
                                 made.plan, made.resolution,
                                 {"generator": {"featured": "featured-1", "topup": "topup-1", "topup2": "topup2-1",
                                               "ktopup": "ktopup-1", "ktopup2": "ktopup2-1", "ktopup3": "ktopup3-1"}.get(
                                     what, GENERATOR),
                                  "tier": tier, "stream": stream,
                                  "seed": seed, "names": "template"}, ver)
            if record["id"] in seen:
                stats["duplicate"] += 1
                continue
            seen.add(record["id"])
            record["split"], record["held_out"] = splits.assign(
                record["source"], record["id"], made.plan.lines,
                [r.reply for r in made.resolution.replies])
            if kernel:
                record["kernel"] = kernel
            records.append(record)
    finally:
        if sandbox is not None:
            sandbox.close()
    summary = {"stats": dict(stats), "seconds": round(time.time() - started, 1),
               "kernel_calls": judge.calls, "tried": per}
    _write(stream, index, records, summary, rejects)
    return {"stream": stream, "index": index, "records": len(records),
            "seconds": summary["seconds"]}


# --- structures -------------------------------------------------------------------------------

def structure_shard(job: tuple) -> dict:
    kind, index = job
    stream = f"structures-{kind}"
    if is_done(stream, index):
        return {"stream": stream, "index": index, "skipped": True}
    from forge.generators.structures import KINDS
    from forge.generators.structures.sampling import sample
    from forge.plans_data.structures import convert

    fast.share_bodies()
    started = time.time()
    ver = {**versions("structures"),
           "structures": config.package_version("generators/structures")}
    held_out = config.held_out_kinds(list(KINDS))
    rng = random.Random(f"plans:structures:{kind}:{index}")
    records, rejects, stats, seen = [], [], Counter(), set()
    flags: Counter = Counter()
    for _ in range(PER_SHARD["structures"]):
        structure = sample(KINDS[kind], rng)
        if structure.id in seen:
            stats["duplicate draw"] += 1
            continue
        seen.add(structure.id)
        try:
            made = convert(structure)
        except Exception as error:          # noqa: BLE001
            stats["converter error"] += 1
            rejects.append({"structure": structure.id, "variant": structure.choices,
                            "given": structure.given, "reason": f"converter error: {error!r}"[:300]})
            continue
        if not made.ok:
            stats["not converted"] += 1
            rejects.append({"structure": structure.id, "variant": structure.choices,
                            "given": structure.given, "step": made.failed_step,
                            "reason": made.reason})
            continue
        flags.update(made.flags)
        stats["converted"] += 1
        stats["with above ground after the first line"] += bool(made.flags["above_ground_after_first"])
        stats["with long decimals"] += bool(made.flags["long_decimals"])
        record = make_record("structures", made.text, made.plan, made.resolution,
                             {"kind": kind, "structure_id": structure.id, "level": structure.level,
                              "variant": structure.choices, "given": structure.given,
                              "shift_xy": list(made.shift), "names": "from the generator's steps",
                              "flags": made.flags}, ver)
        record["split"], record["held_out"] = splits.assign(
            "structures", record["id"], made.plan.lines,
            [r.reply for r in made.resolution.replies], kind=kind, held_out_kinds=held_out)
        record["kind"] = kind
        records.append(record)
    summary = {"stats": dict(stats), "flags": dict(flags), "kind": kind,
               "seconds": round(time.time() - started, 1), "tried": PER_SHARD["structures"]}
    _write(stream, index, records, summary, rejects)
    return {"stream": stream, "index": index, "records": len(records),
            "seconds": summary["seconds"]}


def structure_rows_shard(job: tuple) -> dict:
    """Convert the verified rows of data/structures/<kind>.jsonl (the generators' own run),
    400 rows per shard. Each row is rebuilt from its variant and stated numbers."""
    kind, index = job
    stream = f"structures-rows-{kind}"
    if is_done(stream, index):
        return {"stream": stream, "index": index, "skipped": True}
    from forge.generators.structures import KINDS
    from forge.plans_data.structures import convert
    from forge.runs import PROJECT_ROOT

    fast.share_bodies()
    started = time.time()
    ver = {**versions("structures"),
           "structures": config.package_version("generators/structures")}
    held_out = config.held_out_kinds(list(KINDS))
    per = PER_SHARD["structures"]
    records, rejects, stats = [], [], Counter()
    flags: Counter = Counter()
    with open(PROJECT_ROOT / "data" / "structures" / f"{kind}.jsonl") as f:
        for number, line in enumerate(f):
            if number < index * per:
                continue
            if number >= (index + 1) * per:
                break
            row = json.loads(line)
            try:
                structure = KINDS[kind].build(row["variant"], row["given"])
                if len(structure.solids) != row["expected"]["n_parts"]:
                    raise ValueError("the generator no longer builds this row's part count")
                made = convert(structure)
            except Exception as error:          # noqa: BLE001
                stats["converter error"] += 1
                rejects.append({"structure": row["id"], "reason": f"error: {error!r}"[:300]})
                continue
            if not made.ok:
                stats["not converted"] += 1
                rejects.append({"structure": row["id"], "variant": row["variant"],
                                "given": row["given"], "step": made.failed_step,
                                "reason": made.reason})
                continue
            flags.update(made.flags)
            stats["converted"] += 1
            stats["with above ground after the first line"] += bool(made.flags["above_ground_after_first"])
            stats["with long decimals"] += bool(made.flags["long_decimals"])
            record = make_record("structures", made.text, made.plan, made.resolution,
                                 {"kind": kind, "structure_id": row["id"], "level": row["level"],
                                  "variant": row["variant"], "given": row["given"],
                                  "from": f"data/structures/{kind}.jsonl",
                                  "shift_xy": list(made.shift), "names": "from the generator's steps",
                                  "flags": made.flags}, ver)
            record["split"], record["held_out"] = splits.assign(
                "structures", record["id"], made.plan.lines,
                [r.reply for r in made.resolution.replies], kind=kind, held_out_kinds=held_out)
            record["kind"] = kind
            records.append(record)
    summary = {"stats": dict(stats), "flags": dict(flags), "kind": kind,
               "seconds": round(time.time() - started, 1), "tried": per}
    _write(stream, index, records, summary, rejects)
    return {"stream": stream, "index": index, "records": len(records),
            "seconds": summary["seconds"]}


# --- single parts -----------------------------------------------------------------------------

def part_files() -> list[tuple[str, Path, bool]]:
    from forge.system1.parts import BASES, GENERATED_DIR, LONG_DIR
    files = [(f"parts-{base}", GENERATED_DIR / f"composed_{base}.jsonl", False) for base in BASES]
    files += [(f"parts-long-{base}", LONG_DIR / f"composed_{base}.jsonl", True) for base in BASES
              if (LONG_DIR / f"composed_{base}.jsonl").exists()]
    return files


def part_shard(job: tuple) -> dict:
    stream, path, long, index = job
    if is_done(stream, index):
        return {"stream": stream, "index": index, "skipped": True}
    from forge.plans_data.parts import convert
    from forge.system1.steps import FEATURES

    fast.share_bodies()
    started = time.time()
    ver = versions("parts")
    per = PER_SHARD["parts"]
    records, rejects, stats = [], [], Counter()
    by_kind: Counter = Counter()
    with open(path) as f:
        for number, line in enumerate(f):
            if number < index * per:
                continue
            if number >= (index + 1) * per:
                break
            part = json.loads(line)
            part["long"] = long
            made = convert(part)
            kinds = sorted(set(made.kinds)) if made.kinds else []
            if not made.ok:
                stats["not converted"] += 1
                by_kind[f"not converted: {made.failed_kind}"] += 1
                rejects.append({"part": part["id"], "family": part["family"],
                                "reason": made.reason})
                continue
            stats["converted"] += 1
            for kind in kinds:
                by_kind[f"converted: {kind}"] += 1
            record = make_record("parts", made.text, made.plan, made.resolution,
                                 {"part_id": part["id"], "family": part["family"],
                                  "part_generator_version": part["generator_version"],
                                  "part_fingerprint": part["geom_fingerprint"],
                                  "turned_quarter": made.turned, "long_part": long,
                                  "feature_kinds": [k for k in made.kinds if k in FEATURES],
                                  "feature_lines_checked": "assumed one solid (TrustStored); "
                                                           "see the kernel sample"}, ver)
            record["split"], record["held_out"] = splits.assign(
                "parts", record["id"], made.plan.lines,
                [r.reply for r in made.resolution.replies], part=part)
            record["family"] = part["family"]
            records.append(record)
    summary = {"stats": dict(stats), "by_kind": dict(by_kind),
               "seconds": round(time.time() - started, 1), "tried": per}
    _write(stream, index, records, summary, rejects)
    return {"stream": stream, "index": index, "records": len(records),
            "seconds": summary["seconds"]}


# --- the command ------------------------------------------------------------------------------

def _range(text: str) -> list[int]:
    low, _, high = text.partition("-")
    return list(range(int(low), int(high or low) + 1))


def past(until: str | None) -> bool:
    return bool(until) and time.strftime("%H:%M") >= until and time.strftime("%H") < "12"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build raw shards of the plans data set.")
    parser.add_argument("what", choices=("random", "single", "featured", "topup", "topup2", "ktopup",
                                         "ktopup2", "ktopup3", "structure-rows", "negatives",
                                         "long", "structures", "parts"))
    parser.add_argument("--tier", choices=("arithmetic", "kernel"), default="arithmetic")
    parser.add_argument("--shards", default="0-0")
    parser.add_argument("--kinds", nargs="*")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--until", help="HH:MM (before noon): hand out no new shard after this")
    args = parser.parse_args()

    indices = _range(args.shards)
    if args.what in ("random", "single", "featured", "topup", "topup2", "ktopup", "ktopup2",
                     "ktopup3", "negatives", "long"):
        tier = ("kernel" if args.what in ("featured", "ktopup", "ktopup2", "ktopup3")
                else args.tier)
        worker, jobs = random_shard, [(args.what, tier, i) for i in indices]
    elif args.what == "structure-rows":
        from forge.generators.structures import KINDS
        from forge.runs import PROJECT_ROOT
        worker, jobs = structure_rows_shard, []
        for kind in args.kinds or list(KINDS):
            path = PROJECT_ROOT / "data" / "structures" / f"{kind}.jsonl"
            if path.exists():
                with open(path) as f:
                    rows = sum(1 for _ in f)
                jobs += [(kind, i) for i in range((rows + PER_SHARD["structures"] - 1)
                                                  // PER_SHARD["structures"])]
    elif args.what == "structures":
        from forge.generators.structures import KINDS
        kinds = args.kinds or list(KINDS)         # the registry as it is NOW, never a fixed list
        worker, jobs = structure_shard, [(kind, i) for i in indices for kind in kinds]
    else:
        worker, jobs = part_shard, []
        for stream, path, long in part_files():
            with open(path) as f:
                rows = sum(1 for _ in f)
            jobs += [(stream, path, long, i) for i in range((rows + PER_SHARD["parts"] - 1)
                                                            // PER_SHARD["parts"])]
    run_dir = start_run(f"plans-{args.what}-{args.tier}", {**vars(args), "jobs": len(jobs),
                                                           "versions": _VERSIONS})
    done = 0
    with ProcessPoolExecutor(args.workers) as pool:
        pending = iter(jobs)
        running = set()
        from concurrent.futures import FIRST_COMPLETED, wait
        while True:
            while len(running) < args.workers and not past(args.until):
                job = next(pending, None)
                if job is None:
                    break
                running.add(pool.submit(worker, job))
            if not running:
                break
            finished, running = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                result = future.result()
                done += 1
                log_metrics(run_dir, **{k: str(v) if isinstance(v, Path) else v
                                        for k, v in result.items()})
                print(f"[{time.strftime('%H:%M:%S')}] {result}", flush=True)
    print(f"{done} of {len(jobs)} shards handled; run folder {run_dir}")


if __name__ == "__main__":
    main()
