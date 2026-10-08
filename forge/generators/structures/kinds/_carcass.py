"""The carcass a cabinet and a chest of drawers share: a top board on two sides, a bottom
board between them, an optional back, and what it all stands on.

    plinth  the sides run to the floor; a set-back board under the bottom board
    legs    four short legs under the corners; the sides and bottom start on top of them
    none    the sides and the bottom board stand straight on the floor

The top board lies ON the sides and covers the whole footprint; doors, drawer
fronts and the back panel stand on the bottom board and reach up to the top board.
"""

from __future__ import annotations

from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft

CLEAR = "top_width - 2 * sides_thickness"      # between the two sides
MIN_INNER = 250       # a carcass narrower than this between its sides is not a cupboard


def carcass(d: Draft, base: str, back: bool) -> None:
    """Open the steps top, (legs), sides, (plinth), bottom, (back_panel) on the draft."""
    top = d.step("top", "top", "level")
    width, depth = top.stated("width"), top.stated("depth")
    height = d.given["top_top"]
    span = max(width, height)
    top.default("thickness", rules.board_thickness, span=span)
    top.stated("top")
    top.done()

    if base == "legs":
        legs = d.step("legs", "legs", "corners")
        legs.default("thickness", rules.cabinet_leg_thickness, width=width)
        legs.derive("bottom", "0")
        legs.default("top", rules.cabinet_leg_height, height=height)
        legs.derive("spread_x", "top_width")
        legs.derive("spread_y", "top_depth")
        legs.done()
        d.require(2 * d.values["legs_thickness"] <= depth / 2, "legs too thick for the unit")

    sides = d.step("sides", "panels", "sides")
    sides.derive("height", "top_top - top_thickness" + (" - legs_top" if base == "legs" else ""))
    sides.default("thickness", rules.board_thickness, span=span)
    sides.derive("top", "top_top - top_thickness")
    sides.derive("length_y", "top_depth")
    sides.derive("spread_x", "top_width")
    sides.derive("y", "0")
    sides.done()
    d.require(width - 2 * d.values["sides_thickness"] >= MIN_INNER, "carcass too narrow inside")

    if base == "plinth":      # a board under the bottom board, set back from the front
        plinth = d.step("plinth", "rail", "across")
        plinth.derive("width", CLEAR)
        plinth.default("height", rules.plinth_height, height=height)
        plinth.default("thickness", rules.board_thickness, span=span)
        plinth.derive("y", "-top_depth / 2 + 2 * plinth_thickness")
        plinth.derive("top", "plinth_height")
        plinth.done()

    bottom = d.step("bottom", "bottom", "level")
    bottom.derive("width", CLEAR)
    bottom.derive("depth", "top_depth")
    bottom.default("thickness", rules.board_thickness, span=span)
    under = {"plinth": "plinth_height + ", "legs": "legs_top + ", "none": ""}[base]
    bottom.derive("top", under + "bottom_thickness")
    bottom.done()
    d.require(d.values["sides_top"] - d.values["bottom_top"] >= 250, "no room inside the carcass")

    if back:                  # stands on the bottom board, let in between the sides
        panel = d.step("back_panel", "back_panel", "across")
        panel.derive("width", "bottom_width")
        panel.derive("height", "sides_top - bottom_top")
        panel.default("thickness", rules.thin_panel_thickness, span=span)
        panel.derive("y", "(top_depth - back_panel_thickness) / 2")
        panel.derive("top", "sides_top")
        panel.done()


BASE_WORDS = {
    ("base", "plinth"): ["on a plinth", "+a plinth", "+a kick board"],
    ("base", "legs"): ["on short legs", "+short legs", "+a leg under each corner",
                       "raised on legs"],
    ("base", "none"): ["standing straight on the floor", "no plinth", "without a plinth"],
    ("back", "yes"): ["+a back panel", "+a closed back"],
    ("back", "no"): ["no back panel", "open at the back", "without a back panel"],
}
CARCASS_OVERRIDES = {
    "top_thickness": 1, "sides_thickness": 1, "bottom_thickness": 1, "plinth_height": 10,
    "legs_top": 10, "legs_thickness": 5, "back_panel_thickness": 1,
}
CARCASS_OVERRIDE_WORDS = {
    "top_thickness": ["top {v} thick", "top board {v} thick", "+{v} thick top"],
    "sides_thickness": ["sides {v} thick", "side panels {v} thick", "+{v} thick sides"],
    "bottom_thickness": ["bottom board {v} thick", "+{v} thick bottom board"],
    "plinth_height": ["plinth {v} high", "plinth height {v}"],
    "legs_top": ["legs {v} high", "+{v} high legs", "raised {v} off the floor"],
    "legs_thickness": ["legs {v} square", "+{v} square legs"],
    "back_panel_thickness": ["back panel {v} thick", "+{v} thick back panel"],
}
