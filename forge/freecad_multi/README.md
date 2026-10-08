# freecad_multi/ — structures of many parts in FreeCAD, command by command

`forge/freecad/` builds ONE part with real history; this folder makes the same runtime build a STRUCTURE: every part in its own body, put in its place, features on the right body, and the result proved equal to the reference.

**Nothing in `forge/freecad/` is edited.** This package imports it and adds to it from the outside. No model is trained or defined here. No FreeCAD window is ever opened.

```
freecad_multi/
  multi_catalogue.py  the 11 added commands; COMMANDS = the base 37 + these          (both Pythons)
  multi_valid.py      valid_commands(snapshot) for several bodies                    (both Pythons)
  client.py           MultiClient: the base client, started on our worker
  recipes.py          a resolved plan (forge.resolve Bodies) -> the ordered commands
  build.py            issue a recipe; compare with the resolver, a stored row, the kernel
  sources.py          where test structures come from (plans, structure rows, random plans)
  prove.py            proof: FreeCAD's structure = the reference                     [command]
  undo_proof.py       proof: undo restores the snapshot; a wrong body leaves nothing behind [command]
  bench.py            speed with 1 and 2 workers, crash recovery, memory             [command]
  show.py             print a structure's build, command by command                  [command]
  save.py             save a structure as an .FCStd that opens visible               [command]
  visible.py          the display settings a background FreeCAD cannot write
  lean.py             the snapshot as a session record stores it
  teacher.py          for any plan and any snapshot: the acceptable next commands
  noise.py            wrong commands, including wrongly placed and wrongly sized bodies
  play.py             one session: the teacher labels every state, noise sometimes acts
  sessions.py         record sessions, resumable, with a manifest                    [command]
  audit.py            audit the session files                                        [command]
  --- added on 6 Oct 2026 for the second recording; not described below ---
  shards.py           how the session files are packed (named in the manifest's contract)
  load.py             what a model may read from a session (named in the manifest's contract)
  manifest.py, oracle.py, plan_facts.py, selection.py, show_session.py
  inside/             runs INSIDE FreeCAD's Python (3.11)
    worker.py         our entry script: loads the base worker's modules, serves a MultiSession
    multi_session.py  MultiSession(Session): the added handlers and the structure snapshot
    part_shapes.py    outline sketch, symmetric pad, cone, sphere, dome, tapered box
    sides.py          "the front of the body as it sits": sketches, faces and edges by side
    structure.py      placement, global solids, who touches whom, the whole structure
```

## How it extends the base runtime without editing it

| Base piece | What we do |
| --- | --- |
| `inside/session.py` `Session` | `MultiSession` subclasses it. `run`, transactions, undo and the rebuild past 20 steps are inherited unchanged. We add one `_name` method per new command and override `_new_sketch`, `_thickness`, `_pad`, `_pocket`, `_item`, `_take_snapshot`. |
| `catalogue.py`, `valid.py` | Not changed. `multi_catalogue.COMMANDS` is a new dict (base entries first). `multi_valid.why_not` answers for the new commands, changes four rules, and hands every other question to the base function. |
| `inside/worker.py` | It starts a `Session` when loaded, so it cannot be imported. Our `inside/worker.py` is a second entry script: it puts the base folders on the path, imports the base modules, and points the two names the base session uses (`catalogue`, `valid`) at ours, in its own process only. |
| `client.py` `FreeCADClient` | `MultiClient` subclasses it; `start` runs the base code with our worker's path. Sandbox, time limit, restart-and-replay, worker recycling are inherited. |
| `inside/shapes.py`, `features.py`, `snapshot.py`, `rules.py` | Imported and called as they are. |

A base worker and a multi worker can run side by side (a test does it).

## The added commands

| Group | Command | What it does |
| --- | --- | --- |
| Bodies | `activate_body(index)` | Make the n-th body made the active one. (`new_body` already exists and makes the new body active.) |
| Drawing | `sketch_outline(points)` | A closed outline through the given points, every side blocked where it is (0 degrees of freedom). For profile bars and wedges. |
| | `pad_symmetric(length)` | Pad the selected sketch half to each side of its plane. |
| | `add_cone(bottom_diameter, top_diameter, height)` | PartDesign's Additive Cone, centred. |
| | `add_sphere(diameter)` | Additive Sphere. |
| | `add_dome(diameter, height)` | Additive Sphere cut off below a latitude: the top slice of a ball. |
| | `add_tapered_box(bottom_length, bottom_depth, top_length, top_depth, height)` | Additive Wedge, stood upright. Also a ridge or a pyramid. |
| Placing | `move_body(x, y, z)` | Put the active body's origin at this point of the structure. |
| | `turn_body(yaw, pitch, roll)` | Turn the active body about its own origin (FreeCAD's placement angles). |
| Sides | `select_side(side)` | A side of the active body AS IT SITS: `top bottom left right front back`. Then `new_sketch(offset)` draws on it and `thickness(value)` hollows the body, open at that side. |
| | `select_side_edges(side, rule)` | The edges `around` that side, or the straight edges `along` its normal. Then `fillet` / `chamfer`. |

Which tool builds which plan shape (all exact in the proof):

| Plan shape | Built with |
| --- | --- |
| box, cylinder, tube, pipe bar | sketch (rectangle / circle / two circles) + `pad_symmetric` |
| prism | `sketch_polygon` with `constrain_across_flats` (even) or `constrain_diameter` (odd) + `pad_symmetric` |
| bar L, T, U, I | `sketch_outline` (the resolver's outline) + `pad_symmetric` |
| wedge | `sketch_outline` (a triangle) on the plane it does not slope in + `pad_symmetric` |
| cone, sphere, dome, tapered box | FreeCAD's additive primitives: exact cone, ball and flat faces. A loft between two circles would give spline surfaces. |

**Every part is drawn with the middle of its frame on the body's origin**, the way the resolver describes a standing shape. That is what makes placing simple.

## Placing: two commands, and why

The resolver states a part as: shape and sizes, `centre` (the middle of its frame) and `matrix` (its turn). Because the shape is drawn centred:

- `move_body` takes `centre` unchanged;
- `turn_body` takes the turn unchanged (the matrix written as yaw, pitch, roll; every quarter turn is whole multiples of 90);
- the two do not interact (a turn about the middle does not move the middle), so they form an any-order group, like the dimensions of a sketch.

One command with six numbers was the alternative. Two were chosen because most parts stand upright and need no turn at all (26,015 of 27,115 parts in the proof), because a wrong position can be undone without touching a right turn, and because each command then has three numbers that are copied straight from one field of the plan. A separate "select the body" step is not needed: the body just made is the active one; `activate_body` is for coming back to an earlier body (a feature line written later in the plan).

A move to the origin or a turn by nothing is not issued.

**Mirror images.** A placement can turn a body but not mirror it. A mirrored copy is drawn as its mirror image (only the L bar and the wedge differ from their mirror image; their outline's x is negated) and then turned by what is left of the matrix.

**Copies: one body per copy.** `legs: ..., at each corner of seat` makes four bodies. The plan can point at each copy later, put a feature on one of them, and the checker needs each copy's own solid. A PartDesign pattern makes one body, and a body must be one solid; an `App::Link` shares its original's shape for ever and cannot be a mirror image.

## Features on a placed body

A feature line names a face as the part sits (`on deck's front: hole ...`). `select_side front` takes the same word; the worker works out which of the body's own directions that is from the body's placement at that moment. The sketch's x and y are the face's first and second direction of the plan language, and its origin is the body's origin dropped onto the face, so the feature's written position is its sketch position. The sketch hangs on a base plane with an offset, never on a face.

For three of the six sides a sketch with those axes looks into the body; features made from such a sketch get FreeCAD's "Reversed", so a pad still grows out of the face and a pocket still cuts into it. A body turned by an odd angle has no sides (`rejected`); its features are built before it is turned.

Through holes are pockets as deep as the material under the face (a number the resolver states); pairs, rows and circles of holes are several circles in one sketch; a counterbored hole is two pockets.

## The snapshot

The base snapshot plus: every body's `index` and `placement`; `structure`; `side` on sketches and edge treatments; `symmetric` on pads; entries for the primitives. A body's `solid` is measured IN THE STRUCTURE (FreeCAD's `Body.Shape` already carries the placement), so its bounding box says where the part is. The top-level `solid` is still the active body's.

A real one, two bodies (a 80 x 40 x 10 plate on the ground, a cone lying on it pointing right; sketch `geometry` and `constraints` cut short):

```json
{
 "items": [
  {"type": "body", "name": "Body", "tip": "Pad", "active": false,
   "solid": {"volume": 31999.999999999993, "bbox": [-40.0, -20.0, 0.0, 40.0, 20.0, 10.0], "size": [80.0, 40.0, 10.0], "solids": 1, "valid": true},
   "valid": true,
   "placement": {"position": [0.0, 0.0, 5.0], "turn": [0.0, 0.0, 0.0], "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]},
   "index": 1},
  {"type": "sketch", "name": "Sketch", "body": "Body", "plane": "XY", "offset": 0.0, "shapes": [{"shape": "rectangle", "first": 0, "x": 0.0, "y": 0.0, "length": 80.0, "width": 40.0, "fixed": ["length", "width", "x", "y"]}], "geometry": ["..."], "constraints": ["..."], "dof": 0, "closed": true, "used_by": "Pad", "valid": true, "side": null},
  {"type": "pad", "name": "Pad", "body": "Body", "sketch": "Sketch", "length": 10.0, "valid": true, "symmetric": true},
  {"type": "body", "name": "Body001", "tip": "Cone", "active": true,
   "solid": {"volume": 3141.592653589793, "bbox": [40.0, -10.0, 0.0, 70.0, 10.0, 20.0], "size": [30.0, 20.0, 20.0], "solids": 1, "valid": true},
   "valid": true,
   "placement": {"position": [55.0, 0.0, 10.0], "turn": [0.0, 90.0, 0.0], "matrix": [[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]},
   "index": 2},
  {"type": "cone", "name": "Cone", "body": "Body001", "bottom_diameter": 20.0, "top_diameter": 0.0, "height": 30.0, "valid": true}
 ],
 "session": {"document": true, "active_body": "Body001", "tip": "Cone", "open_sketch": null, "selection": null, "undo_depth": 15, "finished": false},
 "solid": {"volume": 3141.592653589793, "bbox": [40.0, -10.0, 0.0, 70.0, 10.0, 20.0], "size": [30.0, 20.0, 20.0], "solids": 1, "valid": true},
 "structure": {"bodies": 2, "solids": 2, "volume": 35141.59265358979, "bbox": [-40.0, -20.0, 0.0, 70.0, 20.0, 20.0], "size": [110.0, 40.0, 20.0], "valid": true, "touching": [[1, 2]], "overlapping": []}
}
```

`valid` in the same reply: `new_document, new_body, select_plane, select_face, select_edges, select_tip, undo, done, activate_body, move_body, turn_body, select_side, select_side_edges`.

`structure.touching` lists the pairs of body numbers closer than 1e-6 mm; `structure.overlapping` the pairs sharing more than 1e-6 mm^3, with the shared volume. These are the reference checker's own numbers (`forge/assembly_geometry.py`), so a checker reads contact straight off the snapshot. The kernel is asked only about pairs whose bounding boxes meet, and only when one of the two bodies has changed.

## Valid commands

All base rules still hold, read for the active body. Added and changed:

| Command | Available when |
| --- | --- |
| `activate_body` | there are two bodies or more, no sketch is open (the index is checked when it is carried out) |
| `add_cone`, `add_sphere`, `add_dome`, `add_tapered_box` | the active body has no feature yet |
| `move_body`, `turn_body` | there is an active body and no sketch is open |
| `sketch_outline` | a sketch is open |
| `pad_symmetric` | as `pad` |
| `select_side`, `select_side_edges` | the active body has a solid |
| `new_sketch` (changed) | a plane OR a side is selected |
| `thickness` (changed) | a face OR a side is selected |
| `constrain_*` (changed) | not on an outline: it is fixed as drawn |
| `done` (changed) | no sketch is open and EVERY body holds a valid solid |

## Recipes from a resolved plan

`recipes.structure_items(resolution.bodies)` gives one item per part and one per feature line, in the plan's order; `recipes.script_of(items)` the whole build. `{ ... }` is an any-order group.

```
part            new_body, <shape>, { move_body centre, turn_body turn }, [features that came with the part]
feature line    [activate_body n,] select_side, new_sketch, <shapes with their dimensions>, leave_sketch, pad / pocket
                or  select_side_edges, fillet / chamfer      or  select_side, thickness
```

Every argument records where it comes from (`Cmd.sources`): `size:<slot>`, `body:centre`, `body:matrix`, `standing:outline` (the resolver's outline), `cut:<slot>`, `cut:spot`, `const`, or `derived:...`. The only derived number is a pipe bar's bore (outer diameter minus twice the wall). `uv run python -m forge.freecad_multi.show --row stool` prints a whole recipe.

Not buildable yet (reported by name, never built wrong): a part hollowed with no open face (PartDesign's Thickness always removes a face). None of the 3,228 proof structures has one.

## Undo

Nothing new was needed for the snapshot to come back the same. Read this with `forge/freecad/README.md`, "FreeCAD's own undo is not exact": the proof below compares snapshots, and one single-part session showed that FreeCAD's undo can leave state the snapshot does not show. The first structure recording was made without the fix (exact undo); the second asks for it (`exact_undo: true` in its manifest).

 Each placing command and each primitive runs in one FreeCAD transaction; the active body lives in the session's `meta`, which the base session saves before every command and restores on undo; the rebuild past FreeCAD's 20 remembered steps replays the history, whatever commands it holds.

## The teacher, noise and sessions

`teacher(items, snapshot)` follows the single-part teacher rule for rule. It walks the script against the document's objects (which FreeCAD lists in the order they were made, across all bodies) and reads each body's placement. Added to the single-part rules:

- the next command belongs to a body that is not active: the answer is `activate_body` (a repair, not an undo);
- a body the plan has not reached, a body that is neither where it was made nor where the plan puts it, a body turned neither as made nor as planned, an object in the wrong body, a wrong size: off plan, the answer is `undo`. Every body's placement is looked at on every call, so a misplaced earlier body is found at once.

`noise.py` keeps the six single-part kinds and adds `misplaced_body`, `misturned_body`, `wrong_body` (a wrong `activate_body`) and `extra_body`. A wrongly sized body is `wrong_argument` on a dimension, a pad or a primitive. Noise levels 0, 0, 0.1, 0.2, 0.3, as for single parts.

`sessions.py` records one session per multi-part plan of `data/plans` (generator output with `source`, `license`, `generator_version` and a split). Splits are copied from there, never decided here; the test splits (`iid`, `kinds`, `combo`) are recorded for evaluation, in slices of their own. Each stored plan is resolved again from its text and must give its stored parts. `audit.py` has the four checks of the single-part audit. This paragraph and the two before it describe the code as first written, on the night of 5 to 6 Oct 2026. Sessions have been recorded since then, twice, and the second recording uses more kinds of noise and more slices than are listed here: see "Sessions on disk" below.

## Sessions on disk (state on 6 Oct 2026, 23:34)

There are two recordings. Neither is ready to train on.

### `data/freecad_multi/boxes_v1/` — the first recording (kept, not for training as it is)

Source: `data/freecad_multi/boxes_v1/manifest.json`, `audit_checkpoint1.log` and `README.md` in that folder.

| | |
| --- | --- |
| Sessions, all ended `done` | 14,889 |
| Labelled steps | 2,773,958 |
| Bodies built | 196,483 |
| Slices (sessions) | `structures_train` 12,288; `structures_iid` 1,049; `structures_kinds` 656; `random_train` 32; `random_iid` 688; `random_combo` 176 |
| Wrong commands carried out / steps off plan | 437,392 / 321,546 |
| `undo` share of targets | 11.6% |
| Size | 7,914.9 bytes per step uncompressed |

Its audit FAILED (exit 1): teacher 0 sessions with a difference on 2,773,958 records; replay 600 sessions (112,154 steps), 0 differ; splits 0 bad; endings 6 bad. The log names 5 of the 6. A separate check found all 196,483 bodies at the right size and position.

Why it is not the training set: boxes and cylinders only, no features; 9 of 15 kinds in train (recorded in alphabetical order and stopped part way); the held-out-kinds slice holds only `shelf`; the first mistake mix; every step stores every body whole; part names and the kind are in the records and no loader hides them. Its files are in the old format and the current `audit.py`, `load.py` and `shards.py` do not read them.

### The resolver fault

The 6 bad endings are sessions that ended on a document where two parts overlap and the plan did not declare it (`not declared sunk` in the audit log; overlaps of 392.7 to 11,253.9 mm^3 in the 5 that are named). The teacher and FreeCAD did what the plan said; the plan was wrong. The cause is in the plan builder (copied groups) and the rate is about 1 in 150 random plans with copied groups. For the second recording such plans are kept out before recording: `data/freecad_multi/selection/selection.log` lists 737 excluded plans, 617 of them as `stale_declaration` and 120 as `not_buildable`. The fault itself is not fixed in `data/plans/`.

### `data/freecad_multi/sessions/` — the second recording ("v2"): IN PROGRESS, NOT AUDITED

**Do not train on this and do not quote it as a result.** Recording was still running when this was written. No audit of it has finished: `data/freecad_multi/sessions/audit_v2.log` holds only a Python warning and no check results.

Source for every number below: the recording's manifest as it was on 6 Oct 2026 at 23:34:46 (the file's modification time; the manifest holds no timestamp of its own). `frozen: false`. The counts grow while the recording runs.

| Slice | Plans from | Split | Seed | Shards (done / planned) | Plans tried | Sessions | Steps |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `train` | train | train | 20 | 1,848 / 4,781 | 46,200 | 46,188 | 8,327,021 |
| `train_rare_s1` | train_rare | train | 21 | 86 / 86 | 2,143 | 2,102 | 214,061 |
| `train_rare_s2` | train_rare | train | 22 | 86 / 86 | 2,143 | 2,102 | 219,573 |
| `train_rare_s3` | train_rare | train | 23 | 86 / 86 | 2,143 | 2,102 | 220,994 |
| `iid` (test) | iid | iid | 20 | 48 / 48 | 1,200 | 1,199 | 215,768 |
| `kinds` (test) | kinds | kinds | 20 | 48 / 48 | 1,200 | 1,200 | 154,421 |
| `combo` (test) | combo | combo | 20 | 56 / 56 | 1,400 | 1,389 | 200,118 |
| `long` (test) | long | long | 20 | 12 / 12 | 300 | 300 | 237,596 |
| **Total** | | | | 2,270 files | 56,729 | 56,582 | 9,789,552 |

- All 56,582 sessions ended `done`. 147 plans were dropped because FreeCAD cannot build them (77 "top refused", 55 "done refused (a body is not one valid solid)", 10 "a feature came out invalid", 4 "pair refused", 1 "a body is not one solid"). 4 sessions are flagged `sessions_with_problems`, all in `train`; what the problem is has not been looked at.
- Bodies built: 540,239. Sessions by number of bodies: 1 body 12,552; 2 to 10: 22,363; 11 to 20: 16,005; 21 to 30: 5,362; over 30: 300 (the `long` slice).
- Kinds: `random` plans 35,860 sessions; the furniture and mechanical kinds 357 (`pallet`) to 1,716 (`cabinet`) each. The held-out kinds are `shelf` (393), `standoffs` (374) and `stool` (433): they are only in `kinds`, and `kinds` holds nothing else.
- Shapes: 14,975 sessions (26.5%) have a shape other than box or cylinder; 6,597 (11.7%) have a feature; 33,543 have a turned part.
- Mistake mix `s2`. Noise levels 0, 0, 0.1, 0.2, 0.3 (sessions: 22,460 / 11,388 / 11,389 / 11,345). Wrong commands carried out: 1,917,732; steps off plan: 2,278,941; `undo` is 23.2% of targets. Wrong numbers are 31.9% of the wrong commands. Kinds in the files: `wrong_size` 419,472; `carry_on` 339,754; `random_valid` 190,596; `stray_click` 144,702; `later_item` 127,308; `extra_undo` 110,785; `wrong_body` 96,905; `repeat` 94,414; `misplaced_body` 76,158; `wrong_word` 71,402; `failing_feature` 61,016; `extra_body` 57,478; `early_done` 42,464; `stray_selection` 40,717; `misturned_body` 24,458; `wrong_feature` 15,372; `wrong_side` 4,731.
- Start states (sessions): empty 33,925; partial 8,667; stray body 5,585; an empty document 5,566; opened partial 2,839.
- Two code versions: `2a37166cb249` (7,915 sessions: the four test slices and most of `train_rare_s1` and `_s2`) and `e1a5862418b5` (48,667 sessions: `train`, `train_rare_s3` and the rest). What changed between them is not recorded here. The test slices and most of the train slice were therefore made by different code.
- Size: 1,022.7 bytes per step uncompressed, 46.7 compressed (457,031,512 bytes compressed in all). Worker restarts: 52. Teacher commands FreeCAD refused: 0.
- Which plans were recorded was fixed first (`data/freecad_multi/selection/`, selection `5cd59a983e00`).
- A 400-session trial of this recording (`data/freecad_multi/trial_v2/audit.json`) did NOT pass its audit: checks `content` and `coverage` failed (two held-out halves not in train on their own; no train session of four kinds). All 400 trial sessions replayed without a difference. Whether the full recording has the same two faults is not measured.

What is still to do before this can be training data: finish the recording; freeze it; run the audit to the end and read the result; decide the part cap and the held-out kinds.

## Saved files that open visible

A FreeCAD file is a zip. `Document.xml` holds the objects; `GuiDocument.xml` holds what the window knows (what is shown, the camera). A background FreeCAD writes no `GuiDocument.xml`, so its files open with nothing to see. `visible.py` writes that file itself and appends it as the LAST entry of the zip (FreeCAD reads the zip front to back, and the display file must come after everything `Document.xml` refers to): one entry per object with a `Visibility` flag (shown: each body and its newest feature; hidden: sketches, older features, origins) and an isometric camera that fits the whole structure. The format was read off a file saved by FreeCAD's own window (`EngineBlock.FCStd`, shipped with FreeCAD). The worker also sets each object's own `Visibility` flag in `Document.xml` and gives the bodies the plan's names.

Tried and not usable: `FreeCADGui.setupWithoutGUI()` in FreeCAD 1.1.4's bundled Python creates no view objects and still writes no `GuiDocument.xml`.

**Not checked in a window.** The files were reopened in the background and every body measured the same; the zip order and the XML are tested. Whether the parts show when a person opens the file has to be seen by a person.

## Commands to run

From the repo root.

| I want to… | Command |
| --- | --- |
| See how a structure is built | `uv run python -m forge.freecad_multi.show --plan forge/plan/examples/chair.txt` (add `--snapshot` or `--teacher`) |
| Prove the recipes | `uv run python -m forge.freecad_multi.prove --structures-per-kind 260 --random 1100 --kernel-every 10 --workers 2` |
| Prove undo | `uv run python -m forge.freecad_multi.undo_proof --rows-per-kind 5 --random 50 --workers 2` |
| Measure speed, recovery, memory | `uv run python -m forge.freecad_multi.bench` |
| Save the five example files | `uv run python -m forge.freecad_multi.save --examples` (writes `data/freecad_multi/examples/`) |
| Record sessions (resumable) | `uv run python -m forge.freecad_multi.sessions --workers 2 --max-minutes 60` |
| Audit the sessions | `uv run python -m forge.freecad_multi.audit --replay-sample 60 --workers 2` |
| Run this folder's tests | `uv run pytest tests/test_freecad_multi_*.py` (the FreeCAD ones skip if FreeCAD is not installed) |

## Measured

6 Oct 2026, this Mac, FreeCAD 1.1.4. The laptop was on battery and heavily loaded by other jobs for most of the night, so the long runs were cut short; every number below names its run folder under `runs/`. Speeds were taken under that load and are a floor, not a benchmark.

| What | Result | Run |
| --- | --- | --- |
| Full proof, first pass (before the box-contact shortcut in `inside/structure.py`) | 3,228 structures, 27,115 parts, 302,482 commands: 6 example plans, 55 tuning plans, 2,080 structure rows (260 of each of 8 kinds), 1,087 random plans (13 more had fewer than 2 parts); 378 of them also compared solid for solid with the reference kernel. 2 structures differ, both by the one part described below | `2026-10-06-010129-freecad-multi-prove` |
| Proof with the final code | 6 example plans, 55 tuning plans, 820 structure rows (stopped early), then 297 random plans and 150 stored plans of `data/plans` (25 multi-part plans from each split): 1,328 structures, 18,767 parts, 208,478 commands, 191 kernel comparisons. The same 2 differ, nothing else | `2026-10-06-014535-...` (stopped, resumable) and `2026-10-06-020215-...` |
| The one mismatch | `column`, a tube 60 / 50 by 150 with a 10 mm hole drilled across it, in both `every_form` example plans. FreeCAD's volume is 128802.1346, the reference kernel's 128801.9118: 1.7e-6 apart, tolerance 1e-6. The exact volume (by numerical integration) is 128801.9842; FreeCAD is 1.2e-6 above it, the reference kernel 5.6e-7 below, and the same solid made from Part primitives 1.4e-6 below. The two solids share all their volume. The kernel's volume of a solid bounded by a cylinder-through-cylinder curve is only good to about 1e-6. The tolerance was not loosened | same runs |
| Parts by shape (first pass) | box 24,600; cylinder 2,080; wedge 83; cone 83; tube 70 (2 mismatched: the column, twice); prism 59; sphere 51; dome 50; tapered box 27; bar I 4; bar L, T, U, pipe 2 each | first pass |
| Parts by placement (first pass) | standing 26,015 (2 mismatched); quarter turn 1,006; quarter turn mirror image 42; standing mirror image 36; spanning 6; odd turn about the upright axis 6; tilted 4. A test adds 18 shapes in 8 turns each (132 bodies) | first pass |
| Features built (example and tuning plans) | blind hole 9, boss 3, pair of bosses 2, rounded corners 2, counterbored hole 2, hole 7, pair of holes 4, pad 2, pocket 10, pair of pockets 3, circle of holes 2, row of holes 3, hollowed out 14, slot 3, top chamfer 2, top fillet 4: all in structures that match solid for solid | both passes |
| Undo | 92 structures (example and tuning plans of at most 30 parts, 16 structure rows, random plans), 735 bodies, final code. Undo then issue again after every command: 7,847 checks, all identical both ways (735 `move_body`, 94 `turn_body`, 6 `activate_body`, 53 primitives, 33 `select_side`, 20 pockets, 12 hollowings among them). 291 wrong sequences (1,203 commands: misplaced, misturned, wrong-size and extra bodies, an earlier body moved, a hole in a side; 84 of them overlapped the structure) undone: snapshot identical and no stray object, every time. 46 structures undone to the empty document, 4,429 undos, chains up to 287, past the 20-step cap many times: 0 different (36 differed only in the last bits of a volume) | `2026-10-06-021308-freecad-multi-undo-proof` |
| Speed (one worker, loaded machine, inside the proof) | structure rows: about 10.8 parts and 120 commands per second per worker before the box-contact shortcut; the shortcut cut a 17-part crate from 3.1 s to 1.3 s and a 16-part chair from 2.0 s to 1.65 s. `bench.py` (1 and 2 workers, recovery, memory) is written and was NOT run tonight | first pass; a scratch timing |
| Crashes | 0 worker restarts in every run above (about 560,000 commands). Recovery after a killed worker is covered by one test, not by a long run | the runs; `tests/test_freecad_multi_runtime.py` |
| Memory growth | not measured tonight (`bench.py` does it) | |
| Sessions | none were recorded that night. Two recordings were made later on 6 Oct 2026: see "Sessions on disk" | |

To continue at full scale later:

```
uv run python -m forge.freecad_multi.prove --resume runs/<run> --structures-per-kind 260 --random 1100 --kernel-every 10 --workers 2
uv run python -m forge.freecad_multi.prove --sources plans --plans-per-split 500 --kernel-every 10 --workers 2
uv run python -m forge.freecad_multi.undo_proof --rows-per-kind 5 --random 50 --workers 2
uv run python -m forge.freecad_multi.bench
uv run python -m forge.freecad_multi.sessions --workers 2 --max-minutes 120      # run again to go on
uv run python -m forge.freecad_multi.audit --replay-sample 60 --workers 2
```

## FreeCAD behaviours that got in the way

| Behaviour | What we do |
| --- | --- |
| The Thickness tool fails on a slice of a ball that has been moved from where it was made (kernel: `BRep_API: command not done`). The same slice unmoved works; cones and boxes work moved. | A dome is hollowed with a Subtractive Sphere of the same middle (`part_shapes.hollow_dome`), which is the same solid. The snapshot describes it as the `thickness` it stands in for. |
| Thickness with join type "Intersection" (the base session's choice) fails on a box whose top edges were rounded first; "Arc" works. | Hollowing from a side uses "Arc". Walls that grow inwards leave no gaps on a convex part, so plain boxes come out the same. |
| Thickness fails when the wall is thicker than a rounded edge's radius. | Nothing: the feature is left invalid, as in FreeCAD's window; the remedy is `undo`. |
| A body's placement can turn but not mirror. | Mirror images are drawn mirrored (above). |
| The kernel's volume of a solid bounded by a cylinder-through-cylinder curve is good to about 1e-6 relative, and differs between two ways of building the same solid. | Reported as the proof's one mismatch; the tolerance was not loosened. |
| A background FreeCAD stores no display settings; `setupWithoutGUI()` does not help. | `visible.py`. |
| `body.newObject(...)` makes the new feature the body's tip at once, before it has a shape. | Read `body.Tip` before creating a feature that needs the old tip's shape. |
| The base session describes every object again after each command. | `MultiSession` keeps the entries of bodies a command cannot have touched. |

## Not here yet

- Hollowing with no open face.
- FreeCAD's `Hole` feature on a side (through holes and counterbores on sides are pockets).
- Patterns and mirrors of features on a side (the recipes use several shapes in one sketch).
- An audited set of structure sessions. Two recordings exist (see "Sessions on disk"); the first is not fit for training as it is and the second is still being recorded and is NOT audited.
- The speed, recovery and memory runs of `bench.py`.
- The real interface (see `forge/freecad_ui/`).
