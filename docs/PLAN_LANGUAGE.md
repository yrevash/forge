# The plan language: what a planner writes and the executor reads

- **Status:** Draft 3, 6 Oct 2026. Draft 3 changes no word of the language: it writes down what the reader and the resolver already do in the places where Draft 2 was silent (contact on the real solid, groups placed by their frame, centring and the ground, sets, `between`, `through`, defaults, replies), and replaces the examples with plans that build and pass every check.
- **Why it exists:** Forge is two models. A planner that knows the world writes a plan; the System 1 executor builds it step by step and checks every step. This file is the contract between them.
- **The reader and the resolver:** `forge/plan/` is a strict parser for exactly this document; `forge/resolve/` works out the geometry and gives the reply to every line. Every example in this file is run through both by the tests. If the code and this file disagree, one of them is a bug.

A planner needs only this file. Read "Three things that catch planners out" first. Sections 2 to 10 are the language; section 11 has three whole plans; section 12 shows every form once.

## Three things that catch planners out

All five plans of the tuning list that failed to build (section 15) failed on one of these. None of them is a missing word; each is a rule about geometry.

**1. A placement works on frames; touching is decided on the real solid.** `left of X` puts the new part's frame against X's frame (section 2). Then the executor asks whether the two real solids share at least a point. For a box the frame and the solid are the same thing. For a cone, a dome, a sphere, a tapered box, the sloping side of a wedge, or an edge that has been rounded or chamfered, part of the frame's face is empty air, and a part put there is `rejected: touches nothing`.

| Written (cone is `cone 100 by 20 by 100`) | Reply | Why |
| --- | --- | --- |
| `key: box 10 by 10 by 10, left of cone` | `rejected: touches nothing` | half way up, the cone is narrower than its frame |
| `key: box 10 by 10 by 10, left of cone, flush with cone's bottom` | `built` | at its bottom the cone is as wide as its frame |
| `key: box 30 by 10 by 10, left of cone, sunk 25 into cone` | `built` | pushed in until it reaches the solid |
| `cap: box 10 by 10 by 10, on top of ball` | `built` | a ball reaches its frame at one point; one shared point is enough |

So against a sloping or round part: put the new part at the end where the solid fills its frame, or sink it in with `sunk N into X`, or use a box as the stand-in. The same rule works the other way round: a flat end pressed against the inside of a round wall cuts into it, and is `rejected: overlaps X`.

**2. A group is placed by its frame.** A placed group (section 8) is moved as one block, using the frame around all its parts. Its widest part decides where it ends up. A leg whose foot is 50 wide and whose upper part is 40 wide, placed `left of body`, has the foot's side in line with the body's side and the upper part 5 short of the body: nothing touches, and `done` answers `rejected: not joined to the ground`. Cure: `offset 5 to the right` on the line that places the group (section 11, the robot dog), or make the part that must touch the widest one.

**3. A part is centred on the face it touches, and nothing may go below the ground.** `left of X` centres the new part on X's left face in the other two directions, also up and down. If the new part is taller than X and X stands on the ground, the new part would reach below height 0: `rejected: does not fit`.

| Written (base is 20 high, on the ground) | Reply |
| --- | --- |
| `block: box 50 by 50 by 50, left of base` | `rejected: does not fit` (15 of it would be below the ground) |
| `block: box 50 by 50 by 50, left of base, flush with base's bottom` | `built` |

## 1. Principles

1. **One line, one step.** Each line adds one part, one set of like parts, or one feature.
2. **A controlled language, not free text.** The planner writes lines in the fixed forms below, word for word. Every planner interface that worked in the research was a constrained language or code; one system trained on template sentences scored about 80% on rephrasings but 16–18% on free human commands. The executor is still trained on many wordings of every line, and free human typing is measured as its own test.
3. **Say what and where, never coordinates.** The executor works out positions. (One exception is kept for features, section 9.)
4. **Leave to a rule what does not matter.** A size written `default` is filled by a rule and reported.
5. **A closed set of moves.** The executor knows this vocabulary and nothing about objects. Whatever the vocabulary can express, it can build. Section 13 lists what it cannot express.
6. **Every step is checked and echoed.** After each line the executor reports how it read the line and whether it was built or rejected, in fixed words (section 10).

## 2. Directions, names, faces and sets

**Directions.** Length runs left to right, depth runs front to back, height runs bottom to top. The front faces the viewer. Every distance is in millimetres.

**Frame.** Every part has a *frame*: the smallest upright box that holds it. A part's six faces are the faces of its frame: `top bottom front back left right`. So a cylinder has a left face too (it touches that face along a line), and a sphere touches each face at one point. A part's length, depth and height are the sizes of its frame as it sits. A boss or a pad raised on a part (section 9) makes its frame bigger.

Placements, alignments and sizes all work on frames. Whether two parts touch or overlap is decided on the real solids (see "Three things", point 1).

**Names.** Every part has a name, unique in the whole plan. Later lines refer to parts by name. A name is made of letters, digits, spaces and hyphens. It may not be `ground`, `done`, `undo`, `end`, `group`, `rest` or `default`, and may not start with `on ` or `group `. Avoid the word `and` inside a name.

**Pointing at a part.** `seat's top` is the top face of seat. After a name ending in s, write `legs's top` or `legs' top`.

**Places on a part (anchors).** Besides the six faces, a part has twelve edges and eight corners, named by the faces that meet there, in the order top/bottom, front/back, left/right:

| Place | Written | Means |
| --- | --- | --- |
| a face | `seat's top` | the middle of that face of the frame |
| an edge | `seat's top-front edge` | the middle of that edge of the frame |
| a corner | `seat's top-front-left corner` | that corner of the frame |

Faces are used everywhere. Edges and corners are used only by `spans` (section 5). On a round part an edge or a corner of the frame is in the air.

**Inner faces.** A hollow part (a `tube`, or a part with the feature `hollowed out`) also has inner faces: `tray's inner left` is the inside of its left wall, `tray's inner bottom` is its floor. A tube has no floor: its `inner bottom` is level with its bottom. Only flat inner walls can be used; the sloping inside of a hollowed cone or tapered box cannot (`rejected: does not fit`).

**Sets.** A line with a repetition (section 7) makes several copies under one name: `legs` may be four parts. Such a name is a *set*. What a set means as a target is fixed in section 7.

**The axis of a part.** For a cylinder, tube, cone, dome, prism or `bar tube` it is the part's own axis as it sits. For every other part, a sphere included, it is the upright line through the middle of its frame.

## 3. How to read a line

A part line has up to five kinds of piece, always in this order, separated by commas:

```
NAME: SHAPE SIZES [TURNING], PLACEMENT [, OPTION ...] [, ALIGNMENT ...] [, REPETITION]
```

Take this line:

```
front legs: box 40 by 40 by rest, under seat, down to ground, inset 20, at the front corners of seat
```

| Piece | Text | Read as |
| --- | --- | --- |
| name | `front legs` | what later lines call this |
| shape and sizes | `box 40 by 40 by rest` | a box 40 long, 40 deep; its height is whatever is left (section 4) |
| placement | `under seat` | its top touches seat's bottom (section 5). Exactly one per line |
| alignment | `down to ground` | it reaches the ground, which gives `rest` its value (section 6) |
| alignment | `inset 20` | moved 20 in from the corner it sits in |
| repetition | `at the front corners of seat` | two copies, one at each front corner (section 7). At most one, always last |

Rules for every line:

- The placement comes straight after the sizes. Options (section 5) and alignments (section 6) follow in any order. The repetition is last.
- Sizes are joined with `by`, never with commas. Commas only separate the pieces.
- A part named in a line must have been made by an earlier line.
- The other kinds of line are `on X: FEATURE` (section 9), `group NAME` ... `end` (section 8), and `undo`, `undo to NAME`, `done` (section 10).

How the executor works out one part line, in order: the sizes; the turn; where the placement puts the frame; what each alignment changes; the value of `rest`; the copies of the repetition; then the checks (nothing below the ground, no overlap, it touches something).

## 4. Shapes and sizes

### Shapes

Write every size of the row, in this order, joined with `by`.

| Shape | Sizes | Example |
| --- | --- | --- |
| `box` | length by depth by height | `box 420 by 420 by 30` |
| `cylinder` | diameter by height | `cylinder 40 by 100` |
| `tube` | outer diameter by inner diameter by height (the inner must be smaller than the outer) | `tube 60 by 50 by 150` |
| `cone` | bottom diameter by top diameter by height (top may be 0: a point) | `cone 520 by 0 by 500` |
| `sphere` | diameter | `sphere 30` |
| `dome` | diameter by height (the top part of a ball, flat side down; height is at most half the diameter) | `dome 60 by 20` |
| `prism` | sides by across by height (sides is a whole number, 3 or more; across is the distance between two opposite flat sides, or from a flat side to the opposite corner when sides is odd; one flat side faces the front) | `prism 6 by 40 by 15` |
| `wedge` | length by depth by height (see "Pointing" below) | `wedge 80 by 60 by 30` |
| `tapered box` | bottom length by bottom depth by top length by top depth by height (top sizes may be 0; the top is centred over the bottom) | `tapered box 100 by 80 by 60 by 40 by 50` |
| `bar` | a profile letter, then the profile's sizes, then length | `bar L 30 by 30 by 3 by 200` |

The profiles of a `bar`, seen from the end:

| Profile | Sizes | Example |
| --- | --- | --- |
| `L`, `T`, `U`, `I` | profile width by profile depth by thickness by length | `bar I 40 by 60 by 4 by 300` |
| `tube` | outer diameter by wall by length | `bar tube 20 by 2 by 300` |

A standing bar has its length running up, its profile width left to right and its profile depth front to back. The L has its corner at the back left; the T has its bar at the back; the U opens to the front; the I has its two bars at front and back. The thickness must leave a profile: less than the profile width and depth (for a U, twice the thickness less than the width; for an I, twice the thickness less than the depth).

Sizes that cannot make the shape are `rejected: does not fit`:

- **A tall dome.** A dome higher than half its diameter is not a dome: `lid: dome 60 by 40, on top of pot` does not fit; `lid: dome 60 by 30, on top of pot` is half a ball.
- **An odd prism.** With an odd number of sides, `across` runs from a flat side to the opposite corner, so the middle of the frame is not on the prism's axis. Centring uses the frame; `on the same axis as`, `around` and `through` use the axis. `pin: cylinder 10 by 10, on top of post, on the same axis as post` sits on the axis of a three-sided post; without the alignment it sits on the middle of the post's frame, a little further back.

### Each size is one of

| Written as | Meaning | Example |
| --- | --- | --- |
| a number | millimetres; decimals are fine; greater than zero | `12.5` |
| `same as X's length` (or `depth`, `height`, `diameter`) | copied from part X as it sits | `same as seat's length` |
| `half of X's length`, `third of`, `quarter of`, `double X's length` | a share of that size of X | `half of tray's depth` |
| `rest` | whatever the rest of the line leaves (rules below) | `box 40 by 40 by rest` |
| `default` | filled by a rule, and reported | `box 400 by default by 20` |

Any other distance in a line (an inset, a gap, a height) is a number, where zero is allowed, or one of the two reference forms: `inset half of X's depth`.

What a copied size is:

- `X's length`, `depth`, `height` are the sizes of X's frame as it sits, after any turn.
- `X's diameter` is the diameter of a round part: a cylinder's, a sphere's, a dome's, the wider end of a cone. **A tube's diameter is its outer diameter**: `plug: cylinder same as pipe's diameter by 10, on top of pipe` is as wide as the outside of the pipe, not as its hole. Every other shape (a box, a prism, a wedge, a tapered box, a bar with a letter profile) has no diameter (`rejected: does not fit`).
- When X is a set: section 7.

### `rest`

`rest` means "long enough to reach". It may be written only for a size a placement can leave over: a box's or wedge's length, depth or height; the height of a cylinder, tube, cone, dome, prism or tapered box; a bar's length. Never a diameter.

A line may have one `rest`, and the line must fix both ends of that size:

| The line has | Then `rest` is | Example |
| --- | --- | --- |
| `between A and B` or `between X` | the gap between the two parts | `rail: box rest by 20 by 40, between left post and right post` |
| `down to ground`, and something that fixes the top (`under X`, `top at height H`, a top alignment) | the height, from that top down to the ground | `leg: box 40 by 40 by rest, under seat, down to ground` |
| both end faces stated by the placement and alignments | the distance between them | `post: box 40 by 40 by rest, on top of seat, top at height 900` |
| `spans from ... to ...` | the distance between the two points (exactly one size must be `rest`) | `stay: box rest by 10 by 10, spans from base's top to tray's bottom` |
| `inside X` | the inner length and/or depth of X, wall to wall (both may be `rest`) | `divider: box 5 by rest by 20, inside tray` |
| `across X` | the two directions along the touched face, over the whole set X (both may be `rest`) | `deck: box rest by rest by 20, on top of legs, across legs` |

The executor reports the value: `rest height = 420`. If nothing is left (the two ends meet or cross), the line is `rejected: does not fit`.

### `default`

A `default` size is taken from the other sizes on the same line, so the part keeps a sensible proportion. The rules know nothing about objects. For each size the rules are tried in order, and the reply says which one was used: `default length = 300 (ten times its larger profile side)`.

| Shape | Size | Rule |
| --- | --- | --- |
| `box`, `wedge` | length | same as its depth; else same as its height |
| | depth | same as its length; else same as its height |
| | height | a tenth of its longer side |
| `cylinder` | diameter, height | same as the other |
| `tube` | outer diameter | 1.25 times its inner diameter; else same as its height |
| | inner diameter | 0.8 times its outer diameter |
| | height | same as its outer diameter |
| `cone` | bottom diameter | same as its height |
| | top diameter | 0 (a point) |
| | height | same as its wider end |
| `dome` | diameter | twice its height (half a ball) |
| | height | half its diameter (half a ball) |
| `prism` | across, height | same as the other |
| `tapered box` | bottom length, bottom depth | same as the other |
| | top length, top depth | half the bottom's |
| | height | same as its bottom length |
| `bar` | profile width, profile depth | same as the other |
| | thickness | a tenth of the smaller profile side |
| | wall (`bar tube`) | a tenth of its outer diameter |
| | length | ten times its larger profile side (`bar tube`: ten times its outer diameter) |
| any | when no rule can be worked out from the line | the smaller side of the face of the part the placement names (the two sides seen from above when the placement is not a face placement); 100 when the placement names no part (`on ground`, `above ground`, the first part of a group) |

Example: `slab: box default by default by default, on top of base`, on a base 400 by 300, is 300 long (nothing on the line sets it), 300 deep (same as its length) and 30 high (a tenth of its longer side). A sphere's diameter has no rule of its own and always takes the last row. A prism's number of sides cannot be `default`.

### Lying down

A `box`, `cylinder`, `tube`, `prism` or `bar` may be turned a quarter turn. The words come straight after the sizes, with no comma. **Sizes are always written for the part standing**, then it is turned.

| Written | Meaning | Example |
| --- | --- | --- |
| nothing, or `standing` | as the shape table says | `cylinder 40 by 100` |
| `lying along length` | tipped over to the right: the written height (a bar's length) now runs left to right. For a box, its written length now runs up; its depth stays | `axle: cylinder 10 by 100 lying along length, through column` |
| `lying along depth` | tipped over backwards: the written height now runs front to back. For a box, its written depth now runs up; its length stays | `hub: cylinder 20 by 60 lying along depth, in front of base` |

Tipping over to the right turns the part's left face up; tipping over backwards turns its front face up. That fixes how a lying bar's profile sits:

| Bar | `lying along length` | `lying along depth` |
| --- | --- | --- |
| `L` | corner at the top back | corner at the bottom left |
| `T` | bar at the back, stem pointing to the front | bar at the bottom, stem pointing up |
| `U` | opens to the front | opens upward (a channel that holds water) |
| `I` | bars at front and back | bars at top and bottom (a beam as it is normally laid) |

Example: `gutter: bar U 30 by 20 by 3 by 200 lying along depth, on top of wall` is a trough open at the top, running front to back.

### Pointing

A `cone`, `dome`, `tapered box` or `wedge` may point in six directions: `pointing up` (the default, not written), `down`, `left`, `right`, `front`, `back`. The words come straight after the sizes.

For a cone, dome or tapered box, the written top end faces that way, and the written height runs along it: `spike: cone 30 by 0 by 60 pointing down, under tray`. For a tapered box pointing left or right, its written length runs up; pointing front or back, its written depth runs up.

A **wedge** is a box cut in half along a slope. Its three sizes are the sizes of its frame as it sits (they are not turned). It keeps two whole faces and thins to a sharp edge:

- `pointing DIR`: the wedge gets thinner towards DIR. The face on the opposite side is whole.
- the *flat* face is the second whole face. It is the bottom, unless `flat FACE` is written after the direction. For `pointing up` and `pointing down` it is the back. The flat face must be beside the direction, not on it or opposite it.
- the sharp edge is where the DIR side meets the flat face. The sloping face runs from there to the far edge of the whole opposite face.

| Written | Whole faces | Sharp edge | Looks like |
| --- | --- | --- | --- |
| `wedge 80 by 60 by 30 pointing left` | right, bottom | bottom-left | a ramp rising to the right |
| `pointing right` | left, bottom | bottom-right | a ramp rising to the left |
| `pointing front` | back, bottom | bottom-front | a ramp rising to the back |
| `pointing back` | front, bottom | bottom-back | a ramp rising to the front |
| `pointing up` (default) | bottom, back | top-back | a lean-to roof whose slope faces the front |
| `pointing down` | top, back | bottom-back | the same, upside down |
| `wedge 60 by 10 by 80 pointing right flat back` | left, back | the upright back-right edge | a blade, triangular seen from above |
| `pointing left flat top` | right, top | top-left | a ramp hanging under a ceiling |

Only the two whole faces of a wedge are solid all over. Put other parts against those.

A `sphere` cannot lie or point. No shape both lies and points.

## 5. Placement: exactly one per line

| Placement | Meaning | Example |
| --- | --- | --- |
| `on ground` | bottom at height 0 | `base: box 400 by 300 by 20, on ground` |
| `above ground` | free-standing, held by height alone; needs `top at height H` or `bottom at height H`. Later parts must connect it to the ground before `done` | `seat: box 420 by 420 by 30, above ground, top at height 450` |
| `on top of X` / `under X` | touching X's top or bottom from outside | `neck: box 60 by 60 by 70, on top of body` |
| `in front of X` / `behind X` / `left of X` / `right of X` | touching that face of X from outside | `plinth: box 100 by 80 by 50, in front of base` |
| `inside X` | X is hollow; the part rests on X's inner floor (for a tube: level with the tube's bottom). It may stick out of the opening | `liner: box 100 by 80 by 20, inside tray` |
| `through X` | in X's hole, on the hole's axis, centred along it. It need not fill the hole | `axle: cylinder 10 by 100 lying along length, through column` |
| `around X` | a `tube` on X's axis with X inside its hole. It sits at the middle of X's height unless an alignment says where | `collar: tube 80 by 60 by 10, around column, bottom 20 above column's bottom` |
| `between A and B` | touching the facing faces of A and B; its size in that direction is `rest` | `post: cylinder 40 by rest, between base and deck` |
| `between X` | X is a set of two copies: between those two | `rail: box rest by 10 by 10, between ears` |
| `spans from X's PLACE to Y's PLACE` | a straight part from one point to another; a PLACE is a face, an edge or a corner (section 2). The size written `rest` runs from point to point. This is the only tilted part in the language | `stay: box rest by 10 by 10, spans from base's top-back-left corner to tray's bottom` |

The first part of a plan is `on ground` or `above ground`: there is nothing else to place it against.

### What every placement obeys

- **Centred.** Unless an option or alignment says otherwise, the new part is centred on the face it touches, in both directions of that face. Centring uses the middles of the two frames.
- **Sideways on the ground.** An `on ground` or `above ground` part stands over the plan's origin: the point of the ground under the middle of the first part. So a second `on ground` part lands inside the first unless an alignment moves it: `annex: box 50 by 50 by 50, on ground, right flush with base's left` stands beside the base.
- **Nothing below the ground.** A line that would put any part of the new part below height 0 is `rejected: does not fit`. This is checked on the frame.
- **No overlap.** A new part that would share volume with an earlier part is `rejected: overlaps X`. Only `sunk N into X` and the two ends of a spanning part may overlap.
- **It must touch.** Every new part (every copy, for a set) must share at least a point with an earlier part or stand on the ground, or it is `rejected: touches nothing`. A face, a line (a cylinder beside a wall) or one point (a ball on a table) all count. Three placements may float until `done`: `above ground`, a face placement with `gap`, and `through`.
- **Touching is decided on the real solids**, not on frames ("Three things", point 1). `finder: box 25 by 8 by 15, behind hump` touches nothing when hump is a tapered box, because the hump's back slopes away from its frame; `finder: box 25 by 8 by 15, behind hump, flush with hump's bottom` touches it along its bottom edge.

### `inside`, `through`, `around`

**`inside X`.** The part stands on X's inner floor, centred between the walls. A `rest` length or depth reaches from wall to wall. In a tube the "walls" are the frame of the round hole, so a box written `rest by rest` cuts into the round wall and overlaps: write a cylinder with the hole's diameter, `plug: cylinder 50 by 10, inside pipe`. A part inside a tube has no floor to rest on; it must fit the hole or touch whatever the tube stands on.

**`through X`.** X is one part (not a set) with a hole. The holes of X are a tube's own hole and every hole made by a feature that goes all the way through: `hole`, `counterbored hole`, `pair of holes`, `circle of holes`, `row of holes`.

- A round part (cylinder, tube, cone, dome, prism, `bar tube`) uses only holes that run the way it runs. Turn it first: `axle: cylinder 10 by 60 lying along length, through column` needs a hole through the column from left to right.
- When X has several such holes, the most recent hole line wins.
- When that line made several holes, the line makes one part per hole, as a set: after `on plate: row of holes count 3, hole diameter 10, spacing 40`, the line `pins: cylinder 10 by 30, through plate` makes three pins.
- The part is centred on the middle of X along the hole. It is checked for overlap like any part: a pin longer than the plate is thick must have room on both sides.
- It may float until `done` (a thin axle in a wide hole touches nothing). Something must hold it by then.

**`around X`.** The new part is a tube and must run the way X's axis runs: `tyre: tube 680 by 600 by 35 lying along depth, around hub` for a hub that lies along depth. It may not float: its hole must fit X (inner diameter equal to X's diameter), or it must touch another part. `ring: tube 80 by 60 by 10, around post` on a post 40 wide touches nothing; `ring: tube 80 by 40 by 10, around post` is built.

### `between`

- **Direction.** The part runs along the direction in which the frames of A and B are apart. When they are apart in more than one direction, it runs along the direction of the size written `rest`; with no `rest`, along the largest gap.
- **Size.** The size along that direction is the gap. Write `rest`. A number is accepted only if it equals the gap exactly: `post: cylinder 40 by 90, between base and deck` in a gap of 100 is `rejected: does not fit`.
- **Centred on what both share.** In the other two directions the part is centred on the piece of the two facing faces that both parts cover. `side aprons: box 20 by rest by 60, between front legs and back legs` is centred on the 40 by 420 patch where a front leg faces a back leg; `top flush with seat's bottom` then moves it up. If the two parts do not face each other at all, the line does not fit.
- Repetitions spread copies over that shared patch: `slats: box 40 by 12 by rest, between lower rail and top rail, 3 evenly spaced along length`.

### `spans`

A spanning part is a `box`, `cylinder`, `tube`, `prism` or `bar` with exactly one size `rest`. Its two ends say everything, so its line has no turning words, options, alignments or repetition. Each end is on a single part, not a set.

- **The two points** are places on the frames of X and Y. The middle of each end of the part is on its point.
- **Cross-section.** The part is not rolled about its length: the first of its two other sizes stays level. `stay: box rest by 10 by 30, spans from base's top to tray's bottom` has its 10 side level and its 30 side in the upright plane of the span. A span that runs exactly along length, depth or height is the same part as one written lying or standing that way.
- **Square ends.** The ends are cut square to the part's length, so a tilted end pokes a little into the part it meets. That overlap with its two end parts is allowed. Overlap with any third part is not: do not end a span on an edge where another part sits.
- It must touch like any other part. A point on the frame of a round part may be in the air.

### Options: changing how the placement touches

These follow a face placement (`on top of`, `under`, `in front of`, `behind`, `left of`, `right of`).

| Option | Meaning | Example |
| --- | --- | --- |
| `gap N` | leave N between the two faces instead of touching. The part may float until `done`; another part must join it to the rest | `blade: wedge 60 by 10 by 80 pointing right, right of base, gap 10` |
| `sunk N into X` | push the new part N into X so the two merge. X is the part the placement names. This is the only overlap the language allows | `knob: sphere 30, on top of column, sunk 5 into column` |
| `across X` | X is a set: make one part that covers all its copies instead of one part per copy. X is the set the placement names. Sizes written `rest` stretch over the whole set | `deck: box rest by rest by 20, on top of legs, across legs` |

A line has a gap or is sunk, not both.

- **`sunk` may be deeper than the part.** `pin: box 10 by 10 by 10, on top of base, sunk 15 into base` lies wholly inside the base. That is allowed, and the pin counts as joined to the base. A sunk part may overlap only the part it names.
- **`across` on a side placement** works the same way: `rest` sizes stretch over the set in the two directions along the touched face. `panel: box rest by 5 by rest, in front of posts, across posts` is as long and as high as the frame around all the posts.
- **`across` without `rest`** centres the one part on the frame around the whole set. If no copy is under its middle it touches nothing: `beam: box 100 by 20 by 10, on top of posts, across posts` floats between two posts 360 apart. Write `rest` for the direction that must reach the copies.

## 6. Alignment: none, one or several

An alignment may name any earlier part, not only the one in the placement.

| Alignment | Meaning | Example |
| --- | --- | --- |
| `flush with X's FACE` | the new part's same-named face is in line with that face of X | `neck: box 60 by 60 by 70, on top of body, flush with body's front` |
| `FACE flush with X's FACE` | that face of the new part is in line with the named face of X. The two faces close off the same direction (top with bottom, left with right) | `apron: box rest by 20 by 60, between legs, top flush with seat's bottom` |
| `FACE N above X's FACE` / `below` | for top and bottom faces: that face of the new part is N higher or lower than the named face of X | `rail: box rest by 20 by 80, between posts, top 20 below posts' top` |
| `FACE N inside X's FACE` / `beyond` | for the other four faces: `inside` is N towards X's middle from that face; `beyond` is N away from X's middle | `fence: box 5 by 100 by 30, on top of deck, left 10 inside deck's left` |
| `inset N` | wherever the part is flush with a face (by `flush with`) or sits in a corner (by a corner repetition), it moves N inward from there instead. A line with neither cannot have `inset` | `legs: cylinder 30 by 200, on top of base, inset 20, at each corner of base` |
| `offset N to the FACE` | moved N towards that side from where it would otherwise be (`to the top` is up) | `head: box 110 by 140 by 90, on top of neck, offset 40 to the front` |
| `top at height H` / `bottom at height H` | that face at a stated height above the ground | `post: box 40 by 40 by rest, on ground, top at height 900` |
| `down to ground` | a part is stretched until its bottom is on the ground: write its height as `rest`, and fix its top with the placement or another alignment. A group (section 8) is moved, not stretched, until its lowest face is on the ground | `leg: box 40 by 40 by rest, under seat, down to ground` |
| `on the same axis as X` | centred on X's axis (section 2) | `nut: prism 6 by 40 by 15, under deck, on the same axis as column` |

An inner face may be named wherever X's face is written: `left 40 inside tray's inner left`, `flush with column's inner bottom` (the new part's bottom on the tube's floor level).

### How alignments combine

Along each of the three directions, the placement and every alignment say where one face (or the middle) of the new part is. The executor settles each direction on its own:

- **The size is known: the last statement wins.** The placement's "centred" is the weakest and loses to anything written. When two alignments move the part in the same direction, the later one wins.
- **The size is `rest`: the last statement about each end is used**, and the size is what lies between them.
- **An alignment also wins over the placement's own contact.** `shelf: box 50 by 50 by 50, on top of base, bottom 30 above base's top` puts the shelf 30 above the base, where it touches nothing and is rejected. An alignment that lifts a part off, or pushes it into, the part it was placed against is almost always a mistake; use `gap N` or `sunk N into X`.
- **The ground cannot be overridden.** `on ground` and `down to ground` fix the bottom at height 0. A line that also puts that part somewhere else in height does not fit: `block: box 50 by 50 by 50, on ground, top at height 80` is `rejected: does not fit` (its top is at 50). A group placed `under X, down to ground` fits only if it is exactly as tall as the space under X.
- **Every `offset` is added at the end**, after all of the above, and several offsets add up. A mirrored copy gets the mirrored offset.
- **`inset` works only with same-named flush faces and with corners.** `flush with base's left, inset 10` puts the part 10 in from the base's left. `right flush with base's left, inset 10` is not moved: the part is outside the base, and there is no "inward". With a `FACE N inside` alignment, write the distance there instead.
- **A set in an alignment** means the frame around all its copies, with one exception (section 7).

## 7. Repetition and sets: none or one

| Repetition | Meaning | Example |
| --- | --- | --- |
| `at each corner of X` | four copies, one in each corner of the face of X the placement touches (for `on ground` and `above ground`: X's outline seen from above). Each copy is tucked into its corner: its two outer faces are flush with X's | `legs: cylinder 30 by 200, on top of base, at each corner of base` |
| `at the front corners of X` (also `back`, `left`, `right`) | the two corners on that side | `pegs: cylinder 10 by 20, on top of deck, at the front corners of deck` |
| `at the front-left corner of X` (also `front-right`, `back-left`, `back-right`) | one part, in that corner | `stud: cylinder 10 by 20, on top of deck, at the back-left corner of deck` |
| `N evenly spaced along length` (or `depth`, `height`) | N copies in a row across the face the placement touches, with equal gaps, **including** a gap before the first and after the last | `slats: box 10 by 100 by 5, on top of deck, 4 evenly spaced along length` |
| `N spread along length` (or `depth`, `height`) | N copies in a row; the first and last are flush with the two ends, with equal gaps between. One copy is centred | `rungs: box 100 by 10 by 5, under deck, 3 spread along depth` |
| `grid N by M` | N by M copies with equal gaps (end gaps included). On a top or bottom face: N along length, M along depth. On a front or back face: N along length, M along height. On a left or right face: N along depth, M along height | `tiles: box 30 by 30 by 3, on top of base, grid 3 by 2` |
| `N around X on circle D` | N copies with their middles on a circle of diameter D about X's axis, the first at the front, in equal steps. Used when the placement is not a side placement | `bolts: cylinder 8 by 10, on top of deck, 6 around column on circle 150` |
| `N around X` | used with `in front of X`, `behind X`, `left of X` or `right of X`: the line places the first copy against X, and the others are that copy turned about X's axis in equal steps. No circle is written, because the copies touch X | `fins: wedge 40 by 5 by 60 pointing left, left of column, 4 around column` |
| `... facing outward` | added to `N around X on circle D`: each copy is also turned about X's axis, so the face that looks away from the axis on the first copy looks away from it on every copy. Without it the copies are only moved and all face the same way. It may also be written after `N around X` beside X, where it changes nothing | `lugs: box 20 by 10 by 10, on top of deck, 4 around column on circle 200 facing outward` |
| `mirrored left-right` / `mirrored front-back` | the line places one part; a second, a true mirror image, is made across the middle of the part the placement names (with `between`: the middle of the gap). Not with `on ground`, `above ground`, `around` or `spans` | `ears: box 30 by 15 by 40, on top of head, flush with head's left, inset 20, mirrored left-right` |

X's axis is defined in section 2. When the axis is not upright the first copy of a circle is at the top.

More about each:

- **`facing outward` beside X changes nothing.** With a side placement, `N around X` always turns each copy about X's axis, whether or not `facing outward` is written: `fins: wedge 250 by 20 by 400 pointing left, left of first stage, 4 around first stage facing outward` and the same line without the last two words make the same four fins.
- **On a circle**, the copies stay at the height (the place along X's axis) where the line put the first one; only the other two directions come from the circle.
- **Rows and grids** spread the copies over the whole face of the part the placement names (the shared patch, for `between`; the inside, for `inside`). Copies are spaced along a direction that lies in the touched face: `on top of X, 3 evenly spaced along height` is not possible. Copies that do not fit in the face are `rejected: does not fit`.
- **Corners.** The part the repetition names gives the positions; the placement gives the contact. They are usually the same part. `legs: box 40 by 40 by rest, on ground, top at height 700, at each corner of top` stands four legs on the ground under the corners of top.
- **Copies of one line may touch each other but not overlap.** Overlapping copies are `rejected: does not fit`.
- **Every copy must touch** an earlier part or the ground by itself; touching another copy of the same line is not enough.
- **Names of copies.** In the executor's notes the copies are numbered: `legs 1` to `legs 4`. A plan cannot use these names (section 13).

### A set as a target

A name that stands for several copies may be used wherever a part is named.

| Written | Meaning | Example |
| --- | --- | --- |
| a placement on a set | one new part per copy, each placed on its own copy. The new name is a set of the same size | `caps: sphere 12, on top of pegs` |
| the same, with `across X` | one part covering all the copies (section 5) | `deck: box rest by rest by 20, on top of legs, across legs` |
| `between X` | X has exactly two copies: one part between them | `rail: box rest by 10 by 10, between ears` |
| `between A and B`, both sets of the same size | one new part per pair: each copy of A with the copy of B nearest to it | `side aprons: box 20 by rest by 60, between front legs and back legs` |
| `between A and B`, one a set | one new part per copy of the set, each between that copy and the single part | `struts: box 10 by 10 by rest, between feet and deck` |
| a set in an alignment, a size or a repetition | the frame around all its copies; but a set the line's own placement is on means, for each new part, its own copy | `top rail: box rest by 20 by 80, between back posts, top 20 below back posts' top` |

A repetition on a line that already makes one part per copy applies to each of them.

The rule for a set in an alignment or a size, in full:

- **Any set means the frame around all its copies**: `bar: box same as legs' length by 10 by 10, on top of base` is as long as the whole row of legs, outside to outside.
- **Except the set this line is placed on** (or either of the two sets of `between A and B`): there each new part sees only its own copy. In `caps: cylinder same as legs' length by 5, on top of legs`, each cap is as wide as its own leg, and `flush with legs' left` would line each cap up with its own leg.
- **`X's diameter` of a set** is the diameter its copies share.
- **Parts placed on a mirrored set are not mirrored.** `tips: box 4 by 4 by 4, on top of ears, flush with ears' left` puts both tips at the left of their own ear. For mirror-image children, place them against the single part the ears stand on and write `mirrored left-right` again.
- **`through` and the two ends of `spans`** need a single part, not a set.

## 8. Groups

A group is a few parts declared once and then placed like a single part, using the frame around all of them.

```
group leg
  upper: box 40 by 50 by 160
  lower: box 30 by 40 by 120, under upper
  foot: box 50 by 70 by 20, under lower
end
front legs: leg, left of body, down to ground, flush with body's front, inset 30, offset 5 to the right, mirrored left-right
```

- The first line of a group has a name, shape and sizes only (no `rest`). It is where the group starts; it has nothing to be placed against.
- Every later line in the group is an ordinary part line or feature line. It may name only parts of the same group. Inside a group there is no ground: `on ground`, `above ground` and `down to ground` do not fit there.
- Parts of a group cannot be named from outside it. Their names still must be unique in the whole plan.
- A group cannot be opened inside a group.
- A group is used by writing its name where the shape goes, with no sizes: `NAME: GROUPNAME, PLACEMENT, ...`. It can take a placement, options, alignments and a repetition like any part. It must be declared before it is used, and may be used more than once.

**A group is placed by its frame** ("Three things", point 2). The placement, every alignment and the centring use the frame around all the group's parts, and the group is moved as one block; nothing in it is stretched or turned. In the example the foot is 50 wide and the upper part 40, so `left of body` alone would leave the upper part 5 short of the body; `offset 5 to the right` closes that. `down to ground` moves the block until its lowest face is at height 0.

Each placed copy must touch something, or float by the usual rules, and no part of it may overlap an earlier part. A feature cannot be put on a placed group; put it on the part inside the group. In the executor's notes the parts of a placed group are called `front legs 1 upper`, `front legs 2 foot`, and so on.

## 9. Features

A feature is cut into, or added onto, one face of a part:

```
on X: FEATURE [SLOT N, SLOT N ...] [, POSITION]
on X's FACE: FEATURE [SLOT N, SLOT N ...] [, POSITION]
```

`on X:` means X's top face. Each slot is its name and a number; a slot that is not written is filled by a rule (below). If X is a set, every copy gets the feature.

| Feature | Slots | Example |
| --- | --- | --- |
| `hole` (all the way through) | `diameter` | `on deck: hole diameter 8` |
| `blind hole` | `diameter`, `depth` | `on deck: blind hole diameter 8, depth 5` |
| `counterbored hole` | `hole diameter`, `diameter`, `depth` (the wider top part) | `on deck: counterbored hole hole diameter 6, diameter 12, depth 4` |
| `boss` (a round raised stud) | `diameter`, `height` | `on deck: boss diameter 20, height 5` |
| `pad` (a raised block) | `length`, `width`, `height` | `on deck: pad length 40, width 20, height 3` |
| `pocket` (a sunk rectangle) | `length`, `width`, `depth` | `on deck's front: pocket length 30, width 8, depth 4` |
| `slot` (a sunk rectangle with round ends) | `length`, `width`, `depth`, `angle` (degrees) | `on deck: slot length 30, width 6, depth 3, angle 90` |
| `circle of holes` | `count`, `hole diameter`, `circle diameter`; always centred | `on base: circle of holes count 6, hole diameter 5, circle diameter 120` |
| `row of holes` | `count`, `hole diameter`, `spacing` | `on base: row of holes count 4, hole diameter 5, spacing 20` |
| `pair of holes`, `pair of bosses`, `pair of pockets` | as the single feature; the twin is a mirror image across the middle of the face, left to right | `on base: pair of holes diameter 6, at (60, 0)` |
| `rounded corners` | `radius`; rounds the four edges that run into the named face. A box only | `on tower's front: rounded corners radius 3` |
| `top chamfer` | `size`; cuts the edge round the top face. Written `on X:` only | `on plinth: top chamfer size 2` |
| `top fillet` | `radius`; rounds the edge round the top face. Written `on X:` only | `on shelf: top fillet radius 2` |
| `hollowed out` | `wall`; makes the part a shell with walls of that thickness. Add `open at FACE` to leave one face open. Written `on X:` only | `on tray: hollowed out wall 5, open at top` |

On a face, a feature's `length` runs along the first direction of that face and its `width` along the second: top or bottom face, length then depth; front or back face, length then height; left or right face, depth then height.

### Where on the face

| Position | Meaning | Example |
| --- | --- | --- |
| nothing, or `centred` | the middle of the face | `on deck: boss diameter 20, height 5, centred` |
| `N from X's FACE` (one, or two in different directions) | the middle of the feature is N in from that edge of the face. A direction that is not written stays centred. X is the feature's own part | `on deck: blind hole diameter 8, depth 5, 30 from deck's left, 20 from deck's front` |
| `at (x, y)` | measured from the middle of the face: x along its first direction (to the right, or to the back on a side face), y along its second (to the back, or up) | `on deck: hole diameter 6, at (50, -20)` |

`at (x, y)` is the one place a planner may write coordinates. It is kept so the existing single-part data (which records x and y) stays usable. Prefer edge distances.

A feature may be put on an inner face of a hollow part: `on tray's inner left: hole diameter 4`.

How the patterns sit:

- **A pair needs a position off the middle**, because the twin is its mirror image: `on base: pair of holes diameter 6` (centred) is `rejected: does not fit`; `on base: pair of holes diameter 6, 40 from base's left` makes one hole 40 from the left edge and its twin 40 from the right edge.
- **A row of holes** runs along the face's first direction and is centred on the position.
- **A circle of holes** has its first hole on the face's first direction (to the right on a top face), and goes round towards the second.
- **A slot's `angle`** is measured from the face's first direction towards its second: on a top face `angle 0` is a slot running left to right, `angle 90` front to back.

### Slots that are not written

Every slot may be left out; the reply says what was used (`default diameter = 75 (a quarter of the face's smaller side)`). `on deck: hole` is a whole line.

| Slot | Rule |
| --- | --- |
| `diameter` | a quarter of the face's smaller side (`counterbored hole`: twice its hole diameter) |
| `hole diameter` | a tenth of the face's smaller side |
| `depth` | half the material under the face (`counterbored hole`: a quarter of it) |
| `height` | a tenth of the face's smaller side |
| `length`, `width` | half the face's first side, half its second side (`slot`: its width is a quarter of its length) |
| `angle` | 0 |
| `count` | 4 |
| `circle diameter` | 0.6 of the face's smaller side |
| `spacing` | twice the hole diameter |
| `radius`, `size` | a tenth of the face's smaller side |
| `wall` | a tenth of the part's smallest side |

A position can follow only a written slot: write `on deck: boss diameter 20, 60 from deck's right`. The bare feature name followed straight by a comma and a position is not read.

### What a feature may and may not do

A feature that breaks one of these is `rejected: does not fit`.

- It must lie inside its face, every hole of a pattern included.
- A `depth` may be at most the material under the face. Equal to it is allowed: `on lid: pocket length 20, width 20, depth 10` on a lid 10 thick is a rectangular opening right through.
- The wider part of a counterbored hole must be wider than its hole.
- A `boss` or `pad` needs a face that is flat and solid all over: any face of a box, an end of a cylinder or prism, the wide end of a cone or tapered box, the flat side of a dome, the two whole faces of a wedge.
- `rounded corners`: the face must be wider than twice the radius. `top chamfer` and `top fillet`: the size must be less than half the face's smaller side and less than the part's height.
- `hollowed out` works on a box, cylinder, cone, dome, sphere, tapered box or prism, and must come before every other feature on that part except rounded corners, a top chamfer or a top fillet. The walls must leave an inside (twice the wall less than the part's smallest side). Without `open at FACE` the part is a closed shell. The open face must be a flat face of the part.
- After every feature the part must still be one solid.
- A feature goes on a face of the frame, so every copy of the part must be turned by quarter turns only. Copies turned about an axis (`N around X` beside X, or `facing outward` on a circle) can take features only when N is 1, 2 or 4.

Order matters in three places:

- **`on X:` always means the part's own face**, also after a boss or pad has been raised on it. A hole written after a boss is placed and measured on the part's own top face, not on top of the boss.
- **A boss or pad makes the part's frame bigger.** A later `on top of X` sits on top of the boss, and is centred on the bigger frame.
- **A top chamfer or fillet treats every edge of the top face**, the rims of holes already in it included. Write the edge treatment first.

A rounded or chamfered edge is no longer where the frame is: a part placed `right of tank, flush with tank's top` after `on tank: top fillet radius 60` touches nothing ("Three things", point 1). A boss that would run into another part is `rejected: overlaps X`.

The slot names are those of `docs/STEPS.md` section 2 with a space for the underscore (`wall` is `wall_thickness`); the STEPS slots `x` and `y` are the position.

## 10. Control and replies

| Line | Meaning |
| --- | --- |
| `undo` | take back the last line that was built |
| `undo to NAME` | take back every line built after NAME. NAME stays |
| `done` | the plan is finished. It is the last line |

After every line the executor answers with its reading of the line in the fixed wording of this document, and one of:

| Reply | Meaning |
| --- | --- |
| `built` | added. Also the reply to `group NAME`, `end`, `undo`, `undo to NAME` and `done` when they are accepted |
| `rejected: overlaps X` | it would run into part X (only `sunk N into X` and the two ends of a span may overlap) |
| `rejected: touches nothing` | it would float. `above ground`, `gap` and `through` are allowed to float until `done` |
| `rejected: does not fit` | a size or position is impossible: below the ground, a `rest` with nothing left, copies that do not fit or run into each other, a shape or feature that cannot be made |
| `rejected: unknown part X` | the line names part X, which does not exist any more: the line that made it was rejected, or was taken back |
| `rejected: not understood` | the line is not in the language. Also: a name no line has made, a name used twice, a line after `done`, `undo` with nothing to take back |
| `rejected: not joined to the ground` | only at `done`: the structure is not one connected body that reaches the ground |

With a reply come notes: the value of every `rest` and `default` (`rest height = 420`), and for a rejection the reason in words (`keys 1 would float`, `rear tyre runs into rear spokes 1`).

A rejected line changes nothing.

**`undo`.**

- `undo` takes back one line, whatever it was: a part, a feature (the part gets its old shape back), or a control line. Inside a group it takes back the last line of the group; straight after `end` it opens the group again.
- A rejected line needs no `undo`: there is nothing to take back. An `undo` written after a rejected line takes back the last line that *was* built.
- `undo to NAME` keeps NAME and takes back everything after it, features on NAME included.
- After a line was rejected for its geometry, write the corrected line under a new name. Its old name still counts as used.

**`done`.**

- The structure must be **one connected body, and it must reach the ground**. Two separate bodies that both stand on the ground are rejected: `shed: box 50 by 50 by 50, on ground, offset 500 to the right` beside a house it does not touch makes `done` answer `rejected: not joined to the ground`.
- "Connected" means joined through parts that touch. A part touches the ground when its frame reaches height 0. A part sunk wholly inside another counts as joined to it.
- Everything that was allowed to float (`above ground`, `gap`, `through`) must be joined by now.
- An open group must have its `end` before `done`.

## 11. Three examples

These three plans are accepted in full by the strict reader and built by the resolver with every check passing: no overlap, every part touching, one connected body on the ground, and the CAD kernel's measurements equal to the resolver's arithmetic. Tests pin both.

**Chair** (16 parts; 420 by 420 by 900)

```
seat: box 420 by 420 by 30, above ground, top at height 450
front legs: box 40 by 40 by rest, under seat, down to ground, at the front corners of seat
back legs: box 40 by 40 by rest, under seat, down to ground, at the back corners of seat
back posts: box 40 by 40 by rest, on top of seat, top at height 900, at the back corners of seat
side aprons: box 20 by rest by 60, between front legs and back legs, top flush with seat's bottom
front apron: box rest by 20 by 60, between front legs, top flush with seat's bottom
back apron: box rest by 20 by 60, between back legs, top flush with seat's bottom
top rail: box rest by 20 by 80, between back posts, top 20 below back posts' top
lower rail: box rest by 20 by 40, between back posts, bottom 110 above seat's top
slats: box 40 by 12 by rest, between lower rail and top rail, 3 evenly spaced along length
done
```

The seat is `above ground` because it floats until its legs arrive. A post cannot pass through the seat (that would overlap), so each back post is two parts: a back leg under the seat's back corner and a back post on top of it, both inside the seat's outline. The two side aprons are one line: `front legs` and `back legs` are both sets of two, so each front leg is paired with the back leg nearest to it.

**Robot dog** (18 parts; 250 by 480 by 580)

```
body: box 160 by 400 by 120, above ground, top at height 380
group leg
  upper: box 40 by 50 by 160
  lower: box 30 by 40 by 120, under upper
  foot: box 50 by 70 by 20, under lower
end
front legs: leg, left of body, down to ground, flush with body's front, inset 30, offset 5 to the right, mirrored left-right
back legs: leg, left of body, down to ground, flush with body's back, inset 30, offset 5 to the right, mirrored left-right
neck: box 60 by 60 by 70, on top of body, flush with body's front
head: box 110 by 140 by 90, on top of neck, offset 40 to the front
ears: box 30 by 15 by 40, on top of head, flush with head's left, inset 20, mirrored left-right
tail: box 20 by 20 by 110, on top of body, flush with body's back
done
```

Each leg group is placed against the body's left face by its frame, moved down until its foot is on the ground, set 30 back from the body's front, and mirrored to the right side. The frame is as wide as the foot (50), and the upper part is 40 wide, so `offset 5 to the right` brings the upper part up against the body; the mirror image gets the same offset mirrored.

**Rocket** (10 parts; 870 by 870 by 7100)

```
first stage: cylinder 370 by 4200, on ground
second stage: cylinder 370 by 1400, on top of first stage
adapter: cone 370 by 520 by 200, on top of second stage
fairing: cylinder 520 by 800, on top of adapter
nose: cone 520 by 0 by 500, on top of fairing
fins: wedge 250 by 20 by 400 pointing left, left of first stage, flush with first stage's bottom, 4 around first stage facing outward
band: tube 390 by 370 by 40, around first stage, flush with first stage's top
done
```

The first fin is a ramp against the left of the first stage, thin end outward; its whole right face meets the cylinder along a line. The other three are that fin turned about the rocket's axis. The band's hole is exactly as wide as the stage, so it touches it all round.

## 12. Every form once

This plan uses every form of sections 4 to 10 at least once. It is a sampler for checking the reader and the resolver, and for looking up how a form is written. It is not an object, but it builds: 82 parts, every check passing, 650 by 460 by 700.

```
base: box 400 by 300 by 20, on ground
tray: box 200 by 150 by 40, above ground, bottom at height 300, left 150 beyond base's left
lamp: sphere 40, above ground, top at height 700
legs: cylinder 30 by 200, on top of base, inset 20, at each corner of base
deck: box rest by rest by 20, on top of legs, across legs
column: tube 60 by 50 by 150, on top of deck
knob: sphere 60, on top of column, sunk 15 into column
cap: dome 30 by 10, on top of knob
stem: cylinder same as cap's diameter by rest, between cap and lamp
nut: prism 6 by 40 by 15, under deck, on the same axis as column
ramp: wedge 80 by 60 by 30 pointing left, left of base, flush with base's bottom, flush with base's front
blade: wedge 60 by 10 by 20 pointing right flat back, right of base, gap 10
link: box rest by 10 by 10, between base and blade
spike: cone 30 by 0 by 60 pointing down, under tray, offset 60 to the left
funnel: cone 50 by 20 by 40, on top of tray, flush with tray's left
plinth: tapered box 100 by 80 by 60 by 40 by 50, in front of base, flush with base's bottom
bowl: dome 60 by 20 pointing down, on top of plinth
angle: bar L 30 by 30 by 3 by 200 lying along depth, left of tray
tee: bar T 30 by 30 by 3 by default, behind base, flush with base's bottom
channel: bar U 30 by 20 by 3 by 200 lying along length, behind tray
joist: bar I 40 by 60 by 4 by rest, between base and deck, offset 100 to the right
pipe: bar tube 20 by 2 by rest, under tray, down to ground
on column's left: hole diameter 10
axle: cylinder 10 by 60 lying along length, through column
collar: tube 80 by 60 by 10, around column, bottom 20 above column's bottom
on tray: hollowed out wall 5, open at top
liner: box half of tray's length by third of tray's depth by quarter of tray's height, inside tray
divider: box 5 by rest by 20, inside tray, left 40 inside tray's inner left
plug: cylinder 50 by 10, inside column, flush with column's inner bottom
tower: box 20 by 20 by double base's height, on top of base, flush with base's front, offset 60 to the right
stay: box rest by 10 by 10, spans from base's top-back-left corner to tray's bottom-back edge
strut: cylinder 8 by rest, spans from base's top-left edge to tray's bottom-front edge
guy: cylinder 6 by rest, spans from funnel's top to lamp's left
lip: box 100 by 5 by 10, in front of deck, top flush with deck's bottom
fence: box 5 by 100 by 30, on top of deck, left 10 inside deck's left
ledge: box 20 by 100 by 5, left of deck, back 15 beyond deck's back
shelf: box 100 by 80 by 10, right of column, top 100 below column's top
hook: box 10 by 10 by rest, behind deck, bottom 50 above base's top, flush with deck's top
pegs: cylinder 10 by 20, on top of deck, at the front corners of deck
stud: cylinder 10 by 20, on top of deck, at the back-left corner of deck
slats: box 10 by 100 by 5, on top of deck, 4 evenly spaced along length
rungs: box 100 by 10 by 5, under deck, 2 spread along depth
tiles: box 30 by 30 by 3, on top of base, grid 3 by 2
bolts: cylinder 8 by 10, on top of deck, 6 around column on circle 150
fins: wedge 40 by 5 by 60 pointing left, left of column, flush with column's top, 4 around column facing outward
lugs: box 20 by 10 by 10, on top of deck, 4 around column on circle 200 facing outward
ears: box 10 by 10 by 30, on top of deck, flush with deck's left, offset 90 to the back, mirrored left-right
handles: box 40 by 10 by 10, right of tray, flush with tray's front, inset 20, mirrored front-back
rail: box rest by 10 by 10, between ears
caps: sphere 12, on top of pegs
group wheel pair
  hub: cylinder 20 by 60 lying along depth
  front wheel: tube 80 by 20 by 10 lying along depth, in front of hub
  back wheel: tube 80 by 20 by 10 lying along depth, behind hub
end
wheels: wheel pair, behind base, down to ground, flush with base's left, inset 40, mirrored left-right
on deck: hole diameter 8
on deck: blind hole diameter 8, depth 5, 30 from deck's left, 20 from deck's front
on deck: counterbored hole hole diameter 6, diameter 12, depth 4, at (50, -20)
on tower's front: rounded corners radius 3
on tower: boss diameter 10, height 5, centred
on deck: pad length 40, width 20, height 3, 40 from deck's right
on deck's front: pocket length 30, width 8, depth 4
on deck's front: slot length 12, width 6, depth 3, angle 90, 60 from deck's left
on base: circle of holes count 6, hole diameter 5, circle diameter 120
on base: row of holes count 4, hole diameter 5, spacing 20
on base: pair of holes diameter 6, at (150, 0)
on base: pair of bosses diameter 10, height 4, at (60, 40)
on base: pair of pockets length 20, width 10, depth 3, at (60, -40)
on plinth: top chamfer size 2
on shelf: top fillet radius 2
on tray's inner left: hole diameter 4
extra: box 10 by 10 by 10, on top of shelf
undo
spare: box 10 by 10 by 10, on top of shelf
on spare: hole diameter 3
undo to spare
done
```

Things to notice, each a rule from above: the tray and the lamp are `above ground` and are joined later (by `pipe` and `stem`); the tray is moved off the plan's origin with `left 150 beyond base's left`, or the column would run into it; the `blade` has a gap and `link` joins it; the `plinth` and the `tee` are `flush with base's bottom`, or they would be centred on the base's 20 high side and reach below the ground; the `axle` needs the hole `on column's left` to run the way it runs; the `plug` is written `cylinder 50`, the tube's inner diameter, because `column's diameter` is 60.

## 13. Not in the language

These cannot be written, on purpose. A planner that needs one of them picks the nearest blocky stand-in or leaves the detail out.

- **Free-form and bent shapes.** Curved hulls, sculpted heads, a tube bent into an elbow or an S, an arch, an egg or oval dish, an ogive nose, a cylinder cut at a slant. This was the largest gap in the 60-object test (18 objects).
- **Free tilt angles.** The only tilted part is one that `spans` two points. A part cannot lean by a stated angle, be twisted about its own length (a pitched fan blade), or be turned by anything other than quarter turns.
- **Placing a part against the real surface of a sloping or round part.** A placement meets the frame ("Three things", point 1). Sink the part in, or put it where the solid fills its frame.
- **Flexible parts.** Straps, cords, chains, cushions.
- **Fine surface detail.** Threads, pleats, weave, grilles and mesh, relief, printed marks.
- **Copies of graded size.** Every copy in a repetition is the same (no harp strings of falling length). Each size needs its own line.
- **Patterns that are not a row, a grid or a circle**, a circle that starts anywhere but at the first copy, and copies over only part of a face (split the part, or use `offset`).
- **Picking one copy out of a set.** A set is used whole. Give a part its own line and name if it must be used alone.
- **Mirror-image children of a mirrored set**, and a top-bottom mirror.
- **Hollowing several stacked parts as one vessel.** `hollowed out` works on one part. Use tubes for the stacked parts.
- **Features positioned from another part** ("a hole above the knob"), and features that follow a slope.
- **Two separate objects in one plan.** `done` needs one connected body.
- **A check that the structure would stand up.** Connection to the ground is checked at `done`; stability is not.
- **Moving joints.** Nothing hinges or slides.

## 14. What the executor has to learn, layer by layer

| Layer | What the data teaches | Exists today |
| --- | --- | --- |
| 1. Shapes | Section 4: ten shapes, lying, pointing, the wedge table | 4 of 10 shapes; no turning |
| 2. Features | Section 9, on any face, with edge distances and unwritten slots | Top face and `at (x, y)` only: 120,000 parts, 248,000 sessions |
| 3. Placement and alignment | Sections 5 and 6, between random pairs of parts, every relation in every direction, inner faces, gap and sunk; which statement wins; contact on the real solid | No |
| 4. Repetition, sets and groups | Sections 7 and 8, including a set as a target, `across`, and a group placed by its frame | Holes only |
| 5. Relative sizes and `rest` | Section 4: every row of the `rest` table | No |
| 6. Spanning parts | Section 5: every pair of place kinds (face, edge, corner) | No |
| 7. Defaults | Sizes written `default`: the table of section 4 | The rule table is in the resolver; furniture defaults are being written for eight furniture kinds |
| 8. Recovery | Undo after any wrong step, and every reply in section 10 | Single parts only |
| 9. Phrasing | Each line in many wordings | Parts only |

The resolver (`forge/resolve/`) now gives, for any plan, the exact size and place of every part and the reply to every line, and its arithmetic is checked against the CAD kernel. It is the source of the right answers for layers 3 to 8.

The research's sharpest warning applies to layer 3: a small model that had never seen one direction in training scored 0% on it, and 49–59% on unseen relative positions. Every relation and every direction must be in training; only combinations are held out for testing.

The three rules at the top of this file are where a planner's picture of the object and the geometry part ways, so the executor's replies there matter most: training must hold many lines that are `rejected: touches nothing` against a sloping or round part, `rejected: does not fit` for reaching below the ground, and `rejected: not joined to the ground` for a group whose frame touches while its parts do not.

Knowing that a "dining chair" is a seat, legs and a back is the planner's job, not the executor's. The furniture generators are still needed: they supply realistic plans for the executor to practise on, and (request, plan) pairs for training the planner later.

## 15. How we know the vocabulary is enough

1. **Paper test (needs no engine):** a planner writes each object from a list; the strict reader (`uv run python -m forge.plan.check FOLDER`) accepts or rejects every line; each rejection names the missing move.
2. **Build test:** accepted plans are resolved and built (`uv run python -m forge.resolve.build FOLDER --out DIR`), and every check must pass.
3. **Eyes:** a person marks pictures of the results.

Rules that keep it honest: one list for tuning and a second, sealed list for the final number; objects marked blocky or not beforehand; written, built and accepted reported separately; planner retries counted; a rule for when to stop adding moves; and plans written by a planner never enter training.

**What has been measured so far (tuning list, 60 objects).** The plans were written once, by a planner that saw only the Draft 2 document and ran no checker. Draft 3 does not change the language, so the same plans score the same.

| Measure | Result | Source |
| --- | --- | --- |
| The planner's own rating of its plans | 48 faithful, 12 rough, 0 not expressible; 52 of the 60 objects marked blocky | `data/coverage/tuning_notes_draft2.md` |
| Reader: plans accepted in full | 60 of 60 plans, 467 of 467 lines | `data/coverage/tuning_draft2_summary.json` |
| Resolver: every line built and `done` accepted | 55 of 60 | `data/coverage/built/summary.json` |
| Built by the CAD kernel with every check passing | 55 of 60 | `data/coverage/built/summary.json` |
| Marked by eye | not done yet | |

**The five that did not build**, with the first rejected line of each:

| Plan | First rejected line | Reply | Rule |
| --- | --- | --- | --- |
| bicycle | `rear tyre: tube 680 by 600 by 35 lying along depth, around rear hub` | `rejected: overlaps rear spokes` | the flat ends of the spokes cut into the round inside of the tyre (point 1). Later: `seat lug: box 40 by 40 by 60, under saddle` touches nothing under a tapered saddle (point 1), and a span runs into a third part |
| camera | `viewfinder: box 25 by 8 by 15, behind prism hump` | `rejected: touches nothing` | the hump is a tapered box; its back slopes away from its frame (point 1) |
| flute | `crown: cylinder 20 by 12 lying along length, left of head joint` | `rejected: does not fit` | a 20 wide crown centred on the end of a 19 wide tube lying on the ground reaches 0.5 below the ground (point 3) |
| motorcycle | `yoke: box 80 by 260 by 40, right of tank, flush with tank's top` | `rejected: touches nothing` | the tank's top edge was rounded with `top fillet radius 60`; the solid is not there any more (point 1) |
| saxophone | `keys: cylinder 30 by 6 lying along depth, in front of body, 6 evenly spaced along height` | `rejected: touches nothing` | the body is a cone, narrower than its frame (point 1) |

**The shared cause.** Not one of the five is a missing word: the reader accepted all 467 lines. Each plan failed on a rule of geometry that Draft 2 did not state, and in four of the five it is the same rule: a part was placed against the frame of a sloping or rounded part at a spot where the real solid is not. The fifth is centring pushing a part below the ground. Most of the other rejected lines in those plans only name a part whose own line had been rejected (`rejected: unknown part X`). These rules are now the first section of this file; whether a planner that reads Draft 3 avoids them has to be measured by writing the list again.

Earlier history, for the record: against Draft 1 the first tuning plans scored 9 of 60 plans and 226 of 390 lines on the Draft 1 reader, and 14 of 60 and 233 of 390 on the Draft 2 reader (`data/coverage/tuning_draft1_summary.json`, `data/coverage/tuning_draft1_plans_on_draft2.json`); 152 of the 158 rejections were sizes joined with commas, which Draft 1's shape table had shown.

The sealed list has not been touched.

Object lists with usable licences: Open Images' 600 classes (CC BY 4.0), Wikidata (CC0), Open English WordNet (CC BY 4.0). PartNet and ShapeNet are non-commercial and are not used.

## 16. Ideas taken from earlier work

Ideas only; no code or data. ShapeAssembly (parts joined by attaching points, "squeeze" between two parts, a part attached at two ends, reflect, translate), named anchors and distribute/grid/radial helpers (aDSL), pointing at an earlier part's size (PlankAssembly), relations over coordinates (Holodeck, SpatialGrammar), and fixed failure replies with rollback (LegoGPT, Inner Monologue).
