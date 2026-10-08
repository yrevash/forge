# system1/ — step-by-step building: the engine and the training sessions

The System 1 model builds a part one step at a time: it reads the sentence and what is built so far, picks the next step, and for each number that step needs it points at a number in the sentence. This folder holds everything around the model except the model itself: the step vocabulary, the engine that applies steps, the teacher, and the code that cuts our verified composed parts into training sessions.

**The contract is the step table in `steps.py`.**

No model is trained or defined here. No CAD kernel is needed except for `build` output, which is run only through `forge.sandbox`.

```
system1/
  steps.py        the vocabulary: every step kind and its slots; a part's parameters <-> its steps
  engine.py       State (what is built), apply / undo, the rule check, build (-> CadQuery program text)
  mentions.py     a prompt with its mentions, and the plan: which mention fills which slot
  teacher.py      for any state, the one correct next step (plan order; undo if off the plan; done)
  noise.py        the five kinds of wrong step injected into sessions
  sessions.py     play (part, prompt) sessions and write them as gzip JSONL shards   [command]
  splits.py       train / iid / pairing / long; the held-out pairs of feature kinds   [command]
  long_parts.py   generate and verify the 6-to-12-feature test parts                  [command]
  prove.py        proof that step-by-step building gives the generator's own solid    [command]
  audit.py        the five checks on the session files     [command]
  show.py         print sessions in plain words, for reading                          [command]
  parts.py        read the verified composed parts
```

## How the pieces fit

```
verified composed part (params)
   │ steps.steps_of                       prompt builder (forge/generators/prompts.py)
   ▼                                         │ records each number as it writes it
ordered steps ──────── mentions.align ◄──── mentions (position, text, value, parameter)
   │                         │
   ▼                         ▼
              PLAN: steps, each slot pointing at a mention
                             │
     ┌───────────────────────┴───────────────────────┐
     ▼                                               ▼
 teacher(plan, state) ── target ──►  session record  ◄── executed step (teacher's, or a wrong one)
     ▲                                               │
     └────────────── state ◄── engine.execute ◄──────┘
```

Three ideas worth knowing before reading the code:

1. **Numbers are pointed at, never written.** A step's slot holds `mention:i`, the i-th number in the sentence. `engine.resolve` copies the value. A model that only points cannot invent a dimension.
2. **The engine reuses the generator.** Whether a feature fits is decided by the same functions `forge/generators/families/composed.py` uses to place features, and the program comes from its `assemble`. So the engine cannot drift from the generator, and `prove.py` shows on the kernel that it has not.
3. **Only the teacher's answer is a target.** Wrong steps are executed so the data contains the states that follow a mistake, but what is stored as the right answer is always what the teacher says for that state.

## Commands

Run from the repo root.

| I want to… | Command |
| --- | --- |
| See the pair counts and split sizes | `uv run python -m forge.system1.splits` |
| Generate the long test parts (once) | `uv run python -m forge.system1.long_parts --per-base 1000 --workers 7` |
| Prove the engine against the kernel | `uv run python -m forge.system1.prove --kernel-parts 8000 --workers 7` |
| Make the sessions | `uv run python -m forge.system1.sessions --prompts-per-part 2 --workers 7 --seed 0` |
| Audit them (exit code 0 = all five checks pass) | `uv run python -m forge.system1.audit` |
| Read some sessions | `uv run python -m forge.system1.show --n 5 --noise 0.2` |
| Run this folder's tests | `uv run pytest tests/test_system1_*.py` |

## Files written (all under `data/`, never committed)

```
data/system1/long_parts/composed_<base>.jsonl     the long parts, same row format as data/generated/
data/system1/sessions/<split>/shardNNN.jsonl.gz   sessions; split is train, iid, pairing or long
data/system1/sessions/manifest.json               counts per split, noise histogram, targets per kind,
                                                  held-out pairs, file hashes, speed
data/system1/sessions/audit.json                  the last audit's result
```

`data/generated/` and `data/dataset/` are only read.

## The session file format

One JSON object per line. Each session starts with a header (it has a `plan` key), followed by one line per step:

```
header: session, part_id, family, split, variant, prompt, mentions, noise_level, plan,
        source, license, generator_version, prompt_model
step:   session, t, state {built, used, last, steps},
        target {kind, slots: {name: "mention:i"}},
        executed {kind, slots, was_noise, noise_kind, outcome}
```

`state` is the state BEFORE the step. `sessions.read_sessions(path)` yields `(header, steps)`; `sessions.full_record(header, step)` joins the two into one self-contained record.

## Choices made here that the contract leaves open

Each is a constant with a comment in the code; all are "our choice" and cheap to change (the data is regenerated by one command).

| Choice | Where | Value |
| --- | --- | --- |
| Held-out pairs | `splits.HELD_OUT_PAIRS` | boss+slot, counterbore+polar, blind_hole+boss_pair, pad+hole_pair |
| Train / iid share | `splits.IID_FRACTION` | 5% iid, by a hash of the geometry fingerprint |
| Which prompts a part gets | `mentions.VARIANTS` | request-0, compact-0, then request-1, compact-1, request-2, spec |
| Mix of wrong-step kinds | `noise.wrong_step` | the five kinds tried in a random order; the first that applies is used |
| Step budget | `sessions.BUDGET_FACTOR` | noise may strike during the first 3 x (plan length + 1) steps; after that the teacher's step is always executed, so every session ends at its target |
| "Same as the plan" | `teacher.matches` | same kind and same slot VALUES (not the same mention) |
| Numbers that fill no slot | `mentions.spec_prompt` | the feature numbers in a spec caption ("hole 2 diameter") are mentions with an empty `fills` |
| Rules the engine enforces | `engine.check` | listed in its docstring; ranges that are only taste (a boss is never a "needle") are not enforced |
