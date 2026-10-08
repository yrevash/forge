"""Toolbox (open tote): a bottom board, two tall ends, two low sides, a handle between the ends.

The handle is a round bar lying left to right between the two ends (shape
`cylinder_x`), or a flat bar. A divider is one more board from front to back
across the middle. All size ranges are our own design choices, not a standard.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "toolbox"

OPTIONS = {
    "handle": ("round", "flat"),
    "divider": ("no", "yes"),
    "base": ("flat", "skids"),           # runners under the bottom, as under a crate
}
OPTION_COUNTS: dict = {}
OVERRIDES = {
    "bottom_thickness": 1, "ends_thickness": 1, "walls_height": 10, "handle_diameter": 5,
    "handle_height": 5, "skids_height": 5,
}


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"handle": "round", "divider": "no", "base": "flat"}
    return {"handle": rng.choice(["round", "round", "flat"]),
            "divider": rng.choice(["no", "no", "yes"]),
            "base": rng.choice(["flat", "flat", "skids"])}


def headline(rng: random.Random, c: dict) -> dict:
    return {"bottom_width": shared.pick(rng, 350, 700, 10),
            "bottom_depth": shared.pick(rng, 150, 300, 10),
            "ends_top": shared.pick(rng, 200, 350, 10)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    return {}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    bottom = d.step("bottom", "bottom", "level")
    width, depth = bottom.stated("width"), bottom.stated("depth")
    span = max(width, depth)
    bottom.default("thickness", rules.crate_board_thickness, span=span)
    if c["base"] == "skids":     # runners from front to back under the bottom board
        skids = d.step("skids", "bars", "cross_row")
        skids.default("count", rules.skid_count, width=width)
        skids.default("width", rules.crate_post_thickness, span=span)
        skids.derive("length_y", "bottom_depth")
        skids.default("height", rules.skid_height, span=span)
        skids.derive("top", "skids_height")
        skids.derive("pitch", "(bottom_width - skids_width) / (skids_count - 1)")
        skids.done()
        bottom.derive("top", "skids_height + bottom_thickness")
    else:
        bottom.derive("top", "bottom_thickness")
    bottom.done()

    ends = d.step("ends", "panels", "sides")           # the two tall ends that carry the handle
    height = d.given["ends_top"]
    ends.stated("top")
    ends.derive("height", "ends_top - bottom_top")
    ends.default("thickness", rules.crate_board_thickness, span=span)
    ends.derive("length_y", "bottom_depth")
    ends.derive("spread_x", "bottom_width")
    ends.derive("y", "0")
    ends.done()
    inside = "bottom_width - 2 * ends_thickness"       # between the two ends

    walls = d.step("walls", "panels", "front_back")    # the low front and back sides
    walls.default("height", rules.tote_side_height, height=height)
    walls.derive("thickness", "ends_thickness")
    walls.derive("top", "bottom_top + walls_height")
    walls.derive("length_x", inside)
    walls.derive("spread_y", "bottom_depth")
    walls.done()

    if c["divider"] == "yes":        # across the middle, as tall as the sides
        divider = d.step("divider", "panels", "side")
        divider.derive("thickness", "ends_thickness")
        divider.derive("length_y", "bottom_depth - 2 * walls_thickness")
        divider.derive("height", "walls_height")
        divider.derive("top", "walls_top")
        divider.derive("x", "0")
        divider.done()

    round_handle = c["handle"] == "round"
    handle = d.step("handle", "handle", "across", "cylinder_x" if round_handle else "box")
    handle.derive("width", inside)
    if round_handle:
        tall = handle.default("diameter", rules.tote_handle_size, depth=depth)
    else:
        tall = handle.default("height", rules.tote_handle_size, depth=depth)
        handle.derive("thickness", "ends_thickness")
    handle.derive("y", "0")
    handle.derive("top", "ends_top - ends_thickness")          # a board thickness below the top
    handle.done()
    d.require(d.values["handle_top"] - tall - d.values["walls_top"] >= 60,
              "no room for a hand under the handle")
    d.require(tall <= depth / 3, "handle too thick for the box")
    return d.finish(("bottom_width", "bottom_depth", "ends_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    return ["toolbox", "toolbox", "open toolbox", "tool tote", "wooden toolbox", "tool caddy",
            "carry-all tote"]


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, d, h = v["bottom_width"], v["bottom_depth"], v["ends_top"]
    return s.one([f"{s.size(w, d, h)}"], [f"{s.size(w, d)}", f"{s.mm(h)} high"],
                 [f"{s.mm(w)} long", f"{s.mm(d)} wide", f"{s.mm(h)} high"],
                 [f"length {s.mm(w)}", f"width {s.mm(d)}", f"height {s.mm(h)}"])


OPTION_WORDS = {
    ("handle", "round"): ["+a round handle", "+a dowel handle", "+a round bar handle"],
    ("handle", "flat"): ["+a flat bar handle", "+a flat handle", "+a slat for a handle"],
    ("divider", "yes"): ["+a divider", "+a divider across the middle", "+a centre divider"],
    ("divider", "no"): ["no divider", "without a divider"],
    ("base", "skids"): ["on skids", "+skids underneath", "+runners under the bottom"],
    ("base", "flat"): ["no skids", "+a flat bottom"],
}
COUNT_THINGS: dict = {}
OVERRIDE_WORDS = {
    "bottom_thickness": ["bottom {v} thick", "+a {v} thick bottom", "bottom board {v} thick"],
    "ends_thickness": ["ends {v} thick", "+{v} thick ends", "boards {v} thick"],
    "walls_height": ["sides {v} high", "+{v} high sides", "side height {v}"],
    "handle_diameter": ["handle {v} in diameter", "+a {v} diameter handle"],
    "handle_height": ["handle bar {v} high", "+a {v} high handle bar"],
    "skids_height": ["skids {v} high", "+{v} high skids", "skid height {v}"],
}
