# freecad_ui/ — our FreeCAD runtime, at interface level

`forge/freecad/` builds a part by sending FreeCAD commands. This folder builds the same part the way a person does: it presses toolbar buttons, types into dialog fields, chooses dropdown entries, clicks OK and Cancel, in a real FreeCAD window. The window is hidden and is always one we started ourselves.

No model is trained or defined here.

```
freecad_ui/
  launch.py     start one hidden FreeCAD with its own settings folder; kill it
  watchdog.py   a second process that kills that FreeCAD if we die, time is up, or memory is over the cap
  client.py     UIClient: one interface action at a time, time limit, restart-and-replay, planned restarts
  recipes.py    for each of the 20 step kinds of forge/system1/steps.py: its interface actions
  build.py      build a part action by action and compare with the reference engine
  prove.py      proof: the interface builds the stored solid, per step kind      [command, resumable]
  recover.py    proof: cancel, undo, refused OK and a killed FreeCAD            [command]
  bench.py      speed and memory                                                [command]
  teacher.py    for any (plan, interface snapshot): the acceptable next interface actions
  noise.py      wrong interface actions, injected while sessions are recorded
  snapshots.py  the lean interface snapshot a session record keeps
  play.py       play one session: the teacher labels every state, noise sometimes acts
  teacher_proof.py  proof: the teacher alone builds parts; with noise it still ends right  [command, resumable]
  sessions.py   record sessions into shards, with a manifest                    [command, resumable]
  audit.py      check every session; replay a sample in a fresh FreeCAD         [command]
  show_session.py   print recorded sessions step by step, for reading           [command]
  inside/       the code that runs INSIDE FreeCAD's Python (3.11), never in ours
    boot.py         imported by the stub macro the launcher writes
    server.py       the socket server, the modal guard, the self guard
    ops.py, dispatch.py   the requests
    widgets.py      find FreeCAD's real widgets (toolbars, task panel, tree, 3D view, on-view fields)
    elements.py     the interface elements: the plain list a model will choose from
    act.py          act on one element, then wait for FreeCAD to catch up
    picks.py        the few semantic picks, and the pointer in the 3D view
    document.py     what has been built, as plain data (for checks, later the teacher)
    session.py, tools.py   how far a sketcher drawing tool has got
    qt.py, macos.py, numbers_text.py   waiting, App Nap, how a number is typed
```

## How it runs

```
our Python 3.12                                a hidden FreeCAD window (its own Python 3.11)
UIClient.act("field:lengthEdit", 12.5)  ->  inside/server.py -> act.py -> the real QWidget
     reply  <-  {"status": "ok", "elements": [...], "context": {...}, "modals": [], "ms": 21}
```

- **Hidden and separate.** macOS `open -n -g -j` starts a new FreeCAD (never one you have open), behind everything, hidden. `FREECAD_USER_HOME` points it at a temp folder with a `user.cfg` we write first: English, PartDesign workbench, no start page, no auto-save. Your FreeCAD settings are never read or written. Only a process whose command line contains that temp folder is ever measured or killed.
- **One request, one action.** Requests are JSON over a Unix socket, carried out on FreeCAD's interface thread. Every action is "act, then wait for a condition" (the dialog is open, the sketch is closed, the shape exists); nothing sleeps.
- **Modal guard.** FreeCAD reports some refusals in a pop-up that blocks everything. A 30 ms timer notes its text and closes it, so the reply says `"status": "refused"` with FreeCAD's own words.
- **It can always be stopped.** `UIClient.close()` kills it; inside, a self guard exits on a deadline, a memory cap, or when our process is gone; outside, `watchdog.py` does the same in case the interface thread is stuck.
- **Restart and replay.** The client remembers the actions that succeeded since `new_part()`. If FreeCAD dies or is silent past the time limit, a new one is started, the actions are replayed, the state is compared (context, controls, tree, picks) and the failed action is tried once more.
- **Planned restart** every 60 parts (memory grows about 11 MB per part, see "Measured").
- **Out of the Dock.** At start the instance sets its activation policy to "accessory" (`inside/macos.py`): no Dock icon, not in Cmd-Tab, it cannot come to the front. `lsappinfo` reports it as `UIElement`. The icon can flash for a second or two at launch, before the policy is set.
- **Not drawn.** The hidden window's painting is switched off (`setUpdatesEnabled(False)`): parts build 2.5 times faster and nothing we read depends on the picture. `Instance(paint=True)` turns it back on.

## The interface elements

`reply["elements"]` is a plain list. Every element has `kind`, `id`, `role` (the words on it or next to it) and `value` (what it shows now). Ids come from FreeCAD's own names, never from positions, so they are the same in every session and after every restart.

| kind | id | extra keys | how it is acted on |
| --- | --- | --- | --- |
| `button` | `button:<Command>` | `toolbar` | pressed (only enabled buttons on visible toolbars are listed; a button with a drop-down arrow is listed as its entries) |
| `field` | `field:<name>` | `text` | a number is typed into it |
| `dropdown` | `dropdown:<name>` | `entries` | one entry is chosen |
| `check`, `radio` | `check:<name>`, `radio:<name>` | | clicked |
| `list` | `list:<name>` | `entries` | one entry is chosen |
| `panel_button` | `panel:<name>` | | clicked |
| `dialog_button` | `dialog:OK`, `dialog:Cancel`, `dialog:Close` | | clicked |
| `view_field` | `view:<role>` | `text`, `entered` | a number is typed, then Enter (the sketcher's on-view fields) |
| `tree` | `tree:<ObjectName>` | `type`, `selected` | clicked: selects that object |
| `pick` | `pick:plane:XY\|XZ\|YZ`, `pick:face:top\|bottom`, `pick:edges:vertical\|top_face\|bottom_face\|all`, `pick:origin` | | a semantic pick, resolved by the runtime |

`reply["context"]` says where the session is: `workbench`, `document`, `dialog` (a stable name such as `TaskPadPocketParameters`), `sketch_open`, `tool`, `selection` (edges and faces named by rule, never by FreeCAD's numbers), `tip`, `undo`.

A real one: the Pad dialog open on a second sketch of a 60 x 40 x 10 block with one hole (61 elements: 34 buttons, 16 tree items, 11 dialog controls; the buttons and the origin items of the tree are cut here).

```json
{"kind": "dropdown", "id": "dropdown:sidesMode", "role": "Mode", "value": "One sided", "entries": ["One sided", "Two sided", "Symmetric"]}
{"kind": "dropdown", "id": "dropdown:changeMode", "role": "Type", "value": "Dimension", "entries": ["Dimension", "To last", "To first", "Up to face", "Up to shape"]}
{"kind": "field", "id": "field:lengthEdit", "role": "Length", "value": 10.0, "text": "10.00 mm"}
{"kind": "field", "id": "field:taperEdit", "role": "Taper angle", "value": 0.0, "text": "0.00°"}
{"kind": "check", "id": "check:checkBoxReversed", "role": "Reversed", "value": false}
{"kind": "dropdown", "id": "dropdown:directionCB", "role": "Direction/edge", "value": "Sketch normal", "entries": ["Sketch normal", "Select reference…", "Custom direction"]}
{"kind": "check", "id": "check:checkBoxUpdateView", "role": "Recompute on change", "value": true}
{"kind": "check", "id": "check:showFinalCheckBox", "role": "Show final result", "value": false}
{"kind": "check", "id": "check:showTransparentPreviewCheckBox", "role": "Show preview overlay", "value": true}
{"kind": "dialog_button", "id": "dialog:OK", "role": "OK", "value": null}
{"kind": "dialog_button", "id": "dialog:Cancel", "role": "Cancel", "value": null}
{"kind": "tree", "id": "tree:Pad", "role": "Pad", "value": null, "type": "pad", "selected": false}
{"kind": "tree", "id": "tree:Sketch", "role": "Sketch", "value": null, "type": "sketch", "selected": false}
{"kind": "tree", "id": "tree:DatumPlane", "role": "DatumPlane", "value": null, "type": "datum_plane", "selected": false}
{"kind": "tree", "id": "tree:Pocket", "role": "Pocket", "value": null, "type": "pocket", "selected": false}
{"kind": "tree", "id": "tree:Sketch002", "role": "Sketch002", "value": null, "type": "sketch", "selected": true}
```

context: `{"workbench": "PartDesignWorkbench", "document": true, "dialog": "TaskPadPocketParameters", "sketch_open": null, "tool": null, "selection": [], "tip": "Pad001", "undo": 15}`

While the circle tool waits for its centre: `{"kind": "view_field", "id": "view:x", "role": "x", "value": 77.3, "text": "77.30 mm", "entered": false}`, the same for `view:y`, and `{"kind": "pick", "id": "pick:origin", "role": "sketch origin", "value": null}`.

A reply's `status` is `ok`; `rejected` (no such element, it is disabled, no such entry: nothing changed); `refused` (FreeCAD said no: an error box, or OK did not close the dialog); `error`; or `crash` / `timeout` (the instance was replaced).

## Interface recipes

`recipes.recipe(step, context)` gives the actions for one step. `{ ... }` is an any-order group. `H` is the base's height; `floor` is `H`, or the wall thickness on a shelled base. Pieces used below:

| Piece | Actions |
| --- | --- |
| sketch on XY | `button:PartDesign_NewSketch`, `list:listWidget = XY-plane (Base plane)`, `dialog:OK` |
| sketch at z | `tree:XY_Plane`, `button:Part_DatumPlane`, `field:attachmentOffsetZ = z`, `dialog:OK`, `tree:<the new datum plane>`, `button:PartDesign_NewSketch` |
| circle | `button:Sketcher_CreateCircle`, { `view:x`, `view:y` }, `view:diameter` |
| rectangle | `button:Sketcher_CreateRectangle_Center`, { `view:x`, `view:y` }, { `view:length`, `view:width` } |
| hexagon | `button:Sketcher_CreateHexagon`, { `view:x 0`, `view:y 0` }, { `view:corner_radius = across_flats / sqrt 3`, `view:angle 90` } |
| slot | `button:Sketcher_CreateSlot`, { `view:x`, `view:y` } (centre of one round end), { `view:centre_distance = length - width`, `view:angle` }, `view:end_radius = width / 2` |
| pad h | `button:PartDesign_Pad`, `field:lengthEdit = h`, `dialog:OK` |
| pocket d | `button:PartDesign_Pocket`, `field:lengthEdit = d`, `dialog:OK` |
| pocket through | `button:PartDesign_Pocket`, `dropdown:changeMode = Through all`, `dialog:OK` |

| Step kind | Actions (**bold** = semantic pick) |
| --- | --- |
| `block` | `button:Std_New`, `button:PartDesign_Body`, sketch on XY, rectangle at 0, 0, `button:Sketcher_LeaveSketch`, pad height |
| `cylinder` | as `block` with a circle |
| `hex` | as `block` with the hexagon |
| `ring` | as `block` with two circles in the one sketch |
| `corner_radius` | **`pick:edges:vertical`**, `button:PartDesign_Fillet`, `field:filletRadius`, `dialog:OK` |
| `top_chamfer` | **`pick:edges:top_face`**, `button:PartDesign_Chamfer`, `field:chamferSize`, `dialog:OK` |
| `top_fillet` | **`pick:edges:top_face`**, `button:PartDesign_Fillet`, `field:filletRadius`, `dialog:OK` |
| `shell` | **`pick:face:top`**, `button:PartDesign_Thickness`, { `field:Value`, `dropdown:joinComboBox = Intersection` }, `dialog:OK` |
| `hole` | sketch at H, circle, leave, pocket through |
| `blind_hole` | sketch at H, circle, leave, pocket depth |
| `counterbore` | sketch at H, circle (hole diameter), leave, `button:PartDesign_Hole`, { `dropdown:HoleCutType = Counterbore`, `dropdown:DepthType = Through all`, `field:Diameter` }, { `field:HoleCutDiameter`, `field:HoleCutDepth` }, `dialog:OK` |
| `boss` | sketch at floor, circle, leave, pad height |
| `pad` | sketch at floor, rectangle, leave, pad height |
| `pocket` | sketch at H, rectangle, leave, pocket depth |
| `slot` | sketch at H, slot, leave, pocket depth |
| `polar` | as `hole` at x = circle_diameter / 2, y = 0, then `tree:<tip>`, `button:PartDesign_PolarPattern`, { `dropdown:comboDirection = Base Z-axis`, `field:spinOccurrences` }, `dialog:OK` |
| `row` | as `hole` at x = -(count - 1) * spacing / 2, then `tree:<tip>`, `button:PartDesign_LinearPattern`, { `dropdown:comboDirection = Base X-axis`, `dropdown:comboMode = Spacing`, `field:spinOccurrences` }, `field:spinSpacing`, `dialog:OK` |
| `hole_pair`, `boss_pair`, `pocket_pair` | the single feature, then `tree:<tip>`, `button:PartDesign_Mirrored`, `dropdown:comboPlane = Base YZ-plane`, `dialog:OK` |

Only the four edge treatments use a semantic pick (one each): which edges or which face of the solid. Everything else, all 12 features and all 4 starts included, goes through real widgets. Every position and size is a typed number that FreeCAD turns into a dimension constraint; nothing is placed by a free click. `pick:plane:*`, `pick:face:bottom`, the other edge rules and `pick:origin` exist as elements but no recipe needs them.

Three things differ from the command recipes, all forced by the interface:

1. **Height.** The sketcher's "new sketch" has no height field. A sketch above the XY plane sits on a datum plane made first (`Part_DatumPlane`, offset typed into its dialog). So every feature leaves one datum plane in the tree.
2. **Derived numbers.** FreeCAD's hexagon tool asks for a corner radius and its slot tool for end centres and an end radius, so those are worked out from the step's slots (arithmetic, shown above). The command recipes need arithmetic only for `polar` and `row`.
3. **Six decimals.** The sketcher writes every typed number into the sketch with six decimals (a field that holds 11.547005383792516 gives a constraint of 11.547005). Plan numbers are far shorter; only the hexagon's corner radius is rounded, by at most 0.0000005 mm, well inside the tolerance. The hexagon's rotation is typed (90 degrees) but FreeCAD adds no constraint for it, so that sketch keeps one degree of freedom; its geometry is exact.

## Commands to run

From the repo root. Each starts ONE hidden FreeCAD (about 1 GB and a full core) and kills it at the end.

| I want to… | Command |
| --- | --- |
| Prove the recipes against the stored solids (carries on where it stopped) | `uv run python -m forge.freecad_ui.prove --per-base 150 --long-per-base 25 --workers 1 --max-minutes 15` |
| Print the numbers of the proof so far | `uv run python -m forge.freecad_ui.prove --summary-only` |
| Prove recovery | `uv run python -m forge.freecad_ui.recover --per-base 3 --long-per-base 1 --kills 4` |
| Measure speed and memory | `uv run python -m forge.freecad_ui.bench --parts 64 --skip-two` |
| Run the tests without FreeCAD | `uv run pytest tests/test_freecad_ui_recipes.py` |
| Run the tests with one hidden FreeCAD | `FORGE_UI_LIVE=1 uv run pytest tests/test_freecad_ui_live.py` |

The proof writes one line per part to `data/freecad_ui/prove/results.jsonl`; parts already there are skipped.

## Measured (6 Oct 2026, this Mac on battery and heavily loaded by other jobs, FreeCAD 1.1.4)

| What | Result | Command |
| --- | --- | --- |
| Recipes against the stored solids | 680 parts (600 with 1 to 5 features, 80 long), 3,271 steps, 48,016 interface actions, of which 269 are semantic picks: 0 parts differ; all 20 step kinds matched every time (table below). 20 long ring parts of the 700-part sample are not built yet | `python -m forge.freecad_ui.prove --per-base 150 --long-per-base 25` (three runs, resumed) |
| One mismatch found and fixed on the way | 1 of the first 610 parts (a slot along Y, high up on a ring) came out mirrored: the typed angle took its sign from the pointer. Fixed in `picks.park_pointer`; that part and the 70 after it were built with the fix, the first 609 before it. Its old line is kept in `data/freecad_ui/prove/superseded_failures.jsonl` | same |
| A wrong dialog opened and cancelled | 100 of 100 left nothing behind (same items, same solid, same undo count); in 91 FreeCAD had already made the feature when the dialog opened; in 57 a number was typed first | `python -m forge.freecad_ui.recover --per-base 3 --long-per-base 1 --kills 4` |
| A wrong number confirmed, then one press of Undo | 37 of 37 restored the document exactly | same run |
| An OK that FreeCAD refuses (a fillet ten times the part) | 40 of 40 reported as `refused` with FreeCAD's text ("BRep_API: command not done"), dialog still open, nothing left after Cancel | same run |
| FreeCAD killed at a random action | 4 of 4 parts finished and matched; a kill cost 6.1 s on average (slowest 8.1 s), 42 actions replayed on average (most 86) | same run |
| Parts built with those mistakes in them | 16 of 16 still matched the stored solid | same run |
| Speed, one instance | 23.0 actions/s, 998 parts/hour (64 mixed parts, 5,311 actions); 25.7 actions/s and 1,440 parts/hour on 313 mostly short parts | `python -m forge.freecad_ui.bench --parts 64 --skip-two`; the second proof run |
| Speed, two instances | 35.3 actions/s, 1,439 parts/hour (48 parts, 16 of them long) | `python -m forge.freecad_ui.prove --per-base 8 --long-per-base 4 --workers 2` (a trial, before the machine had to be left to other jobs) |
| FreeCAD crashes or hangs on its own | 0 in every run above (about 60,000 actions) | the runs above |
| Memory of one instance | 711 MB after 10 parts, 1,246 MB after 60: about 11 MB per part. Time per action stayed flat (40 ms at 10 parts, 38 ms at 60) | the bench run |

The speeds were measured while other jobs loaded the machine (load average 25 to 56 on 8 cores), so they are a floor.

| Step kind | Built | Matched |
| --- | --- | --- |
| `block` | 175 | 175 |
| `cylinder` | 175 | 175 |
| `hex` | 175 | 175 |
| `ring` | 155 | 155 |
| `corner_radius` | 23 | 23 |
| `top_chamfer` | 99 | 99 |
| `top_fillet` | 89 | 89 |
| `shell` | 58 | 58 |
| `hole` | 342 | 342 |
| `blind_hole` | 238 | 238 |
| `counterbore` | 187 | 187 |
| `boss` | 294 | 294 |
| `pad` | 242 | 242 |
| `pocket` | 193 | 193 |
| `slot` | 176 | 176 |
| `polar` | 153 | 153 |
| `row` | 65 | 65 |
| `hole_pair` | 205 | 205 |
| `boss_pair` | 146 | 146 |
| `pocket_pair` | 81 | 81 |

## What FreeCAD did that got in the way

- **A hidden app is demoted after about 30 seconds.** `ps` priority fell from 46 to 28, timers fired 100 to 300 ms late and parts took 40 to 90 s instead of 4. The cure is an `NSProcessInfo` activity with the "user initiated" and "latency critical" options, called with exact C prototypes (`inside/macos.py`). A first version of that call returned a token and did nothing.
- **Timers do not fire inside a timer callback's nested event loop** (macOS). With the server on a polling timer, a command that opened a blocking box from inside our action hung FreeCAD for ever, because the modal guard is a timer too. The server now runs from socket notifiers.
- **The menu bar leaks.** On macOS FreeCAD empties and rebuilds the menu bar on every workbench change, and entering or leaving a sketch is one. About 180 dead menus per part stay behind and FreeCAD's own button refresh walks them all: a 1.9 s part took 4.4 s ten parts later. `dispatch.drop_stale_menus` deletes them between parts; part time is then flat.
- **Crashes found by the noisy runs** (none in any clean run): clicking a tree row twice in our own code while the sketch-plane dialog was rebuilding rows; Qt sending a toolbar button a "pointer entered" event while a dialog was being destroyed (toolbars are now deaf to the mouse); Undo while a drawing tool was part-way through a shape. Each wrote a macOS crash report and may have shown a "quit unexpectedly" box.
- **Memory still grows** about 11 MB per part, hence the planned restart.
- **A typed angle takes its sign from the side the pointer is on.** A slot typed at 90 degrees ran downwards while the pointer sat below its start. The pointer is parked up and to the right of the origin.
- **A dialog's feature exists as soon as the dialog opens**; Cancel removes it (measured below). A pattern's half-made feature can have unset links, so the document reader tolerates unreadable features.
- **Undo and Redo have no object name** on the toolbar; they are recognised by their text.
- **The sketcher's element list** answers `item(i)` with an object that has no text; lists are read through `QListWidget` itself.
- `Gui.isCommandActive` is never called (it crashes FreeCAD 1.1.4 for `Std_Expression`); a button's own `QAction.isEnabled()` is read instead. Count boxes are typed into, never `setValue`.

## Teacher, noise and sessions

Same shape as the command level (`forge/freecad/teacher.py`, `noise.py`, `sessions.py`, `audit.py`).

**Teacher.** `teacher(plan, snapshot)` is a pure function. It reads the plan as segments of the recipes (new document, body, sketch, datum plane, shape, leave, dialog), ticks them off against the document's items, and answers with the set of acceptable next actions:

- on plan: what the next segment needs now. A selection, its button, the dialog settings that do not yet show the plan's value (a wrong number is simply typed again), OK when they all do, the on-view fields not entered yet, leaving a finished sketch;
- a dialog or sketch that is open but not needed is closed first (`dialog:Cancel`, `button:Sketcher_LeaveSketch`);
- off plan (a confirmed wrong number, a wrong or extra object, a failed feature, or a solid whose volume is not what the plan gives so far): `dialog:Cancel` if a dialog is open, otherwise `button:Std_Undo` until what is left is on plan;
- everything built and nothing open: `done`, the one target that is not an interface element.

The volume check exists because every setting of a feature can read right while its solid is wrong: a hole switched to "Countersink" and back to "Counterbore" kept all its numbers and cut 729 cubic millimetres too many. It is made only between plan items.

**Noise.** Levels 0, 0, 0.1, 0.2, 0.3 per session. Kinds: `wrong_button`, `wrong_value`, `wrong_entry`, `ok_too_early`, `stray_dialog`, `stray_click`, `extra_undo`. Only buttons in `noise.WRONG_BUTTONS` are ever pressed. Buttons that open a macOS panel (`Std_Open`, `Std_Save`, print, import, export, ...) are not listed as elements at all (`widgets.HIDDEN_BUTTONS`). Noise never changes a setting no recipe sets, never presses "new document" or "new body", and never presses Undo while a drawing tool is part-way through a shape (that crashed FreeCAD).

**Sessions.** One per part, splits from `forge.system1.splits`, slices in this order: `iid` (300 parts), `pairing` (300), `long` (200), then `train`. Shards of 50 parts (25 for `long`); a shard appears only when it is whole; the manifest is rewritten after every shard. A session that reaches `done` on a part that is not the stored solid is never written: it is counted (`excluded_wrong_part`) and named in the shard's stats. Every session starts from an empty FreeCAD (the command level's other start states are not done here).

| I want to… | Command |
| --- | --- |
| Record sessions (carries on where it stopped) | `uv run python -m forge.freecad_ui.sessions --workers 2 --max-minutes 200` |
| Audit them, replaying a sample in a fresh FreeCAD | `uv run python -m forge.freecad_ui.audit --replay-sample 160 --replay-workers 2` |
| Read sessions step by step | `uv run python -m forge.freecad_ui.show_session --shard data/freecad_ui/sessions/iid/shard000.jsonl.gz --first 3 --brief` |
| Prove the teacher (clean, then with noise) | `uv run python -m forge.freecad_ui.teacher_proof --per-base 65 --long-per-base 15 --workers 2` (add `--noise 0.3`) |

### Sessions recorded so far: three small trials, none audited clean, no training data

This line was an unfilled placeholder (`SESSION_NUMBERS`) until 6 Oct 2026. The numbers are from the trial logs in `data/freecad_ui/`.

| Trial | Folder | Sessions | Steps | Audit (`*_audit.log`) |
| --- | --- | --- | --- | --- |
| 1 | `sessions_trial` | 100 (25 per slice: `iid`, `pairing`, `long`, `train`) | 13,304 | FAILED on replay: 4 of 100 sessions did not reproduce ("the snapshot's elements is not the recorded one"). Teacher, splits and endings passed. 2 instance restarts during the replay |
| 2 | `sessions_trial2` | 60 (15 per slice) | 8,017 | FAILED on replay: 3 of 60 did not reproduce (an action "gave ok, recorded refused"). Teacher, splits and endings passed. 2 instance restarts |
| 3 | `sessions_trial3` | 30 written when the log ends (`iid` 15, `pairing` 15); 4 shards were planned | 3,603 | no audit result: `trial3_audit.log` is empty (0 bytes) |

So the interface level has a proof (680 parts) and no usable sessions. Noisy interface sessions do not yet replay exactly, and that is not solved.

## Not here yet

- **Start states other than empty** (a half-built or stray document, as `forge/freecad/starts.py` has).
- **Noise on settings no recipe sets** (a pad's "Symmetric" mode): nothing in the document reading would show the teacher what to repair.
- **Exact replay of every noisy session.** In the two audited trials, 4 of 100 and 3 of 60 replayed sessions did not reproduce step for step (table above).
- **Clearing the selection.** There is no element for "click on empty space".
- **A fully constrained hexagon**, and picks beyond top / bottom faces and four edge rules.
- **Linux and Windows.** Hiding the window, the Dock policy and the App Nap call are macOS only.
- **A visible, watchable build** (`paint=True` and a window that is not hidden) has not been tried.
- Structures of several parts.
