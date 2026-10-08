"""Chair: a seat on four legs with a back, as in the hand-written demo (forge/system1/demo_chair.py).

With every option at its default and the demo's four numbers, this builds the
demo chair: same 16 parts, same overall size, same volume (a test checks it).

All size ranges here are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "chair"

# Each option with its choices; the FIRST choice is the default (what you get if nothing is said).
OPTIONS = {
    "back": ("slats", "rails", "panel"),
    "arms": ("no", "yes"),
    "stretchers": ("none", "ring", "h"),
    "legs": ("square", "round"),
}
# An option choice that a stated count also expresses: "4 slats" says the back is slatted.
OPTION_COUNTS = {("back", "slats"): "slats_count"}

# Normally-default sizes that may be stated instead, each with the grid its values lie on.
OVERRIDES = {
    "seat_thickness": 5, "legs_thickness": 5, "legs_diameter": 5, "aprons_height": 10,
    "posts_thickness": 5, "slats_width": 5, "top_rail_height": 10, "stretchers_top": 10,
}


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    """A random variant, or with `plain` the default one (the first choice of every option)."""
    if plain:
        return {"back": "slats", "arms": "no", "stretchers": "none", "legs": "square"}
    return {"back": rng.choice(["slats", "slats", "slats", "rails", "panel"]),
            "arms": rng.choice(["no", "no", "yes"]),
            "stretchers": rng.choice(["none", "none", "ring", "h"]),
            "legs": rng.choice(["square", "square", "round"])}


def headline(rng: random.Random, c: dict) -> dict:
    seat_top = shared.pick(rng, 400, 480, 10)
    return {"seat_width": shared.pick(rng, 380, 500, 10),
            "seat_depth": shared.pick(rng, 380, 480, 10),
            "seat_top": seat_top,
            "posts_top": seat_top + shared.pick(rng, 380, 520, 10)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    return {"slats_count": rng.randint(2, 5)} if c["back"] == "slats" else {}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    seat = d.step("seat", "top", "level")
    width, depth = seat.stated("width"), seat.stated("depth")
    seat.default("thickness", rules.top_thickness, span=max(width, depth))
    seat.stated("top")
    seat.done()
    shared.corner_legs(d, "seat", round_legs=c["legs"] == "round")
    shared.aprons(d)
    if c["stretchers"] != "none":
        shared.stretchers(d, c["stretchers"])
    shared.back(d, "seat", c["back"], arms=c["arms"] == "yes")
    return d.finish(("seat_width", "seat_depth", "posts_top"))


# --- wording (template, see forge/generators/prompts.py) --------------------------

def names(c: dict, v: dict) -> list[str]:
    return ["chair", "dining chair", "wooden chair", "kitchen chair", "side chair"]


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, d, h, b = v["seat_width"], v["seat_depth"], v["seat_top"], v["posts_top"]
    return [s.one(f"seat {s.size(w, d)}", f"seat {s.size(w, d)}", f"{s.size(w, d)} seat",
                  f"seat {s.mm(w)} wide and {s.mm(d)} deep"),
            s.one(f"seat height {s.mm(h)}", f"seat height {s.mm(h)}", f"seat {s.mm(h)} high",
                  f"seat {s.mm(h)} off the floor"),
            s.one(f"back height {s.mm(b)}", f"back height {s.mm(b)}", f"overall height {s.mm(b)}",
                  f"{s.mm(b)} to the top of the back")]


# A leading "+" marks a phrase that can follow "with": "with arms, round legs and 4 slats".
OPTION_WORDS = {
    ("back", "slats"): ["+a slatted back", "+back slats", "slat back"],
    ("back", "rails"): ["+a rail back", "+only a top rail and a lower rail in the back",
                        "no slats", "open back"],
    ("back", "panel"): ["+a solid back panel", "+a panel back", "solid back", "+a back panel"],
    ("arms", "yes"): ["+arms", "+armrests", "+arms on both sides"],
    ("arms", "no"): ["no arms", "without arms", "armless"],
    ("stretchers", "ring"): ["+stretchers all round", "+stretchers between all the legs",
                             "+a stretcher between each pair of legs"],
    ("stretchers", "h"): ["+an H stretcher", "+an H-shaped stretcher",
                          "+side stretchers and a cross stretcher"],
    ("stretchers", "none"): ["no stretchers", "without stretchers"],
    ("legs", "round"): ["+round legs", "+turned round legs", "+cylindrical legs"],
    ("legs", "square"): ["+square legs", "+square-section legs"],
}
COUNT_THINGS = {"slats_count": [("slat", "slats"), ("back slat", "back slats"),
                                ("vertical slat", "vertical slats"),
                                ("slat in the back", "slats in the back")]}
OVERRIDE_WORDS = {
    "seat_thickness": ["seat {v} thick", "seat thickness {v}", "+{v} thick seat"],
    "legs_thickness": ["legs {v} square", "leg thickness {v}", "+{v} square legs"],
    "legs_diameter": ["legs {v} in diameter", "leg diameter {v}", "+{v} diameter legs"],
    "aprons_height": ["aprons {v} high", "apron height {v}", "+{v} high aprons",
                      "seat rails {v} high"],
    "posts_thickness": ["back posts {v} square", "+{v} square back posts"],
    "slats_width": ["slats {v} wide", "slat width {v}", "+{v} wide slats"],
    "top_rail_height": ["top rail {v} high", "top rail height {v}"],
    "stretchers_top": ["top of the stretchers {v} above the floor", "stretcher tops at {v}"],
}
