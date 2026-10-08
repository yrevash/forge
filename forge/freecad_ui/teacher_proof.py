"""Proof that the interface teacher can build parts on its own, and survive mistakes.

  clean   the teacher alone drives FreeCAD's interface (no recipe is replayed: every action
          is the teacher's answer to the snapshot in front of it). Every part must end at
          its stored solid in exactly as many actions as a clean build takes.
  noisy   the same with wrong actions injected at a fixed chance: every session must still
          end at the stored solid, on a clean document.

Run:    uv run python -m forge.freecad_ui.teacher_proof [--per-base 60] [--long-per-base 15]
            [--noise 0.0] [--workers 1]
Results are appended to data/freecad_ui/teacher_proof/noise<level>.jsonl; parts already
there are skipped. Exit code 0 only if every session passed.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from forge.freecad.parts import session_parts
from forge.freecad_ui.client import UIClient
from forge.freecad_ui.play import play
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.system1.splits import unit_hash

OUT_DIR = PROJECT_ROOT / "data" / "freecad_ui" / "teacher_proof"
_local = threading.local()
_clients: list[UIClient] = []
_write = threading.Lock()


def client() -> UIClient:
    if not hasattr(_local, "client"):
        _local.client = UIClient()
        _local.client.start()
        _clients.append(_local.client)
    return _local.client


def sample(per_base: int, long_per_base: int, seed: int) -> list[dict]:
    """A fixed sample: the first parts of each file in a hashed order."""
    groups: dict[tuple[str, bool], list[dict]] = {}
    for part in session_parts():
        groups.setdefault((part["family"], part["long"]), []).append(part)
    chosen = []
    for (_, long), parts in sorted(groups.items()):
        parts.sort(key=lambda part: unit_hash(f"teacher-proof:{seed}:{part['id']}"))
        chosen += parts[:long_per_base if long else per_base]
    return chosen


def prove_one(part: dict, noise: float, seed: int) -> dict:
    started = time.time()
    try:
        header, records, end = play(client(), part, seed, "proof", (noise,))
    except Exception as error:      # noqa: BLE001 - a lost instance is a failed session, not a crash
        _local.client.close()
        del _local.client
        return {"id": part["id"], "family": part["family"], "long": part["long"], "end": "error",
                "problems": [f"{type(error).__name__}: {str(error)[:300]}"], "steps": 0,
                "clean_length": 0, "noisy": 0, "kinds": [], "noise_kinds": {}, "off_plan": 0}
    noisy = Counter(r["executed"]["noise"] for r in records if r["executed"]["noise"])
    refused = [f"t={r['t']} {r['executed']['id']} -> {r['reply']}" for r in records
               if not r["executed"]["noise"] and r["reply"]["status"] != "ok"]
    return {"id": part["id"], "family": part["family"], "long": part["long"], "end": end["end"],
            "problems": end["problems"] + refused[:3], "steps": len(records),
            "clean_length": header["clean_length"], "noisy": sum(noisy.values()),
            "noise_kinds": dict(noisy), "kinds": [item["kind"] for item in header["plan"]],
            "off_plan": sum(not r["progress"]["on_plan"] for r in records),
            "seconds": round(time.time() - started, 2)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-base", type=int, default=60)
    parser.add_argument("--long-per-base", type=int, default=15)
    parser.add_argument("--noise", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-minutes", type=float, default=None)
    args = parser.parse_args()
    run_dir = start_run("freecad-ui-teacher-proof", vars(args))
    out = OUT_DIR / f"noise{args.noise}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = [json.loads(line) for line in out.read_text().splitlines()] if out.exists() else []
    parts = sample(args.per_base, args.long_per_base, args.seed)
    have = {r["id"] for r in done}
    todo = [part for part in parts if part["id"] not in have]
    print(f"{len(parts)} parts, {len(parts) - len(todo)} already done, {len(todo)} to play",
          flush=True)
    deadline = None if args.max_minutes is None else time.time() + 60 * args.max_minutes

    def work(part: dict) -> dict | None:
        if deadline is not None and time.time() > deadline:
            return None
        result = prove_one(part, args.noise, args.seed)
        with _write, out.open("a") as f:
            f.write(json.dumps(result) + "\n")
        return result

    started = time.time()
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            fresh = [r for r in pool.map(work, todo) if r is not None]
    finally:
        restarts = sum(c.restarts for c in _clients)
        for c in _clients:
            c.close()
    wanted = {part["id"] for part in parts}
    results = [r for r in done if r["id"] in wanted] + fresh
    good = [r for r in results if r["end"] == "done" and not r["problems"]]
    bad = [r for r in results if r not in good]
    exact = sum(r["steps"] == r["clean_length"] for r in good)
    kinds: Counter = Counter(kind for r in good for kind in r["kinds"])
    noise_kinds: Counter = Counter()
    for r in results:
        noise_kinds.update(r["noise_kinds"])
    steps = sum(r["steps"] for r in results)
    print(f"{len(results)} sessions ({sum(r['long'] for r in results)} of long parts), {steps} "
          f"steps, noise {args.noise}: {len(good)} ended at the stored solid on a clean "
          f"document, {len(bad)} did not")
    print(f"  sessions exactly as long as a clean build: {exact}")
    print(f"  wrong actions carried out: {sum(r['noisy'] for r in results)} {dict(noise_kinds)}; "
          f"steps off plan: {sum(r['off_plan'] for r in results)}")
    print(f"  step kinds built: {dict(sorted(kinds.items()))}")
    for r in bad[:15]:
        print(f"  FAILED {r['id']} {r['family']} end={r['end']}: {r['problems'][:2]}")
    if fresh:
        seconds = time.time() - started
        print(f"  this run: {len(fresh)} sessions in {seconds:.0f}s "
              f"({3600 * len(fresh) / seconds:.0f}/hour); instance restarts: {restarts}")
    left = len(parts) - len(results)
    if left:
        print(f"  {left} parts not played yet: run the same command again")
    log_metrics(run_dir, final=True, sessions=len(results), good=len(good), bad=len(bad),
                steps=steps, exact_length=exact, noise=args.noise, noise_kinds=dict(noise_kinds),
                failed_ids=[r["id"] for r in bad], restarts=restarts)
    raise SystemExit(1 if bad or left else 0)


if __name__ == "__main__":
    main()
