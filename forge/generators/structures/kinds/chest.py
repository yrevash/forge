"""Chest of drawers: a carcass with a stack of drawer fronts.

The carcass is kinds/_carcass.py. Only the FRONT of each drawer is built: a board
standing between the sides, flush with the front edges, 3 mm from the next one.
There is no drawer box behind it (README, known limits).

All size ranges here are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _carcass
from forge.generators.structures.kinds import _shared as shared

NAME = "chest"

OPTIONS = {
    "base": ("plinth", "legs", "none"),
    "back": ("yes", "no"),
}
OPTION_COUNTS: dict = {}
OVERRIDES = {**_carcass.CARCASS_OVERRIDES, "fronts_thickness": 1}
GAP = 3               # clear space between two drawer fronts (our choice)
MIN_FRONT, MAX_FRONT = 90, 320     # a drawer front lower or taller than this is not a drawer


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"base": "plinth", "back": "yes"}
    return {"base": rng.choice(["plinth", "legs", "none"]), "back": rng.choice(["yes", "yes", "no"])}


def headline(rng: random.Random, c: dict) -> dict:
    width = shared.pick(rng, 400, 1200, 50)
    return {"top_width": width,
            "top_depth": min(width, shared.pick(rng, 350, 550, 10)),
            "top_top": shared.pick(rng, 500, 1300, 50)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    most = max(2, min(7, int(given["top_top"] // 160)))         # fronts of about 150 or more
    least = max(2, int(given["top_top"] // 300))
    return {"fronts_count": rng.randint(least, max(least, most))}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    _carcass.carcass(d, c["base"], back=c["back"] == "yes")
    opening = d.values["sides_top"] - d.values["bottom_top"]

    fronts = d.step("fronts", "fronts", "stacked_across")
    fronts.default("count", rules.drawer_count, opening=opening)
    fronts.derive("width", "bottom_width")
    # The fronts fill the opening from the bottom board to the top board, GAP apart.
    fronts.derive("height", f"(sides_top - bottom_top - (fronts_count - 1) * {GAP}) / fronts_count")
    fronts.default("thickness", rules.board_thickness,
                   span=max(given["top_width"], given["top_top"]))
    fronts.derive("bottom", "bottom_top")
    fronts.derive("pitch", f"fronts_height + {GAP}")
    fronts.derive("y", "-(top_depth - fronts_thickness) / 2")       # flush with the front
    fronts.done()
    d.require(d.values["fronts_count"] >= 1, "a chest of drawers needs a drawer")
    d.require(MIN_FRONT <= d.values["fronts_height"] <= MAX_FRONT,
              "drawer fronts too low or too tall")
    return d.finish(("top_width", "top_depth", "top_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    found = ["chest of drawers", "chest of drawers", "drawer unit", "drawer chest", "dresser"]
    if v["top_top"] <= 700:
        found += ["bedside drawer unit" if v["top_width"] <= 600 else "low chest of drawers"]
    if v["top_top"] >= 1100:
        found += ["tallboy"]
    return found


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, d, h = v["top_width"], v["top_depth"], v["top_top"]
    return [s.one(f"{s.mm(w)} wide", f"{s.mm(w)} wide", f"width {s.mm(w)}"),
            s.one(f"{s.mm(d)} deep", f"{s.mm(d)} deep", f"depth {s.mm(d)}"),
            s.one(f"{s.mm(h)} high", f"{s.mm(h)} high", f"height {s.mm(h)}", f"{s.mm(h)} tall")]


OPTION_WORDS = dict(_carcass.BASE_WORDS)
COUNT_THINGS = {"fronts_count": [("drawer", "drawers"), ("drawer", "drawers"),
                                 ("drawer front", "drawer fronts")]}
OVERRIDE_WORDS = {
    **_carcass.CARCASS_OVERRIDE_WORDS,
    "fronts_thickness": ["drawer fronts {v} thick", "+{v} thick drawer fronts"],
}
