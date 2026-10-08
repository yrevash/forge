"""Which plan numbers sit on a grid? The table before (composed parts) and after (wide parts).

The first Forge-S1 model called a correct 41.5 mm hexagon wrong because all 30,000 hexagons
it had seen had a whole-number size. This tool finds every
such field instead of guessing: for each plan slot of each item kind it reads every training
value and reports how many different values there are, the smallest step that all of them are
multiples of (the GRID), the range, and how many are whole numbers.

    before   the plans the first two models were trained on: the `train` and `train_long`
             splits of the composed generator
    after    the wide_train parts (wide_parts.py)

It also counts the plan HABITS the composed generator has and the wide sampler breaks: where
the edge treatment comes, how many there are, base-only plans, repeated diameters, which
feature kinds occur on which base, which side a pair's first feature is on.

Run:    uv run python -m forge.freecad.wide_stats
Output: data/system1/wide_parts/field_stats.json and field_stats.md
"""

from __future__ import annotations

import json
from collections import Counter

from forge.freecad.parts import session_parts
from forge.freecad.wide_parts import WIDE_DIR, read_wide
from forge.freecad.wide_sample import decimals_of
from forge.runs import log_metrics, start_run
from forge.system1.splits import split_of
from forge.system1.steps import FEATURES, STARTS, TREATMENTS, steps_of

STEPS = (10.0, 5.0, 2.0, 1.0, 0.5, 0.25, 0.1, 0.05, 0.01, 0.001, 0.0001)
ROUND_SLOTS = ("diameter", "hole_diameter")


def grid_of(values: set[float]) -> float | None:
    """The largest step of STEPS that every value is a multiple of (None: finer than all)."""
    for step in STEPS:
        if all(abs(value / step - round(value / step)) < 1e-6 for value in values):
            return step
    return None


def field_table(plans: list[list[dict]]) -> dict[str, dict]:
    values: dict[str, list[float]] = {}
    for plan in plans:
        for item in plan:
            for slot, value in item["slots"].items():
                values.setdefault(f"{item['kind']}.{slot}", []).append(float(value))
    table = {}
    for field, found in sorted(values.items()):
        distinct = set(found)
        table[field] = {
            "values": len(found), "distinct": len(distinct), "grid_step": grid_of(distinct),
            "min": min(distinct), "max": max(distinct),
            "whole_number_share": round(sum(v == int(v) for v in found) / len(found), 4),
            "most_decimals": max(decimals_of(v) for v in distinct),
            "value_set": sorted(distinct) if len(distinct) <= 12 else None,
        }
    return table


def habits(plans: list[list[dict]]) -> dict:
    n = len(plans)
    count: Counter = Counter()
    where: Counter = Counter()
    per_base: dict[str, Counter] = {}
    for plan in plans:
        kinds = [item["kind"] for item in plan]
        treated = [at for at, kind in enumerate(kinds) if kind in TREATMENTS]
        count[f"items:{len(plan)}"] += 1
        count[f"treatments:{len(treated)}"] += 1
        for at in treated:
            where["second item" if at == 1 else "last item" if at == len(plan) - 1
                  else "in the middle"] += 1
            if at == 1 and at == len(plan) - 1:
                where["second AND last (two-item plan)"] += 1
        count["base only"] += len(plan) == 1
        diameters = [item["slots"][slot] for item in plan[1:] for slot in ROUND_SLOTS
                     if slot in item["slots"]]
        count["a diameter used twice"] += len(diameters) != len(set(diameters))
        count["has two or more round features"] += len(diameters) >= 2
        pairs = [item for item in plan if item["kind"].endswith("_pair")]
        count["pairs"] += len(pairs)
        count["pairs with the first feature at x < 0"] += sum(item["slots"]["x"] < 0 for item in pairs)
        slots = [item for item in plan if item["kind"] == "slot"]
        count["slots"] += len(slots)
        count["slots not along X or Y"] += sum(item["slots"]["angle"] not in (0.0, 90.0)
                                               for item in slots)
        if kinds[0] == "block":
            count["blocks"] += 1
            count["blocks wider than long"] += plan[0]["slots"]["width"] > plan[0]["slots"]["length"]
        count["shell followed by a cut"] += "shell" in kinds and any(
            kind in FEATURES and kind not in ("boss", "pad", "boss_pair")
            for kind in kinds[kinds.index("shell"):])
        per_base.setdefault(kinds[0], Counter()).update(set(kinds[1:]))
    return {"plans": n, "counts": dict(sorted(count.items())),
            "where_the_edge_treatment_is": dict(where),
            "plans_with_each_kind_by_base": {base: dict(sorted(found.items()))
                                             for base, found in sorted(per_base.items())}}


def composed_plans() -> list[list[dict]]:
    plans = []
    for part in session_parts(added=True):
        if split_of(part) in ("train", "train_long"):
            plans.append([{"kind": step.kind, "slots": dict(step.slots)}
                          for step in steps_of(part["family"], part["params"])])
    return plans


def _show(value: float | None) -> str:
    return "none" if value is None else f"{value:g}"


def markdown(before: dict, after: dict) -> str:
    head = ("| Plan field | Before: values (distinct) | Before: grid step, range | Before: whole | "
            "After: values (distinct) | After: grid step, range | After: whole |")
    lines = [head, "| --- | --- | --- | --- | --- | --- | --- |"]
    for field in sorted(set(before) | set(after)):
        cells = []
        for table in (before, after):
            row = table.get(field)
            if row is None:
                cells += ["none", "", ""]
                continue
            which = (f"only {', '.join(_show(v) for v in row['value_set'])}"
                     if row["value_set"] is not None and row["distinct"] <= 12
                     else f"step {_show(row['grid_step'])}, {row['min']:g} to {row['max']:g}")
            cells += [f"{row['values']} ({row['distinct']})", which,
                      f"{row['whole_number_share']:.1%}"]
        lines.append(f"| `{field}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    run_dir = start_run("freecad-wide-stats", {})
    old = composed_plans()
    new = [part["plan"] for part in read_wide("wide_train")]
    before, after = field_table(old), field_table(new)
    report = {"before": {"what": "composed train + train_long", "plans": len(old),
                         "fields": before, "habits": habits(old)},
              "after": {"what": "wide_train", "plans": len(new), "fields": after,
                        "habits": habits(new)},
              "kinds": {"starts": list(STARTS), "treatments": list(TREATMENTS),
                        "features": list(FEATURES)}}
    WIDE_DIR.mkdir(parents=True, exist_ok=True)
    (WIDE_DIR / "field_stats.json").write_text(json.dumps(report, indent=1) + "\n")
    text = markdown(before, after)
    (WIDE_DIR / "field_stats.md").write_text(text)
    on_grid = [field for field, row in before.items()
               if row["grid_step"] is not None and row["grid_step"] >= 0.5]
    still = [field for field, row in after.items()
             if row["grid_step"] is not None and row["grid_step"] >= 0.5
             and not field.endswith(".count")]
    print(f"before: {len(old)} plans, {len(before)} fields, {len(on_grid)} on a grid of 0.5 mm "
          f"or coarser\nafter:  {len(new)} plans, {len(after)} fields, on such a grid "
          f"(counts aside): {still or 'none'}\n")
    print(text)
    for name, found in (("before", report["before"]["habits"]), ("after", report["after"]["habits"])):
        print(name, json.dumps({key: found[key] for key in
                                ("plans", "counts", "where_the_edge_treatment_is")}))
    log_metrics(run_dir, final=True, before_plans=len(old), after_plans=len(new),
                before_fields_on_a_grid=len(on_grid), after_fields_on_a_grid=len(still))


if __name__ == "__main__":
    main()
