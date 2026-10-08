# System 1: steps, state and sessions

The contract for the System 1 model. Code in `forge/system1/` must match this file; if they disagree, one of them is a bug. Version 1.1, 5 Oct 2026 (wording corrected after the first build; behaviour unchanged).

## 1. The idea

The model builds a part one step at a time. At every step it is given the sentence and what has been built so far, and it answers two things: which kind of step comes next, and for each number that step needs, where that number comes from. Code applies the step, and the loop repeats until the model says done.

The design is: a small decision per step, numbers supplied from outside the model, a scripted teacher, and mistakes injected during training.

## 2. Steps

A step has a **kind** and named **slots**. Every slot holds a length in millimetres, an angle in degrees, or a whole-number count.

| Group | Kinds | Slots |
| --- | --- | --- |
| Start | `block` | length, width, height |
| | `cylinder` | diameter, height |
| | `hex` | across_flats, height |
| | `ring` | outer_diameter, inner_diameter, height |
| Edge treatment (at most one, straight after the start) | `corner_radius` (block only) | radius |
| | `top_chamfer` | size |
| | `top_fillet` | radius |
| | `shell` | wall_thickness |
| Feature | `hole` | diameter, x, y |
| | `blind_hole` | diameter, depth, x, y |
| | `counterbore` | hole_diameter, diameter, depth, x, y |
| | `boss` | diameter, height, x, y |
| | `pad` | length, width, height, x, y |
| | `pocket` | length, width, depth, x, y |
| | `slot` | length, width, depth, angle, x, y |
| | `polar` | count, hole_diameter, circle_diameter |
| | `row` | count, hole_diameter, spacing, y |
| | `hole_pair`, `boss_pair`, `pocket_pair` | as the single feature; the twin is mirrored across the YZ plane |
| Control | `undo` | none |
| | `done` | none |

This is the vocabulary of `forge/generators/families/composed.py`. A composed part's parameters map onto steps one to one: `base_*` parameters give the start step and its edge treatment; `<kind>_<n>_<field>` gives feature number n, in build order.

## 3. Where a number comes from

Each slot of a step is filled from one **source**:

| Source | Meaning |
| --- | --- |
| `mention:i` | The i-th number mentioned in the sentence |
| `default` | A rule in code (not used by composed parts in version 1) |
| `table` | A standards table (not used in version 1) |
| `arithmetic` | Worked out from other numbers (not used in version 1) |

A **mention** is one number as it appears in the sentence: its position in the text, its text, and its value. Number words count ("six holes" is a mention with value 6). The generator records, while it writes a prompt, which mention fills which slot; nothing is matched up afterwards. In version 1 every slot is filled by exactly one mention and no mention fills two slots. A number in the sentence that fills no slot (the feature numbers in a spec caption, such as the 2 in "hole 2 diameter") is still a mention, with nothing to fill, so "every mention is used" is not a sign that the part is finished.

## 4. State

What the model is shown at each step, besides the sentence and its mentions:

- **Built items**, in the order they were built: kind, each slot's value, and which mention filled it.
- **Used flags**: for every mention, whether it has been used by a built item.
- **Last outcome**: `none`, `ok`, `rejected` (the engine refused the step and nothing changed) or `undone`.
- **Step count.**

The state is a structured list, like a CAD feature tree. It holds no picture and no measured geometry.

## 5. The engine

- `apply(state, step)` adds the step, or rejects it. A step is rejected when it breaks a rule the generator itself keeps:
  - a feature must lie inside the base, at least 2 mm from the edge and from every other feature;
  - the edge treatment must come straight after the start, and its size must be within the generator's limit;
  - sizes must be positive and fit; a blind cut leaves at least 2 mm of floor;
  - each kind is allowed only on the bases the generator uses it on (a circle of holes only on round bases, a row only on a block, no pocket pair and no shell on a ring);
  - a shelled base takes no cuts; a slot's angle is 0 or 90; a count is a whole number of 2 or more.
- `undo(state)` removes the last built item.
- `build(state)` produces the solid on the CAD kernel.

Proof the engine is right: for any composed part, applying its steps in order and building the result gives the same solid as that part's verified one-go program, to the generators' tolerances (bounding box 0.001 mm, volume one part in a million).

## 6. The teacher

Given the sentence's plan and any state, the teacher names the one correct next step:

1. If anything built is not what the plan has at its position, the answer is `undo`. "Is what the plan has" compares the kind and the slot values, not which mention was used: a 40 by 40 block built from the "other" 40 is correct.
2. Otherwise the answer is the next item of the plan, in order: start, edge treatment if any, then features in the order they are built.
3. When everything is built, the answer is `done`.

Strict order, one correct answer per state. This is the "act on the first item that is not built yet" rule, which matters for long parts. Accepting several orders is left as an experiment.

The edge treatment is built second even when the sentence mentions it last ("...then fillet the top edges 3 mm"), so the model has to look for it anywhere in the sentence.

## 7. Sessions

A session is one (part, prompt) played from an empty state to `done`. Each session draws a noise level from 0, 0, 0.1, 0.2, 0.3. At each step, with that probability, a wrong step is carried out in place of the teacher's:

| Wrong step | Example |
| --- | --- |
| Wrong kind | A boss where a hole was asked for |
| Wrong mention | The hole's diameter taken from the number that was its x |
| Out of order | A later feature built early |
| Repeat | A feature built a second time |
| Random | Any kind with any mentions |

The wrong step is applied (or rejected by the engine), and the session carries on from the state that results. Noise may strike only in the first 3 × (plan length + 1) steps, so every session ends at its target. The mix of wrong-step kinds and that budget are our own choices (`forge/system1/noise.py`, `sessions.py`). **Only the teacher's answer is ever stored as the target.** The wrong step is never a training target.

One record per step:

```
session, part_id, family, split, prompt, mentions, noise_level,
t, state, target {kind, slots: {name: source}}, executed {kind, slots, was_noise, noise_kind, outcome}
```

## 8. Splits

| Split | Contents |
| --- | --- |
| `train` | Parts with at most 5 items after the start (an edge treatment counts as one), with no held-out pairing |
| `iid` | Fresh parts of the same kind |
| `pairing` | Parts containing a pair of feature kinds that never occur together in training |
| `long` | Parts with 6 to 12 items after the start, generated only for testing |
| `human` | Prompts written by people (not built yet) |

A part's split is fixed before any session is made, and every session of a part is in that part's split.

## 9. Audit

- Every session replayed through the engine ends at its target part.
- Every target is what the teacher gives for that state.
- A random sample of final states is built on the kernel and compared with the verified one-go solid.
- No held-out pairing and no long part appears in `train`.
- Every mention index in a target points at a mention whose value equals the slot's value.

## 10. Not in version 1

Structures of several parts, default and table sources, fixed families that are not base-plus-features, and prompts that leave sizes out.
