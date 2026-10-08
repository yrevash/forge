"""Speed, stability and memory of the interface runtime.

  1. one instance builds `--parts` parts without a restart: actions per second,
     parts per hour, and every tenth part the instance's memory, its widget count
     and how long a part takes (does it slow down as it ages?);
  2. the same parts on two instances at once.

Run:    uv run python -m forge.freecad_ui.bench [--parts 120] [--skip-two]
"""

from __future__ import annotations

import argparse
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from forge.freecad.parts import sample_rows
from forge.freecad_ui.build import against_stored, build_part
from forge.freecad_ui.client import UIClient
from forge.runs import log_metrics, start_run


def _build(ui: UIClient, row: dict) -> tuple[bool, int, float]:
    started = time.time()
    result = build_part(ui, row["family"], row["params"], random.Random(row["id"]))
    good = result.ok and not against_stored(result.solid, row["measured"])
    return good, result.actions, time.time() - started


def one_instance(rows: list[dict]) -> dict:
    series = []
    with UIClient(restart_after=None) as ui:
        begin = ui.stats()
        started = time.time()
        good = actions = 0
        window: list[tuple[int, float]] = []
        for number, row in enumerate(rows, start=1):
            matched, count, seconds = _build(ui, row)
            good += matched
            actions += count
            window.append((count, seconds))
            if number % 10 == 0:
                stats = ui.stats()
                point = {"parts": number, "rss_mb": stats["rss_mb"], "widgets": stats["widgets"],
                         "menus": stats["menus"],
                         "ms_per_action": round(1000 * sum(s for _, s in window)
                                                / sum(c for c, _ in window), 1)}
                series.append(point)
                print(f"  after {number:4d} parts: {point['rss_mb']:7.0f} MB, "
                      f"{point['widgets']:5d} widgets, {point['ms_per_action']:5.1f} ms per action",
                      flush=True)
                window = []
        seconds = time.time() - started
        unplanned, failures = ui.restarts, list(ui.failures)
    return {"parts": len(rows), "matched": good, "actions": actions, "seconds": round(seconds, 1),
            "actions_per_s": round(actions / seconds, 1),
            "parts_per_hour": round(3600 * len(rows) / seconds),
            "start_rss_mb": begin["rss_mb"], "series": series, "crashes_or_hangs": unplanned,
            "failures": failures}


def two_instances(rows: list[dict]) -> dict:
    local = threading.local()
    clients: list[UIClient] = []

    def work(row: dict) -> tuple[bool, int, float]:
        if not hasattr(local, "ui"):
            local.ui = UIClient(restart_after=None)
            local.ui.start()
            clients.append(local.ui)
        return _build(local.ui, row)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(work, rows[:2]))          # both instances up before the clock starts
            started = time.time()
            done = list(pool.map(work, rows))
            seconds = time.time() - started
    finally:
        unplanned = sum(c.restarts for c in clients)
        for c in clients:
            c.close()
    actions = sum(count for _, count, _ in done)
    return {"parts": len(rows), "matched": sum(good for good, _, _ in done), "actions": actions,
            "seconds": round(seconds, 1), "actions_per_s": round(actions / seconds, 1),
            "parts_per_hour": round(3600 * len(rows) / seconds), "crashes_or_hangs": unplanned}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--parts", type=int, default=120)
    parser.add_argument("--seed", type=int, default=2)
    parser.add_argument("--skip-two", action="store_true")
    args = parser.parse_args()
    run_dir = start_run("freecad-ui-bench", vars(args))
    # Three quarters with 1 to 5 features, one quarter long (6 to 12), across the four bases.
    rows = sample_rows(args.parts * 3 // 16, args.parts // 16, args.seed)
    random.Random(args.seed).shuffle(rows)

    print(f"one instance, {len(rows)} parts, no restart")
    one = one_instance(rows)
    print(f"  {one['matched']}/{one['parts']} matched, {one['actions']} actions in {one['seconds']}s: "
          f"{one['actions_per_s']} actions/s, {one['parts_per_hour']} parts/hour; "
          f"crashes or hangs: {one['crashes_or_hangs']}")
    log_metrics(run_dir, one_instance=one)
    if not args.skip_two:
        print(f"two instances, the same {len(rows)} parts")
        two = two_instances(rows)
        print(f"  {two['matched']}/{two['parts']} matched, {two['actions']} actions in "
              f"{two['seconds']}s: {two['actions_per_s']} actions/s, "
              f"{two['parts_per_hour']} parts/hour; crashes or hangs: {two['crashes_or_hangs']}")
        log_metrics(run_dir, two_instances=two)


if __name__ == "__main__":
    main()
