"""Write the folder `kaggle kernels push -p` wants: the script with its CONFIG, and metadata.

    uv run python -m forge.remote.kernel_dir --out <folder> --slug forge-fc-probe-a \
        --dataset yashtiwari9182/forge-freecad-bundle --config '{"prove": 300, "jobs": [...]}'
    uvx kaggle kernels push -p <folder>

Always private, CPU only, internet on (FreeCAD is downloaded at every start).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCRIPT = Path(__file__).with_name("kaggle_kernel.py")
MARK = 'CONFIG = {"jobs": [], "prove": 0, "burn": 20}\n'


def metadata(owner: str, slug: str, dataset: str | None) -> dict:
    return {"id": f"{owner}/{slug}", "title": slug, "code_file": "kernel.py",
            "language": "python", "kernel_type": "script", "is_private": True,
            "enable_gpu": False, "enable_internet": True,
            "dataset_sources": [dataset] if dataset else [],
            "competition_sources": [], "kernel_sources": [], "model_sources": []}


def script_with(config: dict) -> str:
    """The kernel script with its CONFIG line replaced (it must be there exactly once)."""
    text = SCRIPT.read_text()
    if text.count(MARK) != 1:
        raise ValueError("kaggle_kernel.py has no CONFIG line to replace")
    return text.replace(MARK, f"CONFIG = json.loads({json.dumps(config)!r})\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--owner", default="yashtiwari9182")
    parser.add_argument("--dataset", default=None,
                        help="owner/slug of the bundle dataset (none: a kernel that only measures)")
    parser.add_argument("--config", required=True, help="JSON (see kaggle_kernel.py)")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "kernel.py").write_text(script_with(json.loads(args.config)))
    (out / "kernel-metadata.json").write_text(
        json.dumps(metadata(args.owner, args.slug, args.dataset), indent=1) + "\n")
    print(out / "kernel-metadata.json")


if __name__ == "__main__":
    main()
