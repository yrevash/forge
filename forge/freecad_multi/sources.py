"""Where structures come from, each turned into a list of resolver Bodies.

Five sources:

    examples    the plans in forge/resolve/examples and forge/plan/examples
    tuning      the tuning plans listed as built in data/coverage/built/summary.json
    structures  rows of data/structures/<kind>.jsonl (furniture and assemblies), read
                through the generator's own part list
    random      forge.resolve.random_plans.random_plan(seed)                (proof only)
    plans       records of data/plans/<source>/<split>/ (forge/plans_data: the plans
                Forge-S1 trains on, with splits and provenance). The record's plan text is
                resolved again here and must give the parts the record stores.

A `Unit` is one structure ready to be built, with what a session file must say about
where it came from. Sessions are made from `plans` units only.

A plan goes through `resolve_text`, so its bodies are the resolver's. A structure row is
not a plan: its part list gives each part as a box or an upright cylinder with the
centre of its BOTTOM face (forge/generators/structures/solids.py). `row_bodies` rewrites
that as a Body, whose centre is the middle of the frame: the same point, half the height
up. Nothing else is changed, and the row's stored kernel measurement is the reference.

This file writes nothing. It reads data other code generated.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from forge.resolve import resolve_text
from forge.resolve import space as sp
from forge.resolve.bodies import Body
from forge.resolve.judge import KernelJudge
from forge.runs import PROJECT_ROOT

EXAMPLE_FOLDERS = ("forge/resolve/examples", "forge/plan/examples")
TUNING_PLANS = "data/coverage/tuning_plans_draft2"
TUNING_SUMMARY = "data/coverage/built/summary.json"
STRUCTURES = "data/structures"
PLANS = "data/plans"
STORED_TOLERANCE = 2e-6     # mm; data/plans stores numbers rounded to a millionth


def example_plans() -> list[Path]:
    return [path for folder in EXAMPLE_FOLDERS
            for path in sorted((PROJECT_ROOT / folder).glob("*.txt"))]


def tuning_plans() -> list[Path]:
    """The tuning plans the reference builder built and passed."""
    summary = json.loads((PROJECT_ROOT / TUNING_SUMMARY).read_text())
    return [PROJECT_ROOT / TUNING_PLANS / f"{plan['plan']}.plan" for plan in summary["plans"]
            if plan["builds_and_passes"]]


def structure_kinds() -> list[str]:
    return sorted(path.stem for path in (PROJECT_ROOT / STRUCTURES).glob("*.jsonl"))


def structure_rows(kind: str, count: int) -> Iterator[dict]:
    """The first `count` rows of one kind."""
    with (PROJECT_ROOT / STRUCTURES / f"{kind}.jsonl").open() as lines:
        for number, line in enumerate(lines):
            if number >= count:
                return
            yield json.loads(line)


def row_bodies(row: dict) -> list[Body]:
    """A structure row's parts as Bodies, in the order the generator lists them."""
    bodies = []
    for line, step in enumerate(row["steps"], start=1):
        for part in step["parts"]:
            x, y, z = part["size"]
            sizes = ({"length": x, "depth": y, "height": z} if part["shape"] == "box"
                     else {"diameter": x, "height": z})
            at = part["at"]         # the centre of the bottom face
            bodies.append(Body(part["name"], step["name"], line, part["shape"], None, sizes,
                               sp.IDENTITY, (at[0], at[1], at[2] + z / 2)))
    return bodies


# --- units: a structure with its provenance ------------------------------------------------------

@dataclass
class Unit:
    """One structure to build, and what a session must record about its origin."""

    id: str
    origin: str                         # "plans" or "rows"
    source: str                         # provenance: "gen:plans:structures", "gen:structure:chair"
    split: str | None
    kind: str | None                    # the structure kind, where there is one
    license: str
    generator_version: str
    where: str                          # the file it was read from, relative to the repo
    bodies: list[Body]
    touching: set[frozenset[str]] | None
    row: dict | None = None             # a structure row: its stored kernel measurement


def unit_from_row(row: dict) -> Unit:
    return Unit(row["id"], "rows", row["source"], row.get("split"), row["kind"], row["license"],
                row["generator_version"], f"{STRUCTURES}/{row['kind']}.jsonl", row_bodies(row),
                None, row)


def plan_files(source: str, split: str) -> list[Path]:
    return sorted((PROJECT_ROOT / PLANS / source / split).glob("shard*.jsonl.gz"))


def plan_records(path: Path) -> Iterator[dict]:
    """Every record of one shard of data/plans."""
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            yield json.loads(line)


def unit_from_record(record: dict, where: Path, judge: KernelJudge | None = None) -> Unit:
    """Resolve a stored plan again and check it gives the parts the record stores.

    The record's `parts` are rounded for storage and leave out two numbers of a feature,
    so the exact Bodies come from the plan's own lines; the stored parts are the check.
    """
    resolution = resolve_text("\n".join(record["lines"]) + "\n", judge)
    stored = record["parts"]
    if not resolution.complete or [body.name for body in resolution.bodies] \
            != [part["name"] for part in stored]:
        raise ValueError(f"plan {record['id']} does not resolve to its stored parts")
    for body, part in zip(resolution.bodies, stored, strict=True):
        frame = body.frame
        if any(abs(a - b) > STORED_TOLERANCE
               for a, b in zip((*frame.low, *frame.high), (*part["low"], *part["high"]))):
            raise ValueError(f"plan {record['id']}: {body.name} is not where the record says")
    return Unit(record["id"], "plans", f"gen:plans:{record['source']}", record["split"],
                record.get("kind"), record["license"], record["generator_version"],
                str(where.relative_to(PROJECT_ROOT)), resolution.bodies, resolution.touching)
