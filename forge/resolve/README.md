# resolve/ — from a structured plan to an exact, checked, built structure

**The contract is the plan language (Draft 2), as read by `forge/plan/`.** `forge/plan/` reads the words of a plan; this folder works out the geometry: every part's exact size, position and turn, the executor's reply to every line, and a CadQuery program that builds the result so the CAD kernel can check the arithmetic.

```
resolve/
  space.py          points, quarter turns, frames (Box) and their named anchors
  shapes.py         the ten shapes as numbers: frame, outline (hull), volume by formula
  bodies.py         Body: one solid with its sizes, turn, place, features; its exact frame
  defaults.py       the rule table for sizes written `default` and for unwritten feature slots
  scene.py          what exists so far (World) and how a line looks at it (Scene): sets, sizes
  pins.py           "this face of the new part is at that height": solving one axis
  placing.py        placement and alignments -> pins
  prototype.py      one copy of what a part line describes, in its place
  repeating.py      repetition: corners, rows, grids, circles, mirror images
  spans.py          `spans from ... to ...`: the one tilted part
  featuring.py      features on a named face of a part
  contact.py        overlap / touch / apart by arithmetic (boxes and square cylinders)
  judge.py          the CAD kernel, through the sandbox, for every other pair
  resolver.py       the line-by-line driver: replies, undo, groups, `done`
  program.py        writes the reference CadQuery program (text only; never runs it)
  verify.py         runs that program in the sandbox and compares kernel with arithmetic
  render.py         shaded pictures and the build animation
  build.py          resolve, build and draw plans                                  [command]
  random_plans.py   small random plans from the vocabulary (test inputs only)
  property_test.py  kernel against resolver on thousands of random plans           [command]
  comma_fix.py      commas between sizes -> `by`, in memory (old Draft 1 plans)
  examples/         plans that build: a corrected robot dog, a buildable every-form sampler
```

## Use it

```
uv run python -m forge.resolve.build forge/plan/examples/chair.txt --out data/resolve/demo
uv run python -m forge.resolve.build data/coverage/tuning_plans_draft2 --out data/coverage/built --workers 3
uv run python -m forge.resolve.property_test --plans 3000 --workers 3
```

`build` prints the reply to every line and, for a complete plan, writes `NAME.step`, `NAME.py` (the program), `NAME.png`, `NAME.gif` and `summary.json`. Options: `--partial` (also build what was accepted of an incomplete plan), `--fix-commas`, `--workers N` (at most 3).

```python
from forge.resolve import resolve_text

resolution = resolve_text(plan_text)
for reply in resolution.replies:
    reply.number, reply.reply, reply.echo, reply.notes     # "built", how it was read, rest/default values
resolution.complete     # every line built and `done` accepted
resolution.bodies       # every solid: shape, sizes, matrix, centre, frame, volume
```

## How one part line is resolved

Directions: length is x (left low, right high), depth is y (front low, back high), height is z. Every part has a *frame*, the upright box that holds it.

1. **Sizes.** Numbers, `same as`, shares. `default` by the rule table below. `rest` stays open.
2. **Turn.** Sizes are written standing; `lying` / `pointing` is a quarter turn (a matrix). This gives the frame's sizes along length, depth, height.
3. **Pins.** Along each axis the placement and each alignment state one of: *low face at v*, *high face at v*, *middle at v*. "On top of X" pins the new part's bottom to X's top and, weakly, its middle to X's middle in the other two directions. `flush with X's left` pins left to left. These are the document's named anchors: a face anchor is a pin on one axis, an edge two, a corner three.
4. **Solve**, one axis at a time, plain arithmetic (`pins.py`): a known size needs one pin (the last stated one wins); a `rest` size needs a low and a high pin and becomes the distance between them. `offset` is added at the end.
5. **Copies.** A repetition is a list of rigid moves of that one solved copy (slide, turn about an axis, mirror). A placed group is moved the same way, as one unit.
6. **Checks.** Below the ground: `does not fit`. Each new body against every earlier body: `overlaps X`. Each copy must touch an earlier part or the ground: `touches nothing` (not for `above ground`, `gap`, `through`). At `done`: one connected body that reaches the ground.

A rejected line changes nothing (the state is restored). `undo` restores the state before the last built line; `undo to NAME` the state after NAME.

### Turning (our reading of section 4)

| Written | Turn | So |
| --- | --- | --- |
| `lying along length` | tipped over to the right | the top end goes right; the left face becomes the top |
| `lying along depth` | tipped over backwards | the top end goes to the back; the front face becomes the top (a `bar U` opens upward) |
| `pointing down / right / left / back / front` | the written top end faces that way | `right` is the same turn as lying along length |
| wedge | never turned | built directly from its frame, `pointing` and `flat` (section 4 table) |

## The default rules (status: our choice)

A `default` size is taken from the other sizes on the same line; each use is reported in the reply's notes. Rules are tried in order.

| Shape | Size | Rule |
| --- | --- | --- |
| box, wedge | length | same as its depth; else same as its height |
| | depth | same as its length; else same as its height |
| | height | a tenth of its longer side |
| cylinder | diameter / height | same as the other |
| tube | outer diameter | 1.25 times the inner; else same as its height |
| | inner diameter | 0.8 times the outer |
| | height | same as its outer diameter |
| cone | bottom diameter | same as its height |
| | top diameter | 0 (a point) |
| | height | same as its wider end |
| dome | diameter / height | twice the height / half the diameter (half a ball) |
| prism | across / height | same as the other |
| tapered box | bottom length / bottom depth | same as the other |
| | top length, top depth | half the bottom's |
| | height | same as its bottom length |
| bar | profile width / profile depth | same as the other |
| | thickness | a tenth of the smaller profile side |
| | wall (bar tube) | a tenth of the outer diameter |
| | length | ten times the larger profile side (or the outer diameter) |
| any | when no rule can be worked out | the smaller side of the face the placement touches; 100 mm on the ground |

Feature slots that are not written (section 9): `diameter` a quarter of the face's smaller side; `hole diameter` a tenth of it; `depth` half the material under the face; `height` a tenth of the smaller side; `length`, `width` half the face's first / second side; `angle` 0; `count` 4; `circle diameter` 0.6 of the smaller side; `spacing` twice the hole diameter; `radius`, `size` a tenth of the smaller side; `wall` a tenth of the part's smallest side. Exceptions: a counterbored hole's `diameter` is twice its hole diameter and its `depth` a quarter of the material; a slot's `width` is a quarter of its length.

## Which checks are arithmetic, which use the kernel

| Pair | Decided by |
| --- | --- |
| any two parts whose frames are more than 1e-6 mm apart | arithmetic: apart |
| box and box | arithmetic (`contact.py`) |
| box and cylinder, cylinder and cylinder (parallel or crossing at a right angle) | arithmetic |
| anything with a cone, sphere, dome, wedge, tube, prism, tapered box or bar | kernel |
| a part with any feature (hole, boss, hollowed out ...) | kernel |
| a part turned by an angle that is not a quarter turn (a copy `around` with 3, 5, 6 ... copies facing outward) | kernel |
| a spanning part | kernel |
| a `sunk` part | arithmetic if both are plain boxes / cylinders, else kernel |
| every feature line | kernel: is the part still one solid, what does it touch now |
| connection to the ground at `done` | arithmetic on the recorded touching pairs |

"Box" and "cylinder" here mean a plain `box` or `cylinder`, standing or lying, with no features. "Touch" means sharing at least one point and no volume: a face, a line (a cylinder beside a wall) or one point (a ball on a table) all count, as they do for `forge/assembly_geometry.py`.

The kernel is only ever reached through `forge.sandbox`: `judge.py` and `verify.py` write a program with `program.py` and send the text to the sandbox with `structure=True`.

## What `verify` compares

The program builds every body from its written sizes with CadQuery primitives, turns and moves it. The resolver's frame comes from the shape's hull and the matrix, its volume from a formula. Compared: part count and names; one solid per part; each frame (1e-3 mm); each volume where no feature has changed it (1e-6 relative); the overall frame; no overlap other than the declared ones (`sunk`, a span's two ends); every part touches a part or the ground; one group that reaches the ground; and the kernel's touching pairs equal the resolver's.

Not stated exactly by the resolver, and so not compared: the volume of a part with features.

## Document gaps and what was chosen

Each is a place where the plan language did not fix the geometry. The choice is implemented and tested.

| # | Gap | Chosen here |
| --- | --- | --- |
| 1 | a set in an alignment or a size | the frame round all copies; a new part sees its own copy only when the alignment names the set its own placement is on (or one of the two sets of `between A and B`). `X's diameter` of a set is the diameter its copies share |
| 2 | `across` on a side placement | the same rule: `rest` sizes stretch over the set in the two directions along the touched face |
| 3 | `facing outward` | `N around X` beside X always turns each copy about X's axis (the words add nothing there); `N around X on circle D` only moves the copies' middles unless `facing outward` is written |
| 4 | round and sloping contacts | a placement puts frame against frame; contact is then whatever the solids really share. Against a cone, a tapered box or a filleted edge the new part may touch nothing, and is rejected |
| 5 | `through` with several holes | the holes of X are a tube's own hole and every through-hole feature; a round part uses only holes that run the way it runs; the most recent hole line wins; a line that made several holes gives one part per hole. A `through` part may float until `done` |
| 6 | feature slots | the reader's names; unwritten slots by the table above. A `depth` equal to the material is allowed (a pocket right through). `hollowed out` with no `open at` is a closed shell |
| 7 | odd prisms | `across` is flat side to opposite corner, so the frame's middle is not on the axis. Centring uses the frame; `same axis`, `around`, `through` use the axis. A prism counts as a part with its own axis |
| 8 | bar profile when lying | the tipping rule above |
| 9 | tall domes | higher than half the diameter: `does not fit` |
| 10 | a spanning part's cross-section | no roll: the first cross-section size stays level; ends cut square; overlap with its two end parts is allowed like `sunk`; always judged by the kernel |
| 11 | undo in a group | lines are undone one at a time whatever they are: `undo` after `end` reopens the group |
| 12 | where an `on ground` / `above ground` part is, left-right and front-back | over the plan's origin (the middle of the first part), unless alignments move it |
| 13 | below the ground | `does not fit` |
| 14 | `inset` with `top flush with X's bottom` | `inset` applies to same-named flush faces and to corners only |
| 15 | an alignment against the placement | later wins, also over the placement's own contact (the part is then rejected for floating or overlapping). `on ground` and `down to ground` cannot be overridden: `does not fit` |
| 16 | `between A and B` | runs along the direction in which the two frames are apart (several: the `rest` size's direction, else the largest gap); centred on the piece of the facing faces both share; a number instead of `rest` must equal the gap |
| 17 | `done` | one connected body, and it reaches the ground. Two separate bodies both on the ground are rejected |
| 18 | replies to `undo`, `group`, `end`, `done` | `built` (the list has no other word for "accepted") |
| 19 | `sunk` deeper than the part | allowed; a part wholly inside X counts as joined to X |
| 20 | hollow shapes | box and cylinder: exact inner faces. Cone, dome, sphere, tapered box, prism: shelled by the kernel; only flat inner walls, the open face and a ball's round wall are exact; using a sloping inner wall is `does not fit` |
| 21 | features after a boss | `on X:` always means the shape's own face, not the top of a boss already on it |
| 22 | a tube's `diameter` | its outer diameter |
| 23 | `N spread` with N = 1 | centred |
| 24 | slot `angle`, first hole of a `circle of holes` | measured from the face's first direction towards its second; the first hole on the first direction |

Findings about the document's own examples (pinned by tests): the chair comes out 420 x 460 x 900 because `behind seat` puts the posts behind the seat; the robot dog as written is `rejected: not joined to the ground` (the group's frame touches the body, the upper legs stop 5 mm short); the section 12 sampler collides with itself in 12 places. `examples/` holds versions that build.

## Limits

- `top chamfer` and `top fillet` treat every edge of the top face, so holes already in that face get their rims treated too. Write the edge treatment first.
- `hollowed out` must come before other features (rounded or chamfered edges may come first).
- The reader parses a whole plan before the resolver sees it and keeps its own idea of `undo` and of rejected names. When the resolver rejects a line the reader accepted, the two can drift apart; the visible effect is `rejected: unknown part X` on later lines. A live executor would drive the reader one line at a time.

## Tests

`tests/test_resolve_arithmetic.py` (turns, shapes, pins, contact, defaults; no kernel), `tests/test_resolve_plans.py` (whole plans decided by arithmetic alone: the judge fails the test if asked), `tests/test_resolve_kernel.py` (the example plans build and pass every check, features, spans, hollow parts, the command, a 40-plan slice of the property test).
