"""Several third-model runs in ONE process, sharing one copy of the training arrays.

    python -m forge.s1.third.train_many --data <arrays> --max-minutes 160 \
        --job configs/s1_third.yaml:cuda:0:/kaggle/working/run \
        --job configs/s1_third_nohist.yaml:cuda:0:/kaggle/working/run2 \
        --job configs/s1_third_big.yaml:cuda:1:/kaggle/working/run3

Why not one process per run, as for the second models: the third model's training set is
about 19 GB in memory. That is more than a T4 holds (15 GB) and three copies are more than
the machine holds (30 GB). So the arrays stay in ordinary memory ONCE (`data_device: cpu`
in the configs), every run cuts its batches out of the same tensors (reading only) and
moves each batch to its GPU. Each run is a thread; the heavy work is on the GPU, where
threads do not wait for each other.

Each run is exactly forge/s1/third/train.py's: its own seed, model, optimiser, run folder.
"""

from __future__ import annotations

import argparse
import shutil
import threading
import traceback
from pathlib import Path
from typing import ClassVar

import torch
import yaml

from forge.s1.third.data import BindData
from forge.s1.third.train import ThirdKit
from forge.s1.train import train


class SharedData(BindData):
    """BindData whose `load` reads a given set of shards once per process."""

    _loaded: ClassVar[dict] = {}
    _lock: ClassVar = threading.Lock()

    @classmethod
    def load(cls, folder, slices, device, shards=None, max_steps=None, plan_items=None):
        key = (str(folder), tuple(slices), str(device), tuple(shards or ()), max_steps,
               tuple(plan_items or ()))
        with cls._lock:
            if key not in cls._loaded:
                cls._loaded[key] = super().load(folder, slices, device, shards, max_steps,
                                                plan_items)
            return cls._loaded[key]


class SharedKit(ThirdKit):
    data = SharedData


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--job", action="append", required=True,
                        help="<config>:<device>:<run folder>, e.g. configs/a.yaml:cuda:0:run")
    parser.add_argument("--data", required=True, help="folder of the prepared arrays")
    parser.add_argument("--max-minutes", type=float, default=None)
    parser.add_argument("--max-steps", type=int, default=None, help="a short trial")
    parser.add_argument("--resume-from-input", action="store_true",
                        help="carry on from <run folder name>/last.pt of an attached earlier "
                             "session's output, when there is one (a run longer than a session)")
    parser.add_argument("--only-slices", nargs="*", default=None,
                        help="a trial on part of the data: train on these slices only, and "
                             "keep only the held-out groups made of their shards")
    args = parser.parse_args()
    failed: list[str] = []

    def run(config_file: str, device: str, run_dir: str) -> None:
        config = yaml.safe_load(Path(config_file).read_text())
        config["data"]["folder"] = args.data
        if args.max_steps:
            config["train"]["max_steps"] = args.max_steps
        if args.only_slices:
            config["data"]["train_slices"] = list(args.only_slices)
            config["data"]["held_out_groups"] = {
                name: spec for name, spec in config["data"]["held_out_groups"].items()
                if all(file.split("/")[0] in args.only_slices for file in spec["shards"])}
        resume = None
        if args.resume_from_input:
            earlier = sorted(Path("/kaggle/input").rglob(f"{Path(run_dir).name}/last.pt"))
            if earlier:
                shutil.copytree(earlier[-1].parent, run_dir, dirs_exist_ok=True)
                resume = Path(run_dir) / "last.pt"
        try:
            train(config, Path(run_dir), resume, args.max_minutes, torch.device(device),
                  kit=SharedKit)
        except Exception:       # noqa: BLE001 - the other runs go on; this one is reported
            failed.append(config_file)
            (Path(run_dir) / "FAILED.txt").write_text(traceback.format_exc())
            traceback.print_exc()

    threads = []
    for job in args.job:
        config_file, rest = job.split(":", 1)
        device, run_dir = rest.rsplit(":", 1)
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        threads.append(threading.Thread(target=run, args=(config_file, device, run_dir)))
        threads[-1].start()
    for thread in threads:
        thread.join()
    if failed:
        raise SystemExit(f"these runs failed: {failed}")


if __name__ == "__main__":
    main()
