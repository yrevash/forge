"""Pallet: deck boards across three or four stringers, with bottom boards underneath.

    stringers      run the length of the pallet (left to right), side by side
    deck           boards across the stringers with gaps, or one solid board
    bottom boards  boards across under the stringers (left out: a skid)

The overall height is not stated: it is the stack of board, stringer and board.
All sizes here are our own design choices. They are NOT the dimensions of any
pallet standard (EUR, ISO 6780 ...); none of those is in the repo's tables.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "pallet"

OPTIONS = {
    "deck": ("slats", "solid"),
    "bottom_boards": ("yes", "no"),
}
OPTION_COUNTS = {("deck", "slats"): "deck_count"}
OVERRIDES = {
    "deck_height": 1, "deck_width": 10, "deck_thickness": 1, "stringers_width": 5,
    "stringers_height": 10, "bottom_boards_width": 10,
}
MIN_GAP = 15          # clear space between two deck boards


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"deck": "slats", "bottom_boards": "yes"}
    return {"deck": rng.choice(["slats", "slats", "slats", "solid"]),
            "bottom_boards": rng.choice(["yes", "yes", "no"])}


def width_param(c: dict) -> str:
    """The parameter that holds the pallet's width (front to back)."""
    return "deck_length_y" if c["deck"] == "slats" else "deck_depth"


def headline(rng: random.Random, c: dict) -> dict:
    length = shared.pick(rng, 800, 1400, 100)
    return {"stringers_length_x": length,
            width_param(c): min(length, shared.pick(rng, 600, 1200, 100))}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    if c["deck"] != "slats":
        return {}
    length = given["stringers_length_x"]
    return {"deck_count": rng.randint(int(length // 200), int(length // 125))}


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    slatted = c["deck"] == "slats"
    length, width = given["stringers_length_x"], given[width_param(c)]
    span = max(length, width)
    # The deck is built last, but its width is what the stringers are spread over.
    if slatted:
        deck = d.step("deck", "slats", "cross_row")
        deck.stated("length_y")
    else:
        deck = d.step("deck", "top", "level")
        deck.stated("depth")
    across = width_param(c)
    stringers = d.step("stringers", "bars", "long_row")    # sized now; built after the boards
    stringers.stated("length_x")

    floor = "0"
    if c["bottom_boards"] == "yes":
        boards = d.step("bottom_boards", "slats", "cross_row")
        boards.default("count", rules.bottom_board_count, length=length)
        boards.default("width", rules.pallet_board_width, span=span)
        boards.derive("length_y", across)
        boards.default("height", rules.pallet_board_thickness, span=span)
        boards.derive("top", "bottom_boards_height")
        # Evenly spaced, the outer two flush with the ends of the pallet.
        boards.derive("pitch", "(stringers_length_x - bottom_boards_width) "
                               "/ (bottom_boards_count - 1)")
        boards.done()
        floor = "bottom_boards_top"

    stringers.default("count", rules.stringer_count, width=width)
    stringers.default("width", rules.stringer_width, span=span)
    stringers.default("height", rules.stringer_height, span=span)
    stringers.derive("top", "stringers_height" if floor == "0"
                     else "bottom_boards_top + stringers_height")
    # Evenly spaced, the outer two flush with the front and back edges.
    stringers.derive("pitch", f"({across} - stringers_width) / (stringers_count - 1)")
    stringers.derive("y", "0")
    stringers.done()

    if slatted:
        deck.default("count", rules.deck_board_count, length=length)
        deck.default("width", rules.pallet_board_width, span=span)
        deck.default("height", rules.pallet_board_thickness, span=span)
        deck.derive("top", "stringers_top + deck_height")
        deck.derive("pitch", "(stringers_length_x - deck_width) / (deck_count - 1)")
        deck.done()
        d.require(d.values["deck_count"] >= 2, "a slatted deck needs boards")
        d.require(d.values["deck_pitch"] - d.values["deck_width"] >= MIN_GAP,
                  "deck boards too close together")
        d.require(d.values["deck_pitch"] - d.values["deck_width"] <= 150,
                  "deck boards too far apart")
    else:
        deck.derive("width", "stringers_length_x")
        deck.default("thickness", rules.pallet_board_thickness, span=span)
        deck.derive("top", "stringers_top + deck_thickness")
        deck.done()
    return d.finish(("stringers_length_x", across, "deck_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    found = ["pallet", "pallet", "wooden pallet", "shipping pallet", "timber pallet"]
    if c["bottom_boards"] == "no":
        found += ["skid", "skid pallet"]
    return found


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    length, w = v["stringers_length_x"], v[width_param(c)]
    return [s.one(f"{s.size(length, w)}", f"{s.size(length, w)}",
                  f"{s.mm(length)} long and {s.mm(w)} wide", f"length {s.mm(length)}, width {s.mm(w)}",
                  f"deck {s.size(length, w)}")]


OPTION_WORDS = {
    ("deck", "slats"): ["+a slatted deck", "+deck boards with gaps", "+a deck of spaced boards"],
    ("deck", "solid"): ["+a solid deck", "+a solid deck board", "+a closed deck",
                        "+a single sheet for the deck"],
    ("bottom_boards", "yes"): ["+bottom boards", "+bottom boards under the stringers"],
    ("bottom_boards", "no"): ["no bottom boards", "without bottom boards",
                              "+the stringers straight on the floor"],
}
COUNT_THINGS = {"deck_count": [("deck board", "deck boards"), ("top board", "top boards"),
                               ("board on the deck", "boards on the deck")]}
OVERRIDE_WORDS = {
    "deck_height": ["deck boards {v} thick", "+{v} thick deck boards"],
    "deck_width": ["deck boards {v} wide", "+{v} wide deck boards"],
    "deck_thickness": ["deck {v} thick", "+a {v} thick deck board"],
    "stringers_width": ["stringers {v} wide", "+{v} wide stringers"],
    "stringers_height": ["stringers {v} high", "+{v} high stringers", "stringer height {v}"],
    "bottom_boards_width": ["bottom boards {v} wide", "+{v} wide bottom boards"],
}
