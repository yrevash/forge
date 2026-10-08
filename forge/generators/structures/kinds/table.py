"""Table: a top on four legs or on two panel ends; aprons, stretchers and a lower shelf optional.

Three sizes of table are drawn (dining, coffee, side) so that proportions stay
those of real tables; a round top is drawn at coffee and side-table sizes.
All size ranges here are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "table"

OPTIONS = {
    "top": ("rectangular", "round"),
    "supports": ("legs", "panels"),                 # rectangular tops only
    "legs": ("square", "round"),                    # on legs
    "aprons": ("yes", "no"),                        # rectangular top on legs
    "lower": ("none", "stretchers", "shelf"),       # on legs (a round top: no shelf)
    "between_ends": ("nothing", "stretcher", "shelf"),   # on panel ends
}
OPTION_COUNTS: dict = {}
OVERRIDES = {
    "top_thickness": 5, "legs_thickness": 5, "legs_diameter": 5, "aprons_height": 10,
    "ends_thickness": 5, "stretchers_top": 10, "shelf_top": 10,
}

# (width range, depth range, height range), each (low, high, step).
SIZES = {
    "dining": ((1200, 2200, 50), (700, 1000, 50), (720, 780, 10)),
    "coffee": ((800, 1300, 50), (450, 700, 50), (380, 480, 10)),
    "side": ((400, 650, 50), (350, 600, 50), (450, 650, 10)),
}


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"top": "rectangular", "supports": "legs", "legs": "square", "aprons": "yes",
                "lower": "none"}
    c = {"top": rng.choice(["rectangular"] * 4 + ["round"])}
    if c["top"] == "round":
        c["legs"] = rng.choice(["square", "round"])
        c["lower"] = rng.choice(["none", "none", "stretchers"])
        return c
    c["supports"] = rng.choice(["legs", "legs", "panels"])
    if c["supports"] == "legs":
        c["legs"] = rng.choice(["square", "square", "round"])
        c["aprons"] = rng.choice(["yes", "yes", "no"])
        c["lower"] = rng.choice(["none", "none", "stretchers", "shelf"])
    else:
        c["between_ends"] = rng.choice(["nothing", "stretcher", "shelf"])
    return c


def headline(rng: random.Random, c: dict) -> dict:
    if c["top"] == "round":
        return {"top_diameter": shared.pick(rng, 400, 900, 50),
                "top_top": shared.pick(rng, 400, 750, 10)}
    wide, deep, high = SIZES[rng.choice(["dining", "dining", "coffee", "side"])]
    width = shared.pick(rng, *wide)
    return {"top_width": width,
            "top_depth": min(width, shared.pick(rng, *deep)),     # never deeper than wide
            "top_top": shared.pick(rng, *high)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    return {}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    round_top = c["top"] == "round"
    top = d.step("top", "top", "level", "cylinder" if round_top else "box")
    if round_top:
        span = top.stated("diameter")
    else:
        span = max(top.stated("width"), top.stated("depth"))
    top.default("thickness", rules.top_thickness, span=span)
    height = top.stated("top")
    top.done()

    if round_top:
        shared.legs_under_round_top(d, "top", 4, c["legs"] == "round")
        if c["lower"] == "stretchers":
            shared.stretchers(d, "ring")
        return d.finish(("top_diameter", "top_diameter", "top_top"))

    if c["supports"] == "legs":
        shared.corner_legs(d, "top", c["legs"] == "round")
        if c["aprons"] == "yes":
            shared.aprons(d)
        if c["lower"] != "none":
            shared.stretchers(d, "ring")
        if c["lower"] == "shelf":
            # The shelf lies on the front and back stretchers and fits between the legs.
            shelf = d.step("shelf", "shelves", "level")
            shelf.derive("width", f"legs_spread_x - 2 * {shared.leg_size(d)}")
            shelf.derive("depth", "stretchers_spread_y")
            shelf.default("thickness", rules.board_thickness, span=span)
            shelf.derive("top", "stretchers_top + shelf_thickness")
            shelf.done()
    else:
        shared.panel_ends(d, "top")
        if c["between_ends"] == "stretcher":
            shared.stretcher_between_ends(d)
        elif c["between_ends"] == "shelf":
            shelf = d.step("shelf", "shelves", "level")
            shelf.derive("width", "ends_spread_x - 2 * ends_thickness")
            shelf.derive("depth", "top_depth")
            shelf.default("thickness", rules.board_thickness, span=span)
            shelf.default("top", rules.lower_shelf_top, height=height)
            shelf.done()
            d.require(d.values["ends_height"] - d.values["shelf_top"] >= 150,
                      "shelf too close to the top")
    return d.finish(("top_width", "top_depth", "top_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    found = ["table", "table", "wooden table"]
    height, width = v["top_top"], v.get("top_width", v.get("top_diameter"))
    if height <= 500:
        found += ["coffee table", "coffee table", "low table"]
    elif width <= 700:
        found += ["side table", "side table", "end table", "small table"]
    elif height >= 700 and width >= 1200:
        found += ["dining table", "dining table", "kitchen table"]
    return found


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    h = v["top_top"]
    high = s.one(f"{s.mm(h)} high", f"{s.mm(h)} high", f"height {s.mm(h)}", f"{s.mm(h)} tall")
    if c["top"] == "round":
        d = v["top_diameter"]
        return [s.one(f"top diameter {s.mm(d)}", f"top {s.dia(d)}", f"{s.mm(d)} across",
                      f"{s.mm(d)} diameter top"), high]
    w, d = v["top_width"], v["top_depth"]
    return [s.one(f"{s.size(w, d)}", f"{s.size(w, d)}", f"top {s.size(w, d)}",
                  f"{s.mm(w)} long and {s.mm(d)} wide", f"{s.mm(w)} wide, {s.mm(d)} deep"), high]


OPTION_WORDS = {
    ("top", "round"): ["+a round top", "round top", "+a circular top"],
    ("top", "rectangular"): ["+a rectangular top"],
    ("supports", "panels"): ["+panel ends", "+slab ends", "on solid end panels",
                             "+panel ends in place of legs"],
    ("supports", "legs"): ["on legs", "+a leg at each corner"],
    ("legs", "round"): ["+round legs", "+turned round legs", "+cylindrical legs"],
    ("legs", "square"): ["+square legs", "+square-section legs"],
    ("aprons", "no"): ["no aprons", "without aprons", "no rails under the top"],
    ("aprons", "yes"): ["+aprons", "+aprons under the top", "+an apron between each pair of legs"],
    ("lower", "stretchers"): ["+stretchers", "+stretchers between the legs",
                              "+a stretcher between each pair of legs", "+stretchers all round"],
    ("lower", "shelf"): ["+a lower shelf", "+a shelf underneath", "+a shelf between the legs",
                         "+a lower shelf resting on stretchers"],
    ("lower", "none"): ["no stretchers", "nothing between the legs"],
    ("between_ends", "stretcher"): ["+a stretcher", "+a stretcher between the ends",
                                    "+a rail joining the end panels"],
    ("between_ends", "shelf"): ["+a lower shelf", "+a shelf between the ends",
                                "+a shelf underneath"],
    ("between_ends", "nothing"): ["no stretcher", "nothing between the ends"],
}
COUNT_THINGS: dict = {}
OVERRIDE_WORDS = {
    "top_thickness": ["top {v} thick", "top thickness {v}", "+{v} thick top"],
    "legs_thickness": ["legs {v} square", "leg thickness {v}", "+{v} square legs"],
    "legs_diameter": ["legs {v} in diameter", "leg diameter {v}", "+{v} diameter legs"],
    "aprons_height": ["aprons {v} high", "apron height {v}", "+{v} high aprons"],
    "ends_thickness": ["end panels {v} thick", "+{v} thick end panels", "panel end thickness {v}"],
    "stretchers_top": ["top of the stretchers {v} above the floor", "stretcher tops at {v}"],
    "shelf_top": ["shelf {v} above the floor", "top of the shelf at {v}", "shelf height {v}"],
}
