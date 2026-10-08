# data/ — the data factory

Built so far:

| Step | Module | Reads | Writes |
| --- | --- | --- | --- |
| 1. Ingest | `zero2cad_ingest.py` | Zero-to-CAD 1M on Hugging Face, train split, program text only | `data/zero2cad/code/<shard>.parquet` |
| 2–3. Execute and validate | `validate.py` | the ingested shards | `data/zero2cad/validated/<shard>.jsonl` |
| 9. Our generators | `generate.py` | `forge/generators/` | `data/generated/<family>.jsonl` |
| Look before scaling | `contact_sheet.py` | `forge/generators/` | `data/contact_sheets/<family>.png` |
| 7–8, 10. Captions, caption checks, splits, leakage, repair pairs | `build_dataset.py` | `data/generated/`, `configs/dataset_v0.yaml`, optionally `data/mistakes/` | `data/dataset/<version>/` |
| Independent check | `audit.py` | `data/dataset/<version>/` | `audit.json`, exit code |
| Repair data (learning from mistakes) | `mistakes.py` | `data/generated/` (finished families only) | `data/mistakes/<family>.jsonl`, `<family>.rejects.jsonl` |

Not built yet, all on the Zero-to-CAD side: canonicalize, deduplicate, diversity selection, captioning by an open-weight model. Zero-to-CAD programs are therefore not in any dataset version yet.

## The dataset on disk

```
data/dataset/v0/
  parts.parquet            one row per part: params, program, measurements, captions, split
  train.parquet            one row per (prompt, program) pair, with full provenance
  val.parquet
  test_id.parquet          same families and sizes as training, unseen parts
  test_ood_params.parquet  held-out standard sizes and a held-out band of each family's first dimension
  test_ood_family.parquet  whole families never seen in training
  <split>_repair.parquet   only if data/mistakes/ exists: one row per (prompt, wrong program,
                           feedback) -> correct program, in the split of its part
  manifest.json            counts, config, file hashes
  audit.json               result of the last audit
```

Read it with `pyarrow.parquet.read_table("data/dataset/v0/train.parquet")`.

## What each job guarantees

- **Ingest** never touches Zero-to-CAD's validation or test splits, pins the dataset revision, and stamps every row with `source`, `license`, `source_revision` and `source_shard`.
- **Validate** runs every program in the sandbox with a 30-second limit. A program is accepted only if it builds one valid solid, has a sane bounding box and exports to STEP. Rejected programs are kept with their reason.
- **Generate** writes a part only if the kernel's measurement matches the family's arithmetic. Any failure stops the job with a non-zero exit.

- **Build** checks every caption number against its part, assigns splits per part by geometry (so identical shapes never straddle splits), and removes from train and val any shape or program that also appears in a test split.
- **Build, with `max_parts_per_family` in the config** first cuts every family over its cap down to a fixed subset (chosen by a hash of the part id and the seed, never the first lines of the file). Parts with a standard designation are never cut. `forge.data.mistakes --limit-parts` picks parts in the same order, so with the same seed and a limit at or below the cap, every part that gets repairs is a part the build keeps.
- **Audit** trusts none of that: it re-reads the files, re-checks provenance, uniqueness, leakage, hold-outs and caption numbers, and re-executes a random sample of programs in the sandbox.

Ingest and validate can be stopped and re-run; finished shards are skipped.

## Repair data: `mistakes.py`

A model can only use "here is what was wrong, try again" if it was trained on exactly that input. This step makes such examples from verified parts, by code:

```
uv run python -m forge.data.mistakes --per-part 2 --limit-parts 300 --workers 2
```

1. **Mutate.** One mistake is applied to the program text. Operators, by group:

   | Group | Operators |
   | --- | --- |
   | `wrong_number` | `swap_digits`, `off_by_step`, `times_ten`, `other_parameter_value`, `diameter_radius_value`, `diameter_used_as_radius` |
   | `missing_feature` | `drop_feature`, `drop_boolean`, `drop_list_item` |
   | `wrong_count` | `wrong_count` |
   | `wrong_operation` | `swap_boolean`, `extrude_wrong_parameter` |
   | `position` | `flip_parameter_sign`, `flip_offset_sign`, `drop_offset`, `drop_centered`, `wrong_plane` |
   | `does_not_run` | `misspell_method`, `undefined_name`, `missing_bracket`, `no_result` |

   The same part and `--seed` always give the same mutations.
2. **Execute.** The mutated program runs in the sandbox. It is kept only if it fails, or builds a solid that measures differently from the correct part (same tolerances as the generators). Everything else goes to `<family>.rejects.jsonl` with a reason: `same_solid`, `failed_but_should_build`, `built_but_should_fail`, `timeout`, `crash`, `verifier_flags_correct_part`, `original_not_reproduced`.
3. **Feedback.** `feedback(stated, outcome, code)` writes the verifier message from three inputs only: the dimensions the prompt states, the sandbox outcome of the wrong program, and the wrong program's own text. It never sees the correct program, so the harness can produce the same sentences at inference time. Two kinds of sentence:
   - measured: `hole diameter 7.5 is stated, but no round face of diameter 7.5 was measured.`
   - read from the program's own `name = value` lines, always starting with `Program text:`: ``Program text: `thickness = 30.0`, but the prompt states thickness 3.``
4. **Rows.** `id`, `part_id`, `family`, `mistake`, `mistake_group`, `wrong_code`, `feedback`, `detected`, `correct_code`, `wrong_outcome`, `correct_measure`, `params`, `designation`, `source`, `license`, `generator_version`, `mistake_version`, `feedback_model`, `geom_fingerprint`.

`detected: false` means the program is really wrong but the feedback has nothing to say (a wrong wall thickness cannot be seen in a bounding box). Those rows are kept as a record of what the verifier misses; they are not used for repair training.

Rows hold no prompt and no split. The dataset build attaches both with `repair_pairs(repair_row, prompts, split)`: one training row per prompt, with the feedback rewritten from only what that prompt states, and the split inherited from the part.

**In the build.** `build_dataset.py` reads `data/mistakes/<family>.jsonl` for every family that has a `.done` marker there, and writes `<split>_repair.parquet`. With no such data it writes exactly what it wrote before. Safeguards:

- a repair whose `generator_version` or correct program no longer matches the part is dropped (stale);
- a repair goes to the split of its part, and in train and val a repair whose wrong program is itself a test part's program is dropped;
- one repair is paired with at most `repairs.prompts_per_repair` prompts (config, default 2).

Counts are in `manifest.json` under `repairs` and in the printed summary. A trial that must not enter the dataset writes elsewhere: `--out-dir data/mistakes_trial`.

**In the audit.** When repair files exist, five more checks run: (8) repair files and manifest agree, (9) complete provenance, known mistake, unique ids, (10) every repair pair is in its part's split, (11) no wrong or correct program in the train/val repair files is a test part's program, (12) a random sample of wrong programs is run again in the sandbox and the feedback written again must equal the stored text (`--reexecute-repairs`, default 500).

## Row format for generated parts

`id`, `source` (`gen:<family>`), `license`, `generator_version`, `dimension_table`, `family`, `designation`, `params`, `code`, `measured`, `geom_fingerprint`, and two fields later steps fill in: `split` and `captions`.
