# freecad/ — our FreeCAD runtime, at command level

Forge-S1 will read a plan and the live state of a FreeCAD session and pick the next command, one at a time. This folder is everything around that decision: a headless FreeCAD we can talk to, the commands it understands, the snapshot it returns, which commands are valid, how each plan step is built, and the proof that the result is the right solid.

It also holds the teacher (the correct next commands for any plan and any session) and the recorded sessions the model will be trained on.

No model is trained or defined here. No FreeCAD window is ever opened.

```
freecad/
  locate.py       find FreeCAD's own Python interpreter
  client.py       FreeCADClient: start a worker, send one command, time limit, restart-and-replay
  catalogue.py    the 37 commands and their arguments (plain data)
  valid.py        valid_commands(snapshot): which commands are available right now
  recipes.py      for each of the 20 step kinds of forge/system1/steps.py: its commands and arguments
  build.py        build a part step by step and compare with the reference engine
  probes.py       points that must be inside or outside the solid (is each feature in place?)
  parts.py        read verified composed parts from data/ (read-only)
  describe.py     a snapshot in three plain lines
  prove.py        proof: FreeCAD's solid = the stored solid, per step kind        [command]
  undo_proof.py   proof: undo and wrong commands leave nothing behind, also in what the snapshot does not show (with exact undo) [command]
  histories.py    the noise-like wrong chains that undo_proof.py part E carries out
  bench.py        speed with 1 and 3 workers, crash recovery, memory              [command]
  show.py         print one part's build, command by command                      [command]
  save.py         save one part as an editable .FCStd document                    [command]
  --- stage E: teacher and sessions ---
  teacher.py      teacher(plan, snapshot): the SET of acceptable next commands
  lean.py         the lean snapshot: what a session record keeps
  noise.py        wrong commands, carried out on purpose
  starts.py       start states: a session does not always begin empty
  play.py         play one session in FreeCAD (teacher labels, noise sometimes acts)
  shards.py       where the session files are, which slices exist, how to read them
  sessions.py     one session per part and slice -> data/freecad/sessions/    [command]
  manifest.py     counts over all shards; which code made which shard; long_pure [command]
  oracle.py       the right next commands from a session's FINAL document (no teacher code)
  audit.py        check the session files; exit 0 only if all six checks pass  [command]
  load.py         the ONLY way a model reads sessions: a whitelist of fields
  baselines.py    what a predictor reaches without reading the plan (the floor) [command]
  teacher_proof.py  proof: any member of the teacher's set, at every step, builds the part [command]
  show_session.py print recorded sessions in plain words                       [command]
  inside/         the code that runs INSIDE FreeCAD's Python (3.11), never in ours
    worker.py     the request loop (one JSON object per line)
    session.py    the session: document, selection, open sketch, undo
    shapes.py     sketch shapes and their constraints (rectangle, circle, polygon, slot)
    features.py   pad, pocket, hole, revolution, fillet, chamfer, thickness, patterns, mirror
    rules.py      pick edges and faces by a geometric rule
    snapshot.py   describe the document as plain data; measure the solid
```

## Two Pythons

FreeCAD ships its own Python 3.11 with the `FreeCAD`, `Part` and `Sketcher` modules built for it. Our code is Python 3.12 and cannot import them. So `client.py` starts FreeCAD's interpreter as a separate process and the two exchange one line of JSON per request:

```
our Python 3.12                              FreeCAD's Python 3.11 (no window)
FreeCADClient.command("pad", length=10)  ->  inside/worker.py -> Session.run -> FreeCAD
            reply  <-  {"status": "ok", "snapshot": {...}, "valid": [...], "ms": 6.4}
```

- **Isolation.** The worker runs in the macOS sandbox (no network; writes only to one temp folder), and FreeCAD is told that folder is its home. It never touches your FreeCAD settings or a FreeCAD window you have open.
- **Time limit.** 30 s per request by default.
- **Restart and continue.** The client remembers the commands that succeeded since `new_document`. If the worker dies or goes silent, it starts a new one, replays them, checks the snapshot is the same as before, and tries the failed command once more.
- **Exact undo.** The client tells the worker to switch exact undo on (see "Undo"). `FreeCADClient(exact_undo=False)` gives the runtime as it was before 6 Oct 2026; the audit uses it to replay sessions recorded then.
- `catalogue.py` and `valid.py` are read by both Pythons, so they import nothing but the standard library.

## Commands

A command is one small thing a person does in the PartDesign or Sketcher workbench. Every number is an argument; the plan supplies it.

| Group | Commands |
| --- | --- |
| Document | `new_document`, `new_body` |
| Select | `select_plane(plane)`, `select_face(rule)`, `select_edges(rule)`, `select_tip`, `select_sketch`, `clear_selection` |
| Sketch | `new_sketch(offset)`, `edit_sketch`, `sketch_rectangle`, `sketch_circle`, `sketch_polygon(sides)`, `sketch_slot`, `leave_sketch` |
| Dimensions (of the shape drawn last) | `constrain_length`, `constrain_width`, `constrain_diameter`, `constrain_across_flats`, `constrain_angle`, `constrain_x`, `constrain_y` (each takes `value`) |
| Features (use the selected sketch) | `pad(length)`, `pocket(depth)`, `pocket_through_all`, `revolution(angle, axis)`, `hole_through(diameter)`, `hole_blind(diameter, depth)`, `hole_counterbore(diameter, counterbore_diameter, counterbore_depth)` |
| Treatments (use the selected edges or face) | `fillet(radius)`, `chamfer(size)`, `thickness(value)` |
| Copies (use the selected tip) | `polar_pattern(count, axis)`, `linear_pattern(count, spacing, direction)`, `mirror(plane)` |
| Control | `undo`, `done` |

Planes are `XY`, `XZ`, `YZ`. Edge rules are `vertical`, `top_face`, `bottom_face`, `all`. Face rules are `top`, `bottom`.

Three choices that keep builds robust:

1. **Sketches hang on base planes, never on faces.** A hole in the top of a 20 mm block is sketched on the XY plane with offset 20. FreeCAD's face names (`Face6`) change when the solid changes; the base planes never do.
2. **Edges and faces are chosen by rule.** "The vertical edges" is turned into FreeCAD's edge names at the moment it is used. The names never appear in the snapshot.
3. **A dimension command redraws the shape before constraining it.** FreeCAD's sketch solver searches from where the geometry is now, and can land on a wrong answer when a shape is stretched a long way (measured on slots: about a third came back wrong while the solver reported success). So `shapes.constrain` first moves the geometry to the answer, then adds the constraint, then lets the solver confirm it and checks that it did.

A reply's `status` is `ok`, `rejected` (not valid now, bad arguments, or nothing fits the rule), `error` (FreeCAD raised), or `crash` / `timeout` (the worker was replaced). After anything but `ok` the session is unchanged. `ok` means "FreeCAD did it", not "it is a good part": a fillet too large for the solid is carried out and leaves an item with `"valid": false`, as FreeCAD's window would show it with a red mark. The remedy is `undo`.

## The snapshot

What the model will see after every command: three plain parts.

- `items`: one entry per object in the document, in the order they were made. Each has `type`, `name`, `valid`, and the fields of its type.
- `session`: active body, tip, open sketch, selection, how many commands can be undone, finished.
- `solid`: the measured solid of the active body: volume, bounding box, number of solids, validity.

A real one (a 80 x 40 x 10 block, after `select_edges rule=vertical`; the sketch's long `geometry` and `constraints` lists are cut short here):

```json
{
 "items": [
  {"type": "body", "name": "Body", "tip": "Pad", "active": true, "solid": {"volume": 31999.999999999993, "bbox": [-40.0, -20.0, 0.0, 40.0, 20.0, 10.0], "size": [80.0, 40.0, 10.0], "solids": 1, "valid": true}, "valid": true},
  {"type": "sketch", "name": "Sketch", "body": "Body", "plane": "XY", "offset": 0.0, "shapes": [{"shape": "rectangle", "first": 0, "x": 0.0, "y": 0.0, "length": 80.0, "width": 40.0, "fixed": ["length", "width", "x", "y"]}], "geometry": [{"kind": "line", "from": [-40.0, -20.0], "to": [40.0, -20.0], "construction": false}, "... 4 more"], "constraints": [{"kind": "DistanceX", "on": [0, 0], "name": "length", "value": 80.0}, {"kind": "DistanceY", "on": [1, 1], "name": "width", "value": 40.0}, "... 11 more"], "dof": 0, "closed": true, "used_by": "Pad", "valid": true},
  {"type": "pad", "name": "Pad", "body": "Body", "sketch": "Sketch", "length": 10.0, "valid": true}
 ],
 "session": {"document": true, "active_body": "Body", "tip": "Pad", "open_sketch": null, "selection": {"type": "edges", "rule": "vertical", "of": "Pad", "count": 4}, "undo_depth": 11, "finished": false},
 "solid": {"volume": 31999.999999999993, "bbox": [-40.0, -20.0, 0.0, 40.0, 20.0, 10.0], "size": [80.0, 40.0, 10.0], "solids": 1, "valid": true}
}
```

`valid` in the same reply: `new_document, new_body, select_plane, select_face, select_edges, select_tip, clear_selection, fillet, chamfer, undo, done`

| Item type | Fields besides `type`, `name`, `body`, `valid` |
| --- | --- |
| `body` | `tip`, `active`, `solid` |
| `sketch` | `plane`, `offset`, `shapes` (what was drawn, with the dimensions `fixed` so far), `geometry`, `constraints`, `dof` (degrees of freedom left; 0 = fully constrained), `closed`, `used_by` |
| `pad` | `sketch`, `length` |
| `pocket` | `sketch`, `through_all`, `depth` |
| `hole` | `sketch`, `diameter`, `through_all`, `depth`, `counterbore_diameter`, `counterbore_depth` |
| `revolution` | `sketch`, `angle`, `axis` |
| `fillet`, `chamfer` | `on`, `edges` (how many), `rule` (the rule that chose the edges), `radius` or `size` |
| `thickness` | `on`, `faces`, `rule`, `value` |
| `polar_pattern` | `of`, `count`, `angle`, `axis` |
| `linear_pattern` | `of`, `count`, `spacing`, `direction` |
| `mirror` | `of`, `plane` |

## Valid commands

`valid.valid_commands(snapshot)` lists the commands available in a state; `valid.why_not(snapshot, command)` gives the reason one is not. The rules look only at the snapshot. The worker runs the same function before every command and refuses the rest, and every reply carries the list as `valid`.

| Command | Available when |
| --- | --- |
| `new_document` | always |
| `undo` | something can be undone |
| `new_body`, `select_plane` | a document (and for `select_plane` a body) exists; no sketch is open |
| `select_face`, `select_edges` | there is a solid |
| `select_tip` | the body has a feature |
| `select_sketch` | a sketch exists that no feature uses |
| `new_sketch` | a plane is selected |
| `sketch_*`, `leave_sketch` | a sketch is open (and then nothing else is, as in the Sketcher workbench) |
| `constrain_*` | a sketch is open, the last shape has that dimension, and it is not fixed yet |
| `pad`, `revolution` | a closed, unused sketch is selected |
| `pocket`, `pocket_through_all` | the same, and there is a solid to cut |
| `hole_*` | the same, and the sketch has a circle |
| `fillet`, `chamfer` | edges are selected |
| `thickness` | a face is selected |
| `polar_pattern`, `linear_pattern`, `mirror` | the tip is selected and it is a pad, pocket, hole or revolution |
| `done` | no sketch is open and the solid is valid |

## Recipes

`recipes.recipe(step, context)` gives the commands for one step. `{ ... }` marks an any-order group: those commands may come in any order and the result is the same (`recipes.AnyOrder`; a teacher must accept all of them). Everything else has one order. `H` is the base's height; `floor` is `H`, or the wall thickness on a shelled base.

| Step kind | Commands |
| --- | --- |
| `block` | `new_document`, `new_body`, `select_plane XY`, `new_sketch 0`, `sketch_rectangle`, { `constrain_length`, `constrain_width`, `constrain_x 0`, `constrain_y 0` }, `leave_sketch`, `pad height` |
| `cylinder` | as `block` with `sketch_circle`, { `constrain_diameter`, `constrain_x 0`, `constrain_y 0` } |
| `hex` | as `block` with `sketch_polygon 6`, { `constrain_across_flats`, `constrain_angle 90`, `constrain_x 0`, `constrain_y 0` } |
| `ring` | as `cylinder`, with two circles in the one sketch: `sketch_circle`, { outer diameter, x 0, y 0 }, `sketch_circle`, { inner diameter, x 0, y 0 } |
| `corner_radius` | `select_edges vertical`, `fillet radius` |
| `top_chamfer` | `select_edges top_face`, `chamfer size` |
| `top_fillet` | `select_edges top_face`, `fillet radius` |
| `shell` | `select_face top`, `thickness wall_thickness` |
| `hole` | `select_plane XY`, `new_sketch H`, `sketch_circle`, { `constrain_diameter`, `constrain_x`, `constrain_y` }, `leave_sketch`, `pocket_through_all` |
| `blind_hole` | as `hole`, ending `pocket depth` |
| `counterbore` | as `hole` (the circle gets the hole diameter), ending `hole_counterbore hole_diameter diameter depth` |
| `boss` | as `hole` but `new_sketch floor`, ending `pad height` |
| `pad` | `select_plane XY`, `new_sketch floor`, `sketch_rectangle`, { `constrain_length`, `constrain_width`, `constrain_x`, `constrain_y` }, `leave_sketch`, `pad height` |
| `pocket` | as `pad` but `new_sketch H`, ending `pocket depth` |
| `slot` | `select_plane XY`, `new_sketch H`, `sketch_slot`, { `constrain_length`, `constrain_width`, `constrain_angle`, `constrain_x`, `constrain_y` }, `leave_sketch`, `pocket depth` |
| `polar` | as `hole` with `constrain_x circle_diameter / 2` and `constrain_y 0`, then `select_tip`, `polar_pattern count Z` |
| `row` | as `hole` with `constrain_x -(count - 1) * spacing / 2`, then `select_tip`, `linear_pattern count spacing X` |
| `hole_pair`, `boss_pair`, `pocket_pair` | the single feature, then `select_tip`, `mirror YZ` |

Every argument records where it comes from (`Cmd.sources`): the step's own slot, the base's height, the floor, a constant of the recipe, or arithmetic. Only `polar` and `row` need arithmetic (one number each, shown above).

## Runtime changes made for stage E (5 and 6 Oct 2026)

- Exact undo, `client.rebuild()`, `client.tolerances()` and `teacher.Watch` (see "Undo"). The worker has three new requests: `configure`, `rebuild`, `tolerances`.
- Fillet, chamfer and thickness items carry `rule`, so the snapshot says which edges were chosen, not only how many.
- `client.reset()` closes the document (a session can start with none). `client.forget_undo()` empties the undo history, as after opening a file. Neither is a command the model can issue.
- Bug fixed: a command refused before it changed anything (a slot wider than long, a 2-sided polygon) left FreeCAD's transaction name pending, and the next `undo` in an open sketch failed with "undo stacks disagree". The session now closes the pending transaction. Found by the noisy sessions; `prove.py` and `undo_proof.py` were run again afterwards and pass.

## Undo

Each command that edits the document runs inside one FreeCAD transaction, and `undo` rolls one back with `doc.undo()`. What FreeCAD does not have without its window (the selection, the open sketch) is kept in the session and restored alongside.

A headless FreeCAD document remembers only its last 20 transactions and offers no way to raise that. The session therefore also keeps the list of commands in effect; an undo that reaches further back rebuilds the document from that list without its last command. Slower, same result.

### FreeCAD's own undo is not exact, and what we do about it

Until 6 Oct 2026 this file said "undo is exact". It was exact in everything the snapshot shows, and the proof checked only that. It is not exact in what the snapshot does not show.

What happens: `doc.undo()` takes a feature away again. It does not take back what COMPUTING that feature did to the shapes it was built from. FreeCAD's geometry kernel may change a shape it is given, in place.

The case that showed it (session `8faf9b4de268c2bd`, part `bf9cb4a07153bd5d`, first recording):

1. A block with one mirrored pocket is built. Every shape has the kernel's normal tolerance, 0.0000001 mm.
2. A wrong sketch gets the same rectangle twice, exactly on top of itself, and is padded by 14 mm. FreeCAD carries it out and marks the pad valid. Its boolean operation raises the tolerance of a vertex of the solid underneath (the shape of `Mirrored`) to 24 mm.
3. The teacher undoes all of it. The pad is gone, the snapshot is exactly what it was, and the shape of `Mirrored` still has a vertex with 24 mm tolerance.
4. Later the plan's second pocket is built on that shape and mirrored. The mirror fails ("Null shape"), every time. The teacher says `undo`, then `mirror` again: 150 times, until the step limit ended the session.

The shortest history that does it is in `tests/test_freecad_sessions.py` (`BEFORE_THE_STRAY_PAD`, `STRAY_PAD`): on a bare block the same stray pad does no harm.

The fix (`inside/session.py`, `exact_undo`): after undoing a feature, an edge treatment or a pattern, every object is marked as changed and worked out again from its own numbers (`_refresh`). The shapes that come out are new, so nothing a removed feature did is left. It costs one recompute of the part (25 ms for the part above) for about one undo in six.

Three more things came with it:

- `client.rebuild()` builds the document again from the commands in effect. The snapshot stays the same; whatever it does not show is gone. For a harness whose build is stuck.
- `client.tolerances()` gives the largest tolerance in every object's shape. `undo_proof.py` part E uses it to look for this kind of hidden state.
- `teacher.Watch` is the exit for anyone who drives a session: the second time the teacher's own command fails at the same place on an on-plan state, the session is `stuck` and the driver stops. `play.py` uses it.

Exact undo is switched on by the client for this folder's worker. `forge/freecad_multi` uses the same `Session` class through its own worker and has to ask for it (`FreeCADClient(exact_undo=True)`). Its first recording (`data/freecad_multi/boxes_v1/`) was being made while this was written, on the runtime without exact undo. Its second recording asks for it: both configs in its manifest hold `exact_undo: true`.

What this does and does not claim. With exact undo, no hidden state was found in 3,013 wrong chains (table below). That is a measurement on a sample, not a proof for every history. The first-mix slices (239,471 sessions) were recorded WITHOUT exact undo; one of them is the looping session above and is excluded. The audit replays each session on the runtime it was recorded with.

How common it is, measured (`undo_proof.py --histories`, 580 parts, 3,013 wrong chains, 24,150 commands undone):

| Runtime | Snapshot after the undos | Chains that left hidden state | Parts with hidden state |
| --- | --- | --- | --- |
| before the fix (`--exact-undo off`) | the same, 3,013 of 3,013 | 21 (0.7%) | 20 of 580 (3.4%) |
| with exact undo | the same in 3,013 of 3,013 (two of three runs; see below) | 0 | 0 |

All 21 came from a chain that made a pad, pocket or hole, most of them with a pattern, mirror or chamfer on top. None of the 20 parts failed to build afterwards: the damage is usually harmless. In the first recording it stopped one session in 239,471 (found by scanning every step for a teacher command that led off plan).

One of the three runs with exact undo reported 2 chains of 3,013 whose snapshot differed after the undos. FreeCAD's worker was replaced twice in that run (FreeCAD crashed or hung on a random command) and both parts pass when run again alone, so this looks like the worker replacement and not like undo, but it is not explained. `undo_proof.py` now prints which fields differ and whether the worker was replaced during the chain.

## The teacher

`teacher(plan, snapshot)` returns the set of commands a careful person could issue next. It is a pure function: it reads the snapshot alone, so it can label any state, however the session got there. The plan is `forge.system1.steps.steps_of(family, params)`.

How it reads the session: the plan is laid out as one long script (the recipe of item 1, then item 2, ...). Every command that edits the document leaves a trace the snapshot shows (a sketch at a height, a shape, a `fixed` dimension with its value, a pad of a length). The teacher ticks the script off against the snapshot's items in order.

| Situation | Answer |
| --- | --- |
| Everything so far is on plan | The first script command with no trace yet. Inside a sketch: every dimension of the newest shape not given yet (any order) |
| That command needs something selected or open first | The one command that makes the session ready: `select_plane`, `select_edges`, `select_face`, `select_tip`; `select_sketch` then `edit_sketch` for an unfinished sketch that was closed; `leave_sketch` for a complete sketch that is open. A wrong selection is replaced, not undone |
| Anything is not what the plan has at its place: a wrong number, a wrong or extra object, a feature that failed to build, a second body, `done` too early | `undo`, again and again until what is left is on plan |
| Off plan and nothing can be undone (a document opened from a file) | `new_document` |
| The plan is complete | `clear_selection` if something is selected, then `done`. After `done` the set is empty |

Each target carries the command, its arguments, the plan item it works on and where every argument comes from (`slot:diameter`, `base:height`, `floor`, `const`, `derived:...`), copied from the recipe. The model picks a command; code fills in the numbers.

Why undo and not "fix in place": it needs no knowledge of what went wrong, and one rule covers every mistake. The price: a mistake buried under good work costs that work too. The rule needs undo to be exact also in what the snapshot does not show; see "Undo" for why FreeCAD's own is not and how the runtime makes it so.

The teacher has no memory, so it cannot notice that it is asking for the same failing command again and again. `teacher.Watch` does (see "Undo").

Three catalogue commands are never a target because no recipe uses them: `revolution`, `hole_through`, `hole_blind`. They are in the valid lists and are sometimes executed as noise.

## Sessions

A session is one part played in real FreeCAD until the teacher's `done` is carried out. `play.py` is the loop; `sessions.py` writes the files; `shards.py` says where they are.

There are two recordings on disk. The first was made on 5 to 6 Oct 2026; the second (6 Oct 2026, slices ending in `_v2`) adds what the first lacked. Nothing of the first was overwritten.

### What is the same in both

- **Noise levels.** Each session draws a level from 0, 0, 0.1, 0.2, 0.3: the chance, at each step, that a wrong command is executed instead of the teacher's. Only the teacher's set is ever stored as the target.
- **Step limit.** Noise may strike only in the first 3 x L steps (L = clean length), so every session reaches `done`. A hard limit of 8 x L + 40 marks a session `budget`.
- **Record.** One JSON line per step: the lean snapshot before the command, the valid commands, the teacher's set, `progress` (on plan? items built, active item), what was executed and whether it was noise, FreeCAD's reply. The header holds the plan once. See the top of `sessions.py`.
- **Lean snapshot** (`lean.py`). Dropped: each sketch's `geometry` and `constraints` lists (their counts are kept; the dimensions are in `shapes`), the body's copy of the solid, the solid's `size`, and the volume's digits past a millionth.
- **Splits** are `forge.system1.splits`, unchanged.

### The first noise mix (slices `iid`, `pairing`, `long`, `train`, `train_heavy`, `train_s1`, `train_s2`)

Six kinds of wrong command: `random_valid`, `wrong_argument`, `later_item`, `repeat`, `extra_undo`, `stray_click`. `done` and `new_document` were never injected. Start states: empty (half the sessions), an empty document, a body, the first commands of a correct build, a stray sketch, a stray solid, or one of the last three "opened from a file".

What it lacks, all measured on the files:

- one session looped until the step limit (see "Undo");
- `done` too early was never seen;
- a wrong number was 15% of the wrong commands, and always a number picked at random from the plan;
- a failed feature was in the snapshot in 0.13% of the steps, `new_document` was the repair in 0.13%, `clear_selection` a target in 0.20%.

These slices are frozen: `sessions.py` refuses to record them. `train_s2` was meant to be a third session of every train part and stopped at 29 of 196 shards (14,500 sessions); it stays that way.

### The second noise mix (slices `train_v2`, `iid_v2`, `pairing_v2`, `long_v2`)

One session per part with a new seed, on the runtime with exact undo. What changed, in `noise.py`, `starts.py` and `play.py`:

| What | How |
| --- | --- |
| Wrong numbers are about a third of the wrong commands | `wrong_argument` has the largest weight. Its values are near misses, by "flavour": another slot of the same plan item (`swapped`: the width for the length), the same slot of another item (`other_item`), any other number of the plan (`plan_number`), a value a little off or with two digits swapped (`a_little_off`), the wrong sign (`wrong_sign`), a wrong plane, rule, axis or direction (`other_word`), any plausible number (`random`), a number no part has room for (`too_big`) |
| Carrying on after a wrong number | With chance 0.2 the session goes on as if nothing were wrong: the rest of that plan item's recipe is issued (`carry_on`), each command with chance 0.8, while the teacher says `undo` at every one of those states. The mistake ends up under several commands and the repair is a chain of undos |
| `done` too early | `early_done`: FreeCAD accepts `done` whenever there is a valid solid and no open sketch. The session is then marked finished and only `undo` and `new_document` are valid; the teacher says `undo`. When `done` is not available (a sketch is open, or there is no solid) it is not injected: FreeCAD would reject it and the model would see the same snapshot again |
| Failed features | `failing_feature`: edges (or the top face) are selected, then rounded, bevelled or hollowed with three times the largest number of the plan. FreeCAD carries it out and the feature is invalid. FreeCAD 1.1 fails little else: a pad that floats above the part or a pocket that cuts nothing comes out valid |
| Stray selections where they matter | `stray_selection` is drawn only when the teacher's command uses what is selected. When the plan is complete the chance of noise is raised to 0.6 and this kind is preferred, so `clear_selection` becomes a target |
| A lost undo history | `reopen`: with chance 0.02 at an off-plan step the undo history is forgotten, as after saving and opening the file again. The repair is then `new_document`. Not a command: the next record carries `before: forget_undo` |
| Start states | new: `wrong_partial`, a correct beginning with ONE wrong number in it, which may lie twenty commands deep. `opened` went from 8% to 14% of the sessions |

Only the teacher's set is ever a target, as before. `new_document` is still never injected.

Measured shares (all slices of each recording; the commands are `forge.freecad.manifest` and the scan described under "Undo"):

Source: `data/freecad/sessions/manifest.json` (frozen 6 Oct 2026). Shares are of all steps unless the row says otherwise.

| | First mix (7 slices) | Second mix (4 slices) |
| --- | --- | --- |
| Sessions | 239,471 | 124,000 |
| Labelled steps | 11,894,479 | 7,771,962 |
| Wrong commands carried out | 1,885,103 (15.8%) | 1,462,719 (18.8%) |
| Steps off plan | 1,352,910 (11.4%) | 1,622,538 (20.9%) |
| `undo` is a target | 1,337,918 (11.2%) | 1,567,413 (20.2%) |
| `clear_selection` is a target | 23,241 (0.20%) | 103,207 (1.33%) |
| `new_document` is a target (normal beginnings included) | 134,455 (1.13%) | 107,209 (1.38%) |
| `new_document` as the repair | not counted in the manifest (a scan: 0.13%) | 55,125 (0.71%) |
| Steps with a failed feature in the snapshot | not counted in the manifest (a scan: 0.13%) | 58,730 (0.76%) |
| Steps in a session finished too early | not counted in the manifest (a scan: never seen) | 46,035 (0.59%) |
| Steps right after a wrong number | not counted in the manifest | 455,434 (5.9%) |
| Undo history lost (`reopen`) | none (not in this mix) | 30,581 times |
| Wrong numbers, share of the wrong commands | 280,390 (14.9%) | 457,843 (31.3%) |
| Sessions where noise was cut off by the 3 x L rule | 3,862 | 14,164 |
| Teacher commands FreeCAD refused | 0 | 0 |
| Worker restarts while recording | 26 | 7 |

"Not counted in the manifest" means the manifest holds 0 in that field for first-mix slices because the first recording did not write the counter; it does not mean the thing never happened.

Wrong commands by kind (share of the wrong commands of that mix):

| Kind | First mix | Second mix |
| --- | --- | --- |
| `wrong_argument` | 280,390 (14.9%) | 457,843 (31.3%) |
| `random_valid` | 404,871 (21.5%) | 184,798 (12.6%) |
| `carry_on` | none | 182,420 (12.5%) |
| `later_item` | 257,414 (13.7%) | 141,146 (9.6%) |
| `stray_click` | 389,721 (20.7%) | 113,958 (7.8%) |
| `extra_undo` | 342,488 (18.2%) | 104,605 (7.2%) |
| `stray_selection` | none | 88,496 (6.1%) |
| `repeat` | 210,219 (11.2%) | 81,885 (5.6%) |
| `failing_feature` | none | 73,304 (5.0%) |
| `early_done` | none | 34,264 (2.3%) |

Wrong numbers of the second mix by flavour: `plan_number` 96,098; `other_word` 94,617; `a_little_off` 88,447; `swapped` 85,818; `random` 47,558; `other_item` 25,286; `wrong_sign` 16,541; `too_big` 3,478. The first mix did not record a flavour.

Noise levels over both recordings (sessions): 0: 144,552; 0.1: 72,024; 0.2: 72,247; 0.3: 71,646; 0.4: 1,498; 0.5: 1,502 (the last two are `train_heavy` only).

Start states (sessions):

| Start | First mix | Second mix |
| --- | --- | --- |
| empty | 119,463 | 52,084 |
| an empty document | 19,169 | 8,573 |
| a body | 19,085 | 8,828 |
| the first commands of a correct build (`partial`) | 23,980 | 12,292 |
| a stray sketch | 21,820 | 9,942 |
| a stray solid | 16,707 | 7,444 |
| a correct beginning with one wrong number (`wrong_partial`) | not in this mix | 7,422 |
| opened from a file: partial / stray sketch / stray solid / wrong partial | 6,406 / 6,343 / 6,496 / none | 4,405 / 4,388 / 4,411 / 4,211 |

### Slices on disk

Source: the same manifest, and the first lines of `data/freecad/sessions/audit_checkpoint1b.log`. All files are frozen read-only.

| Slice | Mix | Split | Seed | Shards (done / planned) | Sessions | Steps | `undo` share of targets | Ended |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `train` | first | train | 0 | 196 / 196 | 97,971 | 4,559,409 | 10.3% | 97,970 done, 1 budget |
| `train_s1` | first | train | 1 | 196 / 196 | 97,971 | 4,570,035 | 10.3% | 97,970 done, 1 error |
| `train_s2` | first | train | 2 | 29 / 196 | 14,500 | 676,519 | 10.2% | all done |
| `train_heavy` (noise 0.4 and 0.5) | first | train | 3 | 6 / 6 | 3,000 | 348,322 | 45.5% | all done |
| `iid` | first | iid | 0 | 11 / 11 | 5,133 | 237,896 | 10.5% | all done |
| `pairing` | first | pairing | 0 | 34 / 34 | 16,896 | 1,038,184 | 9.9% | all done |
| `long` | first | long | 0 | 20 / 20 | 4,000 | 464,114 | 9.5% | all done |
| `train_v2` | second | train | 10 | 196 / 196 | 97,971 | 5,575,981 | 20.2% | all done |
| `iid_v2` | second | iid | 10 | 11 / 11 | 5,133 | 289,811 | 20.4% | all done |
| `pairing_v2` | second | pairing | 10 | 34 / 34 | 16,896 | 1,285,181 | 20.1% | all done |
| `long_v2` | second | long | 10 | 20 / 20 | 4,000 | 620,989 | 19.7% | all done |
| **Total** | | | | 753 | 363,471 | 19,666,441 | 14.8% | 363,469 done, 2 excluded |

Added on 7 Oct 2026, recorded with the second mix on NEW parts, in splits of their own. Source: `data/freecad/sessions/manifest.json` and `audit_checkpoint2.log` (6 of 6 checks passed, 1,800 sessions replayed).

| Slice | Mix | Split | Seed | Shards (done / planned) | Sessions | Steps | `undo` share of targets | Ended |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `train_long_v2` | second | train_long | 10 | 56 / 56 | 11,200 | 1,691,066 | 19.7% | all done |
| `xlong_v2` (TEST ONLY) | second | xlong | 10 | 3 / 3 | 600 | 146,867 | 19.8% | all done |

- `train_long` holds 11,200 parts made by `long_train_parts.py`: 400 for each base and each length from 6 to 12 features (plans of 7 to 13 items), none with a held-out pairing. They are other parts than the 4,000 of `long`: no id, no plan and no geometry fingerprint is shared (`data/system1/long_train_parts/leak_check.json`, and the audit's check 5 over all six splits).
- `xlong` holds 600 parts with 13 to 15 features (plans of 14 to 16 items), none with a held-out pairing: a test of length beyond anything in training. It must never be trained on; `forge/s1/kaggle_pack.py` and `forge/s1/augment.py` refuse it.
- A part reaches one of these splits only through the `set` field its folder gives it (`forge/system1/parts.py`), and `splits.split_of` then checks the length and refuses a held-out pairing. `tests/test_freecad_long_train.py` pins the split of all 124,000 older parts and the contents of the 920 older shards to their digests from before the change.
- Total on disk now: 812 shards, 375,271 sessions, 21,504,374 steps.

- Plan length: 2 to 6 items in every slice except `long` and `long_v2` (7 to 13 items); `pairing` and `pairing_v2` have 3 to 6.
- The test slices of the two mixes hold the same parts (same split, another seed and mix).
- The two sessions that did not end with `done` are excluded and `load.py` skips them: `8faf9b4de268c2bd` in `train` (the loop described under "Undo"; ended `budget` after 384 steps) and `118d531507fc2bc2` in `train_s1` (ended `error` after 0 steps; a lost worker).
- `train_heavy` is nearly half undo. Whether it belongs in a training mix is a choice of the training config.
- Size on disk: 1,056,507,346 bytes compressed, 33,634,092,823 uncompressed (53.7 and 1,710.2 bytes per step).

`long_pure` is not a slice but a listing (`data/freecad/sessions/long_pure.json`): the long sessions whose plan has no held-out pairing in it, so that length can be scored by itself. In `long` and in `long_v2`, 1,486 of the 4,000 sessions are in the listing; the other 2,514 (63%) hold a held-out pairing, so their score mixes length with an unseen pairing.

### Which code made which shard

Every shard of the second recording has the hash of the recording code in its stats file, and every session has it in its header (`code_hash`), with the noise mix (`mix`) and the runtime (`runtime.exact_undo`). The recorder throws a shard away if the code changes while it is being recorded.

The first recording has three hashes, one per run (they are in the run folders under `runs/`; `manifest.json` maps every shard to its hash under `code_versions`):

| Hash | Shards |
| --- | --- |
| `25294e7b7e20` | `iid` 0-10, `pairing` 0-7 |
| `a0cee27d5505` | `pairing` 8-33, `long` 0-19, `train` 0-4 |
| `1c27ac87bd63` | `train` 5-195, `train_heavy`, `train_s1`, `train_s2` 0-28 |

That hash covered every `.py` file of this folder, tools included. By the files' modification times the only files that changed between the three are `extra_parts.py` and `audit.py`, and the recorder imports neither; so the same recording code is behind all three, as far as modification times can show. Seven `train_s2` shards (13-16 and 26-28) were finished by worker processes that outlived their run and are in no run's log; they are given the hash of the run that was going when they were written.

## Reading sessions for a model: `load.py`

A record holds the answer several times over: `progress.on_plan`, what was executed next, FreeCAD's reply, the session's noise level, the start kind, and for every target the plan item and where each number comes from. A model given any of that can score well without reading the plan.

`load.py` is therefore the only way a model reads these files. It does not filter a record; it BUILDS the model's side from lists of allowed fields:

```python
from forge.freecad.load import examples
for example in examples(slices=("train_v2",)):
    example.view     # {"plan": [...], "snapshot": {...}, "valid": [...]}   what the model sees
    example.label    # [{"command": ..., "args": {...}}, ...]               the teacher's set
```

- A field that is not listed cannot get through, at any depth: plan slots are checked against the step kind, label arguments against the catalogue. An unknown object type, step kind or command is an error. `tests/test_freecad_load.py` puts a poisoned extra field into every dictionary of a record and finds none on the model's side.
- Two history traces are removed from the snapshot. The ORDER of a shape's `fixed` list is the order the dimensions were given in, so the list is sorted. `session.undo_depth` counts every command in effect, stray selections included, so it becomes `can_undo` (true or false). Nothing else in the lean snapshot differs between two visits of the same visible state (measured on four shards, 23,788 distinct sketch states).
- Nothing object-revealing gets through. Object names become opaque ids: every object is `#0`, `#1`, ... by its place in the document, and every field that points at an object (`tip`, `sketch`, `on`, `of`, `used_by`, `body`, `open_sketch`, `active_body`, a selection's `name` and `of`) carries that id; the type stays in `type`. Checked on seven shards of both recordings (234,431 steps): the names in the files are FreeCAD's automatic ones and nothing else (`Body`, `Sketch002`, `Pad`, `Pocket001`, `Mirrored`, ...), one stem per object type, so today the ids hide only the counter. They are there so that a generator or a user who names objects by meaning cannot leak it. No command takes a name, so nothing is lost.
- Every string in the view must be an opaque id or a word of a closed vocabulary (`load.VOCABULARY`: command names, step kinds, slot names, object types, planes, axes, rules, shape and dimension names, selection types). Anything else raises an error. The part's family (`composed_block`), its id, source and captions are in the header and are never in the view. The plan's step kinds (`block`, `hole_pair`, ...) are in the view: they are the plan.
- Only sessions that ended with `done` on the stored solid are read.
- The same `load.view` must be used when a model drives FreeCAD.

## The floor: `baselines.py`

Many steps can be answered without the plan: after `sketch_circle` comes a dimension. `baselines.py` measures how far that goes, with three lookups fitted on training sessions ("the most frequent target for this key"): the previous command, the valid list, and the plan-blind state (the valid list, the type of the selection and of the newest object, the newest shape and its fixed dimensions). A prediction is right when it is one of the teacher's commands; arguments are not scored.

**Every model number must be shown next to these, on the same steps.** The table for the second recording, with the lookups fitted on 20 shards of `train_v2`. (This sentence was written before `train_v2` was recorded. The slice was recorded on the evening of 6 Oct 2026 and the table below is from the run made after that.)

Source: the log of `baselines.py` on these slices. The fit found 38 previous-command keys, 158 valid-list keys and 1,317 plan-blind-state keys. Each cell is the share of steps where the lookup's answer is one of the teacher's commands.

| Slice | Group | Steps | Previous command | Valid list | Plan-blind state |
| --- | --- | --- | --- | --- | --- |
| `train_v2` (held shards) | all steps | 84,633 | 48.4% | 59.4% | 68.5% |
| | undo decisions | 17,786 | 42.4% | 19.6% | 26.1% |
| | right after a mistake | 15,335 | 13.0% | 28.6% | 35.1% |
| | wrong-number mistakes | 4,293 | 1.5% | 5.9% | 5.2% |
| | other on-plan steps | 62,319 | 52.5% | 71.5% | 81.7% |
| `iid_v2` | all steps | 289,811 | 48.6% | 59.3% | 68.3% |
| | undo decisions | 61,230 | 42.4% | 19.1% | 25.4% |
| | right after a mistake | 52,540 | 12.7% | 28.0% | 34.5% |
| | wrong-number mistakes | 14,322 | 1.2% | 5.4% | 4.5% |
| | other on-plan steps | 212,848 | 52.9% | 71.6% | 81.9% |
| `pairing_v2` | all steps | 1,285,181 | 49.8% | 60.0% | 67.6% |
| | undo decisions | 267,027 | 42.0% | 19.6% | 26.7% |
| | right after a mistake | 227,446 | 11.9% | 27.7% | 35.0% |
| | wrong-number mistakes | 63,144 | 1.1% | 6.1% | 5.6% |
| | other on-plan steps | 954,500 | 54.5% | 72.1% | 80.0% |
| `long_v2` | all steps | 620,989 | 48.2% | 61.3% | 67.7% |
| | undo decisions | 126,294 | 41.5% | 19.2% | 25.2% |
| | right after a mistake | 107,392 | 11.0% | 26.8% | 33.5% |
| | wrong-number mistakes | 30,749 | 0.8% | 4.8% | 4.4% |
| | other on-plan steps | 466,886 | 52.5% | 73.5% | 80.2% |
| `long_v2` (`long_pure` only) | all steps | 211,214 | 48.2% | 60.6% | 67.9% |
| | undo decisions | 43,690 | 41.9% | 19.4% | 24.8% |
| | right after a mistake | 36,720 | 11.1% | 26.7% | 32.9% |
| | wrong-number mistakes | 10,525 | 1.0% | 4.5% | 4.0% |
| | other on-plan steps | 158,159 | 52.4% | 72.8% | 80.8% |

What it means: a rule that never reads the plan gets about two steps in three right, so "step accuracy 68%" is zero skill. It gets one undo decision in four and one wrong-number mistake in twenty. A model is worth something only where it beats these rows, and the rows to watch are the last three groups, not "all steps".

An earlier floor, fitted on 20 shards of the first-mix `train` before `train_v2` existed, is in `data/freecad/sessions/baselines_checkpoint1.log`. It is superseded by the table above for the `_v2` tests. A floor fitted on first-mix training slices and scored on the first-mix tests (`iid`, `pairing`, `long`) by group was not measured.

The four groups overlap: "wrong-number mistakes" are a part of "right after a mistake", and both contain undo decisions. The wrong-number row is the one that matters most: the state after a wrong number looks like any other state unless a value in the snapshot is compared with the plan, and no lookup gets there.

## Audit

`uv run python -m forge.freecad.audit` exits 0 only if all six checks pass:

1. **teacher**: every target is what the teacher returns for that record; it is valid; a noise command is never a target.
2. **oracle**: the same labels judged by `oracle.py`, which imports nothing of ours. It takes the session's final document (measured against the stored solid) and asks, for every earlier state, whether it is a beginning of that document and what the next missing piece is. Check 1 asks the teacher whether the teacher was right; this one does not.
3. **replay**: a sample of sessions is played again in FreeCAD, on the runtime each was recorded with.
4. **splits**: every session is in its part's split; no held-out pairing and no long part in a train slice.
5. **content**: no plan (kinds and numbers) and no geometry fingerprint is in a train slice and a test split, or in two test splits. This compares what is in the files, not what the split function says.
6. **endings**: every session ends with the teacher's `done` on the stored solid. A session that ended `budget`, `stuck` or `error` FAILS the audit; with `--allow-excluded` it is listed as excluded instead. `load.py` skips such sessions either way.

### Result on the frozen files (6 Oct 2026, evening)

Source: `data/freecad/sessions/audit_checkpoint1b.log`. Run with `--allow-excluded`. The audit exited 0.

| Check | Result |
| --- | --- |
| Read | 363,471 sessions (363,469 finished), 19,666,441 steps, 753 shards, 998 s |
| 1. teacher | PASS: every target is the teacher's answer for its plan and snapshot, is valid, and was carried out when chosen |
| 2. oracle | PASS: every on-plan flag and target set is what the session's final document implies |
| 3. replay | PASS: 2,409 sessions (181,035 steps), 219 from each of the 11 slices, replayed in FreeCAD reproduce every snapshot and end at the stored solid. FreeCAD's worker was replaced twice during the replay |
| 4. splits | PASS: every session is in its part's split; no held-out pairing or long part in train; one session per part and seed |
| 5. content | PASS: no plan and no geometry fingerprint shared between train and a test split, or between two test splits. Distinct plans: train 97,971; iid 5,133; pairing 16,896; long 4,000 |
| 6. endings | PASS with 2 sessions excluded (named under "Slices on disk") |

Limits of this result:

- The replay is a sample: 2,409 of 363,471 sessions (0.7%). Checks 1, 2, 4, 5 and 6 read every session.
- Check 6 passes only because of `--allow-excluded`. Without the flag the two excluded sessions fail the audit.
- The log's line "of 124000 parts" counts distinct parts; the manifest's `parts: 363471` counts one per session. The two files use the word differently.
- The cause of the teacher loop in the excluded `train` session is described under "Undo" for the runtime; the root cause is not fully worked out.
- An earlier audit of the first 263,500 sessions (`data/freecad/sessions/audit_checkpoint1.log`, 2,000 replays) also passed 6 of 6.

## Wide data v3 (7 to 8 Oct 2026)

Why: the first model learned the composed generator's habits as rules (a hexagon has a whole-number size, the edge treatment is item 2, features never touch) and failed on parts from outside. Everything here is NEW modules; no file above was changed except `baselines.py` (it accepts the new slice names), and the frozen recordings are not touched.

```
  wide_sample.py     draw one wide plan (the table of what is free is at its top)
  wide_reference.py  the plan built item by item in CadQuery: the reference solid and its program
  wide_parts.py      generate sets of verified wide parts; the leak proof              [command]
  wide_build.py      build a wide plan in FreeCAD and compare after every item
  wide_noise.py      third mistake mix: order mistakes, decimal slips, bursts
  wide_starts.py     nine more start kinds (three of them held out for a test)
  wide_play.py       play one session of the third mix; `start=` begins in ANY reachable state
  wide_sessions.py   record the slices into data/freecad/sessions_wide/                [command]
  wide_audit.py      the six checks on the wide slices                                 [command]
  wide_prove.py      three proofs: recipes, teacher from every start, strict order     [command]
  wide_stats.py      which plan fields sit on a grid, before and after                 [command]
```

- **Parts** are in `data/system1/wide_parts/<set>/wide_<base>.jsonl`. A row holds its `plan` (a list of kinds and slots) because parameter names cannot say where an edge treatment comes; `forge.system1.steps.steps_of` is not used for wide parts.
- **Verified how**: the kernel builds every item into one valid solid that the item changed; the stored program gives the same solid in the sandbox; FreeCAD builds the same solid through the recipes in the part's session, and a part FreeCAD builds differently gets no session (`shardNNN.rejected.jsonl`).
- **Slices** (`wide_sessions.SLICES`): `train_wide_v3`, `train_wide_long_v3` (14 to 16 items; optional), and the tests `wide_iid_v3`, `wide_numbers_v3`, `wide_order_v3`, `abnormal_starts_v3`. A shard is a balanced sample of its slice; parts made later only add shards.
- **Reading**: `load.examples(("train_wide_v3",), out_dir=wide_sessions.OUT_DIR)`. No vocabulary change.
- **The teacher is the same and demands the plan's order.** An order mistake is repaired by `undo` back to the first command that differs, also when the swapped build would be the same solid (87% of neighbour swaps in wide plans).
- **What it still cannot produce:** only XY sketches, cuts only from the base's top (no hole through a boss), four bases, patterns and mirrors of a few kinds, edge treatments by rule.

| I want to… | Command |
| --- | --- |
| Run the whole thing, resumable | `scripts/wide_v3_pipeline.sh`, then `scripts/wide_v3_continue.sh` (worker count in `data/freecad/sessions_wide/logs/workers`) |
| Make wide parts (run again to add) | `uv run python -m forge.freecad.wide_parts --set wide_train --per-cell 400 --workers 8`; `--check` for the leak proof alone |
| Record their sessions (run again to carry on) | `uv run python -m forge.freecad.wide_sessions --workers 8 [--slices train_wide_v3] [--max-minutes N]` |
| Audit them | `uv run python -m forge.freecad.wide_audit --replay-sample 1800 --workers 8` |
| Prove recipes / starts / measure strict order | `uv run python -m forge.freecad.wide_prove --recipes 300 --accepted --sets wide_iid wide_train`, `--starts 30`, `--orders 200 --sets wide_train composed` |
| The grid table | `uv run python -m forge.freecad.wide_stats` |
| The floor on the new tests | `uv run python -m forge.freecad.baselines --dir data/freecad/sessions_wide --fit train_wide_v3 --eval wide_iid_v3 wide_numbers_v3 wide_order_v3 abnormal_starts_v3` |

## Commands to run

From the repo root.

| I want to… | Command |
| --- | --- |
| Watch one part being built, command by command | `uv run python -m forge.freecad.show --part <id>` (add `--valid` to list the valid commands too) |
| Save one part as an editable FreeCAD document | `uv run python -m forge.freecad.save --part <id>` (writes `data/freecad/<id>.FCStd`) |
| Prove the recipes against the stored solids | `uv run python -m forge.freecad.prove --per-base 500 --long-per-base 150 --workers 3` |
| Prove undo | `uv run python -m forge.freecad.undo_proof --per-base 75 --long-per-base 20 --workers 3` |
| Prove undo on noise-like histories (add `--exact-undo off` for the old runtime) | `uv run python -m forge.freecad.undo_proof --histories --per-base 125 --long-per-base 20 --workers 3` |
| Measure speed, recovery and memory | `uv run python -m forge.freecad.bench` |
| Prove the teacher (clean sessions, random member of the set) | `uv run python -m forge.freecad.teacher_proof --per-base 500 --long-per-base 150 --workers 4` |
| Record sessions of the second mix (run again to carry on) | `uv run python -m forge.freecad.sessions --workers 3 [--slices train_v2] [--max-minutes N]` |
| Make new long parts (training, and the extra-long test) with their leak check | `uv run python -m forge.freecad.long_train_parts --set train_long --per-cell 400`, then `--set xlong --per-cell 50`; `--check` for the proof alone |
| Rewrite the manifest for everything on disk, and list `long_pure` | `uv run python -m forge.freecad.manifest [--freeze]` |
| Audit the session files | `uv run python -m forge.freecad.audit --replay-sample 2000 --workers 3 --allow-excluded` |
| Measure the plan-blind floor | `uv run python -m forge.freecad.baselines --fit train_v2 --eval iid_v2 pairing_v2 long_v2` |
| Read sessions in plain words | `uv run python -m forge.freecad.show_session --n 3 --noise 0.2` (also `--split train_heavy`, `--start stray`, `--kind early_done`, `--session <id>`) |
| Run this folder's tests | `uv run pytest tests/test_freecad_*.py` (they skip if FreeCAD is not installed) |

A part id is the `id` field of a row in `data/generated/composed_<base>.jsonl` or `data/system1/long_parts/composed_<base>.jsonl`, for example `648d8771353deeb3`.

FreeCAD is looked for in `/Applications/FreeCAD.app`; set `FORGE_FREECAD_PYTHON` and `FORGE_FREECAD_LIB` to use another one.

## Measured (5 Oct 2026, this Mac, FreeCAD 1.1.4)

Each number comes from the command next to it; the run folders are under `runs/`.

| What | Result | Command |
| --- | --- | --- |
| Recipes against the stored solids | 2,600 parts (2,000 with 1 to 5 features, 600 long), 14,279 steps, 126,832 commands: 0 parts differ; all 20 step kinds matched every time | `python -m forge.freecad.prove --per-base 500 --long-per-base 150 --workers 3` |
| Undo then issue again, after every command (compares the snapshot only; 5 Oct 2026, before exact undo) | 18,066 checks on 380 parts: 0 different (1 undo and 1 re-issue differed only in the last bits of the volume) | `python -m forge.freecad.undo_proof --per-base 75 --long-per-base 20 --workers 3` |
| Wrong commands undone | 1,067 wrong sequences (5,057 commands): snapshot identical and no stray object, every time | same run |
| Undo all the way to the empty document | 75 parts, 2,713 undos, chains up to 60: 0 different (29 differed only in the last bits of the volume) | same run |
| Noise-like wrong chains undone (6 Oct 2026, exact undo) | 580 parts, 3,013 chains, 24,150 commands undone: no hidden state in any of three runs; snapshot identical (or volume digits only) in 3,013 of 3,013 in two runs, 3,011 in one (see "Undo") | `python -m forge.freecad.undo_proof --histories --per-base 125 --long-per-base 20 --workers 3` |
| The same on the runtime before the fix | snapshot identical 3,013 of 3,013; hidden state left by 21 chains in 20 parts | the same with `--exact-undo off` |
| Speed, parts with 1 to 5 features | 1 worker: 7.8 parts/s, 288 commands/s. 3 workers: 18.5 parts/s, 688 commands/s | `python -m forge.freecad.bench` |
| Speed, parts with 6 to 12 features | 1 worker: 1.45 parts/s, 126 commands/s. 3 workers: 3.5 parts/s, 305 commands/s | same run |
| FreeCAD crashes or hangs on its own | 0 in the two proof runs above (about 290,000 commands) | the proof runs |
| Recovery when the worker is killed (30 times) or made to hang (10 times) mid-part | 40 of 40 parts finished and matched; slowest part 3.6 s (a hang costs the 3 s time limit) | `python -m forge.freecad.bench` |
| Memory of one worker over 2,000 parts | 96 MB at the start, 174 MB at the end: about 33 MB per 1,000 parts. The client swaps the worker every 1,000 documents by default | same run |

The speeds include taking and sending a full snapshot after every command. About half of the worker's time is FreeCAD recomputing the solid, and a fifth is checking that the solid is valid.

## Not here yet

- Structures of several parts are not in this folder. They are in `forge/freecad_multi/` (placing commands, teacher, sessions), which imports this folder and does not edit it.
- The real interface (toolbar buttons, dialogs) is not in this folder. It is in `forge/freecad_ui/` (stage C2): proven on 680 parts, three small trial session sets, no training data.
- A model. None is defined or trained anywhere in the repo yet.
- A floor (`baselines.py`) by group for the first-mix test slices.
- Closed-loop numbers (a model or a rule driving FreeCAD to a finished part): none exist yet. `baselines.py` scores single steps only.
