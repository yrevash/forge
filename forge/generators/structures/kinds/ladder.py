"""Ladder: two side rails with evenly spaced rungs between them, standing upright.

The side rails are two boards on edge (the same step as the sides of a shelf
unit). The rungs are round bars lying left to right, or flat steps. A
stabiliser is a longer bar under the feet; the side rails then stand on it.
A leaning or folding ladder needs tilted parts, which no kind has (README, limits).

A ladder is tall and narrow by nature, so the "tall narrow units are rare" rule
does not apply to it. All size ranges are our own design choices, not a standard.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "ladder"
TALL_BY_NATURE = True

OPTIONS = {
    "rungs": ("round", "flat"),
    "stabiliser": ("no", "yes"),
}
OPTION_COUNTS: dict = {}
OVERRIDES = {
    "sides_thickness": 5, "sides_length_y": 10, "rungs_diameter": 5, "rungs_height": 5,
    "stabiliser_width": 50,
}
MIN_STEP, MAX_STEP = 200, 400      # distance from one rung to the next


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"rungs": "round", "stabiliser": "no"}
    return {"rungs": rng.choice(["round", "round", "flat"]),
            "stabiliser": rng.choice(["no", "no", "yes"])}


def headline(rng: random.Random, c: dict) -> dict:
    return {"sides_spread_x": shared.pick(rng, 350, 500, 10),
            "sides_top": shared.pick(rng, 1500, 4000, 100)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    height = given["sides_top"]
    return {"rungs_count": rng.randint(int(height // 330), int(height // 250) - 1)}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    height = given["sides_top"]
    sides = d.step("sides", "panels", "sides")       # sized now; built after any stabiliser
    width = sides.stated("spread_x")
    sides.default("thickness", rules.stile_thickness, height=height)
    sides.default("length_y", rules.stile_depth, height=height)
    foot = "0"
    if c["stabiliser"] == "yes":       # a longer bar on the floor; the side rails stand on it
        bar = d.step("stabiliser", "stretchers", "across")
        bar.default("width", rules.stabiliser_width, width=width)
        bar.derive("height", "sides_thickness")
        bar.derive("thickness", "sides_length_y")
        bar.derive("y", "0")
        bar.derive("top", "stabiliser_height")
        bar.done()
        d.require(d.values["stabiliser_width"] >= width + 100, "stabiliser no wider than the ladder")
        foot = "stabiliser_top"
    sides.stated("top")
    sides.derive("height", "sides_top" if foot == "0" else "sides_top - stabiliser_top")
    sides.derive("y", "0")
    sides.done()

    round_rungs = c["rungs"] == "round"
    rungs = d.step("rungs", "rungs", "stacked_across", "cylinder_x" if round_rungs else "box")
    rungs.default("count", rules.rung_count, height=height)
    rungs.derive("width", "sides_spread_x - 2 * sides_thickness")
    if round_rungs:
        tall = "rungs_diameter"
        rungs.default("diameter", rules.rung_diameter, height=height)
    else:
        tall = "rungs_height"
        rungs.default("height", rules.tread_thickness, height=height)
        rungs.derive("thickness", "sides_length_y")
    # Evenly spaced: the same step from the floor to the first rung as between rungs.
    rungs.derive("pitch", "sides_top / (rungs_count + 1)")
    rungs.derive("bottom", f"rungs_pitch - {tall} / 2")
    rungs.derive("y", "0")
    rungs.done()
    d.require(d.values["rungs_count"] >= 2, "a ladder needs rungs")
    d.require(MIN_STEP <= d.values["rungs_pitch"] <= MAX_STEP, "rungs too close or too far apart")
    d.require(d.values[tall] <= d.values["sides_length_y"] - 20,
              "rungs too thick for the side rails")
    d.require(4 * d.values["sides_thickness"] <= d.values["rungs_width"], "side rails too thick")
    overall_width = "stabiliser_width" if c["stabiliser"] == "yes" else "sides_spread_x"
    return d.finish((overall_width, "sides_length_y", "sides_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    return ["ladder", "ladder", "wooden ladder", "straight ladder", "simple ladder",
            "loft ladder" if v["sides_top"] >= 2500 else "short ladder"]


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, h = v["sides_spread_x"], v["sides_top"]
    return [s.one(f"{s.mm(w)} wide", f"{s.mm(w)} wide", f"width {s.mm(w)}",
                  f"{s.mm(w)} across the side rails"),
            s.one(f"{s.mm(h)} high", f"{s.mm(h)} tall", f"height {s.mm(h)}", f"{s.mm(h)} long")]


OPTION_WORDS = {
    ("rungs", "round"): ["+round rungs", "+dowel rungs", "+round bar rungs"],
    ("rungs", "flat"): ["+flat steps", "+flat treads in place of round rungs", "+board steps"],
    ("stabiliser", "yes"): ["+a stabiliser bar", "+a stabiliser bar under the feet",
                            "+a wide foot bar"],
    ("stabiliser", "no"): ["no stabiliser bar", "without a stabiliser"],
}
COUNT_THINGS = {"rungs_count": [("rung", "rungs"), ("rung", "rungs"), ("step", "steps")]}
OVERRIDE_WORDS = {
    "sides_thickness": ["side rails {v} thick", "+{v} thick side rails", "stiles {v} thick"],
    "sides_length_y": ["side rails {v} deep", "+{v} deep side rails", "stiles {v} deep"],
    "rungs_diameter": ["rungs {v} in diameter", "rung diameter {v}", "+{v} diameter rungs"],
    "rungs_height": ["steps {v} thick", "+{v} thick steps", "tread thickness {v}"],
    "stabiliser_width": ["stabiliser bar {v} long", "+a {v} long stabiliser bar"],
}
