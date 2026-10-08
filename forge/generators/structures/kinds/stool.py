"""Stool: a seat on legs, no back. Square or round seat, four legs or three, optional foot ring.

All size ranges here are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "stool"

OPTIONS = {
    "seat": ("square", "round"),
    "leg_count": ("four", "three"),      # three legs only under a round seat
    "legs": ("square", "round"),
    "foot_ring": ("no", "yes"),          # stretchers all round; only with four legs
}
OPTION_COUNTS = {("leg_count", "three"): "legs_count"}
OVERRIDES = {
    "seat_thickness": 5, "legs_thickness": 5, "legs_diameter": 5, "aprons_height": 5,
    "stretchers_top": 10,
}


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"seat": "square", "legs": "square", "foot_ring": "no"}
    c = {"seat": rng.choice(["square", "round"]), "legs": rng.choice(["square", "round"])}
    if c["seat"] == "round":
        c["leg_count"] = rng.choice(["four", "three"])
    if c.get("leg_count") != "three":
        c["foot_ring"] = rng.choice(["no", "yes"])
    return c


def headline(rng: random.Random, c: dict) -> dict:
    # A tall stool needs its legs tied together, so only one with a foot ring goes above 550.
    tallest = 780 if c.get("foot_ring") == "yes" else 550
    given = {"seat_top": shared.pick(rng, 400, tallest, 10)}
    if c["seat"] == "round":
        given["seat_diameter"] = shared.pick(rng, 280, 400, 10)
        if c.get("leg_count") == "three":
            given["legs_count"] = 3
    else:
        given["seat_width"] = shared.pick(rng, 280, 400, 10)
        same = rng.random() < 0.6     # most stool seats are square
        given["seat_depth"] = given["seat_width"] if same else shared.pick(rng, 280, 400, 10)
    return given


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    return {}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    round_seat, round_legs = c["seat"] == "round", c["legs"] == "round"
    seat = d.step("seat", "top", "level", "cylinder" if round_seat else "box")
    if round_seat:
        span = seat.stated("diameter")
    else:
        span = max(seat.stated("width"), seat.stated("depth"))
    seat.default("thickness", rules.top_thickness, span=span)
    seat.stated("top")
    seat.done()
    if round_seat:
        shared.legs_under_round_top(d, "seat", 3 if c.get("leg_count") == "three" else 4,
                                    round_legs)
    else:
        shared.corner_legs(d, "seat", round_legs)
        shared.aprons(d)
    if c.get("foot_ring") == "yes":
        shared.stretchers(d, "ring")
    across = "seat_diameter" if round_seat else "seat_width"
    return d.finish((across, "seat_diameter" if round_seat else "seat_depth", "seat_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    found = ["stool", "stool", "wooden stool", "simple stool", "shop stool"]
    if v["seat_top"] >= 650:
        found += ["bar stool", "bar stool", "counter stool", "high stool"]
    if v["seat_top"] <= 450:
        found += ["low stool"]
    return found


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    h = v["seat_top"]
    high = s.one(f"seat height {s.mm(h)}", f"seat height {s.mm(h)}", f"{s.mm(h)} high",
                 f"seat {s.mm(h)} off the floor")
    if c["seat"] == "round":
        d = v["seat_diameter"]
        return [s.one(f"seat diameter {s.mm(d)}", f"seat {s.dia(d)}", f"seat {s.mm(d)} across",
                      f"{s.mm(d)} diameter seat"), high]
    w, d = v["seat_width"], v["seat_depth"]
    return [s.one(f"seat {s.size(w, d)}", f"seat {s.size(w, d)}", f"{s.size(w, d)} seat",
                  f"seat {s.mm(w)} wide and {s.mm(d)} deep"), high]


OPTION_WORDS = {
    ("seat", "round"): ["+a round seat", "round seat", "+a circular seat"],
    ("seat", "square"): ["+a square seat", "+a flat rectangular seat"],
    ("leg_count", "three"): ["on a tripod of legs", "tripod base"],
    ("legs", "round"): ["+round legs", "+turned round legs", "+cylindrical legs"],
    ("legs", "square"): ["+square legs", "+square-section legs"],
    ("foot_ring", "yes"): ["+a foot ring", "+a foot rail all round", "+stretchers all round",
                           "+a footrest ring between the legs"],
    ("foot_ring", "no"): ["no foot ring", "without a foot rail", "no stretchers"],
}
COUNT_THINGS = {"legs_count": [("leg", "legs")]}
OVERRIDE_WORDS = {
    "seat_thickness": ["seat {v} thick", "seat thickness {v}", "+{v} thick seat"],
    "legs_thickness": ["legs {v} square", "leg thickness {v}", "+{v} square legs"],
    "legs_diameter": ["legs {v} in diameter", "leg diameter {v}", "+{v} diameter legs"],
    "aprons_height": ["aprons {v} high", "apron height {v}", "+{v} high aprons"],
    "stretchers_top": ["top of the foot ring {v} above the floor", "foot ring top at {v}"],
}
