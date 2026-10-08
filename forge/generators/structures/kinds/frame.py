"""Frame: an open rectangle of square bars, with 0 to 3 cross bars. Three layouts.

flat     lying on the floor: two long bars, two end bars between them
upright  standing: the rectangle is vertical and stands on two feet
box      a post at each corner with a rectangle of bars at the top and at the bottom

A flat frame may be raised on four short feet. An upright or box frame may have a
mid rail: one more bar (upright) or one more rectangle of bars (box) half-way up.

All size ranges are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "frame"

OPTIONS = {"layout": ("flat", "upright", "box"),
           "feet": ("no", "yes"),            # flat only: raised on a short foot at each corner
           "mid_rail": ("no", "yes")}        # upright and box only
OPTION_COUNTS: dict = {}
# One bar size per frame; its parameter is the first bar step's.
OVERRIDES = {
    "long_bars_height": 5, "feet_height": 5, "uprights_thickness": 5, "feet_length_y": 50,
    "corner_feet_top": 10, "mid_bar_top": 50, "mid_bars_top": 50,
}
MIN_GAP = 50          # clear space between neighbouring cross bars


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"layout": "flat", "feet": "no"}
    layout = rng.choice(["flat", "upright", "box"])
    if layout == "flat":
        return {"layout": layout, "feet": rng.choice(["no", "no", "yes"])}
    return {"layout": layout, "mid_rail": rng.choice(["no", "no", "yes"])}


def headline(rng: random.Random, c: dict) -> dict:
    if c["layout"] == "flat":
        width = shared.pick(rng, 400, 2000, 50)
        return {"long_bars_length_x": width,
                "long_bars_spread_y": min(width, shared.pick(rng, 300, 1200, 50))}
    if c["layout"] == "upright":
        return {"feet_spread_x": shared.pick(rng, 400, 1500, 50),
                "uprights_top": shared.pick(rng, 600, 2000, 50)}
    width = shared.pick(rng, 400, 1500, 50)
    return {"uprights_spread_x": width,
            "uprights_spread_y": min(width, shared.pick(rng, 300, 1000, 50)),
            "uprights_top": shared.pick(rng, 400, 1800, 50)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    # No entry means no cross bars, which is what a frame has unless they are asked for.
    if c["layout"] == "upright" and c["mid_rail"] == "yes":
        return {}          # upright cross bars would run straight through the mid rail
    wanted = rng.choice([0, 1, 1, 2, 2, 3])
    return {"cross_bars_count": wanted} if wanted else {}


def _flat(d: Draft, cross: bool, feet: bool) -> tuple[str, str, str]:
    long_bars = d.step("long_bars", "bars", "front_back")      # sized now; built after any feet
    width, depth = long_bars.stated("length_x"), long_bars.stated("spread_y")
    bar = long_bars.default("height", rules.bar_size, span=max(width, depth))
    long_bars.derive("thickness", "long_bars_height")          # square bars
    if feet:       # a short foot of the same bar under each corner
        corner_feet = d.step("corner_feet", "bars", "corners")
        corner_feet.derive("thickness", "long_bars_height")
        corner_feet.derive("bottom", "0")
        corner_feet.default("top", rules.frame_foot_height, bar=bar)
        corner_feet.derive("spread_x", "long_bars_length_x")
        corner_feet.derive("spread_y", "long_bars_spread_y")
        corner_feet.done()
        long_bars.derive("top", "corner_feet_top + long_bars_height")
    else:
        long_bars.derive("top", "long_bars_height")
    long_bars.done()
    ends = d.step("end_bars", "bars", "sides")
    ends.derive("height", "long_bars_height")
    ends.derive("thickness", "long_bars_height")
    ends.derive("top", "long_bars_top")
    ends.derive("length_y", "long_bars_spread_y - 2 * long_bars_thickness")
    ends.derive("spread_x", "long_bars_length_x")
    ends.derive("y", "0")
    ends.done()
    if cross:
        bars = d.step("cross_bars", "bars", "cross_row")
        bars.stated("count")
        bars.derive("width", "long_bars_height")
        bars.derive("length_y", "end_bars_length_y")
        bars.derive("height", "long_bars_height")
        bars.derive("top", "long_bars_top")
        bars.derive("pitch", "(long_bars_length_x - long_bars_height) / (cross_bars_count + 1)")
        bars.done()
    return ("long_bars_length_x", "long_bars_spread_y", "long_bars_top")


def _upright(d: Draft, cross: bool, mid_rail: bool) -> tuple[str, str, str]:
    feet = d.step("feet", "bars", "sides")
    width, height = feet.stated("spread_x"), d.given["uprights_top"]
    feet.default("height", rules.bar_size, span=max(width, height))
    feet.derive("thickness", "feet_height")
    feet.derive("top", "feet_height")
    feet.default("length_y", rules.foot_length, height=height)
    feet.derive("y", "0")
    feet.done()
    uprights = d.step("uprights", "bars", "left_right")
    uprights.derive("thickness", "feet_height")
    uprights.derive("bottom", "feet_height")                   # they stand on the feet
    uprights.stated("top")
    uprights.derive("spread_x", "feet_spread_x")
    uprights.done()
    for name, top in (("top_bar", "uprights_top"), ("bottom_bar", "2 * feet_height")):
        bar = d.step(name, "bars", "across")
        bar.derive("width", "feet_spread_x - 2 * uprights_thickness")
        bar.derive("height", "feet_height")
        bar.derive("thickness", "feet_height")
        bar.derive("y", "0")
        bar.derive("top", top)
        bar.done()
    if mid_rail:       # one more bar between the uprights, half-way up
        d.require(not cross, "upright cross bars would run through the mid rail")
        mid = d.step("mid_bar", "bars", "across")
        mid.derive("width", "top_bar_width")
        mid.derive("height", "feet_height")
        mid.derive("thickness", "feet_height")
        mid.derive("y", "0")
        mid.default("top", rules.mid_rail_top, height=height)
        mid.done()
        d.require(d.values["mid_bar_top"] - d.values["mid_bar_height"]
                  - d.values["bottom_bar_top"] >= 100
                  and d.values["top_bar_top"] - d.values["top_bar_height"]
                  - d.values["mid_bar_top"] >= 100, "mid rail too close to another bar")
    if cross:
        bars = d.step("cross_bars", "bars", "upright_row")
        bars.stated("count")
        bars.derive("width", "feet_height")
        bars.derive("thickness", "feet_height")
        bars.derive("bottom", "bottom_bar_top")
        bars.derive("top", "top_bar_top - top_bar_height")
        bars.derive("pitch", "(top_bar_width + cross_bars_width) / (cross_bars_count + 1)")
        bars.derive("y", "0")
        bars.done()
    return ("feet_spread_x", "feet_length_y", "uprights_top")


def _box(d: Draft, cross: bool, mid_rail: bool) -> tuple[str, str, str]:
    uprights = d.step("uprights", "bars", "corners")
    height = d.given["uprights_top"]
    width, depth = d.given["uprights_spread_x"], d.given["uprights_spread_y"]
    uprights.default("thickness", rules.bar_size, span=max(width, depth, height))
    uprights.derive("bottom", "0")
    uprights.stated("top")
    uprights.stated("spread_x")
    uprights.stated("spread_y")
    uprights.done()
    rings = [("top_bars", "uprights_top"), ("bottom_bars", "uprights_thickness")]
    if mid_rail:       # a third rectangle of bars half-way up
        rings.append(("mid_bars", None))
    for name, top in rings:
        ring = d.step(name, "bars", "ring")
        ring.derive("height", "uprights_thickness")
        ring.derive("thickness", "uprights_thickness")
        if top:
            ring.derive("top", top)
        else:
            ring.default("top", rules.mid_rail_top, height=height)
        ring.derive("length_x", "uprights_spread_x - 2 * uprights_thickness")
        ring.derive("length_y", "uprights_spread_y - 2 * uprights_thickness")
        ring.derive("spread_x", "uprights_spread_x")
        ring.derive("spread_y", "uprights_spread_y")
        ring.done()
    if mid_rail:
        bar = d.values["uprights_thickness"]
        d.require(d.values["mid_bars_top"] - 2 * bar >= 100
                  and height - bar - d.values["mid_bars_top"] >= 100,
                  "mid rail too close to another bar")
    if cross:      # across the top, between the front and back top bars
        bars = d.step("cross_bars", "bars", "cross_row")
        bars.stated("count")
        bars.derive("width", "uprights_thickness")
        bars.derive("length_y", "top_bars_length_y")
        bars.derive("height", "uprights_thickness")
        bars.derive("top", "uprights_top")
        bars.derive("pitch",
                    "(uprights_spread_x - uprights_thickness) / (cross_bars_count + 1)")
        bars.done()
    return ("uprights_spread_x", "uprights_spread_y", "uprights_top")


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    cross = "cross_bars_count" in given
    if c["layout"] == "flat":
        overall = _flat(d, cross, feet=c["feet"] == "yes")
    else:
        build_it = _upright if c["layout"] == "upright" else _box
        overall = build_it(d, cross, mid_rail=c["mid_rail"] == "yes")
    if cross:
        d.require(d.values["cross_bars_count"] >= 1, "a cross bar step needs at least one bar")
        d.require(d.values["cross_bars_pitch"] - d.values["cross_bars_width"] >= MIN_GAP,
                  "cross bars too close together")
    for bar, least in (("long_bars_height", "long_bars_spread_y"), ("feet_height", "feet_spread_x"),
                       ("uprights_thickness", "uprights_spread_x"),
                       ("uprights_thickness", "uprights_spread_y")):
        if bar in d.values and least in d.values:
            d.require(d.values[least] >= 6 * d.values[bar], "bars too thick for the frame")
    return d.finish(overall)


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    return ["frame", "frame", "rectangular frame", "bar frame", "open frame", "support frame",
            "frame of square bars"]


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    if c["layout"] == "flat":
        w, d = v["long_bars_length_x"], v["long_bars_spread_y"]
        return [s.one(f"{s.size(w, d)}", f"{s.size(w, d)}", f"{s.mm(w)} long and {s.mm(d)} wide",
                      f"length {s.mm(w)}, width {s.mm(d)}", f"{s.size(w, d)} overall")]
    if c["layout"] == "upright":
        w, h = v["feet_spread_x"], v["uprights_top"]
        return [s.one(f"{s.mm(w)} wide", f"width {s.mm(w)}"),
                s.one(f"{s.mm(h)} high", f"height {s.mm(h)}", f"{s.mm(h)} tall")]
    w, d, h = v["uprights_spread_x"], v["uprights_spread_y"], v["uprights_top"]
    return [s.one(f"{s.mm(w)} wide", f"width {s.mm(w)}"),
            s.one(f"{s.mm(d)} deep", f"depth {s.mm(d)}"),
            s.one(f"{s.mm(h)} high", f"height {s.mm(h)}", f"{s.mm(h)} tall")]


OPTION_WORDS = {
    ("layout", "flat"): ["lying flat", "flat on the floor", "laid flat"],
    ("layout", "upright"): ["standing upright", "upright on a pair of feet", "standing up on feet",
                            "+feet so it stands upright"],
    ("layout", "box"): ["as a box frame", "box-shaped", "box frame",
                        "+a post at each corner and bars round the top and bottom"],
    ("feet", "yes"): ["+a short foot under each corner", "raised on corner feet",
                      "+feet under the corners", "standing on short feet"],
    ("feet", "no"): ["no feet", "without feet"],
    ("mid_rail", "yes"): ["+a mid rail", "+a rail half-way up", "+a middle rail",
                          "+an extra rail at mid height"],
    ("mid_rail", "no"): ["no mid rail", "without a mid rail"],
}
COUNT_THINGS = {"cross_bars_count": [("cross bar", "cross bars"), ("cross member", "cross members"),
                                     ("crossbar", "crossbars"),
                                     ("intermediate bar", "intermediate bars")]}
_BAR = ["bars {v} square", "bar section {v}", "+{v} square bars", "made of {v} square bar"]
OVERRIDE_WORDS = {
    "long_bars_height": _BAR, "feet_height": _BAR, "uprights_thickness": _BAR,
    "feet_length_y": ["feet {v} long", "+{v} long feet", "foot length {v}"],
    "corner_feet_top": ["feet {v} high", "+{v} high feet", "raised {v} off the floor"],
    "mid_bar_top": ["mid rail top at {v}", "top of the mid rail {v} above the floor"],
    "mid_bars_top": ["mid rail top at {v}", "top of the mid rail {v} above the floor"],
}
