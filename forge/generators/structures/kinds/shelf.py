"""Shelf unit (bookcase): two side panels with 2 to 7 shelves between them.

The lowest shelf is the unit's bottom board and the highest is its top board, so
"5 shelves" means five boards and four openings. A back panel and a plinth are
optional, and so are doors on the lower part: they stand between the sides, in
front of the shelves, and cover the lower half of the openings.
All size ranges are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import math
import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "shelf"

OPTIONS = {"back": ("no", "yes"), "plinth": ("no", "yes"), "doors": ("no", "yes")}
OPTION_COUNTS = {("doors", "yes"): "doors_count"}       # "2 doors" says there are doors
OVERRIDES = {
    "sides_thickness": 1, "shelves_thickness": 1, "plinth_height": 10, "back_panel_thickness": 1,
    "doors_thickness": 1,
}
MIN_OPENING = 150     # clear height between two shelves
MAX_PITCH = 700       # further apart than this and it is a box, not a set of shelves


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"back": "no", "plinth": "no", "doors": "no"}
    return {"back": rng.choice(["no", "yes"]), "plinth": rng.choice(["no", "no", "yes"]),
            "doors": rng.choice(["no", "no", "yes"])}


def headline(rng: random.Random, c: dict) -> dict:
    width = shared.pick(rng, 400, 1200, 50)
    return {"sides_spread_x": width,
            "sides_height": shared.pick(rng, 600, 2200, 50),
            "sides_length_y": min(width, shared.pick(rng, 200, 450, 10))}   # not deeper than wide


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    height = given["sides_height"]
    least = math.ceil(height / MAX_PITCH) + 1
    most = min(7, int((height - 100) // 200) + 1)      # openings of about 200 or more
    found = {"shelves_count": rng.randint(least, max(least, most))}
    if c["doors"] == "yes":
        found["doors_count"] = rng.randint(1, 2)
    return found


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    sides = d.step("sides", "panels", "sides")
    height = sides.stated("height")
    width = sides.stated("spread_x")
    span = max(width, height)
    sides.default("thickness", rules.board_thickness, span=span)
    sides.derive("top", "sides_height")
    sides.stated("length_y")
    sides.derive("y", "0")
    sides.done()
    clear = "sides_spread_x - 2 * sides_thickness"       # between the two sides

    if c["back"] == "yes":       # let in between the sides, flush with their back edges
        back = d.step("back_panel", "back_panel", "across")
        back.derive("width", clear)
        back.derive("height", "sides_height")
        back.default("thickness", rules.thin_panel_thickness, span=span)
        back.derive("y", "(sides_length_y - back_panel_thickness) / 2")
        back.derive("top", "sides_height")
        back.done()
    if c["plinth"] == "yes":     # a board under the bottom shelf, set back from the front
        plinth = d.step("plinth", "rail", "across")
        plinth.derive("width", clear)
        plinth.default("height", rules.plinth_height, height=height)
        plinth.default("thickness", rules.board_thickness, span=span)
        plinth.derive("y", "-sides_length_y / 2 + 2 * plinth_thickness")
        plinth.derive("top", "plinth_height")
        plinth.done()

    doors = None
    if c["doors"] == "yes":      # sized now, built after the shelves they stand in front of
        doors = d.step("doors", "fronts", "upright_row")
        doors.default("thickness", rules.board_thickness, span=span)

    shelves = d.step("shelves", "shelves", "stacked")
    count = shelves.default("count", rules.shelf_count, height=height)
    shelves.derive("width", clear)
    shelves.default("thickness", rules.board_thickness, span=span)
    # The shelves stop at the back panel and behind the doors, whichever of them exist.
    has_back = c["back"] == "yes"
    shelves.derive("depth", "sides_length_y" + (" - back_panel_thickness" if has_back else "")
                   + (" - doors_thickness" if doors else ""))
    if has_back and doors:
        shelves.derive("y", "(doors_thickness - back_panel_thickness) / 2")
    elif has_back:
        shelves.derive("y", "-back_panel_thickness / 2")
    else:
        shelves.derive("y", "doors_thickness / 2" if doors else "0")
    shelves.derive("bottom", "plinth_height" if c["plinth"] == "yes" else "0")
    # Evenly spaced, the highest one flush with the top of the sides.
    shelves.derive("pitch",
                   "(sides_height - shelves_thickness - shelves_bottom) / (shelves_count - 1)")
    shelves.derive("x", "0")
    shelves.done()
    d.require(d.values["shelves_count"] >= 2, "a shelf unit needs a bottom and a top board")
    d.require(d.values["shelves_pitch"] - d.values["shelves_thickness"] >= MIN_OPENING,
              "shelves too close together")
    d.require(d.values["shelves_pitch"] <= MAX_PITCH, "shelves too far apart")
    if doors:
        d.require(count >= 3, "doors on the lower part need at least two openings")
        # Our choice: the doors cover the lower half of the openings (rounded down).
        covered = max(1, (count - 1) // 2)
        doors_count = doors.default("count", rules.door_count,
                                    width=d.values["shelves_width"])
        d.require(doors_count in (1, 2), "one door or a pair")
        doors.derive("width", "shelves_width / doors_count")
        doors.derive("bottom", "shelves_bottom")
        doors.derive("top", f"shelves_bottom + {covered} * shelves_pitch + shelves_thickness")
        doors.derive("pitch", "doors_width")             # a pair meets in the middle
        doors.derive("y", "-(sides_length_y - doors_thickness) / 2")   # flush with the front
        doors.done()
        d.require(150 <= d.values["doors_width"] <= 600, "door too narrow or too wide")
    return d.finish(("sides_spread_x", "sides_length_y", "sides_height"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    return ["shelf", "bookshelf", "bookshelf", "bookcase", "shelving unit", "shelf unit",
            "storage shelf", "set of shelves"]


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, h, d = v["sides_spread_x"], v["sides_height"], v["sides_length_y"]
    return [s.one(f"{s.mm(w)} wide", f"{s.mm(w)} wide", f"width {s.mm(w)}"),
            s.one(f"{s.mm(h)} high", f"{s.mm(h)} high", f"height {s.mm(h)}", f"{s.mm(h)} tall"),
            s.one(f"{s.mm(d)} deep", f"{s.mm(d)} deep", f"depth {s.mm(d)}")]


OPTION_WORDS = {
    ("back", "yes"): ["+a back panel", "+a back", "+a closed back"],
    ("back", "no"): ["no back panel", "open back", "without a back"],
    ("plinth", "yes"): ["+a plinth", "+a plinth at the bottom", "+a kick board", "on a plinth"],
    ("plinth", "no"): ["no plinth", "without a plinth"],
    ("doors", "yes"): ["+doors on the lower part", "+doors at the bottom",
                       "+the lower shelves behind doors", "+a cupboard at the bottom"],
    ("doors", "no"): ["no doors", "without doors", "open-fronted"],
}
COUNT_THINGS = {"shelves_count": [("shelf", "shelves"), ("shelf", "shelves"),
                                  ("shelf board", "shelf boards")],
                "doors_count": [("door on the lower part", "doors on the lower part"),
                                ("door at the bottom", "doors at the bottom"),
                                ("lower door", "lower doors")]}
OVERRIDE_WORDS = {
    "sides_thickness": ["sides {v} thick", "side panels {v} thick", "+{v} thick sides"],
    "shelves_thickness": ["shelves {v} thick", "shelf thickness {v}", "+{v} thick shelves"],
    "plinth_height": ["plinth {v} high", "plinth height {v}"],
    "back_panel_thickness": ["back panel {v} thick", "+{v} thick back panel"],
    "doors_thickness": ["doors {v} thick", "door thickness {v}", "+{v} thick doors"],
}
