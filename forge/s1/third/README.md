# s1/third/ — the third Forge-S1 model

The first two models (one folder up) name a command; a script types the numbers. The third
model also says WHERE every number comes from, so it can drive FreeCAD with no teacher, and
it can be wrong about a number and has to notice.

Nothing here changes the first two models: they keep their vocabulary, arrays
(`data/s1/v1`), checkpoints and driver.

## The idea in one example

The plan's item 2 is a pad: length 15, width 26, height 11. The sketch is open and its
rectangle has no length yet. The model answers with three things:

```
command   constrain_length                 one of the valid commands        (as before)
item      2                                a pointer at a row of the plan   (new)
kinds     ["slot:length"]                  one source per argument          (new)
```

`bindings.resolve(plan, "constrain_length", 2, ["slot:length"])` gives `{"value": 15.0}` by
reading the plan, and FreeCAD carries it out. Had the model said `slot:width`, FreeCAD would
have carried out 26: a wrong number of the model's own. The next view then shows a length
that equals the WIDTH slot of item 2, and the right answer is `undo`.

The kinds are a closed list of 40 (`bindings.KINDS`), exactly the sources the recipes use,
measured on the recordings: a slot of the pointed item; the base's height; the floor (top of
the base, or the bottom of a shelled base); half of the circle diameter; the first place of
a row; the constants 0, 90 and 6; a word (plane, axis, edge or face rule).

## Read the files in this order

| File | What it is |
| --- | --- |
| `bindings.py` | The kinds; `resolve` (binding -> arguments, from the plan alone); `allowed` (the mask); `expressible` (does the teacher's own binding give the recorded arguments back?) |
| `../../freecad/load.py` | `Example.sources` (label side) and `view(..., history=)`; nothing else changed |
| `../vocab.py`, `../encode.py` | The third model's words and link roles, appended after the first models' (their ids do not move). `encode(view, third=True)` adds the previous commands and the "first of a row" relation |
| `prepare.py` | Session files -> `data/s1/v3/` arrays with bindings; also the `_cf` and `_sc` copies; counts what is expressible |
| `data.py` | Batches with the binding queries |
| `model.py` | The first model's body, number features by relation, the binding head |
| `loss.py` | The binding loss and the strict step accuracy |
| `train.py` | The kit that plugs all this into `forge/s1/train.py`'s loop |
| `train_many.py` | Several runs in one process sharing one copy of the arrays (19 GB) |
| `live.py` | One view -> a decision (command, item, kinds, arguments) |
| `drive.py` | Closed loop for all three models: `--arguments model` (teacher-free) or `teacher` |
| `dagger.py` | The model drives TRAINING parts; the teacher labels every state it reaches |
| `evaluate.py`, `report.py` | Step tables for the three models on the same steps; closed-loop runs side by side |
| `kaggle_pack.py`, `kaggle_kernel.py` | Packing for Kaggle; test slices are refused |

## Shapes, once

B steps, R rows, D width, Q binding queries (accepted commands that work on a plan item), P
plan rows, A = 3 arguments at most, K = 40 kinds.

```
facts  --rows-->  [B, R, D]  --blocks-->  h [B, R, D]
h  --score-->            [B, R]      one score per row; candidate rows compete
h[cand row], h[plan rows]  --dot product-->  [Q, P]     which plan item
[h[cand row] ; h[item row]]  --MLP-->        [Q, A, K]  which kind per argument
loss = set loss of the command + binding loss
```

## What changed in what the model sees

1. **Numbers by relation.** A size is shown as: present, zero, negative, and one of 12 coarse
   size classes (two per factor of ten). No raw value, no "is it whole". What a size EQUALS
   in the plan was already a link fact; "first of a row" is a new one. Counts (dof, sides)
   keep their value.
2. **The two previous commands** and FreeCAD's answer to each, as words on the session row.
   `model.use_history: false` blinds a model to them; the arrays are the same.
3. **Random row ids** in training, in scoring and when driving. Run A had
   none and built no plan longer than training; the driver draws
   them by itself for a checkpoint trained with them.

## Two closed-loop numbers; always say which

- **Teacher-free** (`--arguments model`): the model's own command and arguments are carried
  out. The teacher only counts mistakes. This is the third model's headline.
  `--no-repeat` adds a rule of the DRIVER: never the same (state, command, arguments) twice
  in an episode, take the model's next-best command. A number made with it is labelled so.
- **Command choice with teacher-supplied arguments** (`--arguments teacher`): the measure of
  the first two models, kept for continuity.

## Commands

```
uv run python -m forge.s1.third.prepare --slices train_v2 train_long_v2 train_wide_v3
uv run python -m forge.s1.third.prepare --census
uv run python -m forge.s1.third.train --config configs/s1_third.yaml
uv run python -m forge.s1.third.drive --checkpoint <file> --arguments model --seed 0
uv run python -m forge.s1.third.drive --arguments model            # the teacher drives: must be 100%
uv run python -m forge.s1.third.drive --checkpoint <file> --arguments model --no-repeat   # a driver-side EXTRA, never the headline
uv run python -m forge.s1.third.dagger --checkpoint <file> --round 1
uv run python -m forge.s1.third.evaluate --checkpoint third=<file> --checkpoint first=<file>
uv run python -m forge.s1.counterfactual --checkpoint third=<file> --checkpoint first=<file>
uv run python -m forge.s1.third.report --model "third, teacher-free=runs/<a>,runs/<b>"
uv run pytest tests/test_s1_third.py
```

## What the model must never see, and how that is kept

- The model's side of a step is `encode(view, third=True)`; `view` takes the plan, the
  snapshot, the valid commands and the history, and nothing else. `sources` is not an
  argument of `view` (test: poisoned sources leave every view identical).
- The history of step t is read from records before t. Step t's own `executed` and `reply`
  are the answer (test: changing them changes the view of step t + 1 only).
- Scrambling every binding label leaves the command scores bit-identical (test).
- Test slices are refused by `kaggle_pack.py` and `dagger.py` (test), and DAgger never
  rolls out on the held-out TRAIN shards' numbers.
