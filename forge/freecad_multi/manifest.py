"""The manifest of the structure session files: what is on disk, counted, and what code made it.

    data/freecad_multi/sessions/manifest.json    written by the recorder after every shard,
                                                 and by this command for everything at once

Run:    uv run python -m forge.freecad_multi.manifest            rewrite the manifest
        uv run python -m forge.freecad_multi.manifest --freeze   and make the shard files read-only

Everything is added up from the shards' stats files (`shardNNNN.stats.json`), so the
manifest can be rebuilt at any time without reading a session. Each stats file carries
the hash of the code that made its shard and the hash of the selection it was made from;
`code_versions` lists which shards each hash made.

`--freeze` marks the manifest frozen and takes the write permission off every shard and
stats file. The recorder refuses to add to a frozen folder's existing shards (the file
system does); recording more means new shard numbers, which stay writable until the next
freeze.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from forge.freecad.manifest import _ranges, grouped, shares
from forge.freecad_multi.noise import WRONG_NUMBERS
from forge.freecad_multi.shards import OUT_DIR, SLICES, TEST_SLICES, complete_shards

__all__ = ["summary", "write_manifest"]


def summary(counts: Counter) -> dict:
    """The counts of one slice (or of all of them) in the manifest's layout."""
    steps, sessions = max(counts["steps"], 1), max(counts["end:done"], 1)
    by_kind: Counter = Counter()
    for key, value in grouped(counts, "noise:").items():
        by_kind[key.split(":")[0]] += value
    noisy = max(counts["noisy_steps"], 1)
    wrong_numbers = sum(by_kind[kind] for kind in WRONG_NUMBERS)

    def share_of_sessions(prefix: str) -> dict:
        return {key: round(value / sessions, 4) for key, value in grouped(counts, prefix).items()}

    return {
        "plans": counts["plans"], "plans_not_as_stored": counts["plans_not_as_stored"],
        # Plans whose clean trial build in FreeCAD did not end at the resolver's structure
        # (sessions.proof): they get no session.
        "plans_freecad_cannot_build": counts["plans_freecad_cannot_build"],
        "plans_freecad_cannot_build_by_cause": grouped(counts, "cannot_build:"),
        "sessions": counts["sessions"],
        "sessions_ended": grouped(counts, "end:"),
        "sessions_with_problems": counts["sessions_with_problems"],
        "steps": counts["steps"], "parts_built": counts["parts_built"],
        "sessions_by_kind": grouped(counts, "kind:"),
        "sessions_by_bodies": grouped(counts, "bodies:"),
        "sessions_with_shape": grouped(counts, "shape:"),
        "share_of_sessions_with_shape": share_of_sessions("shape:"),
        "sessions_with_another_shape_than_box_or_cylinder": counts["sessions_with_another_shape"],
        "sessions_with_a_feature": counts["sessions_with_a_feature"],
        "sessions_with_feature": grouped(counts, "feature:"),
        "sessions_with_feature_on_side": grouped(counts, "feature_side:"),
        "sessions_with_activate_body_as_target": counts["sessions_with_activate_body"],
        "sessions_with_a_turned_part": counts["sessions_with_a_turned_part"],
        "share_with_another_shape": round(counts["sessions_with_another_shape"] / sessions, 4),
        "share_with_a_feature": round(counts["sessions_with_a_feature"] / sessions, 4),
        "sessions_by_noise_level": grouped(counts, "noise_level:"),
        "steps_by_noise_level": grouped(counts, "steps_at_noise:"),
        "sessions_by_start": grouped(counts, "start:"),
        "steps_by_target_command": grouped(counts, "target:"),
        "steps_by_number_of_targets": grouped(counts, "choices:"),
        "undo_share": round(counts["target:undo"] / steps, 4),
        "off_plan_steps": counts["off_plan_steps"],
        "noisy_steps": counts["noisy_steps"],
        "noisy_steps_by_kind": dict(sorted(by_kind.items())),
        "noisy_steps_share_by_kind": shares(dict(sorted(by_kind.items()))),
        "wrong_number_share_of_noisy_steps": round(wrong_numbers / noisy, 4),
        "noisy_steps_by_kind_reply_effect": grouped(counts, "noise:"),
        "wrong_numbers_by_flavour": grouped(counts, "wrong_number:"),
        "steps_with_a_failed_feature": counts["steps_with_a_failed_feature"],
        "steps_finished_too_early": counts["steps_finished_too_early"],
        "repairs_by_new_document": counts["repairs_by_new_document"],
        "steps_right_after_a_wrong_number": counts["steps_right_after_a_wrong_number"],
        "undo_history_lost": counts["undo_history_lost"],
        "sessions_where_noise_was_cut_off": counts["noise_cut_off"],
        "teacher_commands_refused": counts["teacher_commands_refused"],
        "worker_restarts": counts["worker_restarts"],
        "sessions_played_again": counts["sessions_played_again"],
        "sessions_with_another_draw": counts["sessions_with_another_draw"],
        "raw_bytes": counts["raw_bytes"], "gz_bytes": counts["gz_bytes"],
        "raw_bytes_per_step": round(counts["raw_bytes"] / steps, 1),
        "gz_bytes_per_step": round(counts["gz_bytes"] / steps, 1),
        "whole_bytes_per_step": round(counts["whole_bytes"] / steps, 1),
    }


def code_versions(out_dir: Path) -> dict:
    """code hash -> the shards it made (from each shard's own stats file)."""
    versions: dict[str, dict] = {}
    for stats, path in complete_shards(out_dir):
        entry = versions.setdefault(stats["code_hash"], {"shards": {}, "sessions": 0,
                                                         "selections": set()})
        entry["shards"].setdefault(stats["slice"], []).append(int(path.name[5:9]))
        entry["sessions"] += stats["counts"]["sessions"]
        entry["selections"].add(stats["selection"])
    for entry in versions.values():
        entry["shards"] = {name: _ranges(numbers) for name, numbers in entry["shards"].items()}
        entry["selections"] = sorted(entry["selections"])
    return versions


def write_manifest(out_dir: Path, config: dict | None = None,
                   planned: dict[str, int] | None = None, frozen: bool | None = None) -> dict:
    """Add up the stats of every complete shard. Safe to call at any time.

    `config` and `planned` describe the run that calls this; what an earlier manifest said
    (the planned shards of other slices, the configs of earlier runs) is kept.
    """
    path = Path(out_dir) / "manifest.json"
    earlier = json.loads(path.read_text()) if path.exists() else {}
    per_slice: dict[str, Counter] = {name: Counter() for name in SLICES}
    files, seconds = {}, 0.0
    for shard, _ in complete_shards(out_dir):
        per_slice[shard["slice"]].update(shard["counts"])
        per_slice[shard["slice"]]["shards"] += 1
        files[shard["file"]] = shard["sha256"]
        seconds += shard["seconds"]
    total: Counter = Counter()
    for counts in per_slice.values():
        total.update(counts)
    plans = {name: info.get("shards_planned", 0) for name, info in earlier.get("slices", {}).items()}
    plans.update(planned or {})
    configs = dict(earlier.get("configs", {}))
    if config:
        configs[f"{config['mix']} ({config['code_hash']}, selection {config['selection']})"] = config
    manifest = {
        "contract": "forge/freecad_multi/sessions.py (file format), shards.py (packing), "
                    "teacher.py (rules), load.py (what a model may read)",
        "frozen": earlier.get("frozen", False) if frozen is None else frozen,
        "configs": configs,
        "code_versions": code_versions(Path(out_dir)),
        "totals": summary(total),
        "slices": {name: {"plans_from": SLICES[name][0], "split": SLICES[name][1],
                          "seed": SLICES[name][2], "noise_levels": list(SLICES[name][3]),
                          "test": name in TEST_SLICES, **summary(per_slice[name]),
                          "shards": per_slice[name]["shards"],
                          "shards_planned": plans.get(name, 0)} for name in SLICES},
        "busy_seconds_all_workers": round(seconds, 1),
        "files": files,
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", default=str(OUT_DIR))
    parser.add_argument("--freeze", action="store_true",
                        help="mark the manifest frozen and make every shard file read-only")
    args = parser.parse_args()
    out_dir = Path(args.dir)
    manifest = write_manifest(out_dir, frozen=True if args.freeze else None)
    if args.freeze:
        for _, path in complete_shards(out_dir):
            path.chmod(0o444)
            path.with_name(path.name.replace(".jsonl.gz", ".stats.json")).chmod(0o444)
    totals = manifest["totals"]
    print(f"{out_dir / 'manifest.json'}: {totals['sessions']} sessions, {totals['steps']} steps, "
          f"frozen: {manifest['frozen']}")
    for name, info in manifest["slices"].items():
        print(f"  {name:13s} {info['shards']:4d}/{info['shards_planned']:4d} shards "
              f"{info['sessions']:6d} sessions {info['steps']:8d} steps  ended "
              f"{info['sessions_ended']}")
    for code, entry in manifest["code_versions"].items():
        print(f"  code {code}: {entry['sessions']} sessions, shards {entry['shards']}, "
              f"selection {entry['selections']}")
    print(f"  other shapes: {totals['share_with_another_shape']:.1%} of sessions; a feature: "
          f"{totals['share_with_a_feature']:.1%}; wrong numbers: "
          f"{totals['wrong_number_share_of_noisy_steps']:.1%} of the wrong commands")


if __name__ == "__main__":
    main()
