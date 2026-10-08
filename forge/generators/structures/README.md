# structures/ — several parts placed together

Generators for eighteen kinds of structure (the hand-written chair demo in `forge/system1/demo_chair.py` came first):

- the first eight: **chair, stool, bench, table, desk, shelf, frame, crate**;
- ten added on 6 Oct 2026: **bed, cabinet, chest, workbench, ladder, pallet, cart, standoffs, fence, toolbox**.

They are practice material for Forge-S1, the step-by-step CAD executor, and later request-and-plan pairs for a planner. Nothing here is a model.

**Every size and proportion in this folder is our own design choice.** None of it comes from a furniture, pallet, ladder or standoff standard, or from a catalogue. The ranges and rules were picked so that the results look like ordinary things, and they are listed below so that they can be reviewed and changed.

```
structures/
  vocabulary.py     ROLES, PLACEMENTS and SHAPES: the shared words, and `place()` which turns one step into parts
  formula.py        text formulas ("seat_top - seat_thickness"), evaluated without eval
  solids.py         a part as numbers; overlap / face / line / apart by arithmetic
  defaults.py       the default rules (sizes nobody states)
  draft.py          building one structure: steps, slots with sources, the program text
  sampling.py       drawing a random structure at level a, b or c
  prompts.py        prompts with mentions and option phrases, and their check
  check.py          kernel measurements against arithmetic
  generate.py       data/structures/<kind>.jsonl, one generator version per kind
  contact_sheet.py  20 random structures per kind as shaded pictures
  render.py         one shaded picture
  show.py           a structure in plain words; `--formulas` lists every formula
  kinds/            one file per kind (geometry + its wording)
    _shared.py      leg frames, stretchers, panel ends, backs (chair, stool, bench, table, desk, bed, workbench)
    _carcass.py     the box a cabinet and a chest of drawers share
```

| I want to… | Command |
| --- | --- |
| See structures in plain words | `uv run python -m forge.generators.structures.show --kinds cart --count 2` |
| See every arithmetic formula | `uv run python -m forge.generators.structures.show --formulas` |
| Look at 20 of each kind | `uv run python -m forge.generators.structures.contact_sheet` → `data/contact_sheets/structures/` |
| Generate and verify | `uv run python -m forge.generators.structures.generate --per-kind 3000 --workers 2` → `data/structures/` |
| Generate some kinds only | `... generate --kinds bed cart` (the summary keeps the other kinds) |
| Run the tests | `uv run pytest tests/test_structures_vocabulary.py tests/test_structures_kinds.py tests/test_structures_prompts.py tests/test_structures_new_kinds.py` |

## 1. What a structure is

An ordered list of **steps**. Each step adds one part or a group of like parts. A step has

- a **role**: what the parts are (legs, aprons, slats …);
- a **placement**: the rule that puts them in place (corners, ring, stacked …);
- a **shape**: every part is a `box`, an upright `cylinder`, or a `cylinder_x` (a cylinder lying left to right: a wheel, a rung, a handle bar);
- **slots**: the numbers the placement needs. Each slot records where its number comes from.

Axes: X is the width (left is −X), Y is the depth (front is −Y), Z is up. Millimetres. Every structure stands on z = 0 and is centred on the Z axis.

A step is self-contained: `vocabulary.place(name, placement, shape, slot_values)` returns its parts from the slot values alone. A step engine for sessions needs nothing else.

### Roles

Added on 6 Oct 2026: `rungs`, `fronts`, `wheels`, `axles`, `handle`. These older roles gained a placement: `posts` (`left_right`, `circle`), `stretchers` (`side`), `rail` (`stacked_across`), `slats` (`long_row`, `cross_row`), `panels` (`front_back`), `bars` (`long_row`).

| Role | What it is | Placements it uses |
| --- | --- | --- |
| `top` | the main horizontal surface: a seat, a table or desk top, a deck, a lid | `level` |
| `bottom` | the floor board of a container or cabinet; a base plate | `level` |
| `legs` | uprights that stand on the floor and carry the top | `corners`, `circle` |
| `posts` | other uprights: back posts, arm posts, corner posts, handle posts, fence posts, standoffs between two plates | `corners`, `back_corners`, `front_corners`, `left_right`, `circle` |
| `aprons` | rails directly under a top, joining the legs (the rails of a bed) | `ring` |
| `stretchers` | rails lower down that tie legs or panel ends together | `ring`, `sides`, `across`, `side` |
| `rail` | a cross rail: the top or lower rail of a back, a plinth; several one above another are the rails of a fence or a headboard | `across`, `stacked_across` |
| `rungs` | the steps of a ladder, one above another | `stacked_across` |
| `slats` | thin strips with gaps between them: back slats, the sides of a crate, bed slats, the deck boards of a pallet, fence pickets | `upright_row`, `stacked_ring`, `long_row`, `cross_row` |
| `arms` | armrests, one each side | `sides` |
| `panels` | flat boards standing on edge: side panels, panel ends, the walls of a box, a divider | `sides`, `side`, `ring`, `front_back` |
| `shelves` | horizontal boards carried between uprights or panels | `stacked`, `level` |
| `back_panel` | a board closing the back of a shelf unit, a chair or a desk (modesty panel); a headboard or footboard; the back board of a workbench | `across` |
| `fronts` | boards closing the front: doors side by side, drawer fronts one above another | `upright_row`, `stacked_across` |
| `bars` | the members of a bare frame; the stringers and skids of a pallet, crate or toolbox | `front_back`, `sides`, `ring`, `corners`, `left_right`, `across`, `upright_row`, `cross_row`, `long_row` |
| `wheels` | four wheels: discs standing on edge at the corners | `corners` |
| `axles` | beams running left to right under a deck, a wheel at each end | `long_row` |
| `handle` | something to hold: a bar between two posts or two ends, a cleat on each end of a crate | `across`, `sides` |

### Placements and their slots

Added on 6 Oct 2026: `stacked_across` and `long_row`, and the lying-cylinder forms of `corners`, `across` and `stacked_across`.

| Placement | Slots | Rule in words |
| --- | --- | --- |
| `level` | width, depth, thickness, top (round parts: diameter, thickness, top) | One horizontal board (or disc) centred on the middle, its upper face at height `top` |
| `corners` | thickness, bottom, top, spread_x, spread_y (round parts: diameter, bottom, top, spread_x, spread_y) (lying round parts: diameter, width, bottom, spread_x, spread_y) | Four uprights, one in each corner of a rectangle `spread_x` by `spread_y` (measured to their outer faces), each running from height `bottom` to height `top`. Lying round parts are wheels: four discs `width` thick standing on edge on `bottom`, the rectangle measured to their outer faces and outer rims |
| `back_corners` | thickness, bottom, top, spread_x, spread_y (round parts: diameter, bottom, top, spread_x, spread_y) | Two uprights in the two BACK corners of that rectangle, from `bottom` to `top` |
| `front_corners` | thickness, bottom, top, spread_x, spread_y (round parts: diameter, bottom, top, spread_x, spread_y) | Two uprights in the two FRONT corners of that rectangle, from `bottom` to `top` |
| `left_right` | thickness, bottom, top, spread_x (round parts: diameter, bottom, top, spread_x) | Two uprights on the centre line, one at the left and one at the right, their outer faces `spread_x` apart, from `bottom` to `top` |
| `circle` | count, thickness, bottom, top, circle (round parts: count, diameter, bottom, top, circle) | `count` uprights evenly spaced on a circle of diameter `circle` (through their centres), the first one at the back, from `bottom` to `top` |
| `ring` | height, thickness, top, length_x, length_y, spread_x, spread_y | Four horizontal members closing a rectangle: front and back ones `length_x` long, left and right ones `length_y` long, outer faces `spread_x` / `spread_y` apart, each `thickness` wide and `height` tall with its upper face at `top` |
| `sides` | height, thickness, top, length_y, spread_x, y | Two members running front to back, one at the left and one at the right, outer faces `spread_x` apart, each `length_y` long and centred at `y`, upper face at `top` |
| `front_back` | height, thickness, top, length_x, spread_y | Two members running left to right, one at the front and one at the back, outer faces `spread_y` apart, each `length_x` long, upper face at `top` |
| `stacked_ring` | count, height, thickness, bottom, pitch, length_x, length_y, spread_x, spread_y | A ring (as above) repeated `count` times going up: the lowest starts at height `bottom`, each next one `pitch` higher |
| `across` | width, height, thickness, y, top (lying round parts: width, diameter, y, top) | One member running left to right, centred, `width` long, `thickness` front to back and `height` tall, at depth position `y`, its upper face at `top`. As a lying round part: a bar `width` long whose highest line is at `top` |
| `stacked_across` | count, width, height, thickness, bottom, pitch, y (lying round parts: count, width, diameter, bottom, pitch, y) | `count` members running left to right, one above another, centred, each `width` long, the lowest with its underside at `bottom`, each next one `pitch` higher, at depth `y` |
| `long_row` | count, length_x, width, height, top, pitch, y | `count` horizontal members running left to right, side by side from front to back `pitch` apart, the row centred at depth `y`, each `length_x` long, `width` wide and `height` tall, upper face at `top` |
| `side` | thickness, length_y, height, top, x | One board running front to back at position `x`, `length_y` long, `thickness` wide and `height` tall, its upper face at `top` |
| `upright_row` | count, width, thickness, bottom, top, pitch, y | `count` vertical members side by side in a row, `pitch` apart and centred, each `width` wide and `thickness` front to back, from `bottom` to `top`, at depth `y` |
| `cross_row` | count, width, length_y, height, top, pitch | `count` horizontal members running front to back, side by side `pitch` apart and centred, each `width` wide, `length_y` long and `height` tall, upper face at `top` |
| `stacked` | count, width, depth, thickness, bottom, pitch, x, y | `count` horizontal boards one above the other, the lowest with its underside at `bottom`, each next one `pitch` higher, centred at (`x`, `y`) |

"Outer faces `spread` apart" means the distance is measured to the outside, so a stated overall width can be a slot directly ("800 wide" fills `sides.spread_x` of a shelf unit).

The round form of `left_right` is in the vocabulary but no kind uses it yet.

### Where a slot's number comes from

| Source | Meaning | Recorded with |
| --- | --- | --- |
| `stated` | The person states it; the number is in every prompt of that structure | the parameter name; if it replaces a default, also the rule and what the rule would have given |
| `default` | A default rule chooses it (section 3) | the rule's name and the numbers it was given |
| `arithmetic` | Worked out from other parameters | the formula as text, e.g. `seat_top - seat_thickness`. A plain constant (`0`, a leg starts on the floor) is also written this way |

Every slot is also a named parameter of the program, always `<step name>_<slot>`: `legs_thickness`, `seat_top`.

## 2. The kinds

The first choice of each option is the **default** (what is built when the prompt says nothing about it). A variant lists only the options that apply to it.

| Kind | Options (default first) | Headline sizes (always stated) | Counts (may be stated) | Sizes that may be stated instead of defaulted |
| --- | --- | --- | --- | --- |
| chair | back: slats / rails / panel; arms: no / yes; stretchers: none / ring / h; legs: square / round | seat width, seat depth, seat height, back height | slats (2–5) | seat thickness, leg size, apron height, post thickness, slat width, top rail height, stretcher height above floor |
| stool | seat: square / round; leg_count: four / three (round seat only); legs: square / round; foot_ring: no / yes (four legs only) | seat width and depth, or seat diameter; seat height | legs (3, always said for a three-legged stool) | seat thickness, leg size, apron height, foot ring height above floor |
| bench | back: none / slats / rails / panel; arms: no / yes (with a back); supports: legs / panels; legs: square / round; stretcher: no / yes | seat length, seat depth, seat height; back height if it has a back | back slats (3–9) | seat thickness, leg size, apron height, end panel thickness, post thickness, slat width, top rail height |
| table | top: rectangular / round; supports: legs / panels; legs: square / round; aprons: yes / no; lower: none / stretchers / shelf; between_ends: nothing / stretcher / shelf (panel ends) | top width and depth, or top diameter; height | none | top thickness, leg size, apron height, end panel thickness, stretcher height above floor, shelf height above floor |
| desk | pedestals: none / left / right / both; supports: panels / legs (no pedestal); modesty: yes / no (not with one pedestal) | width, depth, height | shelves per pedestal (2–4) | top thickness, end panel thickness, leg size, apron height, modesty panel height, lowest pedestal shelf above floor |
| shelf | back: no / yes; plinth: no / yes; doors: no / yes (on the lower part) | width, height, depth | shelves (2–7; the lowest is the bottom board, the highest the top board); doors (1–2) | side thickness, shelf thickness, plinth height, back panel thickness, door thickness |
| frame | layout: flat / upright / box; feet: no / yes (flat only); mid_rail: no / yes (upright and box) | flat: length, width. upright: width, height. box: width, depth, height | cross bars (1–3; none unless stated; none in an upright frame with a mid rail) | bar size; foot length (upright); corner foot height; mid rail height |
| crate | sides: slatted / solid; lid: no / yes; base: flat / skids; handles: none / cleats | length, width, height | slats per side (2–5) | bottom, slat, wall and lid thickness; slat width; corner post size; skid height; cleat height |
| bed | headboard: panel / rails / none; footboard: no / yes; base: slats / platform; centre_rail: no / yes | width, length, height of the deck (top of the slats or platform); headboard height if it has one | slats (9–15); headboard rails (2–4) | leg size, rail height, slat width and thickness, platform thickness, headboard thickness, footboard height |
| cabinet | doors: yes / no; base: plinth / legs / none; back: yes / no | width, depth, height | shelves inside (1–5, not counting the bottom and top boards); doors (1–2) | top, side, bottom, shelf, door and back thickness; plinth height; leg height and size |
| chest | base: plinth / legs / none; back: yes / no | width, depth, height | drawers (2–7) | top, side, bottom, drawer front and back thickness; plinth height; leg height and size |
| workbench | lower: shelf / stretchers / h / none; back_board: no / yes | top length and depth, top height | none | top thickness, leg size, apron height, stretcher height above floor, shelf thickness, back board height and thickness |
| ladder | rungs: round / flat; stabiliser: no / yes | width, height | rungs | side rail thickness and depth, rung diameter or step thickness, stabiliser length |
| pallet | deck: slats / solid; bottom_boards: yes / no | length, width (the height follows) | deck boards | deck board thickness and width, solid deck thickness, stringer width and height, bottom board width |
| cart | handle: back / both / none; sides: no / yes; shelf: no / yes (with both handles) | deck length and width; handle height if it has one | none | wheel diameter and width, deck thickness, handle post size, handle bar diameter, side board height, shelf height |
| standoffs | plates: rectangular / round; standoffs: round / square; tiers: two / three | plate length and width, or plate diameter; overall height | standoffs per level (3–6, round plates) | plate thickness, standoff diameter or side |
| fence | infill: pickets / none; cap: no / yes | width across the posts, post height | rails (2–4); pickets | post size, rail height, height of the lowest rail, picket width and thickness, cap rail thickness |
| toolbox | handle: round / flat; divider: no / yes; base: flat / skids | length, width, height | none | bottom and end thickness, side height, handle size, skid height |

Sizes are drawn from these ranges (mm, our choices):

| Kind | Ranges |
| --- | --- |
| chair | seat 380–500 wide, 380–480 deep; seat height 400–480; back 380–520 above the seat |
| stool | seat 280–400 (side or diameter); seat height 400–550, up to 780 only with a foot ring |
| bench | 900–1800 long, 300–450 deep, seat height 400–480; back 400–500 above the seat |
| table | dining 1200–2200 × 700–1000 × 720–780; coffee 800–1300 × 450–700 × 380–480; side 400–650 × 350–600 × 450–650; round top 400–900 across, 400–750 high |
| desk | 1000–1800 wide (1300+ with two pedestals), 500–800 deep, 720–760 high |
| shelf | 400–1200 wide, 600–2200 high, 200–450 deep (never deeper than wide) |
| frame | flat 400–2000 × 300–1200; upright 400–1500 wide, 600–2000 high; box 400–1500 × 300–1000 × 400–1800 |
| crate | 300–800 long, 200–600 wide, 150–500 high (never taller than long or than 1.5 × wide) |
| bed | 800–1800 wide, 1900–2100 long, deck 250–450 above the floor, headboard 400–800 above the deck |
| cabinet | 400–1200 wide, 300–600 deep (never deeper than wide), 600–2000 high |
| chest | 400–1200 wide, 350–550 deep, 500–1300 high |
| workbench | top 1000–2400 long, 500–900 deep, 800–950 high |
| ladder | 350–500 wide, 1500–4000 high |
| pallet | 800–1400 long, 600–1200 wide (never wider than long) |
| cart | deck 400–700 wide, 600–1200 long; handle 800–1000 high |
| standoffs | plates 60–300 × 40–200 or 60–250 across; overall 25–100 high (50–160 with a middle plate) |
| fence | 1200–2400 across the posts; posts 900–1800 high |
| toolbox | 350–700 long, 150–300 wide, 200–350 high |

Good-sense limits a draw must also pass (it is drawn again otherwise, and the generation summary records how often and why): gaps between slats at least 15 (crate and fence 10, bed 30–160), slats at least 30 wide, at least 100 between back rails, shelf openings at least 150 and shelf spacing at most 700, cross bars at least 50 apart, stretchers at least 30 off the floor and 40 below the aprons, bars at most a sixth of the frame's shorter side, doors 150–600 wide, drawer fronts 90–320 tall, ladder rungs 200–400 apart, fence rails at least 150 apart, at least 60 of room for a hand under a toolbox handle, bed legs at most 100 square.

### Two rules added on 6 Oct 2026

| Rule | Where it lives | What it does |
| --- | --- | --- |
| A board that carries weight is at least 15 thick | `draft.py` (`MIN_BOARD_THICKNESS`, roles `top` and `shelves`) | A seat, a table or desk top, a deck or a shelf thinner than 15 is refused, whether a rule or a stated size chose it; so "0.7 of the rule" can no longer give a 13 mm shelf. A step opened with `light=True` is exempt: a crate's lid and the metal plates of a standoff stack |
| Tall narrow units are rare, not excluded | `sampling.py` (`TALL_NARROW_RATIO` 4, `TALL_NARROW_SHARE` 0.03) | A structure whose height is more than 4 times the smaller of its width and depth is "tall and narrow". 3% of draws ask for one; every other draw must not be one. A kind that is tall and narrow by nature sets `TALL_BY_NATURE` and is exempt: the ladder and the fence panel |

A side effect worth knowing: a bookshelf 300 deep is "tall and narrow" above 1200 high, so most shelf units now come out low and tall ones are the 3%.

## 3. Default rules

Every size nobody states comes from one of these functions in `defaults.py`. A test checks that this table names every rule in the file. The 35 rules marked `reviewed` are the first version, checked by hand on 6 Oct 2026 (with the two fixes in section 2). The 52 marked `new` were added later with the new kinds and options and have not been checked the same way.

| Rule | Inputs | What it gives | Status |
| --- | --- | --- | --- |
| `apron_height` | leg | An apron is one and a half leg thicknesses tall. | reviewed |
| `arm_height` | post | An armrest is half a post thickness tall. | reviewed |
| `arm_post_top` | seat_top, back_top | Arm posts rise 45% of the back's rise above the seat (down to 10). | reviewed |
| `arm_width` | post | An armrest is as wide as the post is thick. | reviewed |
| `axle_size` | diameter | A cart's square axle beam is 0.3 of the wheel diameter (down to 5). | new |
| `back_board_height` | depth | The board along the back of a workbench top is a quarter of the top's depth tall (to the nearest 10). | new |
| `back_panel_height` | seat_top, back_top | A solid back panel covers the top 60% of the back's rise (to the nearest 10). | reviewed |
| `bar_size` | span | Square frame bar: 20 if the frame's longest side is under 500, 30 under 1000, 40 under 1500, else 50. | reviewed |
| `bed_slat_count` | length | Bed slats: one per 160 of the length they cover, at least 6 and at most 16. | new |
| `bed_slat_thickness` | width | A bed slat is 20 thick if the bed is under 1200 wide, else 25. | new |
| `bed_slat_width` | width | A bed slat is 70 wide if the bed is under 1200 wide, else 90. | new |
| `board_thickness` | span | Panel, shelf or side board: 18 if the unit's longest side is under 1000, else 25. | reviewed |
| `bottom_board_count` | length | Bottom boards of a pallet: 3 if it is under 1200 long, else 5. | new |
| `cabinet_leg_height` | height | Legs under a cabinet or chest: 100 tall if the unit is under 1200 tall, else 150. | new |
| `cabinet_leg_thickness` | width | Short legs under a cabinet or chest: 40 square if it is under 900 wide, else 50. | new |
| `cabinet_shelf_count` | opening | Shelves inside a cabinet: one per 350 of opening height less one, at least 1 and at most 5. | new |
| `cap_rail_height` | post | A cap rail lying on the fence posts is 0.4 of a post thickness tall. | new |
| `cart_shelf_top` | deck_top, handle_top | The upper shelf of a cart is half-way between the deck and the handle (to the nearest 10). | new |
| `cart_side_height` | length | Side boards of a cart are 15% of the deck's length tall (to the nearest 10). | new |
| `cleat_height` | span | A cleat handle on a crate's end: 30 tall if its longest side is under 450, else 40. | new |
| `cleat_length` | depth | A cleat handle runs 60% of the crate's width (to the nearest 10). | new |
| `crate_board_thickness` | span | Crate boards: 12 if the crate's longest side is under 450, 15 under 650, else 18. | reviewed |
| `crate_post_thickness` | span | Corner posts of a slatted crate: 30 if its longest side is under 450, else 40. | reviewed |
| `crate_slat_count` | wall_height | Slats up each side of a crate: one per 110 of wall height, at least 2 and at most 5. | reviewed |
| `crate_slat_height` | wall_height, count | Crate slats cover three quarters of the wall: 0.75 x wall / count, down to 5. | reviewed |
| `deck_board_count` | length | Deck boards of a pallet: one per 160 of its length, at least 4 and at most 11. | new |
| `door_count` | width | Doors across an opening: 1 if it is under 500 wide, else a pair. | new |
| `drawer_count` | opening | Drawers up the front of a chest: one per 200 of opening height, at least 2 and at most 6. | new |
| `fence_post_thickness` | height | A fence post is 80 square if the fence is under 1300 tall, else 100. | new |
| `fence_rail_bottom` | height | The lowest fence rail starts 15% of the fence's height above the ground (to the nearest 10); the highest ends as far below the top of the posts. | new |
| `fence_rail_count` | height | Rails of a fence panel: 2 if it is under 1300 tall, else 3. | new |
| `foot_length` | height | Feet of an upright frame: 30% of its height (to the nearest 10), at least 200. | reviewed |
| `footboard_top` | deck_top, headboard_rise | A footboard rises 40% as far above the bed's deck as the headboard does (to the nearest 10), at least 100. | new |
| `frame_foot_height` | bar | A short foot under each corner of a flat frame is three bar sizes tall. | new |
| `handle_diameter` | post | A round handle bar is 0.8 of the thickness of the posts that hold it (down to 5). | new |
| `headboard_rail_bottom` | deck_top, rise | The lowest headboard rail starts 35% of the headboard's rise above the deck (to the nearest 10): above where the mattress ends. | new |
| `headboard_rail_count` | rise | Rails in an open headboard: one per 170 of its rise above the deck, at least 2 and at most 4. | new |
| `inner_panel_x` | desk_width, thickness, side | Centre of the one inner panel of a single pedestal: a pedestal width in from the desk's end (side -1 is the left end, +1 the right). | reviewed |
| `inner_panels_spread` | desk_width, thickness | With a pedestal at each end, the two inner panels leave the middle open: their pedestal-side faces are desk width - 2 x pedestal width + 2 x panel thickness apart. | reviewed |
| `leg_circle` | diameter, leg | Three legs under a round top stand on a circle as wide as keeps them under the top: top diameter - 1.5 x leg thickness, down to 10. | reviewed |
| `leg_thickness` | width, depth | Leg section from the mean side m of the top: 30 (m<350), 40 (<600), 50 (<1100), 60 (<1500), else 80. | reviewed |
| `lower_rail_height` | post | The lower rail of a back is one post thickness tall. | reviewed |
| `lower_rail_top` | seat_top, back_top, rail_height | The lower rail starts a quarter of the back's rise above the seat (to the nearest 10). | reviewed |
| `lower_shelf_top` | height | Upper face of a lower shelf between panel ends: 30% of the height (to the nearest 10). | reviewed |
| `mid_rail_top` | height | Upper face of a frame's mid rail: half the frame's height (to the nearest 10). | new |
| `modesty_height` | height | A desk's modesty panel hangs 45% of the desk's height (to the nearest 10). | reviewed |
| `pallet_board_thickness` | span | Pallet boards: 18 thick if the pallet's longer side is under 1000, else 22. | new |
| `pallet_board_width` | span | Pallet boards: 80 wide if the pallet's longer side is under 1000, else 100. | new |
| `pedestal_floor_gap` | none | The lowest pedestal shelf is 50 above the floor. | reviewed |
| `pedestal_shelf_count` | none | A desk pedestal has 3 shelves. | reviewed |
| `pedestal_width` | desk_width | A desk pedestal is 28% of the desk's width (to the nearest 10), from 300 to 450. | reviewed |
| `picket_count` | clear_width | Fence pickets: one per 130 of the width between the posts, at least 3. | new |
| `picket_gap` | none | Pickets end 50 above the ground and 50 below the top of the posts. | new |
| `picket_thickness` | post | A fence picket is a quarter as thick as the post. | new |
| `picket_width` | height | A fence picket is 70 wide if the fence is under 1300 tall, else 90. | new |
| `plate_thickness` | span | A plate on standoffs: 3 thick if its longer side is under 100, 5 under 200, else 8. | new |
| `plinth_height` | height | Plinth under a shelf unit: 60 if the unit is under 1200 tall, else 80. | reviewed |
| `rail_thickness` | leg | Aprons, stretchers and back rails are half as thick as the leg. | reviewed |
| `round_leg_spread` | diameter | Four legs under a round top stand in a square 0.7 of the top's diameter, down to 10. | reviewed |
| `rung_count` | height | Rungs of a ladder: one per 280 of height, less one, at least 2. | new |
| `rung_diameter` | height | A round rung is 30 across if the ladder is under 2500 tall, else 35. | new |
| `shelf_count` | height | Shelves in a shelf unit: one per 350 of height plus one, at least 2 and at most 7. | reviewed |
| `skid_count` | width | Skids under a crate: 2 if it is under 600 long, else 3. | new |
| `skid_height` | span | Crate skids: 30 tall if the crate's longest side is under 450, else 40. | new |
| `slat_count` | clear_width | One back slat per 110 of clear width between the posts, at least 2 and at most 9. | reviewed |
| `slat_thickness` | rail | A back slat is 0.6 of the rail's thickness. | reviewed |
| `slat_width` | post | A back slat is as wide as the post is thick. | reviewed |
| `stabiliser_width` | width | The stabiliser bar under a ladder is 1.8 times the ladder's width (to the nearest 10). | new |
| `standoff_circle` | diameter, standoff | Standoffs under a round plate stand on a circle 3 standoff widths smaller than the plate. | new |
| `standoff_count` | diameter | Standoffs under a round plate: 3 if it is under 120 across, else 4. | new |
| `standoff_diameter` | span | A standoff: 6 across if the plate's longer side is under 100, 8 under 200, else 12. | new |
| `standoff_spread` | side, standoff | Standoffs stand one standoff width in from each edge of the plate: side - 2 x standoff between their outer faces. | new |
| `stile_depth` | height | A ladder's side rail is 70 deep if the ladder is under 2500 tall, else 90. | new |
| `stile_thickness` | height | A ladder's side rail is 25 thick if the ladder is under 2500 tall, else 30. | new |
| `stretcher_height` | leg | A stretcher is as tall as the leg is thick. | reviewed |
| `stretcher_top` | leg_length | Upper face of the stretchers: 35% of the leg length above the floor, to the nearest 10. | reviewed |
| `stringer_count` | width | Stringers of a pallet: 3 if it is under 1100 wide, else 4. | new |
| `stringer_height` | span | A pallet stringer is 90 tall if the pallet's longer side is under 1000, else 100. | new |
| `stringer_width` | span | A pallet stringer is 45 wide if the pallet's longer side is under 1000, else 60. | new |
| `thin_panel_thickness` | span | Back panel of a shelf unit: 6 if the unit's longest side is under 1000, else 10. | reviewed |
| `top_rail_height` | post | The top rail of a back is two post thicknesses tall. | reviewed |
| `top_thickness` | span | Seat or table top: 20 if its longer side is under 400, 30 under 1200, else 40. | reviewed |
| `tote_handle_size` | depth | The handle of an open toolbox is 25 across if the box is under 220 deep, else 30. | new |
| `tote_side_height` | height | The long sides of an open toolbox are 45% of its overall height tall (to the nearest 10). | new |
| `tread_thickness` | height | A flat step of a ladder is 25 thick if the ladder is under 2500 tall, else 30. | new |
| `wheel_diameter` | length | Cart wheels: 100 across if the deck is under 800 long, 125 under 1000, else 160. | new |
| `wheel_width` | diameter | A cart wheel is 0.3 of its diameter wide (down to 5). | new |

Design choices that live in formulas rather than rules (`show --formulas` prints every formula):

| Choice | Formula |
| --- | --- |
| Legs stand flush with the corners of a rectangular top | `legs_spread_x = seat_width` |
| Aprons and stretchers sit on the legs' centre lines | `aprons_spread_x = legs_spread_x - legs_thickness + aprons_thickness` |
| A back's top rail sits half a post below the top of the posts | `top_rail_top = posts_top - posts_thickness / 2` |
| Slats, pickets and cross bars have equal gaps | `slats_pitch = (top_rail_width + slats_width) / (slats_count + 1)` |
| An arm runs from the front edge to the back post | `arms_length_y = seat_depth - posts_thickness` |
| Shelves are evenly spaced, the highest flush with the top of the sides | `shelves_pitch = (sides_height - shelves_thickness - shelves_bottom) / (shelves_count - 1)` |
| A plinth is set back one and a half board thicknesses | `plinth_y = -sides_length_y / 2 + 2 * plinth_thickness` |
| Pedestal shelves split the height evenly, top compartment open | `pedestal_shelves_pitch = (ends_height - pedestal_shelves_bottom) / pedestal_shelves_count` |
| A table's lower shelf lies on the front and back stretchers | `shelf_top = stretchers_top + shelf_thickness` |
| Crate sides stand on the bottom board; the lid lies on top of them | `posts_top = lid_top - lid_thickness` |
| Crate slats: lowest on the bottom board, highest flush with the top | `slats_pitch = (posts_top - slats_bottom - slats_height) / (slats_count - 1)` |
| Frame bars are square | `long_bars_thickness = long_bars_height` |
| Doors on a shelf unit cover the lower half of the openings (rounded down) | `doors_top = shelves_bottom + 2 * shelves_pitch + shelves_thickness` (the 2 is that half) |
| A pair of doors meets in the middle | `doors_pitch = doors_width` |
| Shelves inside a cabinet are evenly spaced between the bottom and top boards | `shelves_pitch = (sides_top - bottom_top + shelves_thickness) / (shelves_count + 1)` |
| Drawer fronts fill the opening, 3 apart | `fronts_height = (sides_top - bottom_top - (fronts_count - 1) * 3) / fronts_count` |
| Bed slats are spread over the length the head and foot boards leave free | `slats_pitch = (legs_spread_y - headboard_thickness - slats_width) / (slats_count - 1)` |
| Ladder rungs: the same step from the floor to the first rung as between rungs | `rungs_pitch = sides_top / (rungs_count + 1)` |
| A cart's axle beams reach 10 past each edge of the deck, so the wheels run clear of it | `axles_length_x = top_width + 20` |
| Axle beams are centred on the wheels | `axles_top = (wheels_diameter + axles_height) / 2` |
| The middle plate of a standoff stack makes both sets of standoffs equally long | `middle_top = (top_top + base_thickness) / 2` |
| Fence rails: the highest ends as far below the top as the lowest starts above the ground | `rails_pitch = (posts_top - 2 * rails_bottom - rails_height) / (rails_count - 1)` |
| Fence rails are flush with the back of the posts, pickets stand in front of the rails | `pickets_y = rails_y - (rails_thickness + pickets_thickness) / 2` |
| A crate's cleat handles add to its overall length | `handles_spread_x = bottom_width + 2 * handles_thickness` |

## 4. Stated or default: three levels

A structure is drawn at one level, and all its prompts are written at that level, so a slot's source never disagrees with a prompt.

| Level | What the prompt says | Share |
| --- | --- | --- |
| a | the name and the headline sizes. Default variant, every other size from the rules | 20% |
| b | the same, plus options in words and counts | 45% |
| c | the same as b, plus one to three normally-default sizes stated with a value 0.7 to 1.5 times what the rule gives | 35% |

At levels b and c a count is stated three times in four; otherwise the rule's count is used. A choice that is not the default is always said; a default choice is said one time in five ("no arms").

## 5. Prompts, mentions and option phrases

`prompts.py` writes up to three prompts per structure, labelled `template`. While it writes, it records

- **mentions**: each number's position, text, value, the parameter it states and the `[step, slot]` it fills;
- **options**: each option phrase's position and text and the choice it expresses (`by: phrase`), or the count that expresses it (`by: count`, e.g. "3 legs" for a three-legged stool).

A choice that a stated count expresses is never also said in words: "3 slats in the back" stands alone. (Before 6 Oct 2026 it was sometimes said twice: "with back slats, with 3 slats in the back".)

`prompt_problems` checks every prompt: the numbers in the text are exactly the recorded mentions, every mention states a `stated` slot with that slot's value, every `stated` slot is mentioned, every option phrase names a choice the structure has, and every non-default choice is expressed.

## 6. The program

One CadQuery program per structure (`draft.write_program`): stated and default numbers at the top, then each arithmetic number as its formula, then one named solid per part at an absolute position, then `result = cq.Assembly(...)` with one named entry per part, so it opens in FreeCAD as a named tree. The helper `lying_cylinder` is written only into programs that have a lying round part.

## 7. Verification

Arithmetic first (`draft.finish`, no kernel): nothing overlaps, every part is joined to the rest, the overall size is what the kind promised. Then the program runs in the sandbox with `structure=True` and `check.py` compares:

- the number of parts, their names, and that each is one solid;
- every part's own volume (1e-6 relative) and bounding box (0.001 mm);
- the overall bounding box, standing on z = 0, centred on the Z axis;
- the total volume;
- no two parts overlap; every part touches another; all parts form one group;
- every joint arithmetic predicts is a touch the kernel found.

The kernel side is `forge/assembly_geometry.py`, reached through `measure(result, structure=True)`. Without the flag `measure` and the sandbox behave exactly as before.

Each row carries `generator_version`, a hash of what that ONE kind depends on: the shared machinery, the kind's own file, the helper files it imports, and the source of every default rule those files use. Adding a kind or a rule leaves the other kinds' versions unchanged.

## 8. Known limits

- A joint is a butt contact between two faces. There are no tenons, screws or glue lines.
- A rail meeting a ROUND leg touches it along a line, not a face (`line` in `solids.relation`). The kernel reports it as touching (distance 0); it is the weakest joint we generate.
- The back posts of a chair or bench stand on the seat; they are separate parts from the back legs, as in the demo chair.
- A top on two panel ends with nothing between them stands, but would rack in real life.
- Wording is template text. It is varied, not human.
- **No part has a hole.** So a shaft cannot pass THROUGH a collar, a pulley or a bearing block, and a bolt cannot pass through a plate. That is why the shaft-and-collar, bearing-support and flanged-joint assemblies are not here: they need a ring (a cylinder with a bore) and a block with a hole as part shapes, with their own exact contact arithmetic. The standoff stack is the one mechanical kind, and its standoffs only stand between the plates.
- **No part is tilted.** A step ladder, a leaning ladder, a diagonal brace and a gate's diagonal are out. The ladder stands upright.
- **Round parts lie only left to right** (`cylinder_x`). An upright round part touching a lying round one is refused by `solids.relation`, because that contact is not a circle-against-rectangle comparison; no kind needs it.
- A chest of drawers has drawer FRONTS only; there is no drawer box behind a front. A cabinet door is a board with no hinge.
- A cart's wheels are plain discs fixed to the ends of square axle beams; nothing turns.
- Everything is centred on the Z axis, so a part can never stick out on one side only. Doors therefore sit between the sides, and a crate's cleats come in pairs.
