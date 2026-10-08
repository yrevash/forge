"""Run folders: every long job gets one.

Every long job writes runs/<date>-<name>/ holding the config, the exact command,
the git commit, a metrics log and short notes, so any number can be traced back.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True,
            check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else "uncommitted"
    except OSError:
        return "unknown"


def start_run(name: str, config: dict) -> Path:
    """Create a new run folder for this invocation of `name`."""
    # One folder per invocation: a second run on the same day must not overwrite
    # the first one's config or mix its metrics in.
    stamp = f"{date.today().isoformat()}-{time.strftime('%H%M%S')}"  # noqa: DTZ011 - local date
    run_dir = PROJECT_ROOT / "runs" / f"{stamp}-{name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    with (run_dir / "command.txt").open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  commit={git_commit()}  "
                f"{' '.join(sys.argv)}\n")
    notes = run_dir / "NOTES.md"
    if not notes.exists():
        notes.write_text(f"# {name}\n\n(what this run was for, and what happened)\n")
    return run_dir


def log_metrics(run_dir: Path, **metrics: object) -> None:
    with (run_dir / "metrics.jsonl").open("a") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **metrics}) + "\n")
