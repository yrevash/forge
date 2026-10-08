"""Are shards recorded on another machine the shards the Mac records? No FreeCAD needed.

A session is decided by (part or plan, seed, code). When the Mac has already recorded a
shard, the same shard from a far machine must hold the same sessions, record for record:
every snapshot, every set of valid commands, every reply. This file reads both and says
exactly what differs. It is a stronger and cheaper check than a replay sample: it compares
every step of every session, and it only reads files.

    uv run python -m forge.remote.compare --recorder wide \
        --far <downloaded>/wide --mac data/freecad/sessions_wide

When the Mac has NOT recorded the shard (bulk recording far away), this cannot be used;
the recorders' own audits replay a sample in the Mac's FreeCAD instead (README.md).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

NOISE = 1e-6    # numbers closer than this (absolute, or relative for big ones) are "noise"


def differences(far: object, mac: object, path: str = "", limit: int = 20) -> list[tuple]:
    """Where two JSON values differ: [(path, far value, mac value)], at most `limit`."""
    if type(far) is not type(mac) and not (isinstance(far, int | float)
                                           and isinstance(mac, int | float)):
        return [(path, far, mac)]
    if isinstance(far, dict):
        found = []
        for key in sorted(set(far) | set(mac)):
            if key not in far or key not in mac:
                found.append((f"{path}.{key}", far.get(key, "<absent>"),
                              mac.get(key, "<absent>")))
            else:
                found += differences(far[key], mac[key], f"{path}.{key}", limit)
            if len(found) >= limit:
                break
        return found[:limit]
    if isinstance(far, list):
        if len(far) != len(mac):
            return [(f"{path}[len]", len(far), len(mac))]
        found = []
        for at, (one, other) in enumerate(zip(far, mac, strict=True)):
            found += differences(one, other, f"{path}[{at}]", limit)
            if len(found) >= limit:
                break
        return found[:limit]
    return [] if far == mac else [(path, far, mac)]


def is_noise(far: object, mac: object) -> bool:
    """Two numbers that differ only in the last digits (a different build of the kernel)."""
    if isinstance(far, bool) or isinstance(mac, bool):
        return False
    if not isinstance(far, int | float) or not isinstance(mac, int | float):
        return False
    return abs(far - mac) <= NOISE * max(1.0, abs(far), abs(mac))


def _snapshots_pass(far: tuple, mac: tuple, same_lean) -> bool:
    """Would the audit's replay accept one for the other? Everything but the snapshots must
    be equal, and every snapshot must pass the audit's own `same_lean`."""
    (_, far_records, far_end), (_, mac_records, mac_end) = far, mac

    def rest(record: dict) -> dict:
        return {key: value for key, value in record.items() if key != "snapshot"}
    if len(far_records) != len(mac_records) or rest(far_end) != rest(mac_end):
        return False
    pairs = [*zip(far_records, mac_records, strict=True), (far_end, mac_end)]
    return all(rest(one) == rest(other) and (one["snapshot"] is other["snapshot"] is None or (
        one["snapshot"] is not None and other["snapshot"] is not None
        and same_lean(one["snapshot"], other["snapshot"]))) for one, other in pairs)


def compare_session(far: tuple, mac: tuple, ignore: tuple[str, ...] = (),
                    same_lean=None) -> dict:
    """One session against its twin, over the WHOLE session. `kind` is the worst thing found:

    same      every record equal
    audit     differs, but only where the audit's snapshot comparison (`same_lean`) allows:
              the last digits of a volume. A replay on the Mac accepts it.
    noise     only numbers differ, each by less than NOISE, but somewhere `same_lean` is
              strict (a bounding box that is 57.5 here and 57.5000001 there): the session
              means the same and a replay on the Mac REJECTS it.
    content   something else differs: a name, a count, a target, a reply.
    """
    (far_header, far_records, far_end), (mac_header, mac_records, mac_end) = far, mac
    far_header = {key: value for key, value in far_header.items() if key not in ignore}
    mac_header = {key: value for key, value in mac_header.items() if key not in ignore}
    found = differences(far_header, mac_header, "header", limit=50)
    for at, (one, other) in enumerate(zip(far_records, mac_records, strict=False)):
        found += differences(one, other, f"t={at}", limit=50)
    if len(far_records) != len(mac_records):
        found.append(("steps", len(far_records), len(mac_records)))
    found += differences(far_end, mac_end, "end", limit=50)
    if not found:
        return {"same": True, "kind": "same"}
    serious = [item for item in found if not is_noise(item[1], item[2])]
    if serious:
        kind = "content"
    elif same_lean is not None and far_header == mac_header \
            and _snapshots_pass(far, mac, same_lean):
        kind = "audit"
    else:
        kind = "noise"
    numbers = [abs(a - b) for _, a, b in found if is_noise(a, b)]
    return {"same": False, "kind": kind, "first": found[0][0], "differences": len(found),
            "largest_number_gap": max(numbers, default=None),
            "fields": sorted({where.split(".", 1)[-1].split("[")[0] if kind != "content"
                              else where for where, _, _ in (serious or found)})[:6],
            "examples": [[where, a, b] for where, a, b in (serious or found)[:4]]}


def _reader(recorder: str):
    """The recorder's own reader of a shard, and its audit's snapshot comparison."""
    if recorder == "multi":
        from forge.freecad_multi.lean import same_lean
        from forge.freecad_multi.shards import read_sessions
    else:
        from forge.freecad.lean import same_lean
        from forge.freecad.shards import read_sessions
    return read_sessions, same_lean


def compare_dirs(recorder: str, far_dir: Path, mac_dir: Path,
                 ignore: tuple[str, ...] = ()) -> dict:
    """Every complete shard under `far_dir` against the shard of the same name under
    `mac_dir`. A far shard may hold only the first parts of the Mac's (a probe)."""
    read, same_lean = _reader(recorder)
    counts: Counter = Counter()
    examples, shards = [], []
    fields: Counter = Counter()     # where the numbers that differ sit
    gap = 0.0
    for stats_path in sorted(far_dir.glob("*/shard*.stats.json")):
        far_stats = json.loads(stats_path.read_text())
        mac_shard = mac_dir / far_stats["file"]
        mac_stats_path = mac_shard.with_name(stats_path.name)
        if not mac_stats_path.exists():
            counts["shards_the_mac_has_not_recorded"] += 1
            continue
        mac_stats = json.loads(mac_stats_path.read_text())
        counts["shards"] += 1
        counts["shards_same_bytes"] += far_stats["sha256"] == mac_stats["sha256"]
        counts["shards_same_code_hash"] += far_stats["code_hash"] == mac_stats.get("code_hash")
        mac_sessions = {header["session"]: (header, records, end)
                        for header, records, end in read(mac_shard)}
        same = 0
        for far in read(far_dir / far_stats["file"]):
            counts["sessions"] += 1
            counts["steps"] += len(far[1])
            twin = mac_sessions.get(far[0]["session"])
            if twin is None:
                counts["sessions_the_mac_shard_lacks"] += 1
                examples.append([far_stats["file"], far[0]["session"], "not in the Mac's shard"])
                continue
            result = compare_session(far, twin, ignore, same_lean)
            if result["same"]:
                same += 1
                counts["sessions_same"] += 1
            else:
                counts[f"sessions_differ_{result['kind']}"] += 1
                for field in result["fields"] if result["kind"] != "content" else ():
                    fields[field] += 1
                gap = max(gap, result["largest_number_gap"] or 0.0)
                if result["kind"] == "content" or len(examples) < 6:
                    examples.append([far_stats["file"], far[0]["session"], result["kind"],
                                     result["first"], result["differences"],
                                     result["examples"]])
        shards.append([far_stats["file"], same, far_stats["counts"].get("sessions", 0)])
    return {"recorder": recorder, "ignored_header_fields": list(ignore), **dict(counts),
            "largest_number_gap": gap, "number_fields": dict(fields),
            "per_shard_same_of_sessions": shards, "examples": examples}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--recorder", required=True, choices=("single", "wide", "multi"))
    parser.add_argument("--far", required=True, help="the folder the far shards were written to")
    parser.add_argument("--mac", required=True, help="the Mac's folder of the same slices")
    parser.add_argument("--ignore", nargs="*", default=[],
                        help="header fields to leave out (code_hash, when the Mac's shards "
                             "were recorded with an older hash of the same behaviour)")
    args = parser.parse_args()
    report = compare_dirs(args.recorder, Path(args.far), Path(args.mac), tuple(args.ignore))
    print(json.dumps(report, indent=1))
    raise SystemExit(0 if report.get("sessions") and report.get("sessions") ==
                     report.get("sessions_same") else 1)


if __name__ == "__main__":
    main()
