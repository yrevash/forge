"""The coverage table with two numbers per cell: the TARGET and the FLOOR.

    target   what was proposed before anything was generated (config.TARGET_*):
             3,000 for a single cell, 200 for a pairing; a tenth of that for cells whose
             plans need the CAD kernel on every line.
    floor    what the audit enforces (config.MIN_*). It is set after measuring how
             fast this machine could generate on the night of the build. A floor below
             the target is a shortfall, and the manifest lists every such cell.

`rows(train, everything)` joins the table with the counts.
"""

from __future__ import annotations

from forge.plans_data import config, coverage


def target_of(cell: str, kernel: bool) -> int:
    if cell.startswith("reply:"):
        return config.TARGET_REPLY
    if cell.startswith("pair:"):
        return config.TARGET_PAIR_KERNEL if kernel else config.TARGET_PAIR_ARITHMETIC
    return config.TARGET_KERNEL if kernel else config.TARGET_ARITHMETIC


def held_out_cells() -> dict[str, str]:
    """Pair cells that are held out of training ON PURPOSE (config.HELD_OUT_LINE_PAIRS).
    Their train count must be zero, so they are not required cells; the manifest lists
    them with how many lines hold them in the `combo` split."""
    found = {}
    for a, b in config.HELD_OUT_LINE_PAIRS:
        left, right = a.split(":")[1], b.split(":")[1]
        if a.startswith("place") and b.startswith("target"):
            found[f"pair:place×target|{left}|{right}"] = f"{a} + {b}"
        elif a.startswith("rep"):
            found[f"pair:place×rep|{right}|{left}"] = f"{a} + {b}"
        else:
            found[f"pair:place×align|{right}|{left}"] = f"{a} + {b}"
    return found


def rows(train: dict[str, int], everything: dict[str, int]) -> dict[str, dict]:
    held = held_out_cells()
    table = {cell: spec for cell, spec in coverage.universe().items() if cell not in held}
    return {cell: {"kernel": spec["kernel"], "floor": spec["minimum"],
                   "target": target_of(cell, spec["kernel"]),
                   "train": train.get(cell, 0), "all": everything.get(cell, 0)}
            for cell, spec in table.items()}


def summary(table: dict[str, dict]) -> dict:
    """How many cells reach the floor and the target, by family (the word before the colon)."""
    families: dict[str, dict] = {}
    for cell, row in table.items():
        family = cell.split(":")[0] + (" (kernel)" if row["kernel"] else "")
        found = families.setdefault(family, {"cells": 0, "at_floor": 0, "at_target": 0,
                                             "lowest_train": None, "median_train": []})
        found["cells"] += 1
        found["at_floor"] += row["train"] >= row["floor"]
        found["at_target"] += row["train"] >= row["target"]
        found["median_train"].append(row["train"])
    for found in families.values():
        counts = sorted(found["median_train"])
        found["lowest_train"], found["median_train"] = counts[0], counts[len(counts) // 2]
        found["highest_train"] = counts[-1]
    return families
