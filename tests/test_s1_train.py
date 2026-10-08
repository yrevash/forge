"""The Forge-S1 training loop: the schedule, learning at all, and resuming."""

import json
import random

import numpy as np
import torch

from forge.s1 import vocab
from forge.s1.encode import encode, target_bits
from forge.s1.live import pick_command, steps_arrays
from forge.s1.model import build_model
from forge.s1.train import batch_steps, learning_rate, train
from tests.s1_helpers import sketch_view

MODEL = {"width": 32, "layers": 2, "heads": 2, "mlp_width": 64, "dropout": 0.0}


def toy_prepared(folder, steps: int = 96) -> None:
    """A prepared-data folder made of toy steps: the block's sketch, with the right length
    (the answer is leave_sketch) or a wrong one (the answer is undo). The only way to tell
    is to compare the sketch with the plan."""
    rng = random.Random(0)
    encoded, targets = [], []
    for _ in range(steps):
        length, width = float(rng.randint(5, 90)), float(rng.randint(5, 90))
        plan = [{"kind": "block", "slots": {"length": length, "width": width, "height": 10.0}}]
        wrong = rng.random() < 0.5
        view = sketch_view(length + rng.choice((-7.0, 0.5, 13.0)) if wrong else length, plan)
        view["snapshot"]["items"][1]["shapes"][0]["width"] = width
        encoded.append(encode(view))
        answer = "undo" if wrong else "leave_sketch"
        targets.append(target_bits(encoded[-1].candidates, [{"command": answer}]))
    (folder / "toy").mkdir(parents=True)
    np.savez_compressed(folder / "toy" / "shard000.npz", **steps_arrays(encoded, targets))
    manifest = {"vocab": vocab.as_json(), "vocab_hash": vocab.vocab_hash(), "code_hash": "toy",
                "group_bits": {"undo decisions": 1},
                "slices": {"toy": {"shards": [{"file": "toy/shard000.npz"}]}}}
    (folder / "manifest.json").write_text(json.dumps(manifest))


def config_for(folder, max_steps: int) -> dict:
    return {"name": "toy", "seed": 3,
            "data": {"folder": str(folder), "train_slices": ["toy"], "train_shards": None,
                     "held_out_shards": None, "monitor_slice": "toy", "monitor_steps": 96},
            "model": dict(MODEL),
            "train": {"batch_size": 16, "passes": 1.0, "max_steps": max_steps, "lr": 3e-3,
                      "min_lr": 0.0, "warmup_steps": 4, "weight_decay": 0.01, "grad_clip": 1.0,
                      "random_ids": True, "mixed_precision": True},
            "log_every": 10, "eval_every": 1000, "checkpoint_minutes": 1000}


def test_learning_rate_schedule():
    # 0 at the start, the peak at the end of warm-up, the floor at the end.
    assert learning_rate(0, 100, 1e-3, 10) == 0.0
    assert learning_rate(10, 100, 1e-3, 10) == 1e-3
    assert abs(learning_rate(100, 100, 1e-3, 10, floor=1e-5) - 1e-5) < 1e-12
    assert abs(learning_rate(55, 100, 1e-3, 10) - 0.5e-3) < 1e-12        # half way down


def test_batches_depend_only_on_seed_and_step():
    first, second = {}, {}
    a = [batch_steps(1, step, 100, 8, first) for step in range(30)]
    b = batch_steps(1, 17, 100, 8, second)                # asked for directly, as after a resume
    assert torch.equal(a[17], b)
    one_pass = torch.cat(a[:12])                          # 100 // 8 = 12 batches in a pass
    assert len(set(one_pass.tolist())) == 96              # no step twice within a pass
    assert not torch.equal(a[0], a[12])                   # the next pass has another order


def test_it_learns_to_compare_the_sketch_with_the_plan(tmp_path):
    toy_prepared(tmp_path / "data")
    result = train(config_for(tmp_path / "data", 150), tmp_path / "run",
                   device=torch.device("cpu"))
    assert result["monitor"]["accuracy"]["all steps"] > 0.95
    lines = [json.loads(line) for line in (tmp_path / "run" / "metrics.jsonl").open()]
    losses = [line["loss"] for line in lines if line["event"] == "train"]
    assert losses[-1] < 0.2 < losses[0]
    for name in ("config.yaml", "command.txt", "NOTES.md", "last.pt", "final.pt"):
        assert (tmp_path / "run" / name).exists()

    # The saved model, loaded again, tells a wrong length from a right one in a new view.
    state = torch.load(tmp_path / "run" / "final.pt", weights_only=True)
    model = build_model(state["config"]["model"], state["vocab"])
    model.load_state_dict(state["model"])
    model.eval()
    plan = [{"kind": "block", "slots": {"length": 37.0, "width": 30.0, "height": 10.0}}]
    assert pick_command(model, encode(sketch_view(37.0, plan)), "cpu") == "leave_sketch"
    assert pick_command(model, encode(sketch_view(73.0, plan)), "cpu") == "undo"


def test_held_out_groups_are_never_trained_on_and_are_scored_one_by_one(tmp_path):
    toy_prepared(tmp_path / "data")
    # A second and a third shard: copies of the first, standing in for "short" and "long".
    folder = tmp_path / "data"
    manifest = json.loads((folder / "manifest.json").read_text())
    for number in (1, 2):
        (folder / "toy" / f"shard00{number}.npz").write_bytes(
            (folder / "toy" / "shard000.npz").read_bytes())
        manifest["slices"]["toy"]["shards"].append({"file": f"toy/shard00{number}.npz"})
    (folder / "manifest.json").write_text(json.dumps(manifest))
    config = config_for(folder, 12)
    config["data"].update(monitor_slice=None, held_out_groups={
        "held_out_short": {"shards": ["toy/shard001.npz"], "steps": 40},
        "held_out_long": {"shards": ["toy/shard002.npz"], "steps": 96}})
    result = train(config, tmp_path / "run", device=torch.device("cpu"))
    assert set(result) == {"held_out_short", "held_out_long"}
    assert result["held_out_short"]["steps"]["all steps"] == 40
    assert result["held_out_long"]["steps"]["all steps"] == 96
    start = json.loads((tmp_path / "run" / "metrics.jsonl").read_text().splitlines()[0])
    assert start["training_steps"] == 96 and start["shards"] == 1      # only shard000
    assert (tmp_path / "run" / "best.pt").exists()


def test_resume_gives_the_same_weights_as_an_unbroken_run(tmp_path):
    toy_prepared(tmp_path / "data")
    device = torch.device("cpu")
    train(config_for(tmp_path / "data", 24), tmp_path / "whole", device=device)
    train(config_for(tmp_path / "data", 24), tmp_path / "broken", device=device, stop_at_step=9)
    stopped = torch.load(tmp_path / "broken" / "last.pt", weights_only=True)
    assert stopped["step"] == 9 and not (tmp_path / "broken" / "final.pt").exists()
    train(stopped["config"], tmp_path / "broken", resume=tmp_path / "broken" / "last.pt",
          device=device)
    whole = torch.load(tmp_path / "whole" / "final.pt", weights_only=True)
    resumed = torch.load(tmp_path / "broken" / "final.pt", weights_only=True)
    assert resumed["step"] == whole["step"] == 24
    for name, weights in whole["model"].items():
        assert torch.equal(weights, resumed["model"][name]), name
    commands = (tmp_path / "broken" / "command.txt").read_text().splitlines()
    assert len(commands) == 2 and "RESUME" in commands[1]


def test_the_kaggle_kernel_trains_two_configs_side_by_side(tmp_path, monkeypatch):
    """forge/s1/kaggle_kernel.py `several`: two real training processes, one run folder each."""
    import yaml

    from forge.runs import PROJECT_ROOT
    from forge.s1 import kaggle_kernel
    toy_prepared(tmp_path / "data")
    for name, width in (("a", 32), ("b", 16)):
        config = config_for(tmp_path / "data", 6)
        config["name"], config["model"]["width"] = name, width
        config["model"]["mlp_width"] = 2 * width
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(config))
    monkeypatch.setattr(kaggle_kernel, "OUT", tmp_path / "working")
    monkeypatch.setattr(kaggle_kernel, "POLL_SECONDS", 1)
    monkeypatch.setattr(kaggle_kernel, "CONFIG", str(tmp_path / "a.yaml"))
    monkeypatch.setattr(kaggle_kernel, "MORE_CONFIGS", [str(tmp_path / "b.yaml")])
    monkeypatch.setattr(kaggle_kernel, "MAX_STEPS", None)
    monkeypatch.setattr(kaggle_kernel, "gpu_count", lambda: 2)
    kaggle_kernel.several(tmp_path / "data", PROJECT_ROOT)
    widths = {}
    for folder in ("run", "run2"):
        state = torch.load(tmp_path / "working" / folder / "final.pt", weights_only=True)
        assert state["step"] == 6 and state["commit"] == kaggle_kernel.COMMIT
        widths[folder] = state["config"]["model"]["width"]
        assert (tmp_path / "working" / folder / "train.log").read_text().strip()
    assert widths == {"run": 32, "run2": 16}

    # A run that fails makes the kernel fail, after the others have finished.
    (tmp_path / "b.yaml").write_text("name: broken\n")
    monkeypatch.setattr(kaggle_kernel, "OUT", tmp_path / "again")
    import pytest
    with pytest.raises(SystemExit, match="b.yaml"):
        kaggle_kernel.several(tmp_path / "data", PROJECT_ROOT)
    assert (tmp_path / "again" / "run" / "final.pt").exists()
