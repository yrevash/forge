# plans_data/ — plans for Forge-S1 to train on

Forge-S1 reads a **structured plan** (the reader's output for plan-language lines) and builds it one item at a time. This folder makes the body of plans it trains on: **what to build**, each plan verified by our own reader and resolver. How each item becomes FreeCAD commands is another folder's job (`forge/freecad*/`).

**The contract is the plan language (Draft 2), as read by `forge/plan/`.** Every plan here is text in that language, read by `forge/plan/` and resolved by `forge/resolve/`. Nothing in a plan is typed by hand: lines come from generator code, and a line is kept only when the reader accepts it and the resolver builds it.

No model is trained here. The numbers of the build of 6 Oct 2026 are in `data/plans/manifest.json` and `data/plans/audit.json`; the last section of this file quotes them.

```
plans_data/
  config.py        every constant: grids, minimums, the held-out combinations and kinds
  coverage.py      the coverage table: which cells exist, and the cells one plan hits
  session.py       reader + resolver, one line at a time, with a way to take a line back
  record.py        one stored record: the text half, the resolver's half, provenance
  random_gen.py    SOURCE 1: random plans from the whole vocabulary (the resolver is the oracle)
  structures.py    SOURCE 2: a structure from forge/generators/structures, re-said as a plan
  parts.py         SOURCE 3: a composed part (base + features), re-said as a plan
  splits.py        train / iid / combo / long / kinds
  fast.py          one speed-up for generation (snapshots share bodies), switched off in the audit
  featured.py      random stream: one part, features on many faces (kernel)
  topup.py, topup2.py            random streams: arithmetic cells under their floor
  ktopup.py, ktopup2.py, ktopup3.py   random streams: kernel cells under their floor
  table.py         the coverage table with a target and a floor per cell
  kernel_explained.json   kernel disagreements that were looked into, with the reason
  build.py         build raw shards                                                   [command]
  kernel_check.py  build a sample on the CAD kernel and compare                       [command]
  finalize.py      raw shards -> data/plans/<source>/<split>/shardNNN.jsonl.gz + manifest [command]
  audit.py         the six checks; exit code 0 only if all pass                       [command]
  show.py          print plans with replies and resolved parts in plain words         [command]
```

| I want to… | Command |
| --- | --- |
| Read some plans | `uv run python -m forge.plans_data.show --source structures --split train --n 3` |
| Audit the data set | `uv run python -m forge.plans_data.audit` |
| Build more random plans (resumable) | `uv run python -m forge.plans_data.build random --tier arithmetic --shards 0-159 --workers 2` |
| Build more kernel-tier plans | `uv run python -m forge.plans_data.build random --tier kernel --shards 0-999 --workers 2 --until 06:00` |
| Convert every structure kind in the registry | `uv run python -m forge.plans_data.build structures --shards 0-9` |
| Convert the composed parts | `uv run python -m forge.plans_data.build parts` |
| Build a kernel sample | `uv run python -m forge.plans_data.kernel_check structures --rate 0.03` |
| Write the final shards and the manifest | `uv run python -m forge.plans_data.finalize` (shards are replaced by rename; a plan already written stays in its shard file, new plans are appended) |
| Run the tests | `uv run pytest tests/test_plans_data_*.py` |

## 1. The three sources

### Random plans (`random_gen.py`)

The generator knows the words of the language and how big the face it is about to use is. It does **not** decide whether a line works. It writes a candidate line; the reader parses it; the resolver answers `built` or `rejected: ...`. A built line is kept. A rejected candidate is thrown away, except in a plan of the negative slice.

Every choice (which shape and turn, which placement, which alignment form, which repetition form, which feature on which face) is drawn **evenly over the cells of the coverage table**, not by how common the form is in real objects, and six times in ten the least-used option of the shard so far is taken. That is what balances the table. It is also why these plans are clutter, not objects.

Two tiers, because the resolver decides plain boxes and cylinders by arithmetic and asks the CAD kernel about everything else:

| Tier | What is in it | Cost |
| --- | --- | --- |
| arithmetic | boxes and cylinders (standing and lying), every placement on faces, `between`, `on ground`, `above ground`, `gap`, `sunk`, `across`, every alignment, every repetition, sets as targets, groups, `rest`, `same as`, shares, `default`, `undo` | milliseconds per line; a line that would need the kernel is thrown away |
| kernel | all ten shapes in every turn, features on every face, `hollowed out` and inner faces, `inside`, `through`, `around`, `spans` | one sandbox call per line |

Other streams: **featured** (`featured.py`: one part with features on several faces, the cheapest plans that need the kernel), **topup** / **topup2** (`topup.py`, `topup2.py`: short arithmetic plans in which six moves in ten aim at cells still under their floor), **ktopup** / **ktopup2** / **ktopup3** (the same idea for kernel cells: a non-box shape against each face and a box against it, `inside` with inner-face alignments, `through`, `around`, `spans`, `hollowed out` with each open face, features on inner and bottom faces), **single** (one part on the ground, walking through every shape and turn; needs no kernel because a lone part has nothing to be checked against), **long** (30 to 45 lines), **negatives** (below).

Moves that belong together are kept or dropped together: a raised part and what holds it up, a part with a `gap` and the link that joins it, a group and its placement. Nothing is kept that would leave the plan floating at `done`.

**The negative slice.** In a plan marked negative, one to three rejected lines are kept with the resolver's reply: natural rejections of ordinary candidates, and lines written to be rejected (a part put where one already is, a part moved off its face, too many copies for a face, a line that points at a rejected part, a line with a planner's likely slip such as `on the ground` or commas between sizes). A plan that `done` refuses (something left floating) is also negative. These plans are stored under `negatives/` and never mixed with the others.

### Structures as plans (`structures.py`)

Every kind in the registry `forge.generators.structures.KINDS` at the time of the run (never a fixed list) is sampled and each structure is re-said **without coordinates**. For every group of like parts of a step the converter lists candidate lines in a fixed order of preference:

1. one new part per copy of a set (`between front legs and back legs`);
2. a repetition: `at each corner of X`, `at the front corners of X`, `mirrored`, `N evenly spaced` / `spread`, `N around X on circle D`;
3. what the part touches: `between A and B`, `under X` (with `down to ground` and a `rest` height when it reaches the floor), `on top of X`, a side face, `on top of X, across X` for a set, `on ground`;
4. where on that: nothing (centred), `flush with`, a face flush with the opposite face of another part, a stated distance `inside` / `beyond` / `above` / `below` a face. A height the generator's slot marks `stated` or `default` is written `top at height H`; an `arithmetic` one is written against a part.

Each candidate is offered to the reader and the resolver and kept only if the parts it builds are exactly the generator's (same count, same shape, every face within 1e-6 mm). If no single line says a group, it is split (four corners into two pairs, pairs into single parts) and tried again. `above ground` for a part that is not the first is the last resort and is counted. A part nothing can say fails the conversion with the step named: that is a finding about the language, not something to force.

### Single parts as plans (`parts.py`)

The 120,000 composed parts (and the 4,000 long ones) are read step by step (`forge.system1.steps`): the start becomes the base line, an edge treatment and every feature an `on part:` line. Half the parts (by a hash of the id) say positions as `at (x, y)`, half as distances from the edges.

## 2. The coverage table (`coverage.py`)

A **cell** is one thing the executor must have seen, read off the reader's structured line: `shape:cone|pointing down`, `place:behind`, `align:flush|left`, `rep:grid|front-back face`, `set:between set and set`, `size:rest|down to ground`, `feat:slot|front`, and the pairings `pair:place×target|behind|cylinder`, `pair:place×align|...`, `pair:place×rep|...`, `pair:shape×place|...`. Only a line the resolver built counts; a rejected line counts as its reply (`reply:overlaps`).

`coverage.universe()` is the table, defined before any plan was generated. A cell is marked `kernel` when plans that hit it cannot be resolved by arithmetic alone. Pairings the language cannot make are left out with the rule (`coverage.impossible_pair`): a single named corner on a side face; `down to ground` with `on top of`.

Each cell has a **target** (proposed before anything was generated: 3,000 lines for a single cell, 200 for a pairing, a tenth of that for kernel cells, 1,000 for a rejection reply) and a **floor** (what the audit enforces: 2,000 / 200 for arithmetic cells, 30 / 5 for kernel cells, 500 for replies). Both are in `config.py` and both are counted on the **train split**: a form that is only in a test split teaches nothing. The pair cells of the held-out combinations (section 3) are not required cells: their train count must be zero.

## 3. Splits (`splits.py`, fixed before anything was written)

| Split | Rule |
| --- | --- |
| `kinds` | structures of a held-out kind: `stool`, `shelf` (furniture) and `standoffs` (mechanical), held out whole |
| `long` | longer than any plan in training: more than 25 lines (single parts: `forge.system1.splits`' own rule, 6 or more items) |
| `combo` | holds a held-out **combination**; each half still appears alone in training |
| `iid` | 5% of the rest, by a hash of the plan's id |
| `train` | everything else |

Held-out combinations (`config.HELD_OUT_LINE_PAIRS`): `behind` with a prism as target, `under` with a tube as target, `left of` with a cone as target (placement × shape of target); `grid` with `in front of`, `spread` with `right of` (repetition × placement); `offset` with `between` (alignment × placement); and two feature kinds on one part: the four pairs of `forge.system1.splits.HELD_OUT_PAIRS`. Single parts keep the split `forge.system1.splits` gave them (its `pairing` is `combo` here).

## 4. What a record holds

One JSON object per plan in `data/plans/<source>/<split>/shardNNN.jsonl.gz`:

| Field | Holds |
| --- | --- |
| `id`, `source`, `split` | a hash of the text; `random`, `negatives`, `structures`, `parts`; the split |
| `lines` | the plan-language lines |
| `plan` | the reader's structured line for each line (`forge/plan/README.md`, "What a line becomes") |
| `replies` | per line: the reply, the echo, the notes (`rest length = 340`, defaults used), the parts it made, whether the kernel was asked |
| `parts` | every resolved part: name, the plan name and line that made it, shape, sizes, `low` / `high` / `size` of its frame, centre, turn (a 3 by 3 matrix), features, what it touches, whether it stands on the ground |
| `overall`, `complete`, `negative` | the frame of everything; whether every line was built and `done` accepted |
| `cells`, `held_out` | the coverage cells it hits; the held-out combinations it holds |
| `kernel` | present when the plan was built on the kernel: the checks done and any disagreement |
| `license`, `generator_version`, `caption_style`, `caption_model`, `geom_fingerprint`, `provenance` | `forge`; the hash of the code that made it; `plan`; `template` (fixed forms and name lists, filled in by code); a fingerprint of the geometry; language draft, reader and resolver versions, the seed or the structure / part it came from |

## 5. Verification

1. **Reader and resolver, every plan.** A plan is kept only if the reader accepts every line and the resolver builds every line and accepts `done` (negative slice: exactly the stored rejections).
2. **Kernel.** Every kernel-tier plan is built on the kernel when it is made (`forge.resolve.verify`), and each of its rejected lines is checked as `forge.resolve.property_test` does. A hash-chosen sample of the arithmetic plans, the structures and the parts is built by `kernel_check.py`. Single parts are compared with the generator's stored measurements, and a sub-sample solid against solid.
3. **Audit.** `audit.py` reads and resolves every plan again from its text and compares with the stored record.

About single parts: the resolver asks the kernel on every feature line whether the part is still one solid. For the 124,000 parts that question is answered "yes" without the kernel (`parts.TrustStored`), because the generator's own kernel run built that part with those features. The kernel sample is the check on that.

## 6. Choices made here (all "our choice")

| Choice | Where | Value |
| --- | --- | --- |
| Size grids | `config.py` | slabs 120 to 600 by 20; blocks 40 to 240 by 10; small parts 5 to 60; amounts 5 to 50 |
| Plan length | `config.MAX_TRAIN_LINES`, `LONG_LINES` | at most 25 lines outside `long`; `long` 30 to 45 |
| Share of balanced choices | `random_gen.Maker.choose` | the least-used option six times in ten |
| Names | `random_gen.WORDS` | sixty neutral part words (`names: template` in provenance), also letter-number names |
| One base word per plan | `random_gen.Maker.new_name` | a part named `pegs 2` would silently replace copy 2 of the set `pegs` in the resolver |
| Kernel workers | `kernel_check.py` | never more than 2 |

## 7. Numbers of the build of 6 Oct 2026

Source: `data/plans/manifest.json` (written 2026-10-06 14:48:36) and its audit log. Language: the plan language Draft 2. Every record has `license: forge` and a `generator_version`.

271,623 plans (3,790 duplicates dropped).

| Source | train | iid | combo | long | kinds | Total |
| --- | --- | --- | --- | --- | --- | --- |
| `random` | 94,098 | 4,994 | 7,757 | 1,178 | none | 108,027 |
| `negatives` | 4,964 | 244 | 703 | none | none | 5,911 |
| `structures` | 49,719 | 2,565 | none | 684 | 10,114 | 63,082 |
| `parts` | 78,238 | 4,142 | 9,965 | 2,258 | none | 94,603 |

Held-out kinds: `stool`, `shelf`, `standoffs`. Plan length: at most 25 lines outside `long`; `random/long` 30 to 45 lines, `structures/long` 26 to 32, `parts/long` 8 to 14.

The audit FAILED (exit 1), on one check of six:

| Check | Result |
| --- | --- |
| 1 reader | PASS |
| 2 resolver | PASS: 268,023 plans resolved again by arithmetic, rate 1.0; of the 3,600 kernel-tier plans, 60 were resolved again on the kernel, 0 differing |
| 3 kernel | PASS: 7,108 plans built on the kernel (random 4,050; negatives 75; parts 2,163; structures 820), 1 disagreement, explained in `forge/plans_data/kernel_explained.json` |
| 4 coverage | FAIL: 573 of 576 required cells are at their floor in train (396 at their target). Under the floor of 30: `feat:pad|left` 24, `feat:pair of bosses|back` 26, `feat:pair of bosses|left` 26 |
| 5 splits | PASS |
| 6 records | PASS |

Known faults and limits:

- About 1 in 150 random plans with copied groups has overlapping parts that the plan does not declare. It showed up as 6 bad endings in the first structure recording; see `forge/freecad_multi/README.md`, "The resolver fault". Not fixed in the stored plans.
- Geometry fingerprints are shared between train and some test splits (the audit prints this and does not count it as a check): `parts/combo` 4,854; `parts/iid` 2,745; `random/iid` 555; `random/combo` 13; `structures/kinds` 15; `structures/iid` 2; all `long` and `negatives` slices 0. The plan text differs; the solid is the same. Anyone scoring by geometry on these splits must know this.
- The manifest's `kernel_disagreements` list is empty and its generation line says 0 disagreements; the audit, run later, reports 1 (explained). The two were written at different times.
- Part names in random plans come from sixty neutral words in the code (`caption_model: template`). They are names, not training targets for the executor, but they are in the records.
