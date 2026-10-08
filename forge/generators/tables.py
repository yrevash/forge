"""Standard-part dimension tables.

The numbers come from files copied unchanged from the bd_warehouse project
(Apache-2.0) at the commit recorded in tables/bd_warehouse/COMMIT. Nothing in
this repo types a standard dimension by hand: generators read them from here.

A table's columns look like `iso4032:m` (standard, then field). A blank cell
means that standard does not define that size.
"""

from __future__ import annotations

import csv
import re
from functools import cache
from pathlib import Path

TABLE_DIR = Path(__file__).parent / "tables" / "bd_warehouse"
TABLE_SOURCE = {
    "name": "bd_warehouse",
    "url": "https://github.com/gumyr/bd_warehouse",
    "license": "Apache-2.0",
    "commit": (TABLE_DIR / "COMMIT").read_text().strip(),
}


@cache
def load(table: str) -> dict[str, list[dict]]:
    """Return {standard: [{"size": "M8-1.25", field: value, ...}, ...]} for one table."""
    by_standard: dict[str, list[dict]] = {}
    with (TABLE_DIR / f"{table}.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            per_standard: dict[str, dict] = {}
            for column, cell in row.items():
                if ":" not in column or not cell.strip():
                    continue
                standard, name = (part.strip() for part in column.split(":", 1))
                try:
                    per_standard.setdefault(standard, {})[name] = float(cell)
                except ValueError:
                    # Inch sizes written as fractions ("1/16"). We only build
                    # metric parts, so such a field is left out and the family's
                    # own filter drops the row.
                    continue
            # One upstream table (cheese_head_parameters) leaves the size column's
            # heading blank. The files are kept unchanged, so that is handled here.
            size = (row["Size"] if "Size" in row else row[""]).strip()
            for standard, fields in per_standard.items():
                by_standard.setdefault(standard, []).append({"size": size, **fields})
    return by_standard


@cache
def clearance_holes(fit: str = "Normal") -> dict[str, float]:
    """Clearance hole diameter for each metric thread size: {"M8": 9.0, ...}.

    `fit` is one of the table's columns: "Close", "Normal" or "Loose". The table
    also lists inch sizes; those rows are skipped.
    """
    with (TABLE_DIR / "clearance_hole_sizes.csv").open(newline="") as f:
        return {row["Size"].strip(): float(row[fit]) for row in csv.DictReader(f)
                if row["Size"].strip().startswith("M")}


@cache
def nominal_lengths(standard: str) -> list[float]:
    with (TABLE_DIR / "nominal_screw_lengths.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if row["Screw_Type"] == standard:
                return [float(x) for x in row["Nominal_Sizes"].split(",")]
    raise KeyError(f"no nominal lengths for {standard}")


def thread_diameter(size: str) -> float:
    """'M8-1.25' -> 8.0. The number after M is the nominal thread diameter in mm."""
    match = re.fullmatch(r"M(\d+(?:\.\d+)?)(?:-[\d.]+)?", size)
    if not match:
        raise ValueError(f"not a metric thread size: {size!r}")
    return float(match.group(1))


def short_size(size: str) -> str:
    """'M8-1.25' -> 'M8' (drop the thread pitch)."""
    return size.split("-")[0]
