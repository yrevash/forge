# forge/ — the data layer, the FreeCAD runtimes and the Forge-S1 models

Run everything from the repo root inside the project environment: `uv run python -m forge.<module>`.

```
forge/
  sandbox.py            run CadQuery programs we did not write, isolated (no network, no writes)
  _sandbox_server.py    the helper process the sandbox starts; not used directly
  geometry.py           measure a solid: validity, volume, bounding box, round features, fingerprint
  assembly_geometry.py  measure a multi-part result: per-part sizes, overlaps, touching, groups (structures)
  runs.py               run folders: config, command, commit, metrics (runs/<date>-<name>/)
  generators/           our own part families (see generators/README.md)
  data/                 the data factory (see data/README.md)
  system1/              step-by-step building: step vocabulary, engine, teacher, training sessions (see system1/README.md)
  freecad/              our headless FreeCAD runtime: commands, snapshot, valid commands, recipes per step kind, proofs (see freecad/README.md)
  s1/                   the first Forge-S1 model: encoding of a view into rows of facts, network, set loss, training, evaluation by group, closed-loop driver (see s1/README.md)
  freecad_multi/        structures of many parts in FreeCAD, on top of freecad/ without editing it: bodies, placing, features on a side, proofs, teacher and sessions (see freecad_multi/README.md)
  freecad_ui/           the same parts built through FreeCAD's real buttons and dialogs : proof on 680 parts, trial sessions only (see freecad_ui/README.md)
  plan/                 strict reader for the plan language; no geometry (see plan/README.md)
  resolve/              from a read plan to exact parts, replies, a reference CadQuery build and pictures (see resolve/README.md)
  plans_data/           the plans the executor is trained and tested on: generation, coverage table, splits, audit (see plans_data/README.md)
```

| I want to… | Command |
| --- | --- |
| Run the tests | `uv run pytest` |
| Fetch Zero-to-CAD's program text | `uv run python -m forge.data.zero2cad_ingest` |
| Validate fetched programs | `uv run python -m forge.data.validate --minutes 90` |
| Look at random parts per family | `uv run python -m forge.data.contact_sheet` |
| Generate verified parts | `uv run python -m forge.data.generate --per-family 2000` |
| Generate verified structures (chair, table …) | `uv run python -m forge.generators.structures.generate --per-kind 3000 --workers 2 --resume` |
| Look at random structures per kind | `uv run python -m forge.generators.structures.contact_sheet` |
| Build the dataset | `uv run python -m forge.data.build_dataset --config configs/dataset_v0.yaml` |
| Audit the dataset | `uv run python -m forge.data.audit --version v0` |
| Make and audit the step sessions on our own engine (the earlier, feature-level sessions; the FreeCAD sessions replace them as executor training data) | `uv run python -m forge.system1.sessions --prompts-per-part 2 --workers 7` then `uv run python -m forge.system1.audit` |
| Check plans against the plan language | `uv run python -m forge.plan.check forge/plan/examples --echo` |
| Resolve, build and draw plans | `uv run python -m forge.resolve.build forge/plan/examples/chair.txt --out data/resolve/demo` |
| Check the resolver against the CAD kernel on random plans | `uv run python -m forge.resolve.property_test --plans 3000 --workers 3` |
| Make repair examples (wrong program + feedback + fix) | `uv run python -m forge.data.mistakes --per-part 2 --limit-parts 300 --workers 2` |

| Record single-part FreeCAD sessions, then manifest and audit | see `forge/freecad/README.md`, "Commands to run" |
| Record structure sessions | see `forge/freecad_multi/README.md`, "Commands to run" |
| Generate and audit the plans | `uv run python -m forge.plans_data.audit` (generation commands in `plans_data/README.md`) |

Outputs go to `data/` and `runs/`, which are never committed.

Only macOS is supported by the sandbox so far. Linux needs its own isolation.
