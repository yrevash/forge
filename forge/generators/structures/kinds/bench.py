"""Bench: a long seat on legs or panel ends, with or without a back.

The back is the chair's back, wider (kinds/_shared.py). All size ranges here are
our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "bench"

OPTIONS = {
    "back": ("none", "slats", "rails", "panel"),
    "arms": ("no", "yes"),               # only with a back
    "supports": ("legs", "panels"),
    "legs": ("square", "round"),         # only on legs
    "stretcher": ("no", "yes"),          # legs: an H of three rails; panel ends: one rail
}
OPTION_COUNTS = {("back", "slats"): "slats_count"}
OVERRIDES = {
    "seat_thickness": 5, "legs_thickness": 5, "legs_diameter": 5, "aprons_height": 10,
    "ends_thickness": 5, "posts_thickness": 5, "slats_width": 5, "top_rail_height": 10,
}


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"back": "none", "supports": "legs", "legs": "square", "stretcher": "no"}
    c = {"back": rng.choice(["none", "none", "slats", "slats", "rails", "panel"])}
    if c["back"] != "none":
        c["arms"] = rng.choice(["no", "yes"])
    c["supports"] = rng.choice(["legs", "legs", "panels"])
    if c["supports"] == "legs":
        c["legs"] = rng.choice(["square", "square", "round"])
    c["stretcher"] = rng.choice(["no", "yes"])
    return c


def headline(rng: random.Random, c: dict) -> dict:
    given = {"seat_width": shared.pick(rng, 900, 1800, 50),
             "seat_depth": shared.pick(rng, 300, 450, 10),
             "seat_top": shared.pick(rng, 400, 480, 10)}
    if c["back"] != "none":
        given["posts_top"] = given["seat_top"] + shared.pick(rng, 400, 500, 10)
    return given


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    return {"slats_count": rng.randint(3, 9)} if c["back"] == "slats" else {}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    seat = d.step("seat", "top", "level")
    width, depth = seat.stated("width"), seat.stated("depth")
    seat.default("thickness", rules.top_thickness, span=max(width, depth))
    seat.stated("top")
    seat.done()
    if c["supports"] == "legs":
        shared.corner_legs(d, "seat", round_legs=c["legs"] == "round")
        shared.aprons(d)
        if c["stretcher"] == "yes":
            shared.stretchers(d, "h")
    else:
        shared.panel_ends(d, "seat")
        if c["stretcher"] == "yes":
            shared.stretcher_between_ends(d)
    if c["back"] != "none":
        shared.back(d, "seat", c["back"], arms=c.get("arms") == "yes")
    return d.finish(("seat_width", "seat_depth", "seat_top" if c["back"] == "none"
                     else "posts_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    return ["bench", "bench", "wooden bench", "garden bench", "hall bench", "seating bench"]


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, d, h = v["seat_width"], v["seat_depth"], v["seat_top"]
    said = [s.one(f"{s.mm(w)} long", f"{s.mm(w)} wide", f"length {s.mm(w)}",
                  f"seat {s.mm(w)} long"),
            s.one(f"{s.mm(d)} deep", f"seat depth {s.mm(d)}", f"depth {s.mm(d)}",
                  f"seat {s.mm(d)} deep"),
            s.one(f"seat height {s.mm(h)}", f"seat height {s.mm(h)}", f"seat {s.mm(h)} high",
                  f"seat {s.mm(h)} off the floor")]
    if "posts_top" in v:
        b = v["posts_top"]
        said.append(s.one(f"back height {s.mm(b)}", f"overall height {s.mm(b)}",
                          f"{s.mm(b)} to the top of the back"))
    return said


OPTION_WORDS = {
    ("back", "none"): ["no back", "without a back", "backless"],
    ("back", "slats"): ["+a slatted back", "+a back", "+a back with upright slats"],
    ("back", "rails"): ["+a rail back", "+a back made of a top rail and a lower rail",
                        "+an open back of a pair of rails"],
    ("back", "panel"): ["+a solid back panel", "+a panel back", "+a solid back"],
    ("arms", "yes"): ["+arms", "+armrests", "+an armrest at each end"],
    ("arms", "no"): ["no arms", "without arms", "armless"],
    ("supports", "panels"): ["+panel ends", "+slab ends", "on solid end panels",
                             "+panel ends in place of legs"],
    ("supports", "legs"): ["on legs", "+a leg at each corner"],
    ("legs", "round"): ["+round legs", "+turned round legs", "+cylindrical legs"],
    ("legs", "square"): ["+square legs", "+square-section legs"],
    ("stretcher", "yes"): ["+a stretcher", "+a stretcher underneath", "+a long stretcher low down"],
    ("stretcher", "no"): ["no stretcher", "without a stretcher"],
}
COUNT_THINGS = {"slats_count": [("back slat", "back slats"), ("slat in the back", "slats in the back"),
                                ("upright back slat", "upright back slats")]}
OVERRIDE_WORDS = {
    "seat_thickness": ["seat {v} thick", "seat thickness {v}", "+{v} thick seat"],
    "legs_thickness": ["legs {v} square", "leg thickness {v}", "+{v} square legs"],
    "legs_diameter": ["legs {v} in diameter", "leg diameter {v}", "+{v} diameter legs"],
    "aprons_height": ["aprons {v} high", "apron height {v}", "+{v} high aprons"],
    "ends_thickness": ["end panels {v} thick", "+{v} thick end panels", "panel end thickness {v}"],
    "posts_thickness": ["back posts {v} square", "+{v} square back posts"],
    "slats_width": ["slats {v} wide", "slat width {v}", "+{v} wide slats"],
    "top_rail_height": ["top rail {v} high", "top rail height {v}"],
}
