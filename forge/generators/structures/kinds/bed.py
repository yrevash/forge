"""Bed frame: four legs joined by rails, a deck of slats (or one platform board), a headboard.

The bed is `width` across (X) and `length` from foot to head (Y); the head is at
the back. The slats lie across the bed on top of the rails, between the
headboard and the footboard. A headboard is a solid panel, or two posts with
rails between them; it stands on the back legs (on the platform, if the deck is
a platform board).

All size ranges here are our own design choices, not standard mattress sizes.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "bed"

OPTIONS = {
    "headboard": ("panel", "rails", "none"),
    "footboard": ("no", "yes"),
    "base": ("slats", "platform"),
    "centre_rail": ("no", "yes"),        # a rail from foot to head under the middle of the deck
}
OPTION_COUNTS = {("base", "slats"): "slats_count", ("headboard", "rails"): "head_rails_count"}
OVERRIDES = {
    "legs_thickness": 10, "aprons_height": 10, "slats_width": 10, "slats_height": 5,
    "platform_thickness": 1, "headboard_thickness": 5, "footboard_top": 10,
}
MIN_GAP, MAX_GAP = 30, 160       # clear space between two slats


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"headboard": "panel", "footboard": "no", "base": "slats", "centre_rail": "no"}
    return {"headboard": rng.choice(["panel", "panel", "rails", "rails", "none"]),
            "footboard": rng.choice(["no", "no", "yes"]),
            "base": rng.choice(["slats", "slats", "platform"]),
            "centre_rail": rng.choice(["no", "no", "yes"])}


def deck(c: dict) -> str:
    """The step that is the bed's deck: what the mattress lies on."""
    return "slats" if c["base"] == "slats" else "platform"


def head(c: dict) -> str | None:
    """The step whose top is the stated headboard height, if there is a headboard."""
    return {"panel": "headboard", "rails": "head_posts", "none": None}[c["headboard"]]


def headline(rng: random.Random, c: dict) -> dict:
    deck_top = shared.pick(rng, 250, 450, 10)
    given = {"legs_spread_x": shared.pick(rng, 800, 1800, 50),
             "legs_spread_y": shared.pick(rng, 1900, 2100, 50),
             f"{deck(c)}_top": deck_top}
    if head(c):
        given[f"{head(c)}_top"] = deck_top + shared.pick(rng, 400, 800, 10)
    return given


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    found = {}
    if c["base"] == "slats":
        found["slats_count"] = rng.randint(9, 15)
    if c["headboard"] == "rails":
        found["head_rails_count"] = rng.randint(2, 4)
    return found


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    slatted = c["base"] == "slats"
    # The legs come first, but they stop under the deck, so the deck is sized first.
    legs = d.step("legs", "legs", "corners")
    width, length = d.given["legs_spread_x"], d.given["legs_spread_y"]
    if slatted:
        slats = d.step("slats", "slats", "long_row")
        slats.default("height", rules.bed_slat_thickness, width=width)
        deck_top = slats.stated("top")
        under_deck = "slats_top - slats_height"
    else:
        platform = d.step("platform", "top", "level")
        platform.default("thickness", rules.board_thickness, span=length)
        deck_top = platform.stated("top")
        under_deck = "platform_top - platform_thickness"

    leg = legs.default("thickness", rules.leg_thickness, width=width, depth=length)
    legs.derive("bottom", "0")
    legs.derive("top", under_deck)
    legs.stated("spread_x")
    legs.stated("spread_y")
    legs.done()
    d.require(leg <= 100, "legs too heavy for a bed")
    shared.aprons(d)                     # the four rails of the bed, between the legs
    d.require(d.values["legs_top"] - d.values["aprons_height"] >= 50, "rails touch the floor")

    if c["centre_rail"] == "yes":        # between the foot rail and the head rail
        centre = d.step("centre_rail", "stretchers", "side")
        centre.derive("thickness", "aprons_thickness")
        centre.derive("length_y", "aprons_spread_y - 2 * aprons_thickness")
        centre.derive("height", "aprons_height")
        centre.derive("top", "legs_top")
        centre.derive("x", "0")
        centre.done()

    # A headboard and a footboard stand on the legs with a slatted deck, on the platform
    # otherwise; the slats lie between them.
    stands_on = "legs_top" if slatted else "platform_top"
    head_depth, foot_depth = "", ""
    rise = given[f"{head(c)}_top"] - deck_top if head(c) else 0.0
    if c["headboard"] == "panel":
        board = d.step("headboard", "back_panel", "across")
        board.derive("width", "legs_spread_x")
        board.default("thickness", rules.rail_thickness, leg=leg)
        board.stated("top")
        board.derive("height", f"headboard_top - {stands_on}")
        board.derive("y", "(legs_spread_y - headboard_thickness) / 2")
        board.done()
        head_depth = "headboard_thickness"
    elif c["headboard"] == "rails":
        posts = d.step("head_posts", "posts", "back_corners")
        posts.derive("thickness", "legs_thickness")
        posts.derive("bottom", stands_on)
        posts.stated("top")
        posts.derive("spread_x", "legs_spread_x")
        posts.derive("spread_y", "legs_spread_y")
        posts.done()
        rails = d.step("head_rails", "rail", "stacked_across")
        rails.default("count", rules.headboard_rail_count, rise=rise)
        rails.derive("width", "legs_spread_x - 2 * head_posts_thickness")
        rails.default("height", rules.lower_rail_height, post=leg)
        rails.default("thickness", rules.rail_thickness, leg=leg)
        rails.default("bottom", rules.headboard_rail_bottom, deck_top=deck_top, rise=rise)
        # Evenly spaced, the highest rail flush with the top of the posts.
        rails.derive("pitch", "(head_posts_top - head_rails_bottom - head_rails_height) "
                              "/ (head_rails_count - 1)")
        rails.derive("y", "(legs_spread_y - head_posts_thickness) / 2")
        rails.done()
        d.require(d.values["head_rails_count"] >= 2, "an open headboard needs two rails")
        d.require(d.values["head_rails_pitch"] - d.values["head_rails_height"] >= 40,
                  "headboard rails too close together")
        head_depth = "head_posts_thickness"
    if c["footboard"] == "yes":
        board = d.step("footboard", "back_panel", "across")
        board.derive("width", "legs_spread_x")
        board.default("thickness", rules.rail_thickness, leg=leg)
        board.default("top", rules.footboard_top, deck_top=deck_top, headboard_rise=rise)
        board.derive("height", f"footboard_top - {stands_on}")
        board.derive("y", "-(legs_spread_y - footboard_thickness) / 2")
        board.done()
        d.require(80 <= d.values["footboard_top"] - deck_top <= 450,
                  "footboard too low or too tall")
        if head(c):
            d.require(d.values["footboard_top"] < given[f"{head(c)}_top"],
                      "footboard taller than the headboard")
        foot_depth = "footboard_thickness"

    if slatted:
        slats.default("count", rules.bed_slat_count, length=length)
        slats.derive("length_x", "legs_spread_x")
        slats.default("width", rules.bed_slat_width, width=width)
        # Evenly spaced over the length the head and foot boards leave free.
        free = "legs_spread_y" + "".join(f" - {part}" for part in (head_depth, foot_depth) if part)
        slats.derive("pitch", f"({free} - slats_width) / (slats_count - 1)")
        if head_depth and foot_depth:
            slats.derive("y", f"({foot_depth} - {head_depth}) / 2")
        elif head_depth or foot_depth:
            slats.derive("y", f"-{head_depth} / 2" if head_depth else f"{foot_depth} / 2")
        else:
            slats.derive("y", "0")
        slats.done()
        d.require(d.values["slats_count"] >= 2, "a slatted deck needs slats")
        gap = d.values["slats_pitch"] - d.values["slats_width"]
        d.require(MIN_GAP <= gap <= MAX_GAP, "bed slats too close together or too far apart")
    else:
        platform.derive("width", "legs_spread_x")
        platform.derive("depth", "legs_spread_y")
        platform.done()

    top = f"{head(c)}_top" if head(c) else "footboard_top" if foot_depth else f"{deck(c)}_top"
    return d.finish(("legs_spread_x", "legs_spread_y", top))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    found = ["bed", "bed frame", "bed frame", "wooden bed", "wooden bed frame"]
    if c["base"] == "platform":
        found += ["platform bed", "platform bed"]
    if v["legs_spread_x"] <= 1000:
        found += ["single bed", "single bed frame"]
    elif v["legs_spread_x"] >= 1350:
        found += ["double bed", "double bed frame"]
    return found


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, length, h = v["legs_spread_x"], v["legs_spread_y"], v[f"{deck(c)}_top"]
    lies_on = "slats" if c["base"] == "slats" else "platform"
    said = [s.one(f"{s.mm(w)} wide", f"{s.mm(w)} wide", f"width {s.mm(w)}"),
            s.one(f"{s.mm(length)} long", f"{s.mm(length)} long", f"length {s.mm(length)}"),
            s.one(f"top of the {lies_on} at {s.mm(h)}", f"{lies_on} {s.mm(h)} above the floor",
                  f"mattress base {s.mm(h)} off the floor", f"deck height {s.mm(h)}")]
    if head(c):
        b = v[f"{head(c)}_top"]
        said.append(s.one(f"headboard height {s.mm(b)}", f"headboard {s.mm(b)} high",
                          f"{s.mm(b)} to the top of the headboard", f"overall height {s.mm(b)}"))
    return said


OPTION_WORDS = {
    ("headboard", "panel"): ["+a solid headboard", "+a panel headboard", "+a headboard"],
    ("headboard", "rails"): ["+a railed headboard", "+an open headboard of rails between posts",
                             "+a headboard made of horizontal rails"],
    ("headboard", "none"): ["no headboard", "without a headboard"],
    ("footboard", "yes"): ["+a footboard", "+a low footboard", "+a footboard at the other end"],
    ("footboard", "no"): ["no footboard", "without a footboard"],
    ("base", "platform"): ["+a solid platform in place of slats", "+a platform deck",
                           "+a solid deck board"],
    ("base", "slats"): ["+a slatted base", "+slats across", "slatted"],
    ("centre_rail", "yes"): ["+a centre rail", "+a centre rail under the deck",
                             "+a middle rail from foot to head"],
    ("centre_rail", "no"): ["no centre rail", "without a centre rail"],
}
COUNT_THINGS = {"slats_count": [("slat", "slats"), ("bed slat", "bed slats"),
                                ("slat across", "slats across")],
                "head_rails_count": [("headboard rail", "headboard rails"),
                                     ("rail in the headboard", "rails in the headboard")]}
OVERRIDE_WORDS = {
    "legs_thickness": ["legs {v} square", "leg thickness {v}", "+{v} square legs"],
    "aprons_height": ["side rails {v} high", "rails {v} high", "+{v} high side rails"],
    "slats_width": ["slats {v} wide", "slat width {v}", "+{v} wide slats"],
    "slats_height": ["slats {v} thick", "slat thickness {v}", "+{v} thick slats"],
    "platform_thickness": ["platform {v} thick", "+{v} thick platform board"],
    "headboard_thickness": ["headboard {v} thick", "+{v} thick headboard"],
    "footboard_top": ["footboard {v} high", "top of the footboard at {v}"],
}
