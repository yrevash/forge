"""Fresh composed parts for more training sessions, beyond the stored 120,000.

Same generator, same check, a different seed. Every part is built by the CAD
kernel in the sandbox and its measured bounding box, volume and round features
must equal the generator's arithmetic (`forge.data.generate.verify`), exactly as
for data/generated/. Nothing is written into data/generated/.

A fresh part is kept only if
  - its id and its geometry are not among the stored parts or the long test parts, and
  - `forge.system1.splits.split_of` puts it in `train`: no held-out pairing, at most five
    items, and not in the 5% that the geometry hash gives to `iid`.
So the splits stay exactly as they are, and these parts are train parts by the same rule.

Run:    uv run python -m forge.freecad.extra_parts [--per-base 5000] [--seed 1] [--workers 1]
        (run again with a larger --per-base to add more; what is there is kept)
Output: data/freecad/extra_parts/composed_<base>.jsonl   (same row format as data/generated/)
"""

from __future__ import annotations

import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

from forge.data.generate import generator_version, row, verify
from forge.generators import FAMILIES
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.system1.parts import BASES, read_parts
from forge.system1.splits import held_out_pairs_in, split_of
from forge.system1.steps import steps_of

EXTRA_DIR = PROJECT_ROOT / "data" / "freecad" / "extra_parts"
BATCH = 250


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-base", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    EXTRA_DIR.mkdir(parents=True, exist_ok=True)
    version = generator_version()
    run_dir = start_run("freecad-extra-parts", {**vars(args), "generator_version": version})

    seen_ids, seen_shapes = set(), set()
    for part in read_parts():
        seen_ids.add(part["id"])
        seen_shapes.add(part["geom_fingerprint"])
    totals = {"kept": 0, "failed": 0, "not_train": 0}
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        # Round and round the bases, a batch at a time, so stopping early leaves a balanced set.
        files = {base: EXTRA_DIR / f"composed_{base}.jsonl" for base in BASES}
        have = {}
        for base, path in files.items():
            rows = [json.loads(line) for line in path.read_text().splitlines()] \
                if path.exists() else []
            have[base] = len(rows)
            seen_ids.update(r["id"] for r in rows)
            seen_shapes.update(r["geom_fingerprint"] for r in rows)
        rngs = {base: random.Random(f"{args.seed}:extra:{base}:{have[base]}") for base in BASES}
        while any(have[base] < args.per_base for base in BASES):
            for base in BASES:
                if have[base] >= args.per_base:
                    continue
                family = FAMILIES[f"composed_{base}"]
                batch = {}
                while len(batch) < BATCH:
                    part = family.sample(rngs[base])
                    kinds = [step.kind for step in steps_of(part.family, part.params)]
                    if part.id not in seen_ids and part.id not in batch \
                            and not held_out_pairs_in(kinds):
                        batch[part.id] = part
                kept = []
                for part, measure, problems in pool.map(verify, batch.values()):
                    if problems:
                        totals["failed"] += 1
                        continue
                    made = row(part, measure, version)
                    if made["geom_fingerprint"] in seen_shapes \
                            or split_of({**made, "long": False}) != "train":
                        totals["not_train"] += 1
                        continue
                    seen_ids.add(part.id)
                    seen_shapes.add(made["geom_fingerprint"])
                    kept.append(made)
                with files[base].open("a") as f:
                    f.write("".join(json.dumps(r) + "\n" for r in kept))
                have[base] += len(kept)
                totals["kept"] += len(kept)
                print(f"[{(time.time() - started) / 60:5.1f} min] composed_{base}: {have[base]} "
                      f"parts on disk  (this run: {totals})", flush=True)
                log_metrics(run_dir, base=base, on_disk=have[base], **totals)
    log_metrics(run_dir, final=True, on_disk=have, **totals)
    print(f"done: {have}; failed verification: {totals['failed']}")


if __name__ == "__main__":
    main()
