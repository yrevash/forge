# s1/ — the first Forge-S1 model

Forge-S1 reads a structured plan and the live FreeCAD state, and picks the next command from
the commands that are valid right now. It reads no English and no object names. This folder
is the whole model: encoding, network, loss, training, evaluation.

## Read the files in this order

| File | What it is | Needs |
| --- | --- | --- |
| `vocab.py` | The closed lists: 252 words, 60 number fields, 1,132 link roles. Built from `load.py`'s whitelists | forge |
| `encode.py` | One view -> rows of facts. Match flags, ordinals. `decode` goes back (for tests and for looking) | forge |
| `prepare.py` | Session files -> arrays on disk (`data/s1/v1/`), with a manifest. Run once | forge |
| `data.py` | Arrays -> batches on the GPU. Random row ids | torch, numpy |
| `model.py` | Row vectors, 6 transformer layers, one score per row. 2,166,241 parameters | torch |
| `loss.py` | The set loss, step accuracy by group, the Wilson interval | torch |
| `train.py` | The training loop: schedule, mixed precision, checkpoints, resume, the run folder | torch, yaml |
| `evaluate.py` | Step accuracy on whole test slices beside the plan-blind rule | forge |
| `live.py` | One view -> a batch, with no files (for driving FreeCAD and for tests) | torch |
| `drive.py` | Closed loop: the model drives real FreeCAD and the finished part is checked | forge, FreeCAD |
| `kaggle_pack.py`, `kaggle_kernel.py` | Packing for a free Kaggle GPU. Never touch the API token. Only training slices are packed | |
| `augment.py` | Counterfactual plans: a recorded TRAINING state read against a plan with two features swapped, relabelled by the teacher. Writes `<slice>_cf` arrays | forge |
| `augment_proof.py` | Shows in real FreeCAD that those labels lead to the stored solid | forge, FreeCAD |
| `counterfactual.py` | The counterfactual table on test states (plan order, one number). Printed by `evaluate.py` | forge |
| `drive_report.py` | Several closed-loop runs side by side: seeds pooled, by plan length | |

`data.py`, `model.py`, `loss.py` and `train.py` import nothing from the rest of forge, so
they can be copied to a GPU machine by themselves.

## The idea in one example

The plan says a block is 24 long. The sketch on screen says 42. The right command is `undo`.
If the sketch said 24 the right command would be `leave_sketch`. Every other value in the
view is identical.

`encode.py` compares every size in the document with every number of the plan before the
model sees anything, and writes the result into the sketch shape's row:

```
length is 24   ->  link  ("eq:shape.length:length", plan item 0)
length is 42   ->  word  "nomatch:shape.length"
length is 30   ->  link  ("eq:shape.length:width", plan item 0)      the width's value: still wrong
```

So the model does not have to subtract floats. It has to learn which matches are the right
ones for the plan item being built, and that is what the transformer layers are for.

Look at a real step yourself:

```
uv run python - <<'EOF'
from forge.freecad.load import examples
from forge.s1.encode import encode, facts_by_row
example = next(e for e in examples(("train_v2",), shards=[0]) if e.t == 9)
for row in facts_by_row(encode(example.view)):
    print(row["words"], row["numbers"], row["links"])
print([target["command"] for target in example.label])
EOF
```

## Shapes, once

B steps in a batch, R rows in the longest of them, D = 160 the width of a row vector.

```
facts of the batch (flat lists)  --model.rows-->  [B, R, D]   one vector per row
[B, R, D]  --6 x Block-->  [B, R, D]                          rows look at each other
[B, R, D]  --score-->  [B, R]                                 one number per row
scores on candidate rows only  --softmax-->  a probability per valid command
loss = -log(sum of the probabilities of the commands the teacher accepts)
```

## Commands

```
uv run python -m forge.s1.prepare                                   # about 10 minutes, 4 processes
uv run python -m forge.s1.train --config configs/s1_smoke_overfit.yaml
uv run python -m forge.s1.train --config configs/s1_first.yaml      # the full run (meant for a GPU)
uv run python -m forge.s1.train --resume runs/<run>/last.pt
uv run python -m forge.s1.evaluate --checkpoint checkpoints/s1/<file>.pt
uv run python -m forge.s1.drive --checkpoint checkpoints/s1/<file>.pt --parts 100
uv run python -m forge.s1.drive --policy teacher --parts 20         # checks the harness itself
uv run python -m forge.s1.drive --policy rule --parts 100           # the plan-blind rule: the floor
uv run python -m forge.s1.drive --checkpoint <file> --seed 1        # other parts: seed k takes parts 100k..100k+99
uv run python -m forge.s1.drive --checkpoint <file> --no-repeat-undo   # a driver rule, NOT the model; never the headline
uv run python -m forge.s1.drive_report --model "name=runs/<a>,runs/<b>,runs/<c>"
uv run python -m forge.s1.evaluate --checkpoint <file> --held-out [--random-ids]   # held-out TRAIN shards only
uv run python -m forge.s1.augment --slices train_v2 train_long_v2 --share 0.10     # plan-order counterfactuals
uv run python -m forge.s1.augment_proof --sessions 400                             # their labels, checked in FreeCAD
uv run pytest tests/test_s1_encoding.py tests/test_s1_loss.py tests/test_s1_train.py
```

## What the model must never see, and how that is kept

- The model's side of a step is `encode(example.view)`; `view` is built by the whitelist in
  `forge/freecad/load.py`. The encoder has no other argument.
- `prepare.py` stores the evaluation group of a step (undo decision, right after a mistake,
  wrong number, other) beside the label. `model.py` never reads `group`, `target` or
  `is_target`; a test checks that.
- `tests/test_s1_encoding.py::test_the_label_side_cannot_reach_the_model_arrays` scrambles
  the label side of 25 real sessions and checks the model-side arrays stay bit-identical.
- Test slices (`iid_v2`, `pairing_v2`, `long_v2`, `xlong_v2`) are read by `evaluate.py`,
  `counterfactual.py` and `drive.py` only. The first model logged a fixed 20,000-step sample
  of `iid_v2` during training (nothing was chosen on it). Since the second model no test
  shard is read in training or uploaded to Kaggle: `best.pt` is picked on held-out shards
  of the training slices, and `kaggle_pack.py`, `augment.py` and `drive.py` each refuse the
  wrong kind of slice (tests in `tests/test_s1_counterfactual.py`).

## How to read a closed-loop number

Every closed-loop number of `drive.py` is **command choice with teacher-supplied
arguments**: when the model names a command the teacher accepts, the teacher's arguments
are used. The model cannot make a wrong-number mistake of its own, and it cannot build a
part without the teacher. Say that label every time such a number is quoted.

## The second model

Three things changed after the first model:

1. **Long plans in training.** `train_long_v2`: sessions of 11,200 NEW parts with plans of
   7 to 13 items (`forge/freecad/long_train_parts.py`), none of them a part of the `long`
   test. `xlong_v2` (plans of 14 to 16 items) is a test only.
2. **Row ids** (`data.py`). The first version drew 32 sorted ids of 64 and
   used the first n, so short plans only trained the small ids. Now a step that uses n ids
   gets n of all 64.
3. **Plan order.** The first model noticed that a feature of the plan was built, not that
   it was built in the wrong place. Recorded sessions never show that, so `augment.py`
   makes such states from training states: same screen, two plan features swapped, the
   teacher's answer for the swapped plan (usually `undo`).

## The third model

It lives in `third/` (start with `third/README.md`): it names where every argument comes
from, so it runs without the teacher. Changes in THIS folder for it, none of which alter the
first two models: `vocab.py` and `encode.py` have its extra words and link roles appended
(`encode(view, third=True)`); `train.py` takes a `Kit` (data class, model, loss, evaluation),
has `init_from`, `patience`, `plan_items`, and opens every checkpoint as data only
(`load_checkpoint`, `weights_only=True`); `data.py` joins shards one array at a time;
`counterfactual.py` scores third models and the wide slices too.

**Row ids (measured 8 Oct 2026).** A model without random row ids fails completely on
plans longer than those it was trained on (0 of 300 on `xlong_v2`; 0.588 against 0.994 on
held-out TRAIN plans two to three items longer than trained). "No ids" in the list above for
the second model was measured only at trained lengths. Use random row ids.

## Not in this model

- It does not produce numbers. Arguments are filled by code; in `drive.py` they are the
  teacher's when the command is right.
- No "is this plan item built" output, no DAgger, no training on the first-mix slices.
