"""Workbench: a heavy top on four legs with aprons, a lower shelf or stretchers, a back board.

It is the table's leg frame (kinds/_shared.py) at bench height. The back board
stands on the top along its back edge, so the overall height is then the height
of the top plus the board.

All size ranges here are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "workbench"

OPTIONS = {
    "lower": ("shelf", "stretchers", "h", "none"),     # what ties the legs together low down
    "back_board": ("no", "yes"),
}
OPTION_COUNTS: dict = {}
OVERRIDES = {
    "top_thickness": 5, "legs_thickness": 5, "aprons_height": 10, "stretchers_top": 10,
    "shelf_thickness": 1, "back_board_height": 10, "back_board_thickness": 1,
}


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"lower": "shelf", "back_board": "no"}
    return {"lower": rng.choice(["shelf", "shelf", "stretchers", "h", "none"]),
            "back_board": rng.choice(["no", "yes"])}


def headline(rng: random.Random, c: dict) -> dict:
    return {"top_width": shared.pick(rng, 1000, 2400, 50),
            "top_depth": shared.pick(rng, 500, 900, 50),
            "top_top": shared.pick(rng, 800, 950, 10)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    return {}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    top = d.step("top", "top", "level")
    span = max(top.stated("width"), top.stated("depth"))
    top.default("thickness", rules.top_thickness, span=span)
    top.stated("top")
    top.done()
    shared.corner_legs(d, "top", round_legs=False)
    shared.aprons(d)
    if c["lower"] in ("stretchers", "shelf"):
        shared.stretchers(d, "ring")
    elif c["lower"] == "h":
        shared.stretchers(d, "h")
    if c["lower"] == "shelf":
        # The shelf lies on the front and back stretchers and fits between the legs.
        shelf = d.step("shelf", "shelves", "level")
        shelf.derive("width", "legs_spread_x - 2 * legs_thickness")
        shelf.derive("depth", "stretchers_spread_y")
        shelf.default("thickness", rules.board_thickness, span=span)
        shelf.derive("top", "stretchers_top + shelf_thickness")
        shelf.done()
    if c["back_board"] == "yes":       # stands on the top, flush with its back edge
        board = d.step("back_board", "back_panel", "across")
        board.derive("width", "top_width")
        board.default("height", rules.back_board_height, depth=given["top_depth"])
        board.default("thickness", rules.board_thickness, span=span)
        board.derive("y", "(top_depth - back_board_thickness) / 2")
        board.derive("top", "top_top + back_board_height")
        board.done()
        return d.finish(("top_width", "top_depth", "back_board_top"))
    return d.finish(("top_width", "top_depth", "top_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    return ["workbench", "workbench", "work bench", "garage workbench", "shop bench",
            "wooden workbench", "workshop bench"]


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, d, h = v["top_width"], v["top_depth"], v["top_top"]
    return [s.one(f"top {s.size(w, d)}", f"{s.size(w, d)} top", f"{s.mm(w)} long and {s.mm(d)} deep",
                  f"{s.mm(w)} wide, {s.mm(d)} deep", f"length {s.mm(w)}, depth {s.mm(d)}"),
            s.one(f"top {s.mm(h)} high", f"worktop height {s.mm(h)}", f"top at {s.mm(h)}",
                  f"working height {s.mm(h)}")]


OPTION_WORDS = {
    ("lower", "shelf"): ["+a lower shelf", "+a shelf underneath", "+a shelf between the legs",
                         "+a lower shelf resting on stretchers"],
    ("lower", "stretchers"): ["+stretchers all round", "+stretchers between the legs",
                              "+a stretcher between each pair of legs"],
    ("lower", "h"): ["+an H stretcher", "+side stretchers and a cross stretcher"],
    ("lower", "none"): ["no stretchers", "nothing between the legs", "no lower shelf"],
    ("back_board", "yes"): ["+a back board", "+a board along the back of the top",
                            "+an upstand at the back", "+a backsplash board"],
    ("back_board", "no"): ["no back board", "without a back board"],
}
COUNT_THINGS: dict = {}
OVERRIDE_WORDS = {
    "top_thickness": ["top {v} thick", "top thickness {v}", "+{v} thick top"],
    "legs_thickness": ["legs {v} square", "leg thickness {v}", "+{v} square legs"],
    "aprons_height": ["aprons {v} high", "apron height {v}", "+{v} high aprons"],
    "stretchers_top": ["top of the stretchers {v} above the floor", "stretcher tops at {v}"],
    "shelf_thickness": ["shelf {v} thick", "+{v} thick shelf"],
    "back_board_height": ["back board {v} high", "+{v} high back board"],
    "back_board_thickness": ["back board {v} thick", "+{v} thick back board"],
}
