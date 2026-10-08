"""Crate: a bottom board with slatted or solid sides standing on it, with or without a lid.

slatted  a post inside each corner, with rows of slats round the outside
solid    four boards as walls
skids    two or three runners under the bottom board, so a fork or a hand gets under it
cleats   a batten on the outside of each end to lift it by; they add to the overall length

All size ranges are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "crate"

OPTIONS = {"sides": ("slatted", "solid"), "lid": ("no", "yes"), "base": ("flat", "skids"),
           "handles": ("none", "cleats")}
OPTION_COUNTS = {("sides", "slatted"): "slats_count"}
OVERRIDES = {
    "bottom_thickness": 1, "slats_thickness": 1, "slats_height": 5, "walls_thickness": 1,
    "posts_thickness": 5, "lid_thickness": 1, "skids_height": 5, "handles_height": 5,
}
MIN_GAP = 10          # clear space between two slats
MIN_SLAT = 30         # a slat narrower than this is a stick


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"sides": "slatted", "lid": "no", "base": "flat", "handles": "none"}
    return {"sides": rng.choice(["slatted", "slatted", "solid"]), "lid": rng.choice(["no", "yes"]),
            "base": rng.choice(["flat", "flat", "skids"]),
            "handles": rng.choice(["none", "none", "cleats"])}


def height_param(c: dict) -> str:
    """The step whose top is the crate's stated height: the lid if there is one."""
    return "lid_top" if c["lid"] == "yes" else \
        "posts_top" if c["sides"] == "slatted" else "walls_top"


def headline(rng: random.Random, c: dict) -> dict:
    width = shared.pick(rng, 300, 800, 50)
    depth = min(width, shared.pick(rng, 200, 600, 50))
    tallest = min(500, width, 1.5 * depth)        # a crate, not a tower
    return {"bottom_width": width, "bottom_depth": depth,
            height_param(c): shared.pick(rng, 150, tallest, 10)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    if c["sides"] != "slatted":
        return {}
    most = max(2, min(5, int(given[height_param(c)] // 70)))    # about 70 of height per slat
    return {"slats_count": rng.randint(2, most)}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    bottom = d.step("bottom", "bottom", "level")       # sized now; built after any skids
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
        # Evenly spaced, the outer two flush with the ends of the crate.
        skids.derive("pitch", "(bottom_width - skids_width) / (skids_count - 1)")
        skids.done()
        bottom.derive("top", "skids_height + bottom_thickness")
    else:
        bottom.derive("top", "bottom_thickness")
    bottom.done()

    # The lid is built last, but its numbers come first: the sides stop under it.
    lid = None
    if c["lid"] == "yes":
        lid = d.step("lid", "top", "level", light=True)     # a lid carries nothing
        lid.derive("width", "bottom_width")
        lid.derive("depth", "bottom_depth")
        lid.default("thickness", rules.crate_board_thickness, span=span)
        lid.stated("top")

    def sides_top(step) -> float:
        return step.derive("top", "lid_top - lid_thickness") if lid else step.stated("top")

    if c["sides"] == "slatted":
        slats = d.step("slats", "slats", "stacked_ring")      # sized now, built after the posts
        slats.default("thickness", rules.crate_board_thickness, span=span)
        posts = d.step("posts", "posts", "corners")
        posts.default("thickness", rules.crate_post_thickness, span=span)
        posts.derive("bottom", "bottom_top")                  # they stand on the bottom board
        wall = sides_top(posts) - d.values["bottom_top"]
        posts.derive("spread_x", "bottom_width - 2 * slats_thickness")   # inside the slats
        posts.derive("spread_y", "bottom_depth - 2 * slats_thickness")
        posts.done()
        d.require(wall >= 80, "sides too low for slats")
        count = slats.default("count", rules.crate_slat_count, wall_height=wall)
        d.require(count >= 2, "slatted sides need at least a bottom and a top slat")
        slats.default("height", rules.crate_slat_height, wall_height=wall, count=count)
        slats.derive("bottom", "bottom_top")
        # Evenly spaced, the highest slat flush with the top of the posts.
        slats.derive("pitch", "(posts_top - slats_bottom - slats_height) / (slats_count - 1)")
        slats.derive("length_x", "bottom_width")
        slats.derive("length_y", "bottom_depth - 2 * slats_thickness")
        slats.derive("spread_x", "bottom_width")
        slats.derive("spread_y", "bottom_depth")
        slats.done()
        d.require(d.values["slats_pitch"] - d.values["slats_height"] >= MIN_GAP,
                  "no gap between the slats")
        d.require(d.values["slats_height"] >= MIN_SLAT, "slats too narrow")
        d.require(depth - 2 * d.values["slats_thickness"] >= 3 * d.values["posts_thickness"],
                  "corner posts too thick for the crate")
        top = "posts_top"
    else:
        walls = d.step("walls", "panels", "ring")
        walls.default("thickness", rules.crate_board_thickness, span=span)
        sides_top(walls)
        walls.derive("height", "walls_top - bottom_top")
        walls.derive("length_x", "bottom_width")
        walls.derive("length_y", "bottom_depth - 2 * walls_thickness")
        walls.derive("spread_x", "bottom_width")
        walls.derive("spread_y", "bottom_depth")
        walls.done()
        top = "walls_top"
    overall_width = "bottom_width"
    if c["handles"] == "cleats":     # a batten outside each end, flush with the top of the sides
        board = "slats" if c["sides"] == "slatted" else "walls"
        cleats = d.step("handles", "handle", "sides")
        cleats.default("height", rules.cleat_height, span=span)
        cleats.derive("thickness", f"{board}_thickness")
        cleats.derive("top", top)
        cleats.default("length_y", rules.cleat_length, depth=depth)
        cleats.derive("spread_x", "bottom_width + 2 * handles_thickness")
        cleats.derive("y", "0")
        cleats.done()
        if c["sides"] == "slatted":      # the cleat must sit on the top slat, not over a gap
            d.require(d.values["handles_height"] <= d.values["slats_height"],
                      "cleat taller than the slat it is fixed to")
        overall_width = "handles_spread_x"
    if lid:
        lid.done()
        top = "lid_top"
    return d.finish((overall_width, "bottom_depth", top))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    return ["crate", "crate", "wooden crate", "box crate", "storage crate"]


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, d, h = v["bottom_width"], v["bottom_depth"], v[height_param(c)]
    return s.one([f"{s.size(w, d, h)}"], [f"{s.size(w, d)}", f"{s.mm(h)} high"],
                 [f"{s.mm(w)} long", f"{s.mm(d)} wide", f"{s.mm(h)} high"],
                 [f"length {s.mm(w)}", f"width {s.mm(d)}", f"height {s.mm(h)}"])


OPTION_WORDS = {
    ("sides", "solid"): ["+solid sides", "+solid walls", "+closed sides", "solid-sided"],
    ("sides", "slatted"): ["+slatted sides", "slatted", "+gaps between the side slats"],
    ("lid", "yes"): ["+a lid", "+a flat lid", "+a lid on top"],
    ("lid", "no"): ["no lid", "open top", "without a lid"],
    ("base", "skids"): ["on skids", "+skids underneath", "+runners under the bottom",
                        "+a base on skids"],
    ("base", "flat"): ["no skids", "flat on the floor"],
    ("handles", "cleats"): ["+a cleat handle on each end", "+cleats on the ends to lift it by",
                            "+a batten handle outside each end"],
    ("handles", "none"): ["no handles", "without handles"],
}
COUNT_THINGS = {"slats_count": [("slat per side", "slats per side"),
                                ("slat up each side", "slats up each side"),
                                ("slat on each side", "slats on each side")]}
OVERRIDE_WORDS = {
    "bottom_thickness": ["bottom {v} thick", "+{v} thick bottom", "bottom board {v} thick"],
    "slats_thickness": ["slats {v} thick", "slat thickness {v}", "+{v} thick slats"],
    "slats_height": ["slats {v} wide", "slat width {v}", "+{v} wide slats"],
    "walls_thickness": ["walls {v} thick", "sides {v} thick", "+{v} thick walls"],
    "posts_thickness": ["corner posts {v} square", "+{v} square corner posts"],
    "lid_thickness": ["lid {v} thick", "+{v} thick lid"],
    "skids_height": ["skids {v} high", "+{v} high skids", "skid height {v}"],
    "handles_height": ["cleats {v} high", "+{v} high cleats", "cleat handles {v} tall"],
}
