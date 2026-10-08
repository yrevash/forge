"""Generate the longer test parts: composed parts with 6 to 12 items after the start.

Training parts have at most 5. Whether the step model can keep going past the
length it was trained on is one of the tests it has to pass. These parts exist only for that
test: they are never written into data/generated/ and never enter training.

They go through the normal check: every program runs in the sandbox and its
measured bounding box, volume and round features must equal the generator's
arithmetic (`forge.data.generate.verify`). An "item" is a feature or the edge
treatment, the same way the generator counts to five.

Run:    uv run python -m forge.system1.long_parts [--per-base 1000] [--seed 0] [--workers 7]
Output: data/system1/long_parts/composed_<base>.jsonl   (same row format as data/generated/)
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from forge.data.generate import generator_version, row, verify
from forge.generators import FAMILIES
from forge.generators.base import Part
from forge.runs import log_metrics, start_run
from forge.system1.parts import BASES, LONG_DIR
from forge.system1.steps import steps_of

SHORTEST, LONGEST = 6, 12


def long_parts(base: str, how_many: int, seed: int) -> list[Part]:
    """`how_many` different parts on one base, their lengths spread evenly from 6 to 12."""
    family = FAMILIES[f"composed_{base}"]
    rng = random.Random(f"{seed}:long:{base}")
    parts: dict[str, Part] = {}
    while len(parts) < how_many:
        # Ask for one exact length at a time, in turn. Left to chance, the short
        # lengths (which are easier to place) would crowd out the long ones.
        length = SHORTEST + len(parts) % (LONGEST - SHORTEST + 1)
        part = family.sample(rng, features=(length, length))
        parts.setdefault(part.id, part)
    return list(parts.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-base", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=7)
    args = parser.parse_args()

    LONG_DIR.mkdir(parents=True, exist_ok=True)
    version = generator_version()
    run_dir = start_run("system1-long-parts", {**vars(args), "generator_version": version,
                                               "lengths": [SHORTEST, LONGEST]})
    total_bad = 0
    lengths: Counter = Counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for base in BASES:
            started = time.time()
            good, bad = [], []
            for part, measure, problems in pool.map(verify, long_parts(base, args.per_base, args.seed)):
                if problems:
                    bad.append({"id": part.id, "params": part.params, "problems": problems})
                else:
                    good.append(row(part, measure, version))
                    lengths[len(steps_of(part.family, part.params)) - 1] += 1
            (LONG_DIR / f"composed_{base}.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in good))
            failures = LONG_DIR / f"composed_{base}.failures.jsonl"
            if bad:
                failures.write_text("".join(json.dumps(b) + "\n" for b in bad))
            elif failures.exists():
                failures.unlink()
            total_bad += len(bad)
            seconds = round(time.time() - started, 1)
            print(f"composed_{base:10s} {len(good):6d} verified  {len(bad):4d} FAILED  {seconds}s",
                  flush=True)
            log_metrics(run_dir, base=base, verified=len(good), failed=len(bad), seconds=seconds)
    print(f"items after the start: {dict(sorted(lengths.items()))}")
    log_metrics(run_dir, final=True, failed=total_bad, lengths=dict(sorted(lengths.items())))
    if total_bad:
        raise SystemExit(f"{total_bad} long parts failed verification: see {LONG_DIR}")


if __name__ == "__main__":
    main()
