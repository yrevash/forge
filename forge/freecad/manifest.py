"""The manifest of the session files: what is on disk, counted, and what code made it.

    data/freecad/sessions/manifest.json     written by the recorder after every shard, and by
                                            this command for everything at once
    data/freecad/sessions/long_pure.json    the long parts with no held-out pairing in them

Run:    uv run python -m forge.freecad.manifest            rewrite the manifest for all slices
        uv run python -m forge.freecad.manifest --freeze   and make the shard files read-only

Everything is added up from the shards' stats files (`shardNNN.stats.json`), so the
manifest can be rebuilt at any time without reading a session.

Which code made which shard (`code_versions`). Shards of the second mix carry the
hash in their stats file. The first recording wrote one hash per RUN into the run
folder (runs/<date>-freecad-sessions/config.yaml) and logged each shard it finished
(metrics.jsonl), so the mapping is read from there. A few shards were finished by
worker processes that outlived their run and were never logged; they are given the
hash of the run that was going when their stats file was written.

`long_pure`. The `long` split holds every part with 6 to 12 features, and about
half of them also contain one of the held-out pairings. A score on `long` then
mixes two difficulties. `long_pure` lists the long sessions whose plan has no
held-out pairing, so that length can be reported by itself. It is a listing; no
session is recorded for it.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

from forge.freecad.shards import FROZEN, OUT_DIR, SLICES, complete_shards, read_sessions
from forge.runs import PROJECT_ROOT
from forge.system1.splits import held_out_pairs_in

RUNS_DIR = PROJECT_ROOT / "runs"
# What the three hashes of the first recording differ in. `sessions.legacy_code_hash`
# covered every .py file of forge/freecad, tools included. By the files' modification
# times, the only files that changed after the first production run began (6 Oct 2026
# 00:44:20) are extra_parts.py (00:48:59) and audit.py (02:02:49). Neither is imported
# by the recorder. Checked on 6 Oct 2026 09:10 before any file was edited for the second
# mix: the hash computed then was 1c27ac87bd63.
LEGACY_NOTE = ("The three hashes of the first recording (25294e7b7e20, a0cee27d5505, "
               "1c27ac87bd63) covered every .py file in forge/freecad, tools included. By file "
               "modification times the only files that changed between them are extra_parts.py "
               "(a0cee27d5505) and audit.py (1c27ac87bd63); the recorder imports neither. So "
               "the same recording code is behind all three, as far as modification times can "
               "show; the old file contents were not kept.")


def grouped(counts: Counter, prefix: str) -> dict:
    return {key[len(prefix):]: value for key, value in sorted(counts.items())
            if key.startswith(prefix)}


def shares(counts: dict) -> dict:
    """Counts as fractions of their sum, to four digits."""
    total = sum(counts.values())
    return {key: round(value / total, 4) for key, value in counts.items()} if total else {}


def summary(counts: Counter) -> dict:
    """The counts of one slice (or of all of them) in the manifest's layout."""
    steps = max(counts["steps"], 1)
    by_kind: Counter = Counter()
    for key, value in grouped(counts, "noise:").items():
        by_kind[key.split(":")[0]] += value
    return {
        "parts": counts["parts"], "sessions": counts["sessions"],
        "sessions_ended": grouped(counts, "end:"),
        "sessions_with_problems": counts["sessions_with_problems"],
        "steps": counts["steps"],
        "sessions_by_noise_level": grouped(counts, "noise_level:"),
        "steps_by_noise_level": grouped(counts, "steps_at_noise:"),
        "sessions_by_start": grouped(counts, "start:"),
        "sessions_by_plan_items": grouped(counts, "plan_items:"),
        "steps_by_target_command": grouped(counts, "target:"),
        "steps_by_number_of_targets": grouped(counts, "choices:"),
        "undo_share": round(counts["target:undo"] / steps, 4),
        "clear_selection_share": round(counts["target:clear_selection"] / steps, 4),
        "off_plan_steps": counts["off_plan_steps"],
        "noisy_steps": counts["noisy_steps"],
        "noisy_steps_by_kind": dict(sorted(by_kind.items())),
        "noisy_steps_share_by_kind": shares(dict(sorted(by_kind.items()))),
        "noisy_steps_by_kind_reply_effect": grouped(counts, "noise:"),
        # Counted only by the second mix's recorder (None for the first recording).
        "wrong_numbers_by_flavour": grouped(counts, "wrong_number:") or None,
        "steps_with_a_failed_feature": counts.get("steps_with_a_failed_feature"),
        "steps_finished_too_early": counts.get("steps_finished_too_early"),
        "repairs_by_new_document": counts.get("repairs_by_new_document"),
        "steps_right_after_a_wrong_number": counts.get("steps_right_after_a_wrong_number"),
        "undo_history_lost": counts.get("undo_history_lost"),
        "sessions_where_noise_was_cut_off": counts["noise_cut_off"],
        "teacher_commands_refused": counts["teacher_commands_refused"],
        "worker_restarts": counts["worker_restarts"],
        "sessions_played_again": counts["sessions_played_again"],
        "raw_bytes": counts["raw_bytes"], "gz_bytes": counts["gz_bytes"],
        "raw_bytes_per_step": round(counts["raw_bytes"] / steps, 1),
        "gz_bytes_per_step": round(counts["gz_bytes"] / steps, 1),
    }


# --- which code made which shard -----------------------------------------------------------------

def _legacy_runs(out_dir: Path) -> list[dict]:
    """The runs of the first recording that wrote into `out_dir`: start time, hash, shards."""
    runs = []
    for folder in sorted(RUNS_DIR.glob("*-freecad-sessions")):
        command = (folder / "command.txt").read_text()
        config = (folder / "config.yaml").read_text()
        found = re.search(r"^code_hash: (\w+)$", config, re.MULTILINE)
        out = re.search(r"--out (\S+)", command)
        if found is None or (Path(out.group(1)).resolve() if out else OUT_DIR) != out_dir.resolve():
            continue
        log = folder / "metrics.jsonl"
        lines = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        runs.append({"run": folder.name, "code_hash": found.group(1),
                     "started": time.mktime(time.strptime(command[:19], "%Y-%m-%d %H:%M:%S")),
                     "shards": [line["shard"] for line in lines if "shard" in line]})
    return runs


def code_versions(out_dir: Path) -> dict:
    """code hash -> the shards it made, with how we know (see the top of this file)."""
    runs = _legacy_runs(out_dir)
    logged = {shard: run for run in runs for shard in run["shards"]}
    versions: dict[str, dict] = {}
    for stats, path in complete_shards(out_dir):
        if "code_hash" in stats:
            code, how = stats["code_hash"], "written in the shard's stats file"
        elif stats["file"] in logged:
            code, how = logged[stats["file"]]["code_hash"], "logged by the run that made it"
        else:
            # Finished by a worker that outlived its run: the run going at that moment.
            written = path.with_name(path.name.replace(".jsonl.gz", ".stats.json")).stat().st_mtime
            before = [run for run in runs if run["started"] <= written]
            code = before[-1]["code_hash"] if before else "unknown"
            how = "not logged; the run that was going when its stats file was written"
        entry = versions.setdefault(code, {"shards": {}, "sessions": 0, "how_known": Counter()})
        entry["shards"].setdefault(stats["slice"], []).append(int(path.name[5:8]))
        entry["sessions"] += stats["counts"]["sessions"]
        entry["how_known"][how] += 1
    for entry in versions.values():
        entry["shards"] = {name: _ranges(numbers) for name, numbers in entry["shards"].items()}
        entry["how_known"] = dict(entry["how_known"])
    return versions


def _ranges(numbers: list[int]) -> str:
    """[0, 1, 2, 5] -> '0-2, 5'."""
    numbers = sorted(numbers)
    runs: list[list[int]] = []
    for number in numbers:
        if runs and number == runs[-1][1] + 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


# --- the manifest --------------------------------------------------------------------------------

def write_manifest(out_dir: Path, config: dict | None = None,
                   planned: dict[str, int] | None = None, frozen: bool | None = None) -> dict:
    """Add up the stats of every complete shard. Safe to call at any time.

    `config` and `planned` describe the run that calls this; what an earlier manifest said
    about other slices (their planned shards, the configs of earlier runs) is kept.
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
    if "config" in earlier and "configs" not in earlier:      # the first recording's manifest
        configs["first mix (" + earlier["config"]["code_hash"] + ")"] = earlier["config"]
    if config:
        configs[f"{config.get('mix', 'v1')} ({config['code_hash']})"] = config
    manifest = {
        "contract": "forge/freecad/sessions.py (file format), teacher.py (rules), "
                    "load.py (what a model may read)",
        "frozen": earlier.get("frozen", False) if frozen is None else frozen,
        "configs": configs,
        "code_versions": code_versions(Path(out_dir)),
        "code_versions_note": LEGACY_NOTE,
        "totals": summary(total),
        "slices": {name: {"split": SLICES[name][0], "seed": SLICES[name][1],
                          "noise_levels": list(SLICES[name][2]),
                          "mix": "v1" if name in FROZEN else "v2",
                          **summary(per_slice[name]),
                          "shards": per_slice[name]["shards"],
                          "shards_planned": plans.get(name, 0)} for name in SLICES},
        "long_pure": earlier.get("long_pure"),
        "busy_seconds_all_workers": round(seconds, 1),
        "files": files,
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


# --- long_pure -----------------------------------------------------------------------------------

def long_pure(out_dir: Path) -> dict:
    """List the finished long sessions whose plan has no held-out pairing; write long_pure.json."""
    listing: dict[str, dict] = {}
    for name in (name for name, value in SLICES.items() if value[0] == "long"):
        pure, mixed = [], 0
        for _, path in complete_shards(out_dir, (name,)):
            for header, _, end in read_sessions(path):
                if end["end"] != "done":
                    continue
                if held_out_pairs_in([item["kind"] for item in header["plan"]]):
                    mixed += 1
                else:
                    pure.append({"session": header["session"], "part_id": header["part_id"],
                                 "plan_items": len(header["plan"])})
        listing[name] = {"sessions": len(pure), "with_a_held_out_pairing": mixed, "pure": pure}
    (Path(out_dir) / "long_pure.json").write_text(json.dumps(
        {"what": "long sessions whose plan contains no held-out pairing (splits.HELD_OUT_PAIRS)",
         **listing}, indent=1) + "\n")
    return {name: {key: value for key, value in info.items() if key != "pure"}
            for name, info in listing.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", default=str(OUT_DIR))
    parser.add_argument("--freeze", action="store_true",
                        help="mark the manifest frozen and make every shard file read-only")
    args = parser.parse_args()
    out_dir = Path(args.dir)
    counts = long_pure(out_dir)
    manifest = write_manifest(out_dir, frozen=True if args.freeze else None)
    manifest["long_pure"] = {"file": "long_pure.json", **counts}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if args.freeze:
        for stats, path in complete_shards(out_dir):
            path.chmod(0o444)
            path.with_name(path.name.replace(".jsonl.gz", ".stats.json")).chmod(0o444)
    print(f"{out_dir / 'manifest.json'}: {manifest['totals']['sessions']} sessions, "
          f"{manifest['totals']['steps']} steps, frozen: {manifest['frozen']}")
    for name, info in manifest["slices"].items():
        print(f"  {name:11s} {info['mix']}  {info['shards']:3d}/{info['shards_planned']:3d} shards "
              f"{info['sessions']:7d} sessions {info['steps']:9d} steps  ended "
              f"{info['sessions_ended']}")
    for code, entry in manifest["code_versions"].items():
        print(f"  code {code}: {entry['sessions']} sessions, shards {entry['shards']}")
    print(f"  long_pure: {counts}")


if __name__ == "__main__":
    main()
