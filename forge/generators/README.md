# generators/ — our own part families

Each family turns parameters into a CadQuery program **and** states what that program must measure as. The program is then executed and measured; a part is kept only if the kernel's measurement matches the family's arithmetic (bounding box within 0.001 mm, volume within one part in a million, round features present). The check is independent of the program, so it catches generator bugs.

```
generators/
  base.py               Part, the check, number formatting, shared maths
  tables.py             loads standard dimension tables
  tables/bd_warehouse/  the tables, copied unchanged (Apache-2.0), with LICENSE and COMMIT
  captions.py           spec-style captions built from a part's own names and numbers, and their check
  prompts.py            varied natural prompts and their check; composed parts are said feature by feature
                        (and can record where each number lands: composed_prompt_with_mentions)
  families/
    fasteners.py        sizes come from the tables
    plates.py           plates, flanges, brackets, spacers; sizes from ranges we chose
    shapes.py           general machined shapes: sketch, extrude, cut, revolve, fillet, chamfer, shell, loft
    turned.py           round parts: bushings, collars, pulleys, hubs, pins, knobs, cups
    flat_plates.py      flat parts: slots, L / T / cross outlines, frames, countersinks, shims
    sections.py         more bar sections: rectangular tube, angle, Z, top-hat
    blocks.py           blocks, brackets and housings built from several solids
    standard_parts.py   more parts sized from the tables: O-rings, screws, standoffs, keyed hubs
    composed.py         random structure: a base plus 1-5 random features in random order; its building half
                        (make_base, draft_of, clear, assemble) is also what forge/system1's step engine runs
    _common.py          sampling and arithmetic helpers shared by the five files above
  __init__.py           FAMILIES: the registry
  structures/           several parts placed together: 18 kinds, from chair and table to bed, cabinet, cart and plate on standoffs
                        (its own README; not part of FAMILIES)
```

69 families. "Standard parts" is how many the tables define; the rest are sampled from ranges we chose.

| Family | What it is | Operations | Sizes from |
| --- | --- | --- | --- |
| `hex_nut` | Hexagon nut | polygon, extrude, hole | ISO 4032 / 4033 / 4035 (67 parts) |
| `square_nut` | Square nut | rect, extrude, hole | DIN 557 (6 parts) |
| `plain_washer` | Flat washer | circles, extrude | ISO 7089 / 7091 / 7093 / 7094 (75 parts) |
| `hex_bolt` | Hexagon head bolt | polygon, extrude | DIN 931, ISO 4014 / 4017 + nominal lengths (842 parts) |
| `socket_head_cap_screw` | Socket head cap screw | extrude, blind hexagon cut | ISO 4762 + nominal lengths (275 parts) |
| `shaft_key` | Parallel key | box | DIN 6885 cross-sections; length ours |
| `spacer` | Round spacer | circles, extrude | ours |
| `plate_with_holes` | Plate with a grid of holes | box, rectangular pattern, hole | ours |
| `round_flange` | Flange with bore and bolt circle | extrude, polar pattern, hole | ours |
| `l_bracket` | L-bracket with two holes | boxes, union, cut | ours |
| `mounting_plate` | Rounded plate with four corner holes | fillet, construction rectangle, hole | ours |
| `polygon_prism` | Bar with 3, 5, 6 or 8 sides | polygon, extrude | ours |
| `slotted_link` | Link with rounded ends and two holes | slot, extrude, hole | ours |
| `u_channel` | U-channel section | polyline, extrude | ours |
| `t_section` | T-section bar | polyline, extrude | ours |
| `i_beam` | I-beam | polyline, extrude | ours |
| `stepped_shaft` | Shaft with 2–4 diameters | outline, revolve | ours |
| `block_with_features` | Block with a pocket, a hole and/or a boss | boxes, cut, union | ours |
| `chamfered_block` | Block with chamfered top edges | chamfer | ours |
| `open_box` | Open-top box | shell | ours |
| `tapered_block` | Block tapering to a smaller top | loft | ours |
| `counterbored_block` | Block with a row of counterbored holes | linear pattern, counterbore | ours |
| `flanged_bushing` | Sleeve with a flange and a through bore | stacked extrudes, hole | ours |
| `shaft_collar` | Thick ring with chamfered outer edges | chamfer on circular edges, hole | ours |
| `v_pulley` | Disc with a bore and a V groove in the rim | revolve of a grooved outline | ours |
| `flanged_hub` | Bolt flange with a raised hub and bore | union, cut, polar pattern | ours |
| `countersunk_washer` | Disc with a countersunk hole | countersink (82 or 90 degrees) | ours |
| `cone_frustum` | Round taper | revolve | ours |
| `knob` | Chamfered grip on a neck, blind bore | union, chamfer, blind cut | ours |
| `stepped_sleeve` | Hollow shaft with 2–3 outer diameters | revolve of a hollow outline | ours |
| `dowel_pin` | Pin with chamfered ends | chamfer on circular edges | ours |
| `cup` | Hollow cylinder with a closed bottom | shell on a cylinder | ours |
| `d_shaft` | Shaft with a flat milled on one end | box cut from a cylinder | ours |
| `eccentric_cam` | Disc with an off-centre bore | offset cut | ours |
| `slotted_plate` | Plate with 1–3 parallel slots | slot, linear pattern, cut through | ours |
| `perforated_strip` | Round-ended strip with a row of holes | slot outline, linear pattern, hole | ours |
| `gusset_plate` | Triangular plate with clipped tips, three holes | polyline, cut | ours |
| `corner_plate` | Flat L-shaped plate, three holes | polyline, cut | ours |
| `t_plate` | Flat T-shaped plate, four holes | polyline, cut | ours |
| `cross_plate` | Flat plus-shaped plate, five holes | union of two bars, cut | ours |
| `rectangular_frame` | Plate with a round-cornered window, four holes | fillet, cut, construction rectangle | ours |
| `countersunk_plate` | Plate with a row of countersunk holes | linear pattern, countersink | ours |
| `slotted_shim` | Thin plate with a slot open to one edge | slot cut across an edge | ours |
| `square_flange` | Square plate, rounded corners, bore, four holes | fillet, construction rectangle, holes | ours |
| `rectangular_tube` | Hollow box section with rounded corners | two filleted solids, cut | ours |
| `angle_section` | L-shaped bar | polyline, extrude | ours |
| `z_section` | Z-shaped bar | polyline, extrude | ours |
| `hat_section` | Top-hat bar | half outline, mirror, extrude | ours |
| `slotted_block` | Block with a slot across its top | box cut | ours |
| `stepped_block` | Block with one end raised | union of boxes | ours |
| `wedge` | Triangular prism | polyline on a side plane, two-way extrude | ours |
| `v_block` | Block with a 90 degree V groove | polyline on a side plane, two-way extrude | ours |
| `dovetail_slide` | Base with a dovetail rail | polyline on a side plane, two-way extrude | ours |
| `t_slot_nut` | T-shaped block with a centre hole | union, cut | ours |
| `pillow_block` | Base with a round-topped housing and a horizontal bore | arc outline, union, horizontal cut | ours |
| `clevis_bracket` | Base with two round-topped ears and a pin hole | arc outline, cuts on two planes | ours |
| `gusseted_bracket` | L-bracket with a triangular rib, four holes | union of three solids, cuts on two planes | ours |
| `enclosure_base` | Rounded open box with four screw posts | fillet, shell, union, cut | ours |
| `o_ring` | O-ring (torus) | revolve of a circle | ISO 3601 (349 parts) |
| `set_screw` | Flat-point set screw with hexagon socket | extrude, blind hexagon cut | ISO 4026 + nominal lengths (137 parts) |
| `cheese_head_screw` | Slotted cheese head screw | extrude, slot cut, union | ISO 1207 + nominal lengths (125 parts) |
| `countersunk_screw` | Socket countersunk head screw | revolve, blind hexagon cut | ISO 10642, 90 degree heads + nominal lengths (108 parts) |
| `shoulder_screw` | Socket head shoulder screw | stacked extrudes, blind hexagon cut | ISO 7379 + nominal lengths (77 parts) |
| `hex_standoff` | Hexagon bar with a clearance hole | polygon, extrude, hole | ISO 4032 across-flats + clearance hole table, M12 and smaller; length ours |
| `keyed_hub` | Round hub with a keyway in its bore | extrude, box cut | DIN 6885 bore, key width and hub keyway depth; outer size ours |
| `composed_block` | Block plus 1–5 random features | box, cut, union, slot, linear pattern, mirror, fillet, chamfer, shell | ours |
| `composed_cylinder` | Cylinder plus 1–5 random features | extrude, cut, union, slot, polar pattern, mirror, fillet, chamfer, shell | ours |
| `composed_hex` | Hexagonal prism plus 1–5 random features | polygon, cut, union, slot, polar pattern, mirror, fillet, chamfer, shell | ours |
| `composed_ring` | Ring (thick tube) plus 1–5 random features | circles, cut, union, polar pattern, mirror, fillet, chamfer | ours |

The general shapes take ideas from published text-to-CAD work (parts as sketch-and-extrude sequences, procedural generation, coverage of operations beyond extrude). No program, prompt or shape from a non-commercial dataset was looked at or copied.

## Composed parts

The first 65 families are fixed recipes: a flange always has a bore and a bolt circle. The four `composed_*` families have no recipe. Each part is one base (block, cylinder, hexagonal prism or ring) plus one to five features drawn at random, in random order, and a kind may repeat. A random structure means a model must read the request instead of recalling a template.

| Feature | Parameters (`<kind>_<n>_...`, n = place in build order) | Volume |
| --- | --- | --- |
| `hole` | diameter, x, y | − π d²/4 · base height |
| `blind_hole` | diameter, depth, x, y | − π d²/4 · depth |
| `counterbore` | hole_diameter, diameter, depth, x, y | − hole through − (wide − hole area) · depth |
| `boss` | diameter, height, x, y | + π d²/4 · height |
| `pad` (rectangular boss) | length, width, height, x, y | + l · w · height |
| `pocket` | length, width, depth, x, y | − l · w · depth |
| `slot` | length (end to end), width, depth, angle (0 or 90), x, y | − ((l − w) · w + π w²/4) · depth |
| `polar` (round bases) | count, hole_diameter, circle_diameter | count × through hole |
| `row` (block) | count, hole_diameter, spacing, y | count × through hole |
| `hole_pair`, `pocket_pair`, `boss_pair` | as the single feature; the twin is mirrored about the YZ plane | 2 × the single feature |

At most one edge treatment, applied to the bare base before any feature: `base_corner_radius` (vertical edges of a block), `base_top_chamfer`, `base_top_fillet_radius`, or `base_wall_thickness` (a shell open at the top; then the only features are bosses standing on the floor). With the base cross-section written as A(d) = area − perimeter·d + corner·d² for "every side moved in by d", a top chamfer c removes perimeter·c²/2 − corner·c³/3, a top fillet r removes perimeter·r²(1 − π/4) − corner·r³(5/3 − π/2), and a shell with wall t removes A(t)·(height − t).

Footprints are kept 2 mm apart from each other and from the (treated) edge, so features never interact and the volume is a plain sum. Every round feature on a part has its own diameter, so the number of cylindrical faces at each diameter is checked exactly.

## Rules

- **No standard dimension is typed by hand.** Fasteners read every value from `tables/`.
- **Simplifications:** threads are not modelled (plain hole or plain shank at the nominal diameter) and edges are not chamfered.
- **Conventions:** millimetres; the part sits on the XY plane and grows towards +Z; every dimension is a named parameter; the program ends with `result`.
- **Captions are spec-style only.** `captions.py` builds them from the family name and parameter names; no free-written wording. Varied phrasing is built by `prompts.py`.
- **Proportions are kept realistic** (a chamfer is at most a fifth of the smallest side, a beam is longer than it is wide, and so on). Each rule is a comment next to the code that applies it.
- **Looked at before use.** `uv run python -m forge.data.contact_sheet` draws random parts per family into `data/contact_sheets/`.

## Known caveats

- The tables' own origin is not stated by their project, so values should be cross-checked against a second source before release.
- `countersunk_screw` leaves out the two sizes with 60 degree heads (M22, M24) and models the head as a 90 degree cone under a short cylindrical rim, whose height is the table's head height minus the cone height.
- `shoulder_screw` leaves out the undercuts and corner radii the table also lists. `cheese_head_screw` uses only the slotted standard (ISO 1207), not the cross-recess ones.
- `keyed_hub` reads the table's `t2` column as the keyway depth in the hub, measured from the bore. The table does not label its columns, so this reading needs the same cross-check.
- `hex_standoff` is not a standard part: it borrows the nut's across-flats size and the clearance hole for the same thread.
- Some `din931` head sizes for small bolts (for example 3.02 where ISO gives 3.2) look like tolerance limits, not nominal sizes. Rows carry their standard, so they can be filtered.

## Adding a family

1. Add a class with `NAME`, `DESCRIPTION`, `TABLE`, `standard_parts()` and `sample(rng)`.
2. Compute `expected_bbox`, `expected_volume` and `expected_cylinders` by arithmetic, not by running the program.
3. Register it in `__init__.py`. `tests/test_generators.py` then checks it automatically.
