"""Train Forge-S1.

    uv run python -m forge.s1.train --config configs/s1_first.yaml
    uv run python -m forge.s1.train --resume runs/<run>/last.pt        # carry on a stopped run

The same file runs on the Mac (MPS), on a CUDA GPU and on a CPU. What happens:

    1. read the prepared arrays (forge/s1/prepare.py) onto the device;
    2. every optimiser step: take a batch of steps, score the candidates (model.py), compute
       the set loss (loss.py), back-propagate, let AdamW change the weights
       at the scheduled learning rate;
    3. every so often: measure accuracy by group on steps the model does not train on, write
       a line to metrics.jsonl, save a checkpoint.

A run writes runs/<date>-<time>-<name>/ with config.yaml, command.txt (the command and the
git commit), metrics.jsonl, NOTES.md, last.pt (the newest checkpoint), best.pt (the best on
the held-out training shards) and final.pt.

RESUME. Which steps are in batch number s depends only on (seed, s), and so do the random
row ids. So a run stopped at step s and resumed gives the same weights as one that was
never stopped (tests/test_s1_train.py checks this on the CPU).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
from pathlib import Path

import numpy as np
import torch
import yaml

from forge.s1.data import StepData, read_manifest
from forge.s1.loss import accuracy_by_group, chosen_is_right, set_loss
from forge.s1.model import build_model, count_parameters


def pick_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def learning_rate(step: int, total: int, peak: float, warmup: int, floor: float = 0.0) -> float:
    """linear warm-up to `peak`, then a cosine down to `floor` at step `total`."""
    if step < warmup:
        return peak * step / warmup
    done = (step - warmup) / max(1, total - warmup)
    return floor + 0.5 * (peak - floor) * (1 + math.cos(math.pi * min(1.0, done)))


def make_optimizer(model: torch.nn.Module, lr: float, weight_decay: float) -> torch.optim.AdamW:
    """AdamW. Weight decay only on the matrices of linear layers: not on
    biases, LayerNorm weights or embedding tables."""
    decayed = [p for name, p in model.named_parameters()
               if p.ndim >= 2 and not isinstance(_owner(model, name), torch.nn.Embedding)]
    ids = {id(p) for p in decayed}
    rest = [p for p in model.parameters() if id(p) not in ids]
    return torch.optim.AdamW([{"params": decayed, "weight_decay": weight_decay},
                              {"params": rest, "weight_decay": 0.0}], lr=lr)


def _owner(model: torch.nn.Module, parameter_name: str) -> torch.nn.Module:
    return model.get_submodule(parameter_name.rpartition(".")[0])


def batch_steps(seed: int, step: int, n: int, batch_size: int, cache: dict) -> torch.Tensor:
    """Which training steps are in optimiser step `step`: a function of (seed, step) only.

    One pass ("epoch") is a random order of all n steps, cut into batches; the last few steps
    that do not fill a batch are left out of that pass. Each pass has its own order."""
    per_pass = n // batch_size
    number, place = divmod(step, per_pass)
    if cache.get("pass") != number:
        generator = torch.Generator().manual_seed(seed * 1_000_003 + number)
        cache["pass"], cache["order"] = number, torch.randperm(n, generator=generator)
    return cache["order"][place * batch_size:(place + 1) * batch_size]


def on_device(batch: dict, device: torch.device) -> dict:
    """The batch with every tensor on the model's device (no copy when it is there already)."""
    return {name: value.to(device, non_blocking=True) if torch.is_tensor(value) else value
            for name, value in batch.items()}


@torch.no_grad()
def evaluate(model: torch.nn.Module, data: StepData, groups: dict[str, int],
             batch_size: int = 1024, random_ids: int | None = None) -> dict[str, dict]:
    """Accuracy by group and mean loss on every step of `data`. Row ids: the identity, or
    with `random_ids` (a seed) one random draw, the same at every call.

    A model trained with random ids is scored with random ids: measured on held-out
    training shards of the second model, identity ids at test cost it 0.3 to 1.4 points
   , and the first run of that model picked its checkpoint on the wrong one."""
    model.eval()
    generator = None if random_ids is None else torch.Generator().manual_seed(random_ids)
    right, bits, losses = [], [], []
    device = next(model.parameters()).device
    for start in range(0, len(data), batch_size):
        batch = on_device(data.batch(torch.arange(start, min(start + batch_size, len(data))),
                                     generator), device)
        scores = model(batch)
        right.append(chosen_is_right(scores, batch["is_candidate"], batch["is_target"]))
        losses.append(set_loss(scores, batch["is_candidate"], batch["is_target"]))
        bits.append(batch["group"])
    model.train()
    table = accuracy_by_group(torch.cat(right), torch.cat(bits), groups)
    return {"loss": float(torch.cat(losses).mean()),
            "accuracy": {name: (ok / n if n else None) for name, (ok, n) in table.items()},
            "steps": {name: n for name, (_, n) in table.items()}}


# Several runs can share one process (forge/s1/third/train_many.py). The global random
# generators are seeded and the model is created under this lock, one run at a time.
SETUP = threading.Lock()


class Kit:
    """The four things that differ between the models of this folder: the data class, the
    model, the loss of a batch and the evaluation. This is the first two models' kit; the
    third model passes its own to `train` (forge/s1/third/train.py)."""

    data = StepData
    build_model = staticmethod(build_model)
    evaluate = staticmethod(evaluate)

    @staticmethod
    def loss(model: torch.nn.Module, batch: dict, use_amp: bool) -> torch.Tensor:
        with torch.autocast("cuda", dtype=torch.float16, enabled=use_amp):
            scores = model(batch)                                               # [B, R]
        return set_loss(scores, batch["is_candidate"], batch["is_target"]).mean()


def find_file(name: str) -> Path:
    """A file given by path, or (on Kaggle) by the end of its path anywhere under
    /kaggle/input: "run2/best.pt" finds .../run2/best.pt of an attached kernel's output."""
    if Path(name).exists():
        return Path(name)
    found = sorted(Path("/kaggle/input").rglob(name))
    if not found:
        raise SystemExit(f"{name} was not found")
    return found[0]


def load_checkpoint(path: str | Path, device: torch.device | str = "cpu") -> dict:
    """Open a checkpoint as DATA ONLY (`weights_only=True`): tensors, numbers, strings,
    lists and dictionaries. A checkpoint comes back from a rented machine; a file that
    could run code when opened is refused by torch here instead of being trusted.
    Every checkpoint in this repository is read through this function."""
    return torch.load(path, map_location=device, weights_only=True)


def plain(value: object) -> object:
    """A config as plain data: tuples become lists, paths become strings. Anything that is
    not a number, a string, a list or a dictionary after that is an error at SAVE time."""
    if isinstance(value, dict):
        return {str(key): plain(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(inner) for inner in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"a checkpoint's config may not hold a {type(value).__name__}")


def save_checkpoint(path: Path, **state: object) -> None:
    """Write to a temporary file first: a run killed while saving must not lose the old one."""
    temporary = path.with_suffix(".tmp")
    torch.save(state, temporary)
    temporary.replace(path)


def log(run_dir: Path, **metrics: object) -> None:
    with (run_dir / "metrics.jsonl").open("a") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **metrics}) + "\n")


def commit_of_code() -> str:
    """The git commit. On a machine without the repository it is passed in FORGE_COMMIT."""
    if os.environ.get("FORGE_COMMIT"):
        return os.environ["FORGE_COMMIT"]
    from forge.runs import git_commit
    return git_commit() or "unknown"


def open_run(run_dir: Path, config: dict, resumed: bool) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    if not resumed:
        (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    with (run_dir / "command.txt").open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  commit={commit_of_code()}  "
                f"{'RESUME  ' if resumed else ''}{' '.join(sys.argv)}\n")
    notes = run_dir / "NOTES.md"
    if not notes.exists():
        notes.write_text(f"# {config['name']}\n\n(what this run was for, and what happened)\n")


def train(config: dict, run_dir: Path, resume: Path | None = None,
          max_minutes: float | None = None, device: torch.device | None = None,
          stop_at_step: int | None = None, kit: type[Kit] = Kit) -> dict:
    """Run (or carry on) one training run. Returns the last evaluation.
    `max_minutes` and `stop_at_step` save a checkpoint and stop early, to be resumed."""
    device = device or pick_device()
    config = plain(config)          # what goes into config.yaml and every checkpoint
    seed, cfg, data_cfg = config["seed"], config["train"], config["data"]
    seed_everything(seed)
    open_run(run_dir, config, resumed=resume is not None)

    manifest = read_manifest(data_cfg["folder"])
    groups = {name: bit for name, bit in manifest["group_bits"].items()}
    # Shards that are never trained on. `held_out_shards` is one group named "held_out";
    # `held_out_groups` names several (short plans and long plans, say), each scored by
    # itself. best.pt is the checkpoint with the highest MEAN "all steps" accuracy over them.
    held_groups = dict(data_cfg.get("held_out_groups") or {})
    if data_cfg.get("held_out_shards"):
        held_groups = {"held_out": {"shards": data_cfg["held_out_shards"],
                                    "steps": data_cfg["held_out_steps"]}, **held_groups}
    held_files = [file for spec in held_groups.values() for file in spec["shards"]]
    files = [note["file"] for name in data_cfg["train_slices"]
             for note in manifest["slices"][name]["shards"]]
    if data_cfg.get("train_shards"):
        files = [file for file in files if file in set(data_cfg["train_shards"])]
    files = [file for file in files if file not in set(held_files)]     # never trained on
    # `data_device: cpu` keeps the arrays in ordinary memory and moves each batch to the
    # GPU (for data larger than the GPU's memory; cutting a batch out is cheap either way).
    data_device = torch.device(data_cfg.get("data_device") or device)
    def only(spec: dict) -> dict:
        """`plan_items: [low, high]` (third model's data only): steps of plans that long."""
        return {"plan_items": spec["plan_items"]} if spec.get("plan_items") else {}
    data = kit.data.load(data_cfg["folder"], data_cfg["train_slices"], data_device, shards=files,
                         max_steps=data_cfg.get("train_max_steps"), **only(data_cfg))
    watched = {name: kit.data.load(data_cfg["folder"], data_cfg["train_slices"], data_device,
                                   shards=spec["shards"], max_steps=spec["steps"], **only(spec))
               for name, spec in held_groups.items()}
    deciding = list(watched) or ["monitor"]     # with no held-out shards, the monitor decides
    if data_cfg.get("monitor_slice"):
        watched["monitor"] = kit.data.load(data_cfg["folder"], [data_cfg["monitor_slice"]], data_device,
                                           max_steps=data_cfg["monitor_steps"])

    with SETUP:     # seeded again right here, so the weights do not depend on what ran before
        seed_everything(seed)
        model = kit.build_model(config["model"], manifest["vocab"]).to(device)
    optimizer = make_optimizer(model, cfg["lr"], cfg["weight_decay"])
    use_amp = bool(cfg["mixed_precision"]) and device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
    per_pass = len(data) // cfg["batch_size"]
    total = cfg["max_steps"] or math.ceil(cfg["passes"] * per_pass)
    step, best, last_eval = 0, -1.0, {}
    stale = 0       # evaluations since the held-out score last improved (train.patience)
    if resume is None and cfg.get("init_from"):
        # Start from another run's weights (a DAgger round carries on from the model that
        # made its rollouts). Only the weights: a new optimiser, a new schedule, step 0.
        start = load_checkpoint(find_file(cfg["init_from"]), device)
        if start["vocab_hash"] != manifest["vocab_hash"]:
            raise SystemExit("init_from was trained with another vocabulary than this data")
        model.load_state_dict(start["model"])
    if resume is not None:
        state = load_checkpoint(resume, device)
        if state["vocab_hash"] != manifest["vocab_hash"]:
            raise SystemExit("the checkpoint was trained with another vocabulary than this data")
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scaler.load_state_dict(state["scaler"])
        step, best, stale = state["step"], state["best"], state.get("stale", 0)
    print(f"device {device} | {count_parameters(model):,} parameters | {len(data):,} training "
          f"steps in {len(files)} shards | {per_pass:,} batches per pass | {total:,} optimiser "
          f"steps in all | starting at {step:,}", flush=True)
    log(run_dir, event="start", device=str(device), parameters=count_parameters(model),
        training_steps=len(data), shards=len(files), batches_per_pass=per_pass,
        total_optimizer_steps=total, start_step=step, mixed_precision=use_amp,
        vocab_hash=manifest["vocab_hash"], data_code_hash=manifest["code_hash"])

    def checkpoint(name: str) -> None:
        save_checkpoint(run_dir / name, model=model.state_dict(), optimizer=optimizer.state_dict(),
                        scaler=scaler.state_dict(), step=step, best=best, stale=stale,
                        config=plain(config),
                        vocab=manifest["vocab"], vocab_hash=manifest["vocab_hash"],
                        commit=commit_of_code())

    def measure() -> dict:
        nonlocal best, last_eval, stale
        last_eval = {name: kit.evaluate(model, held, groups,
                                        random_ids=seed if cfg["random_ids"] else None)
                     for name, held in watched.items()}
        log(run_dir, event="eval", step=step, **last_eval)
        scores = [last_eval[name]["accuracy"]["all steps"] for name in deciding
                  if name in last_eval]
        if scores:
            print(f"  eval at {step}: " + " | ".join(
                f"{name} loss {table['loss']:.4f} "
                + " ".join(f"{group.split()[0]} {value:.3f}"
                           for group, value in table["accuracy"].items() if value is not None)
                for name, table in last_eval.items()), flush=True)
            if sum(scores) / len(scores) > best:            # the held-out training shards decide
                best, stale = sum(scores) / len(scores), 0
                checkpoint("best.pt")
            else:
                stale += 1
        return last_eval

    model.train()
    cache: dict = {}
    started = last_save = time.time()
    seen, loss_sum, window = 0, 0.0, time.time()
    while step < total:
        rate = learning_rate(step, total, cfg["lr"], cfg["warmup_steps"], cfg["min_lr"])
        for group in optimizer.param_groups:
            group["lr"] = rate
        ids = torch.Generator().manual_seed(seed * 7_000_003 + step) if cfg["random_ids"] else None
        batch = on_device(data.batch(batch_steps(seed, step, len(data), cfg["batch_size"],
                                                 cache), ids), device)
        loss = kit.loss(model, batch, use_amp)
        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)              # so the clip sees the true gradient size
        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg["grad_clip"])
        scaler.step(optimizer)
        scaler.update()
        step += 1
        seen += batch["n_steps"]
        loss_sum += float(loss.detach()) if step % 10 == 0 else 0.0     # a sample: reading it waits for the GPU

        if step % config["log_every"] == 0:
            now = time.time()
            log(run_dir, event="train", step=step, loss=loss_sum / (config["log_every"] / 10),
                lr=rate, examples_per_s=seen / (now - window), passes=step / per_pass,
                minutes=(now - started) / 60)
            if step % (config["log_every"] * 10) == 0:
                print(f"step {step:>7,}/{total:,}  loss {loss_sum / (config['log_every'] / 10):.4f}"
                      f"  lr {rate:.2e}  {seen / (now - window):,.0f} examples/s", flush=True)
            seen, loss_sum, window = 0, 0.0, now
        if step % config["eval_every"] == 0 or step == total:
            measure()
            window = time.time()
            # `patience`: stop when the held-out score has not improved for so many
            # evaluations in a row. best.pt is the model to use; the schedule is cut short.
            if cfg.get("patience") and stale >= cfg["patience"] and step < total:
                checkpoint("last.pt")
                checkpoint("final.pt")
                log(run_dir, event="end", step=step, minutes=(time.time() - started) / 60,
                    best_held_out=best, stopped="no improvement for "
                    f"{stale} evaluations")
                print(f"stopped at step {step}: no improvement for {stale} evaluations",
                      flush=True)
                return last_eval
        if time.time() - last_save > config["checkpoint_minutes"] * 60:
            checkpoint("last.pt")
            last_save = time.time()
        out_of_time = max_minutes is not None and time.time() - started > max_minutes * 60
        if (out_of_time or step == stop_at_step) and step < total:
            checkpoint("last.pt")
            log(run_dir, event="stopped", step=step, minutes=(time.time() - started) / 60)
            print(f"stopped at step {step}; carry on with --resume {run_dir / 'last.pt'}",
                  flush=True)
            return last_eval
    checkpoint("last.pt")
    checkpoint("final.pt")
    log(run_dir, event="end", step=step, minutes=(time.time() - started) / 60, best_held_out=best)
    return last_eval


def main(kit: type[Kit] = Kit) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default="configs/s1_first.yaml")
    parser.add_argument("--resume", default=None, help="a checkpoint; its run folder is reused")
    parser.add_argument("--run-dir", default=None, help="default: runs/<date>-<time>-<name>")
    parser.add_argument("--data", default=None, help="folder of the prepared arrays, if not the config's")
    parser.add_argument("--max-minutes", type=float, default=None,
                        help="save and stop after this long (resume later)")
    parser.add_argument("--max-steps", type=int, default=None, help="overrides train.max_steps")
    args = parser.parse_args()
    if args.resume:
        run_dir = Path(args.run_dir) if args.run_dir else Path(args.resume).parent
        config = load_checkpoint(args.resume)["config"]
    else:
        config = yaml.safe_load(Path(args.config).read_text())
        stamp = time.strftime("%Y-%m-%d-%H%M%S")
        run_dir = Path(args.run_dir) if args.run_dir else Path("runs") / f"{stamp}-{config['name']}"
    if args.data:
        config["data"]["folder"] = args.data
    if args.max_steps:
        config["train"]["max_steps"] = args.max_steps
    train(config, run_dir, Path(args.resume) if args.resume else None, args.max_minutes,
          kit=kit)
    print(f"run folder: {run_dir}")


if __name__ == "__main__":
    main()
