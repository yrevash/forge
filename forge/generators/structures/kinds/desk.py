"""Desk: a top on panel ends or legs, with a modesty panel and zero, one or two shelf pedestals.

A pedestal here is open: the desk's end panel, an inner panel, and shelves
between them. All size ranges are our own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "desk"

OPTIONS = {
    "pedestals": ("none", "left", "right", "both"),
    "supports": ("panels", "legs"),      # only without pedestals; a pedestal needs panel ends
    "modesty": ("yes", "no"),            # not with a single pedestal
}
OPTION_COUNTS: dict = {}
OVERRIDES = {
    "top_thickness": 5, "ends_thickness": 5, "legs_thickness": 5, "aprons_height": 10,
    "modesty_panel_height": 10, "pedestal_shelves_bottom": 10,
}


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"pedestals": "none", "supports": "panels", "modesty": "yes"}
    c = {"pedestals": rng.choice(["none", "none", "left", "right", "both"])}
    if c["pedestals"] == "none":
        c["supports"] = rng.choice(["panels", "legs"])
    if c["pedestals"] in ("none", "both"):
        c["modesty"] = rng.choice(["yes", "no"])
    return c


def headline(rng: random.Random, c: dict) -> dict:
    # A pedestal at each end needs a desk wide enough to leave room for knees.
    return {"top_width": shared.pick(rng, 1300 if c["pedestals"] == "both" else 1000, 1800, 50),
            "top_depth": shared.pick(rng, 500, 800, 50),
            "top_top": shared.pick(rng, 720, 760, 10)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    return {"pedestal_shelves_count": rng.randint(2, 4)} if c["pedestals"] != "none" else {}


def _modesty(d: Draft, width: str, y: str, top: str, span: float, height: float) -> None:
    panel = d.step("modesty_panel", "back_panel", "across")
    panel.derive("width", width)
    panel.default("height", rules.modesty_height, height=height)
    panel.default("thickness", rules.board_thickness, span=span)
    panel.derive("y", y)
    panel.derive("top", top)
    panel.done()


def _shelves(d: Draft, name: str, width: str, x: str, span: float, copy_of: str | None) -> None:
    """Shelves of one pedestal. The second pedestal copies the first one's numbers."""
    shelves = d.step(name, "shelves", "stacked")
    if copy_of:
        shelves.derive("count", f"{copy_of}_count")
        shelves.derive("thickness", f"{copy_of}_thickness")
        shelves.derive("bottom", f"{copy_of}_bottom")
    else:
        shelves.default("count", rules.pedestal_shelf_count)
        shelves.default("thickness", rules.board_thickness, span=span)
        shelves.default("bottom", rules.pedestal_floor_gap)
    shelves.derive("width", width)
    shelves.derive("depth", "top_depth")
    # The space is split evenly; the top compartment is left open under the desk top.
    shelves.derive("pitch", f"(ends_height - {name}_bottom) / {name}_count")
    shelves.derive("x", x)
    shelves.derive("y", "0")
    shelves.done()
    d.require(d.values[f"{name}_pitch"] - d.values[f"{name}_thickness"] >= 100,
              "pedestal shelves too close together")
    d.require(d.values[f"{name}_width"] >= 200, "pedestal too narrow")


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    top = d.step("top", "top", "level")
    width, depth = top.stated("width"), top.stated("depth")
    top.default("thickness", rules.top_thickness, span=max(width, depth))
    height = top.stated("top")
    top.done()
    modesty = c.get("modesty") == "yes"

    if c["pedestals"] == "none" and c["supports"] == "legs":
        shared.corner_legs(d, "top", round_legs=False)
        shared.aprons(d)
        if modesty:      # hangs under the back apron, between the back legs
            _modesty(d, "legs_spread_x - 2 * legs_thickness",
                     "(legs_spread_y - legs_thickness) / 2", "aprons_top - aprons_height",
                     width, height)
            d.require(d.values["modesty_panel_thickness"] <= d.values["legs_thickness"],
                      "modesty panel thicker than the legs")
        return d.finish(("top_width", "top_depth", "top_top"))

    shared.panel_ends(d, "top")
    thickness = d.values["ends_thickness"]
    back_edge = "(top_depth - modesty_panel_thickness) / 2"
    if c["pedestals"] == "none":
        if modesty:
            _modesty(d, "ends_spread_x - 2 * ends_thickness", back_edge, "ends_top", width, height)
    elif c["pedestals"] == "both":
        inner = d.step("inner_panels", "panels", "sides")
        inner.derive("height", "ends_height")
        inner.derive("thickness", "ends_thickness")
        inner.derive("top", "ends_top")
        inner.derive("length_y", "top_depth")
        inner.default("spread_x", rules.inner_panels_spread, desk_width=width, thickness=thickness)
        inner.derive("y", "0")
        inner.done()
        bay = "(ends_spread_x - inner_panels_spread_x) / 2 - ends_thickness"
        centre = "(ends_spread_x + inner_panels_spread_x) / 4 - ends_thickness / 2"
        _shelves(d, "pedestal_shelves", bay, f"-({centre})", width, None)
        _shelves(d, "second_pedestal_shelves", bay, centre, width, "pedestal_shelves")
        if modesty:
            _modesty(d, "inner_panels_spread_x - 2 * inner_panels_thickness", back_edge,
                     "ends_top", width, height)
    else:
        side = -1 if c["pedestals"] == "left" else 1
        inner = d.step("inner_panel", "panels", "side")
        inner.derive("thickness", "ends_thickness")
        inner.derive("length_y", "top_depth")
        inner.derive("height", "ends_height")
        inner.derive("top", "ends_top")
        inner.default("x", rules.inner_panel_x, desk_width=width, thickness=thickness, side=side)
        inner.done()
        # From the end panel's inner face to the inner panel's near face.
        if side < 0:
            bay = "inner_panel_x - inner_panel_thickness / 2 + ends_spread_x / 2 - ends_thickness"
            centre = ("(inner_panel_x - inner_panel_thickness / 2 - ends_spread_x / 2 "
                      "+ ends_thickness) / 2")
        else:
            bay = "ends_spread_x / 2 - ends_thickness - inner_panel_x - inner_panel_thickness / 2"
            centre = ("(inner_panel_x + inner_panel_thickness / 2 + ends_spread_x / 2 "
                      "- ends_thickness) / 2")
        _shelves(d, "pedestal_shelves", bay, centre, width, None)
    return d.finish(("top_width", "top_depth", "top_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    return ["desk", "desk", "writing desk", "office desk", "computer desk", "study desk",
            "work desk"]


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, d, h = v["top_width"], v["top_depth"], v["top_top"]
    return [s.one(f"{s.size(w, d)}", f"top {s.size(w, d)}", f"{s.mm(w)} wide and {s.mm(d)} deep",
                  f"{s.mm(w)} wide, {s.mm(d)} deep", f"width {s.mm(w)}, depth {s.mm(d)}"),
            s.one(f"{s.mm(h)} high", f"{s.mm(h)} high", f"height {s.mm(h)}", f"{s.mm(h)} tall")]


OPTION_WORDS = {
    ("pedestals", "left"): ["+a pedestal on the left", "+a left-hand pedestal",
                            "+a shelf pedestal at the left end"],
    ("pedestals", "right"): ["+a pedestal on the right", "+a right-hand pedestal",
                             "+a shelf pedestal at the right end"],
    ("pedestals", "both"): ["+a pedestal on each side", "+a pedestal at both ends",
                            "double pedestal", "+twin pedestals"],
    ("pedestals", "none"): ["no pedestal", "without pedestals"],
    ("supports", "legs"): ["on legs", "+legs", "+a leg at each corner", "+legs and aprons"],
    ("supports", "panels"): ["+panel ends", "on panel ends", "+slab ends"],
    ("modesty", "yes"): ["+a modesty panel", "+a modesty panel at the back"],
    ("modesty", "no"): ["no modesty panel", "without a modesty panel", "open at the back"],
}
COUNT_THINGS = {"pedestal_shelves_count": [("pedestal shelf", "pedestal shelves"),
                                           ("shelf per pedestal", "shelves per pedestal"),
                                           ("shelf in the pedestal", "shelves in the pedestal")]}
OVERRIDE_WORDS = {
    "top_thickness": ["top {v} thick", "top thickness {v}", "+{v} thick top"],
    "ends_thickness": ["end panels {v} thick", "+{v} thick end panels", "panel thickness {v}"],
    "legs_thickness": ["legs {v} square", "leg thickness {v}", "+{v} square legs"],
    "aprons_height": ["aprons {v} high", "apron height {v}", "+{v} high aprons"],
    "modesty_panel_height": ["modesty panel {v} high", "modesty panel height {v}"],
    "pedestal_shelves_bottom": ["lowest pedestal shelf {v} above the floor",
                                "pedestal shelves starting {v} off the floor"],
}
