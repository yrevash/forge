"""Cabinet (cupboard): a carcass with shelves inside and one or two doors in front.

The carcass is kinds/_carcass.py. The doors stand between the sides, flush with
the front edges; the shelves stop behind them. "2 shelves" means two shelves
inside: the bottom board and the top board are not counted.

All size ranges here are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _carcass
from forge.generators.structures.kinds import _shared as shared

NAME = "cabinet"

OPTIONS = {
    "doors": ("yes", "no"),
    "base": ("plinth", "legs", "none"),
    "back": ("yes", "no"),
}
OPTION_COUNTS = {("doors", "yes"): "doors_count"}
OVERRIDES = {**_carcass.CARCASS_OVERRIDES, "shelves_thickness": 1, "doors_thickness": 1}
MIN_OPENING = 150     # clear height between two shelves


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"doors": "yes", "base": "plinth", "back": "yes"}
    return {"doors": rng.choice(["yes", "yes", "no"]),
            "base": rng.choice(["plinth", "legs", "none"]),
            "back": rng.choice(["yes", "yes", "no"])}


def headline(rng: random.Random, c: dict) -> dict:
    width = shared.pick(rng, 400, 1200, 50)
    return {"top_width": width,
            "top_depth": min(width, shared.pick(rng, 300, 600, 10)),     # not deeper than wide
            "top_top": shared.pick(rng, 600, 2000, 50)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    most = max(1, min(5, int(given["top_top"] // 300) - 1))     # openings of about 300 or more
    found = {"shelves_count": rng.randint(1, most)}
    if c["doors"] == "yes":
        found["doors_count"] = rng.randint(1, 2)
    return found


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    _carcass.carcass(d, c["base"], back=c["back"] == "yes")
    span = max(given["top_width"], given["top_top"])
    opening = d.values["sides_top"] - d.values["bottom_top"]

    doors = None
    if c["doors"] == "yes":      # sized now, built after the shelves they stand in front of
        doors = d.step("doors", "fronts", "upright_row")
        doors.default("thickness", rules.board_thickness, span=span)

    shelves = d.step("shelves", "shelves", "stacked")
    shelves.default("count", rules.cabinet_shelf_count, opening=opening)
    shelves.derive("width", "bottom_width")
    shelves.default("thickness", rules.board_thickness, span=span)
    # The shelves stop at the back panel and behind the doors, whichever of them exist.
    has_back = c["back"] == "yes"
    shelves.derive("depth", "top_depth" + (" - back_panel_thickness" if has_back else "")
                   + (" - doors_thickness" if doors else ""))
    # Evenly spaced between the bottom board and the top board.
    shelves.derive("pitch", "(sides_top - bottom_top + shelves_thickness) / (shelves_count + 1)")
    shelves.derive("bottom", "bottom_top + shelves_pitch - shelves_thickness")
    shelves.derive("x", "0")
    if has_back and doors:
        shelves.derive("y", "(doors_thickness - back_panel_thickness) / 2")
    elif has_back:
        shelves.derive("y", "-back_panel_thickness / 2")
    else:
        shelves.derive("y", "doors_thickness / 2" if doors else "0")
    shelves.done()
    d.require(d.values["shelves_count"] >= 1, "a shelf step needs at least one shelf")
    d.require(d.values["shelves_pitch"] - d.values["shelves_thickness"] >= MIN_OPENING,
              "shelves too close together")

    if doors:
        count = doors.default("count", rules.door_count, width=d.values["bottom_width"])
        d.require(count in (1, 2), "one door or a pair")
        doors.derive("width", "bottom_width / doors_count")
        doors.derive("bottom", "bottom_top")             # they stand on the bottom board
        doors.derive("top", "sides_top")
        doors.derive("pitch", "doors_width")             # a pair meets in the middle
        doors.derive("y", "-(top_depth - doors_thickness) / 2")     # flush with the front
        doors.done()
        d.require(150 <= d.values["doors_width"] <= 600, "door too narrow or too wide")
    return d.finish(("top_width", "top_depth", "top_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    found = ["cabinet", "cabinet", "cupboard", "storage cabinet", "wooden cabinet"]
    if v["top_top"] >= 1500:
        found += ["tall cabinet", "tall cupboard"]
    elif v["top_top"] <= 900:
        found += ["base cabinet", "low cupboard", "sideboard" if v["top_width"] >= 900
                  else "floor cabinet"]
    return found


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, d, h = v["top_width"], v["top_depth"], v["top_top"]
    return [s.one(f"{s.mm(w)} wide", f"{s.mm(w)} wide", f"width {s.mm(w)}"),
            s.one(f"{s.mm(d)} deep", f"{s.mm(d)} deep", f"depth {s.mm(d)}"),
            s.one(f"{s.mm(h)} high", f"{s.mm(h)} high", f"height {s.mm(h)}", f"{s.mm(h)} tall")]


OPTION_WORDS = {
    ("doors", "yes"): ["+doors", "+doors on the front", "+a door front"],
    ("doors", "no"): ["no doors", "without doors", "open-fronted", "+an open front"],
    **_carcass.BASE_WORDS,
}
COUNT_THINGS = {"shelves_count": [("shelf inside", "shelves inside"), ("shelf", "shelves"),
                                  ("inner shelf", "inner shelves")],
                "doors_count": [("door", "doors"), ("door", "doors"),
                                ("door on the front", "doors on the front")]}
OVERRIDE_WORDS = {
    **_carcass.CARCASS_OVERRIDE_WORDS,
    "shelves_thickness": ["shelves {v} thick", "shelf thickness {v}", "+{v} thick shelves"],
    "doors_thickness": ["doors {v} thick", "door thickness {v}", "+{v} thick doors"],
}
