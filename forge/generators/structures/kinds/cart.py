"""Cart (platform trolley): a deck on two axle beams with a wheel at each end, and a handle.

The cart rolls from front to back, so the wheels are discs lying left to right
(shape `cylinder_x`). Each square axle beam runs under the deck from one wheel
to the other; the wheels stand outside the deck, 10 clear of its edges, so the
overall width is the deck plus that clearance plus the wheels. A handle is two
posts on the back corners of the deck with a round bar between them.

All sizes here are our own design choices. Wheel sizes are not from a catalogue.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "cart"

OPTIONS = {
    "handle": ("back", "both", "none"),      # at the back end, at both ends, or no handle
    "sides": ("no", "yes"),                  # a board on edge along each long side of the deck
    "shelf": ("no", "yes"),                  # a second deck half-way up; needs both handles
}
OPTION_COUNTS: dict = {}
OVERRIDES = {
    "wheels_diameter": 5, "wheels_width": 5, "top_thickness": 1, "posts_thickness": 5,
    "handle_diameter": 5, "side_boards_height": 10, "shelf_top": 10,
}
CLEARANCE = 10        # between the deck's edge and the wheel beside it (our choice)


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"handle": "back", "sides": "no"}
    c = {"handle": rng.choice(["back", "back", "both", "both", "none"]),
         "sides": rng.choice(["no", "no", "yes"])}
    if c["handle"] == "both":        # four posts can carry a shelf
        c["shelf"] = rng.choice(["no", "yes"])
    return c


def headline(rng: random.Random, c: dict) -> dict:
    given = {"top_width": shared.pick(rng, 400, 700, 50),
             "top_depth": shared.pick(rng, 600, 1200, 50)}
    if c["handle"] != "none":
        given["posts_top"] = shared.pick(rng, 800, 1000, 10)
    return given


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    return {}


def _handle(d: Draft, posts_name: str, bar_name: str, corners: str, sign: str,
            copy_of: str | None) -> None:
    """Two posts on one end of the deck and a round bar between them, near the top."""
    posts = d.step(posts_name, "posts", corners)
    if copy_of:                      # the second handle copies the first one's numbers
        posts.derive("thickness", "posts_thickness")
        posts.derive("top", "posts_top")
    else:
        posts.default("thickness", rules.bar_size, span=d.values["top_depth"])
        posts.stated("top")
    posts.derive("bottom", "top_top")
    posts.derive("spread_x", "top_width")
    posts.derive("spread_y", "top_depth")
    posts.done()
    bar = d.step(bar_name, "handle", "across", "cylinder_x")
    bar.derive("width", "top_width - 2 * posts_thickness")
    if copy_of:
        bar.derive("diameter", "handle_diameter")
    else:
        bar.default("diameter", rules.handle_diameter, post=d.values["posts_thickness"])
    bar.derive("y", f"{sign}(top_depth - posts_thickness) / 2")
    bar.derive("top", "posts_top - posts_thickness / 2")       # half a post below the top
    bar.done()
    d.require(d.values[f"{bar_name}_diameter"] <= d.values["posts_thickness"],
              "handle bar thicker than its posts")


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    # Wheels, axles and deck depend on each other's sizes, so all three are opened first.
    top = d.step("top", "top", "level")
    width, depth = top.stated("width"), top.stated("depth")
    top.default("thickness", rules.board_thickness, span=max(width, depth))
    wheels = d.step("wheels", "wheels", "corners", "cylinder_x")
    diameter = wheels.default("diameter", rules.wheel_diameter, length=depth)
    wheels.default("width", rules.wheel_width, diameter=diameter)
    wheels.derive("bottom", "0")
    axles = d.step("axles", "axles", "long_row")
    axles.derive("count", "2")
    axles.derive("length_x", f"top_width + {2 * CLEARANCE}")
    axles.default("width", rules.axle_size, diameter=diameter)
    axles.derive("height", "axles_width")                      # square beams
    axles.derive("top", "(wheels_diameter + axles_height) / 2")     # centred on the wheels
    axles.derive("pitch", "top_depth - wheels_diameter")       # one under each pair of wheels
    axles.derive("y", "0")
    wheels.derive("spread_x", "axles_length_x + 2 * wheels_width")
    wheels.derive("spread_y", "top_depth")
    wheels.done()
    axles.done()
    top.derive("top", "axles_top + top_thickness")
    top.done()
    d.require(2 * diameter <= depth - 100, "wheels too big for the deck")

    if c["handle"] != "none":
        _handle(d, "posts", "handle", "back_corners", "", None)
    if c["handle"] == "both":
        _handle(d, "front_posts", "front_handle", "front_corners", "-", "posts")

    overall_height = "posts_top" if c["handle"] != "none" else "wheels_diameter"
    if c["sides"] == "yes":          # they stand on the deck, between the handle posts
        boards = d.step("side_boards", "panels", "sides")
        boards.default("height", rules.cart_side_height, length=depth)
        boards.derive("thickness", "top_thickness")
        boards.derive("top", "top_top + side_boards_height")
        # Their length and place depend on which ends have handle posts in the way.
        length_y, y = {"none": ("top_depth", "0"),
                       "back": ("top_depth - posts_thickness", "-posts_thickness / 2"),
                       "both": ("top_depth - 2 * posts_thickness", "0")}[c["handle"]]
        boards.derive("length_y", length_y)
        boards.derive("spread_x", "top_width")
        boards.derive("y", y)
        boards.done()
        if c["handle"] == "none":
            d.require(d.values["side_boards_top"] > diameter, "side boards lower than the wheels")
            overall_height = "side_boards_top"
        else:
            d.require(d.values["side_boards_top"] <= d.values["handle_top"]
                      - d.values["handle_diameter"] - 100, "side boards reach the handle")
    if c.get("shelf") == "yes":      # a second deck between the four posts
        shelf = d.step("shelf", "shelves", "level")
        shelf.derive("width", "top_width - 2 * posts_thickness")
        shelf.derive("depth", "top_depth")
        shelf.derive("thickness", "top_thickness")
        shelf.default("top", rules.cart_shelf_top, deck_top=d.values["top_top"],
                      handle_top=d.values["posts_top"])
        shelf.done()
        low = d.values.get("side_boards_top", d.values["top_top"])
        d.require(d.values["shelf_top"] - d.values["shelf_thickness"] - low >= 150,
                  "shelf too close to the deck")
        d.require(d.values["handle_top"] - d.values["handle_diameter"] - d.values["shelf_top"]
                  >= 100, "shelf too close to the handle")
    return d.finish(("wheels_spread_x", "top_depth", overall_height))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    found = ["cart", "cart", "trolley", "hand cart", "platform trolley", "platform cart"]
    if c["handle"] == "none":
        found += ["dolly", "platform dolly"]
    if c["sides"] == "yes":
        found += ["wagon"]
    if c.get("shelf") == "yes":
        found += ["utility cart", "service trolley", "shelf trolley"]
    return found


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, length = v["top_width"], v["top_depth"]
    said = [s.one(f"deck {s.size(length, w)}", f"deck {s.mm(length)} long and {s.mm(w)} wide",
                  f"platform {s.mm(length)} long, {s.mm(w)} wide",
                  f"deck length {s.mm(length)}, deck width {s.mm(w)}")]
    if "posts_top" in v:
        h = v["posts_top"]
        said.append(s.one(f"handle height {s.mm(h)}", f"{s.mm(h)} to the top of the handle posts",
                          f"overall height {s.mm(h)}", f"handle posts {s.mm(h)} high"))
    return said


OPTION_WORDS = {
    ("handle", "back"): ["+a push handle", "+a handle at the back", "+a handle at the back end"],
    ("handle", "both"): ["+a handle at each end", "+a handle at both ends",
                         "+push handles front and back"],
    ("handle", "none"): ["no handle", "without a handle", "+a bare deck and no handle"],
    ("sides", "yes"): ["+side boards", "+a board along each long side", "+low sides"],
    ("sides", "no"): ["no side boards", "without sides", "+an open deck"],
    ("shelf", "yes"): ["+a shelf half-way up", "+a second deck between the posts",
                       "+an upper shelf"],
    ("shelf", "no"): ["no upper shelf", "without a shelf"],
}
COUNT_THINGS: dict = {}
OVERRIDE_WORDS = {
    "wheels_diameter": ["wheels {v} in diameter", "wheel diameter {v}", "+{v} diameter wheels"],
    "wheels_width": ["wheels {v} wide", "+{v} wide wheels", "wheel width {v}"],
    "top_thickness": ["deck {v} thick", "+a {v} thick deck", "deck thickness {v}"],
    "posts_thickness": ["handle posts {v} square", "+{v} square handle posts"],
    "handle_diameter": ["handle bar {v} in diameter", "+a {v} diameter handle bar"],
    "side_boards_height": ["side boards {v} high", "+{v} high side boards"],
    "shelf_top": ["shelf {v} above the floor", "top of the shelf at {v}"],
}
