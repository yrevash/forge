"""Generate verified standard-part rows from our own generators.

For every part: build the program text, execute it in the sandbox, measure the
solid, and compare the measurement with the family's own arithmetic. Only parts
that pass every check are written. A failure here is a bug in a generator, so
the job reports it loudly and writes the failures to a separate file.

Rows hold geometry only. Prompts (captions) are added by a later step, once the
question of who writes the prompt wording is settled.

Run:    uv run python -m forge.data.generate [--per-family 2000] [--seed 0] [--workers 7]
Output: data/generated/<family>.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from forge.generators import FAMILIES
from forge.generators.base import Part, check
from forge.generators.tables import TABLE_SOURCE
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.sandbox import Sandbox

OUT_DIR = PROJECT_ROOT / "data" / "generated"
_local = threading.local()


def generator_version() -> str:
    """A hash of the geometry generators and their tables: changes whenever a part could.

    Caption code is left out on purpose: it does not affect geometry, and the
    dataset manifest records the caption code's own hash.
    """
    digest = hashlib.sha256()
    root = PROJECT_ROOT / "forge" / "generators"
    for path in sorted(root.rglob("*")):
        # Caption and prompt code do not change geometry, and the structure
        # generators carry their own version, so neither is part of this stamp.
        skipped = (path.name in {"captions.py", "prompts.py"}
                   or "structures" in path.relative_to(root).parts)
        if path.is_file() and path.suffix in {".py", ".csv"} and not skipped:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def parts_for(family_name: str, per_family: int, seed: int) -> list[Part]:
    """All standard parts, plus random ones up to `per_family`, without repeats."""
    family = FAMILIES[family_name]
    parts = {p.id: p for p in family.standard_parts()}
    rng = random.Random(f"{seed}:{family_name}")
    # Table-only families cannot produce more parts than the table holds, so stop
    # sampling once new draws keep repeating.
    misses = 0
    while len(parts) < per_family and misses < 200:
        part = family.sample(rng)
        if part.id in parts:
            misses += 1
        else:
            parts[part.id] = part
            misses = 0
    return list(parts.values())


def verify(part: Part) -> tuple[Part, dict | None, list[str]]:
    if not hasattr(_local, "sandbox"):
        _local.sandbox = Sandbox(timeout=30)
    reply = _local.sandbox.run(part.code, check_export=True)
    if reply["status"] != "ok":
        return part, None, [f"{reply['status']}: {reply.get('error')}"]
    problems = check(part, reply["measure"])
    if not reply.get("step_export_ok"):
        problems.append("STEP export failed")
    return part, reply["measure"], problems


def row(part: Part, measure: dict, version: str) -> dict:
    family = FAMILIES[part.family]
    return {
        "id": part.id,
        "source": f"gen:{part.family}",
        "license": "forge",
        "generator_version": version,
        "dimension_table": TABLE_SOURCE if family.TABLE else None,
        "family": part.family,
        "designation": part.designation,
        "params": part.params,
        "code": part.code,
        "measured": {k: measure[k] for k in
                     ("volume", "area", "bbox", "n_faces", "n_edges", "cylinders")},
        "geom_fingerprint": measure["fingerprint"],
        # Filled by later steps of the data factory:
        "split": None,
        "captions": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-family", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--families", nargs="*", default=sorted(FAMILIES))
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    version = generator_version()
    run_dir = start_run("generate-standard-parts", {
        "per_family": args.per_family, "seed": args.seed, "families": args.families,
        "generator_version": version, "dimension_table": TABLE_SOURCE,
    })

    total_ok = total_bad = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for name in args.families:
            started = time.time()
            # A finished family leaves a marker saying exactly what it was built
            # with, so a long run that is stopped can pick up where it left off.
            marker = OUT_DIR / f"{name}.done"
            stamp = f"{version} per_family={args.per_family} seed={args.seed}"
            if marker.exists() and marker.read_text().split("|")[0] == stamp:
                done = int(marker.read_text().split("|")[1])
                total_ok += done
                print(f"{name:24s} {done:6d} verified  (already done, skipped)", flush=True)
                continue
            marker.unlink(missing_ok=True)
            parts = parts_for(name, args.per_family, args.seed)
            good, bad = [], []
            for part, measure, problems in pool.map(verify, parts):
                if problems:
                    bad.append({"id": part.id, "params": part.params, "problems": problems})
                else:
                    good.append(row(part, measure, version))
            (OUT_DIR / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in good))
            failures = OUT_DIR / f"{name}.failures.jsonl"
            if bad:
                failures.write_text("".join(json.dumps(b) + "\n" for b in bad))
            elif failures.exists():
                failures.unlink()
            if not bad:
                marker.write_text(f"{stamp}|{len(good)}")
            total_ok, total_bad = total_ok + len(good), total_bad + len(bad)
            seconds = round(time.time() - started, 1)
            print(f"{name:24s} {len(good):6d} verified  {len(bad):4d} FAILED  {seconds}s",
                  flush=True)
            log_metrics(run_dir, family=name, verified=len(good), failed=len(bad),
                        seconds=seconds)

    print(f"\ntotal: {total_ok} verified, {total_bad} failed (generator version {version})")
    log_metrics(run_dir, final=True, verified=total_ok, failed=total_bad)
    if total_bad:
        raise SystemExit(f"{total_bad} parts failed verification: see data/generated/*.failures.jsonl")


if __name__ == "__main__":
    main()
