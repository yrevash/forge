"""Build a resolved structure in FreeCAD, command by command, and compare it with the reference.

    result = build_structure(client, resolution.bodies)
    result.problems         # empty when FreeCAD built what the resolver says
    result.snapshot         # the last snapshot

Two references, both from forge.resolve, neither sharing code with the FreeCAD build:

  the resolver's arithmetic   every part's frame (bounding box in the structure's
      (compare_with_resolver)  coordinates) and, for a part without features, its volume
                               by formula; the overall frame; which pairs may overlap
  a structure row's stored     data/structures rows carry what the CadQuery kernel measured
      measurement              when they were generated: total volume, overall size, number
      (compare_with_row)       of touching pairs, no overlap, one group
  the reference kernel build   forge.resolve.verify runs the reference CadQuery program in
      (compare_with_kernel)    the sandbox; its per-part volumes also cover parts WITH
                               features, and its solids are compared with FreeCAD's solid by
                               solid (the volume the two share)

Tolerances are the project's usual ones: 0.001 mm on every bounding-box face, 1e-6
relative on volumes.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from forge.freecad.client import FreeCADClient
from forge.freecad_multi.recipes import Cmd, Item, flatten, script_of, structure_items
from forge.resolve import contact
from forge.resolve import space as sp
from forge.resolve.bodies import Body

BBOX_TOLERANCE_MM = 1e-3
VOLUME_RELATIVE_TOLERANCE = 1e-6
MAX_LISTED = 8

OnCommand = Callable[[int | None, Cmd, dict], None]


@dataclass
class BuildResult:
    items: list[Item] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    snapshot: dict | None = None
    commands: int = 0
    seconds_in_freecad: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.problems


def body_items(snapshot: dict) -> list[dict]:
    return [item for item in snapshot["items"] if item["type"] == "body"]


def run_script(client: FreeCADClient, items: list[Item], rng: random.Random | None = None,
               on_command: OnCommand | None = None) -> BuildResult:
    """Issue the whole script. With `rng`, every any-order group is shuffled."""
    result = BuildResult(items=items)
    for index, entry in script_of(items):
        for command in flatten([entry], rng):
            reply = client.command(command.name, **command.args)
            result.commands += 1
            result.seconds_in_freecad += reply.get("ms", 0.0) / 1000
            if on_command is not None:
                on_command(index, command, reply)
            where = "the structure" if index is None else items[index].name
            if reply["status"] != "ok":
                result.problems.append(f"{where}: {command.name} {command.args} -> "
                                       f"{reply['status']}: {reply.get('reason')}")
                return result
            result.snapshot = reply["snapshot"]
            invalid = [item["name"] for item in result.snapshot["items"] if not item["valid"]]
            if invalid:
                result.problems.append(f"{where}: {command.name} left {invalid} invalid")
                return result
    return result


def compare_with_resolver(bodies: list[Body], items: list[Item], snapshot: dict,
                          touching: set[frozenset[str]] | None = None) -> list[str]:
    """Every difference between FreeCAD's structure and the resolver's arithmetic."""
    problems: list[str] = []
    built = body_items(snapshot)
    parts = [item for item in items if item.kind == "part"]
    if len(built) != len(bodies):
        return [f"part count: FreeCAD {len(built)}, resolver {len(bodies)}"]
    name_of = {}
    for item in parts:
        body, got = bodies[item.source], built[item.body - 1]["solid"]
        name_of[item.body] = body.name
        label = f"{body.name} ({item.shape}, {item.placement})"
        if got is None:
            problems.append(f"{label}: no solid")
            continue
        if got["solids"] != 1 or not got["valid"]:
            problems.append(f"{label}: not one valid solid (solids={got['solids']}, "
                            f"valid={got['valid']})")
        frame = body.frame
        for axis in range(3):
            low, high = got["bbox"][axis], got["bbox"][axis + 3]
            if (abs(low - frame.low[axis]) > BBOX_TOLERANCE_MM
                    or abs(high - frame.high[axis]) > BBOX_TOLERANCE_MM):
                problems.append(f"{label} {'XYZ'[axis]}: FreeCAD {low:.6f} to {high:.6f}, "
                                f"resolver {frame.low[axis]:.6f} to {frame.high[axis]:.6f}")
        want = body.volume
        if want is not None and abs(got["volume"] - want) > VOLUME_RELATIVE_TOLERANCE * want:
            problems.append(f"{label} volume: FreeCAD {got['volume']:.6f}, resolver {want:.6f} "
                            f"(relative {(got['volume'] - want) / want:.2e})")

    whole, measured = sp.around([body.frame for body in bodies]), snapshot["structure"]
    if measured["bodies"] != len(bodies) or measured["solids"] != len(bodies):
        problems.append(f"the structure has {measured['solids']} solids in {measured['bodies']} "
                        f"bodies, expected {len(bodies)}")
    for axis in range(3):
        low, high = measured["bbox"][axis], measured["bbox"][axis + 3]
        if (abs(low - whole.low[axis]) > BBOX_TOLERANCE_MM
                or abs(high - whole.high[axis]) > BBOX_TOLERANCE_MM):
            problems.append(f"overall {'XYZ'[axis]}: FreeCAD {low:.6f} to {high:.6f}, resolver "
                            f"{whole.low[axis]:.6f} to {whole.high[axis]:.6f}")

    declared = {frozenset((body.name, other)) for body in bodies for other in body.may_overlap}
    for a, b, volume in measured["overlapping"]:
        if frozenset((name_of[a], name_of[b])) not in declared:
            problems.append(f"{name_of[a]} and {name_of[b]} overlap by {volume:.6f} mm^3 "
                            "(not declared `sunk`)")
    if touching is not None:
        # As forge/resolve/verify.py: two parts that share volume count as joined.
        found = {frozenset((name_of[a], name_of[b])) for a, b in measured["touching"]}
        found |= {frozenset((name_of[a], name_of[b])) for a, b, _ in measured["overlapping"]}
        missing = sorted(sorted(pair) for pair in touching - found)
        extra = sorted(sorted(pair) for pair in found - touching)
        if missing:
            problems.append(f"joints the resolver recorded and FreeCAD did not find: "
                            f"{missing[:MAX_LISTED]}")
        if extra:
            problems.append(f"touching pairs FreeCAD found and the resolver did not record: "
                            f"{extra[:MAX_LISTED]}")
    return problems


def compare_with_row(row: dict, snapshot: dict) -> list[str]:
    """A structure row's stored kernel measurement against FreeCAD's structure."""
    stored, got = row["measured"], snapshot["structure"]
    problems = []
    if got["bodies"] != stored["n_parts"]:
        problems.append(f"stored: {stored['n_parts']} parts, FreeCAD {got['bodies']}")
    if abs(got["volume"] - stored["volume"]) > VOLUME_RELATIVE_TOLERANCE * stored["volume"]:
        problems.append(f"total volume: FreeCAD {got['volume']:.6f}, stored kernel "
                        f"{stored['volume']:.6f}")
    for axis, got_size, want in zip("XYZ", got["size"], stored["bbox"], strict=True):
        if abs(got_size - want) > BBOX_TOLERANCE_MM:
            problems.append(f"overall size {axis}: FreeCAD {got_size:.6f}, stored {want:.6f}")
    if got["overlapping"]:
        problems.append(f"{len(got['overlapping'])} pairs overlap; the stored structure has none")
    if len(got["touching"]) != stored["n_touching_pairs"]:
        problems.append(f"touching pairs: FreeCAD {len(got['touching'])}, stored kernel "
                        f"{stored['n_touching_pairs']}")
    names = [str(k) for k in range(1, got["bodies"] + 1)]
    groups = contact.joined_groups(names, {frozenset((str(a), str(b)))
                                           for a, b in got["touching"]})
    if len(groups) != stored["n_groups"]:
        problems.append(f"the parts form {len(groups)} groups, stored {stored['n_groups']}")
    return problems


def compare_with_kernel(client: FreeCADClient, bodies: list[Body], items: list[Item],
                        snapshot: dict, kernel_reply: dict, work_dir: Path) -> list[str]:
    """Every difference between FreeCAD's parts and the reference kernel build.

    `kernel_reply` is the sandbox's reply to the reference program run with `export=True`,
    and `work_dir` the folder it wrote each body's solid into (body_<n>_<stage>.brep).
    """
    if kernel_reply.get("status") != "ok":
        return [f"the reference build failed: {kernel_reply.get('error')}"]
    measured = {part["name"]: part for part in kernel_reply["measure"]["structure"]["parts"]}
    built = body_items(snapshot)
    parts = [item for item in items if item.kind == "part"]
    problems, paths = [], []
    for item in parts:
        body, got = bodies[item.source], built[item.body - 1]["solid"]
        want = measured[body.name]
        label = f"{body.name} ({item.shape}, {item.placement})"
        if abs(got["volume"] - want["volume"]) > VOLUME_RELATIVE_TOLERANCE * want["volume"]:
            problems.append(f"{label} volume: FreeCAD {got['volume']:.6f}, reference kernel "
                            f"{want['volume']:.6f}")
        for axis in range(3):
            if (abs(got["bbox"][axis] - want["bbox_min"][axis]) > BBOX_TOLERANCE_MM
                    or abs(got["bbox"][axis + 3] - want["bbox_max"][axis]) > BBOX_TOLERANCE_MM):
                problems.append(f"{label} {'XYZ'[axis]}: FreeCAD {got['bbox'][axis]:.6f} to "
                                f"{got['bbox'][axis + 3]:.6f}, reference kernel "
                                f"{want['bbox_min'][axis]:.6f} to {want['bbox_max'][axis]:.6f}")
        path = work_dir / f"body_{item.source}_{len(body.cuts)}.brep"
        paths.append(path if path.exists() else None)
    # In the order the bodies were made, which is the order of `parts`.
    for item, answer in zip(parts, client.against_files(paths), strict=True):
        body = bodies[item.source]
        label = f"{body.name} ({item.shape}, {item.placement})"
        if answer is None:
            problems.append(f"{label}: the reference build wrote no solid to compare with")
            continue
        union = answer["volume"] + answer["reference"] - answer["shared"]
        if union - answer["shared"] > VOLUME_RELATIVE_TOLERANCE * union:
            problems.append(f"{label}: FreeCAD's solid and the reference solid share "
                            f"{answer['shared']:.6f} of {union:.6f} mm^3 "
                            f"(IoU {answer['shared'] / union:.8f})")
    return problems


def build_structure(client: FreeCADClient, bodies: list[Body],
                    touching: set[frozenset[str]] | None = None,
                    rng: random.Random | None = None,
                    on_command: OnCommand | None = None) -> BuildResult:
    """Build a resolved structure and compare it with the resolver's arithmetic."""
    result = run_script(client, structure_items(bodies), rng, on_command)
    if result.ok:
        result.problems = compare_with_resolver(bodies, result.items, result.snapshot, touching)
    return result
