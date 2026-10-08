"""The floor: how far a predictor gets WITHOUT reading the plan.

Forge-S1's job is to compare the session with the plan. Many steps of a session
can be answered without doing that: after `sketch_circle` the next command is
nearly always a dimension, and when `leave_sketch` is the only sensible valid
command it is the answer. A model's accuracy means nothing until it is shown
next to what these lookups reach, on the same steps.

Three lookups, each "the most frequent target for this key in the fit data":
    previous_command   key: the command that was executed just before (none at step 0)
    valid_list         key: the list of valid commands
    plan_blind_state   key: the valid list, what is selected (its type), the type of the
                       newest object, and, if that is a sketch, its newest shape with the
                       dimensions fixed so far and whether it is open
None of them sees the plan or any number of it. A key never seen in the fit data
gets the most frequent target overall.

A prediction counts as right when it is one of the teacher's commands for that
step. Only the command is scored, not its arguments.

Accuracy is reported overall and for four groups of steps (they overlap, see GROUPS):
    undo decisions            the state is off plan: the answer is `undo` (or `new_document`)
    right after a mistake     the command before was a wrong one and changed the session
    wrong-number mistakes     the command before was a teacher command with ONE NUMBER wrong,
                              and the state is now off plan. Nothing but a value in the
                              snapshot against the plan shows it.
    other on-plan steps       on plan, and not right after a mistake: the ordinary build

The predictors get the model's view only (load.view) and the previous command.
`progress`, `executed.noise` and `reply` are used here to sort steps into groups,
never to predict.

Run:    uv run python -m forge.freecad.baselines --fit train_v2 --eval iid_v2 pairing_v2 long_v2
        uv run python -m forge.freecad.baselines --fit train --eval iid pairing long
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
from collections import Counter
from pathlib import Path

from forge.freecad.load import label, usable, view
from forge.freecad.shards import OUT_DIR, SLICES, complete_shards, read_sessions
from forge.freecad.shards import changed as step_changed
from forge.runs import log_metrics, start_run

# The slices of the third mix (forge/freecad/wide_sessions.py SLICES; a test keeps the two
# lists the same). Written out here so that this tool does not import the recorder.
WIDE_SLICES = ("wide_iid_v3", "wide_numbers_v3", "wide_order_v3", "abnormal_starts_v3",
               "train_wide_v3", "train_wide_long_v3")

BASELINES = ("previous_command", "valid_list", "plan_blind_state")
GROUPS = ("all steps", "undo decisions", "right after a mistake", "wrong-number mistakes",
          "other on-plan steps")

_tables: dict[str, dict] = {}       # baseline -> key -> predicted command; set in every worker
_pure: set[str] = set()             # sessions of long_pure.json


# --- keys: what each lookup looks at -------------------------------------------------------------

def keys_of(seen: dict, previous: str | None) -> dict[str, object]:
    """The three lookup keys of one step, from the model's view and the previous command."""
    snapshot, valid = seen["snapshot"], tuple(seen["valid"])
    items, session = snapshot["items"], snapshot["session"]
    newest = items[-1] if items else None
    shape = None
    if newest is not None and newest["type"] == "sketch":
        drawn = newest["shapes"]
        shape = (drawn[-1]["shape"], len(drawn), tuple(drawn[-1]["fixed"])) if drawn else ("none",)
    selection = session["selection"]
    return {"previous_command": previous, "valid_list": valid,
            "plan_blind_state": (valid, selection["type"] if selection else None,
                                 newest["type"] if newest else None, shape,
                                 session["open_sketch"] is not None)}


def groups_of(record: dict, before: dict | None, before_changed: bool) -> list[str]:
    """Which GROUPS a step belongs to (uses recorded facts a model never sees)."""
    on_plan = record["progress"]["on_plan"]
    mistake = before is not None and before["executed"]["noise"] is not None and before_changed
    found = ["all steps"]
    if not on_plan:
        found.append("undo decisions")
    if mistake:
        found.append("right after a mistake")
        if before["executed"]["noise"] == "wrong_argument" and not on_plan:
            found.append("wrong-number mistakes")
    elif on_plan:
        found.append("other on-plan steps")
    return found


def steps_of_shard(path: str):
    """(keys, the label's commands, groups, session id) for every step of a shard's usable sessions."""
    for header, records, end in read_sessions(Path(path)):
        if not usable(end):
            continue
        before, changed = None, False
        for record in records:
            previous = before["executed"]["command"] if before else None
            keys = keys_of(view(header["plan"], record["snapshot"], record["valid"]), previous)
            names = [target["command"] for target in label(record["target"])]
            yield keys, names, groups_of(record, before, changed), header["session"]
            after = records[record["t"] + 1] if record["t"] + 1 < len(records) else end
            before, changed = record, step_changed(record, after)


# --- fit and score ---------------------------------------------------------------------------------

def count_shard(path: str) -> dict[str, dict]:
    """key -> how often each command was a target (a step with k targets gives each 1/k)."""
    counts: dict[str, dict] = {name: {} for name in (*BASELINES, "overall")}
    for keys, names, _, _ in steps_of_shard(path):
        for name in names:
            for baseline in BASELINES:
                counts[baseline].setdefault(keys[baseline], Counter())[name] += 1 / len(names)
            counts["overall"].setdefault(None, Counter())[name] += 1 / len(names)
    return counts


def fit(paths: list[str], workers: int) -> dict[str, dict]:
    """The lookup tables: for every key the most frequent target command."""
    total: dict[str, dict] = {name: {} for name in (*BASELINES, "overall")}
    with multiprocessing.Pool(workers) as pool:
        for counts in pool.imap_unordered(count_shard, paths):
            for baseline, table in counts.items():
                for key, found in table.items():
                    total[baseline].setdefault(key, Counter()).update(found)
    return {baseline: {key: found.most_common(1)[0][0] for key, found in table.items()}
            for baseline, table in total.items()}


def _load(tables: dict, pure: set[str]) -> None:
    _tables.update(tables)
    _pure.update(pure)


def score_shard(path: str) -> Counter:
    """Right predictions and step counts per (subset, group, baseline) for one shard."""
    scores: Counter = Counter()
    fallback = _tables["overall"][None]
    for keys, names, groups, session in steps_of_shard(path):
        subsets = ("all", "pure") if session in _pure else ("all",)
        for subset in subsets:
            for group in groups:
                scores[(subset, group, "n")] += 1
                for baseline in BASELINES:
                    guess = _tables[baseline].get(keys[baseline], fallback)
                    scores[(subset, group, baseline)] += guess in names
                    scores[(subset, group, f"{baseline}:unseen")] += \
                        keys[baseline] not in _tables[baseline]
    return scores


def table(scores: Counter, subset: str = "all") -> dict[str, dict]:
    """group -> {"steps", baseline -> accuracy} for one evaluated slice."""
    rows = {}
    for group in GROUPS:
        steps = scores[(subset, group, "n")]
        if steps:
            rows[group] = {"steps": steps, **{baseline: round(scores[(subset, group, baseline)]
                                                               / steps, 4)
                                              for baseline in BASELINES}}
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # The slices of the third mix live in a folder of their own (wide_sessions.py): name them
    # here together with `--dir data/freecad/sessions_wide`.
    names = [*SLICES, *WIDE_SLICES]
    parser.add_argument("--fit", default="train_v2", choices=names)
    parser.add_argument("--eval", nargs="+", default=["iid_v2", "pairing_v2", "long_v2"],
                        choices=names)
    parser.add_argument("--fit-shards", type=int, default=20,
                        help="how many shards of the fit slice to count (spread evenly)")
    parser.add_argument("--held-train-shards", type=int, default=3,
                        help="shards of the fit slice kept out of the fit and scored as 'held'")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--dir", default=str(OUT_DIR))
    args = parser.parse_args()
    out_dir = Path(args.dir)
    run_dir = start_run("freecad-baselines", vars(args))

    of_fit = [str(path) for _, path in complete_shards(out_dir, (args.fit,))]
    held = of_fit[-args.held_train_shards:] if args.held_train_shards else []
    rest = of_fit[:len(of_fit) - len(held)]
    every = max(1, len(rest) // args.fit_shards)
    chosen = rest[::every][:args.fit_shards]
    tables = fit(chosen, args.workers)
    print(f"fit on {len(chosen)} shards of {args.fit}: "
          + ", ".join(f"{len(tables[name])} {name} keys" for name in BASELINES))

    pure_file = out_dir / "long_pure.json"
    listing = json.loads(pure_file.read_text()) if pure_file.exists() else {}
    pure = {entry["session"] for name in args.eval for entry in listing.get(name, {}).get("pure", [])}
    evaluated = {f"{args.fit} (held shards)": held} if held else {}
    evaluated.update({name: [str(path) for _, path in complete_shards(out_dir, (name,))]
                      for name in args.eval})
    report = {}
    with multiprocessing.Pool(args.workers, initializer=_load, initargs=(tables, pure)) as pool:
        for name, paths in evaluated.items():
            scores: Counter = Counter()
            for found in pool.imap_unordered(score_shard, paths):
                scores.update(found)
            report[name] = table(scores)
            if scores[("pure", "all steps", "n")]:
                report[f"{name} (long_pure)"] = table(scores, "pure")
    width = max(len(group) for group in GROUPS)
    for name, rows in report.items():
        print(f"\n{name}")
        print(f"  {'group':{width}s} {'steps':>9s}  " + "  ".join(f"{b:>17s}" for b in BASELINES))
        for group, row in rows.items():
            print(f"  {group:{width}s} {row['steps']:9d}  "
                  + "  ".join(f"{row[b]:17.4f}" for b in BASELINES))
    (run_dir / "baselines.json").write_text(json.dumps(
        {"fit": args.fit, "fit_shards": [Path(path).name for path in chosen], "tables": report},
        indent=1) + "\n")
    log_metrics(run_dir, final=True, fit=args.fit,
                **{f"{name}|{group}|{baseline}": row[baseline]
                   for name, rows in report.items() for group, row in rows.items()
                   for baseline in BASELINES})
    print(f"\nrun folder: {run_dir}")


if __name__ == "__main__":
    main()
