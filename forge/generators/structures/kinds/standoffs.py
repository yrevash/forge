"""Plate on standoffs: a base plate, standoffs standing on it, a top plate lying on them.

A small mechanical assembly, the first kind that is not woodwork. The plates are
rectangular with a standoff near each corner, or round with three or more
standoffs on a circle. With three tiers a middle plate sits half-way up, on its
own set of standoffs. Plates have no holes and standoffs are plain round or
square bars: the parts only stand on each other (README, known limits).

All sizes here are our own design choices. They are NOT taken from a standoff
or spacer standard or a catalogue; the repo's tables hold none.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "standoffs"

OPTIONS = {
    "plates": ("rectangular", "round"),
    "standoffs": ("round", "square"),
    "tiers": ("two", "three"),           # plates: a base and a top, or a middle one as well
}
OPTION_COUNTS: dict = {}
OVERRIDES = {
    "base_thickness": 1, "standoffs_diameter": 1, "standoffs_thickness": 1,
}
MIN_STANDOFF = 5      # a standoff shorter than this is a washer


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"plates": "rectangular", "standoffs": "round", "tiers": "two"}
    return {"plates": rng.choice(["rectangular", "rectangular", "round"]),
            "standoffs": rng.choice(["round", "round", "square"]),
            "tiers": rng.choice(["two", "two", "three"])}


def headline(rng: random.Random, c: dict) -> dict:
    tall = c["tiers"] == "three"
    given = {"top_top": shared.pick(rng, 50 if tall else 25, 160 if tall else 100, 5)}
    if c["plates"] == "round":
        given["base_diameter"] = shared.pick(rng, 60, 250, 10)
    else:
        given["base_width"] = shared.pick(rng, 60, 300, 10)
        given["base_depth"] = min(given["base_width"], shared.pick(rng, 40, 200, 10))
    return given


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    return {"standoffs_count": rng.randint(3, 6)} if c["plates"] == "round" else {}


def _plate(d: Draft, name: str, role: str):
    """Open a plate the same size as the base plate. The caller gives it its `top`.

    `light`: these are metal plates, not wooden boards, so the minimum board
    thickness for weight-carrying boards does not apply to them.
    """
    round_plates = "base_diameter" in d.values
    plate = d.step(name, role, "level", "cylinder" if round_plates else "box", light=True)
    if round_plates:
        plate.derive("diameter", "base_diameter")
    else:
        plate.derive("width", "base_width")
        plate.derive("depth", "base_depth")
    plate.derive("thickness", "base_thickness")
    return plate


def _standoffs(d: Draft, c: dict, name: str, bottom: str, top: str, copy: bool) -> None:
    """Standoffs from one plate's upper face to the next plate's underside."""
    round_plates, across = c["plates"] == "round", \
        "diameter" if c["standoffs"] == "round" else "thickness"
    posts = d.step(name, "posts", "circle" if round_plates else "corners",
                   "cylinder" if c["standoffs"] == "round" else "box")
    first = f"standoffs_{across}"
    if copy:                         # the upper set copies the lower set's numbers
        posts.derive(across, first)
    else:
        span = d.values["base_diameter"] if round_plates else \
            max(d.values["base_width"], d.values["base_depth"])
        posts.default(across, rules.standoff_diameter, span=span)
    size = d.values[first]
    posts.derive("bottom", bottom)
    posts.derive("top", top)
    if round_plates:
        if copy:
            posts.derive("count", "standoffs_count")
            posts.derive("circle", "standoffs_circle")
        else:
            posts.default("count", rules.standoff_count, diameter=d.values["base_diameter"])
            posts.default("circle", rules.standoff_circle, diameter=d.values["base_diameter"],
                          standoff=size)
    elif copy:
        posts.derive("spread_x", "standoffs_spread_x")
        posts.derive("spread_y", "standoffs_spread_y")
    else:
        posts.default("spread_x", rules.standoff_spread, side=d.values["base_width"],
                      standoff=size)
        posts.default("spread_y", rules.standoff_spread, side=d.values["base_depth"],
                      standoff=size)
    posts.done()
    d.require(d.values[f"{name}_top"] - d.values[f"{name}_bottom"] >= MIN_STANDOFF,
              "standoffs too short")
    if round_plates:
        # Neighbours on the circle must not touch, and none may hang over the plate's edge.
        d.require(d.values[f"{name}_count"] >= 3, "a round plate needs three standoffs")
        d.require(d.values[f"{name}_circle"] >= 3 * size, "standoffs too thick for the plate")
        d.require(d.values[f"{name}_circle"] + 1.5 * size <= d.values["base_diameter"],
                  "standoffs hang over the edge of the plate")
    else:
        d.require(min(d.values[f"{name}_spread_x"], d.values[f"{name}_spread_y"]) >= 3 * size,
                  "standoffs too thick for the plate")


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    round_plates = c["plates"] == "round"
    base = d.step("base", "bottom", "level", "cylinder" if round_plates else "box")
    if round_plates:
        span = base.stated("diameter")
    else:
        span = max(base.stated("width"), base.stated("depth"))
    base.default("thickness", rules.plate_thickness, span=span)
    base.derive("top", "base_thickness")
    base.done()
    # The top plate is built last, but the standoffs stop under it, so it is sized first.
    top = _plate(d, "top", "top")
    top.stated("top")
    if c["tiers"] == "two":
        _standoffs(d, c, "standoffs", "base_top", "top_top - base_thickness", copy=False)
    else:
        # The middle plate sits so that both sets of standoffs are the same length.
        middle = "(top_top + base_thickness) / 2"
        _standoffs(d, c, "standoffs", "base_top", f"{middle} - base_thickness", copy=False)
        plate = _plate(d, "middle", "shelves")
        plate.derive("top", middle)
        plate.done()
        _standoffs(d, c, "upper_standoffs", "middle_top", "top_top - base_thickness", copy=True)
    top.done()
    across = "base_diameter" if round_plates else "base_width"
    return d.finish((across, "base_diameter" if round_plates else "base_depth", "top_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    found = ["plate on standoffs", "plate on standoffs", "standoff assembly",
             "pair of plates on standoffs", "mounting plate on standoffs", "spaced plate stack"]
    if c["tiers"] == "three":
        found = ["plate stack on standoffs", "stack of plates on standoffs", "standoff assembly",
                 "spaced plate stack"]
    return found


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    h = v["top_top"]
    high = s.one(f"{s.mm(h)} high overall", f"overall height {s.mm(h)}", f"{s.mm(h)} tall overall",
                 f"{s.mm(h)} from the underside of the base to the top face")
    if c["plates"] == "round":
        dia = v["base_diameter"]
        return [s.one(f"plates {s.mm(dia)} in diameter", f"plates {s.dia(dia)}",
                      f"{s.mm(dia)} diameter plates", f"plate diameter {s.mm(dia)}"), high]
    w, d = v["base_width"], v["base_depth"]
    return [s.one(f"plates {s.size(w, d)}", f"{s.size(w, d)} plates",
                  f"plates {s.mm(w)} long and {s.mm(d)} wide", f"plate size {s.size(w, d)}"), high]


OPTION_WORDS = {
    ("plates", "round"): ["+round plates", "+circular plates", "+disc-shaped plates"],
    ("plates", "rectangular"): ["+rectangular plates", "+a standoff near each corner"],
    ("standoffs", "square"): ["+square standoffs", "+square-section standoffs",
                              "+square bar spacers"],
    ("standoffs", "round"): ["+round standoffs", "+cylindrical standoffs", "+round spacers"],
    ("tiers", "three"): ["+a middle plate", "+a middle plate half-way up",
                         "+an extra plate between the base and the top"],
    ("tiers", "two"): ["no middle plate", "+only a base plate and a top plate"],
}
COUNT_THINGS = {"standoffs_count": [("standoff", "standoffs"),
                                    ("standoff per level", "standoffs per level"),
                                    ("spacer between the plates", "spacers between the plates")]}
OVERRIDE_WORDS = {
    "base_thickness": ["plates {v} thick", "plate thickness {v}", "+{v} thick plates"],
    "standoffs_diameter": ["standoffs {v} in diameter", "standoff diameter {v}",
                           "+{v} diameter standoffs"],
    "standoffs_thickness": ["standoffs {v} square", "+{v} square standoffs"],
}
