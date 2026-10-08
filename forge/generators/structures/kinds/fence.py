"""Fence panel: two posts, two or three rails between them, and pickets on the rails.

The rails are let in between the posts, flush with their back faces; the pickets
stand in front of the rails. A cap rail lies on top of the posts. The posts end
at the ground: the part of a post that would be buried is not built.

A fence panel is tall and thin by nature (it is fixed in the ground), so the
"tall narrow units are rare" rule does not apply to it. All size ranges are our
own design choices, not standard dimensions.
"""

from __future__ import annotations

import random

from forge.generators.prompts import FeatureStyle
from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft, Structure
from forge.generators.structures.kinds import _shared as shared

NAME = "fence"
TALL_BY_NATURE = True

OPTIONS = {
    "infill": ("pickets", "none"),       # none: a post-and-rail fence
    "cap": ("no", "yes"),                # a rail lying on top of the posts
}
OPTION_COUNTS = {("infill", "pickets"): "pickets_count"}
OVERRIDES = {
    "posts_thickness": 10, "rails_height": 10, "rails_bottom": 10, "pickets_width": 10,
    "pickets_thickness": 5, "cap_height": 5,
}
MIN_GAP = 10          # clear space between two pickets
MIN_RAIL_GAP = 150    # clear space between two rails


def choices(rng: random.Random, plain: bool = False) -> dict[str, str]:
    if plain:
        return {"infill": "pickets", "cap": "no"}
    return {"infill": rng.choice(["pickets", "pickets", "pickets", "none"]),
            "cap": rng.choice(["no", "no", "yes"])}


def headline(rng: random.Random, c: dict) -> dict:
    return {"posts_spread_x": shared.pick(rng, 1200, 2400, 100),
            "posts_top": shared.pick(rng, 900, 1800, 50)}


def counts(rng: random.Random, c: dict, given: dict) -> dict:
    found = {"rails_count": rng.randint(2, 3 if given["posts_top"] < 1300 else 4)}
    if c["infill"] == "pickets":
        clear = given["posts_spread_x"] - 200
        found["pickets_count"] = rng.randint(int(clear // 220), int(clear // 110))
    return found


def build(c: dict, given: dict) -> Structure:
    d = Draft(NAME, c, given)
    height = given["posts_top"]
    posts = d.step("posts", "posts", "left_right")
    post = posts.default("thickness", rules.fence_post_thickness, height=height)
    posts.derive("bottom", "0")
    posts.stated("top")
    posts.stated("spread_x")
    posts.done()

    rails = d.step("rails", "rail", "stacked_across")
    rails.default("count", rules.fence_rail_count, height=height)
    rails.derive("width", "posts_spread_x - 2 * posts_thickness")
    rails.default("height", rules.lower_rail_height, post=post)
    rails.default("thickness", rules.rail_thickness, leg=post)
    rails.default("bottom", rules.fence_rail_bottom, height=height)
    # Evenly spaced; the highest rail ends as far below the top as the lowest starts above ground.
    rails.derive("pitch", "(posts_top - 2 * rails_bottom - rails_height) / (rails_count - 1)")
    rails.derive("y", "(posts_thickness - rails_thickness) / 2")     # flush with the back
    rails.done()
    d.require(d.values["rails_count"] >= 2, "a fence panel needs two rails")
    d.require(d.values["rails_pitch"] - d.values["rails_height"] >= MIN_RAIL_GAP,
              "rails too close together")
    d.require(d.values["rails_thickness"] <= post, "rails thicker than the posts")

    if c["infill"] == "pickets":       # in front of the rails, with equal gaps
        pickets = d.step("pickets", "slats", "upright_row")
        pickets.default("count", rules.picket_count, clear_width=d.values["rails_width"])
        pickets.default("width", rules.picket_width, height=height)
        thick = pickets.default("thickness", rules.picket_thickness, post=post)
        pickets.default("bottom", rules.picket_gap)
        pickets.derive("top", "posts_top - pickets_bottom")
        pickets.derive("pitch", "(rails_width + pickets_width) / (pickets_count + 1)")
        pickets.derive("y", "rails_y - (rails_thickness + pickets_thickness) / 2")
        pickets.done()
        d.require(d.values["pickets_count"] >= 1, "no pickets")
        d.require(d.values["pickets_pitch"] - d.values["pickets_width"] >= MIN_GAP,
                  "pickets too close together")
        d.require(d.values["rails_thickness"] + thick <= post,
                  "rails and pickets together thicker than the posts")
        d.require(d.values["pickets_bottom"] <= d.values["rails_bottom"],
                  "pickets start above the lowest rail")
    if c["cap"] == "yes":
        cap = d.step("cap", "rail", "across")
        cap.derive("width", "posts_spread_x")
        cap.default("height", rules.cap_rail_height, post=post)
        cap.derive("thickness", "posts_thickness")
        cap.derive("y", "0")
        cap.derive("top", "posts_top + cap_height")
        cap.done()
        return d.finish(("posts_spread_x", "posts_thickness", "cap_top"))
    return d.finish(("posts_spread_x", "posts_thickness", "posts_top"))


# --- wording (template) -----------------------------------------------------------

def names(c: dict, v: dict) -> list[str]:
    found = ["fence panel", "fence panel", "fence section", "garden fence panel",
             "section of fence"]
    found += ["picket fence panel", "picket fence section"] if c["infill"] == "pickets" \
        else ["post and rail fence", "post and rail fence section"]
    return found


def say_headline(v: dict, c: dict, s: FeatureStyle) -> list[str]:
    w, h = v["posts_spread_x"], v["posts_top"]
    return [s.one(f"{s.mm(w)} wide", f"{s.mm(w)} long", f"width {s.mm(w)}",
                  f"{s.mm(w)} across the posts"),
            s.one(f"posts {s.mm(h)} high", f"{s.mm(h)} to the top of the posts",
                  f"post height {s.mm(h)}", f"posts {s.mm(h)} above the ground")]


OPTION_WORDS = {
    ("infill", "pickets"): ["+pickets", "+upright pickets on the rails", "+a picket infill"],
    ("infill", "none"): ["no pickets", "without pickets", "+rails only between the posts"],
    ("cap", "yes"): ["+a cap rail", "+a cap rail on top of the posts", "+a capping rail"],
    ("cap", "no"): ["no cap rail", "without a cap rail"],
}
COUNT_THINGS = {"rails_count": [("rail", "rails"), ("horizontal rail", "horizontal rails"),
                                ("rail between the posts", "rails between the posts")],
                "pickets_count": [("picket", "pickets"), ("picket", "pickets"),
                                  ("upright picket", "upright pickets")]}
OVERRIDE_WORDS = {
    "posts_thickness": ["posts {v} square", "+{v} square posts", "post section {v}"],
    "rails_height": ["rails {v} high", "+{v} high rails"],
    "rails_bottom": ["lowest rail {v} above the ground", "rails starting {v} off the ground"],
    "pickets_width": ["pickets {v} wide", "+{v} wide pickets", "picket width {v}"],
    "pickets_thickness": ["pickets {v} thick", "+{v} thick pickets"],
    "cap_height": ["cap rail {v} thick", "+a {v} thick cap rail"],
}
