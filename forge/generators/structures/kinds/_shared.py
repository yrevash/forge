"""Step groups that several kinds use: a leg frame, stretchers, panel ends, a back.

Each function opens steps on a draft, exactly as a kind file would. Keeping them
here means a chair's legs and a table's legs are the same steps with the same
slots and the same formulas, which is the point of a shared vocabulary.
"""

from __future__ import annotations

import random

from forge.generators.structures import defaults as rules
from forge.generators.structures.draft import Draft


def pick(rng: random.Random, low: float, high: float, step: float) -> float:
    """A random length on a grid, both ends included: pick(rng, 380, 500, 10)."""
    return float(low + step * rng.randint(0, int((high - low) / step + 1e-9)))


def leg_size(d: Draft) -> str:
    """The parameter that holds how thick the legs are: a side for square legs, a diameter for round."""
    return "legs_diameter" if "legs_diameter" in d.values else "legs_thickness"


def corner_legs(d: Draft, top: str, round_legs: bool) -> None:
    """Four legs under the corners of a rectangular top, flush with its edges."""
    legs = d.step("legs", "legs", "corners", "cylinder" if round_legs else "box")
    legs.default("diameter" if round_legs else "thickness", rules.leg_thickness,
                 width=d.values[f"{top}_width"], depth=d.values[f"{top}_depth"])
    legs.derive("bottom", "0")
    legs.derive("top", f"{top}_top - {top}_thickness")
    legs.derive("spread_x", f"{top}_width")
    legs.derive("spread_y", f"{top}_depth")
    legs.done()


def legs_under_round_top(d: Draft, top: str, count: int, round_legs: bool) -> None:
    """Three legs on a circle, or four in a square, tucked under a round top."""
    diameter = d.values[f"{top}_diameter"]
    size = "diameter" if round_legs else "thickness"
    shape = "cylinder" if round_legs else "box"
    legs = d.step("legs", "legs", "circle" if count == 3 else "corners", shape)
    if count == 3:
        legs.stated("count")      # "3 legs" is always said: it is how the prompt asks for them
    leg = legs.default(size, rules.leg_thickness, width=diameter, depth=diameter)
    legs.derive("bottom", "0")
    legs.derive("top", f"{top}_top - {top}_thickness")
    if count == 3:
        legs.default("circle", rules.leg_circle, diameter=diameter, leg=leg)
    else:
        legs.default("spread_x", rules.round_leg_spread, diameter=diameter)
        legs.derive("spread_y", "legs_spread_x")
    legs.done()


def aprons(d: Draft) -> None:
    """A rail between each pair of legs, directly under the top, on the legs' centre lines."""
    leg = leg_size(d)
    step = d.step("aprons", "aprons", "ring")
    step.default("height", rules.apron_height, leg=d.values[leg])
    step.default("thickness", rules.rail_thickness, leg=d.values[leg])
    step.derive("top", "legs_top")
    _between_legs(step, "aprons", leg, ring=True)
    step.done()
    d.require(d.values["aprons_thickness"] <= d.values[leg], "aprons thicker than the legs")


def _between_legs(step, name: str, leg: str, ring: bool) -> None:
    """Lengths and spreads of rails that run between the legs, centred on the legs' lines."""
    if ring:
        step.derive("length_x", f"legs_spread_x - 2 * {leg}")
        step.derive("spread_y", f"legs_spread_y - {leg} + {name}_thickness")
    step.derive("length_y", f"legs_spread_y - 2 * {leg}")
    step.derive("spread_x", f"legs_spread_x - {leg} + {name}_thickness")


def stretchers(d: Draft, style: str) -> None:
    """Lower rails between the legs: `ring` is all four sides, `h` is two sides and a cross rail."""
    leg = leg_size(d)
    step = d.step("stretchers", "stretchers", "ring" if style == "ring" else "sides")
    step.default("height", rules.stretcher_height, leg=d.values[leg])
    step.default("thickness", rules.rail_thickness, leg=d.values[leg])
    step.default("top", rules.stretcher_top, leg_length=d.values["legs_top"])
    _between_legs(step, "stretchers", leg, ring=style == "ring")
    if style != "ring":
        step.derive("y", "0")
    step.done()
    d.require(d.values["stretchers_thickness"] <= d.values[leg], "stretchers thicker than legs")
    d.require(d.values["stretchers_top"] - d.values["stretchers_height"] >= 30,
              "stretchers too close to the floor")
    under = d.values.get("aprons_top", d.values["legs_top"]) - d.values.get("aprons_height", 0)
    d.require(under - d.values["stretchers_top"] >= 40, "stretchers too close to the top")
    if style == "h":
        cross = d.step("cross_stretcher", "stretchers", "across")
        cross.derive("width", "stretchers_spread_x - 2 * stretchers_thickness")
        cross.derive("height", "stretchers_height")
        cross.derive("thickness", "stretchers_thickness")
        cross.derive("y", "0")
        cross.derive("top", "stretchers_top")
        cross.done()


def panel_ends(d: Draft, top: str) -> None:
    """Two slab ends under the top in place of legs, flush with its edges."""
    ends = d.step("ends", "panels", "sides")
    ends.derive("height", f"{top}_top - {top}_thickness")
    ends.default("thickness", rules.top_thickness,
                 span=max(d.values[f"{top}_width"], d.values[f"{top}_depth"]))
    ends.derive("top", "ends_height")
    ends.derive("length_y", f"{top}_depth")
    ends.derive("spread_x", f"{top}_width")
    ends.derive("y", "0")
    ends.done()


def stretcher_between_ends(d: Draft) -> None:
    """One rail between the two panel ends, on the centre line."""
    rail = d.step("stretcher", "stretchers", "across")
    rail.derive("width", "ends_spread_x - 2 * ends_thickness")
    rail.default("height", rules.stretcher_height, leg=2 * d.values["ends_thickness"])
    rail.derive("thickness", "ends_thickness")
    rail.derive("y", "0")
    rail.default("top", rules.stretcher_top, leg_length=d.values["ends_height"])
    rail.done()
    d.require(d.values["stretcher_top"] - d.values["stretcher_height"] >= 30,
              "stretcher too close to the floor")


def back(d: Draft, seat: str, style: str, arms: bool) -> None:
    """A back standing on the seat: two posts, then rails, slats or a panel between them.

    style: `slats` (top rail, lower rail, upright slats between), `rails` (the
    two rails only) or `panel` (one solid board).
    """
    seat_top = d.values[f"{seat}_top"]
    posts = d.step("posts", "posts", "back_corners")
    post = posts.default("thickness", rules.leg_thickness,
                         width=d.values[f"{seat}_width"], depth=d.values[f"{seat}_depth"])
    posts.derive("bottom", f"{seat}_top")
    back_top = posts.stated("top")
    posts.derive("spread_x", f"{seat}_width")
    posts.derive("spread_y", f"{seat}_depth")
    posts.done()
    d.require(2 * post <= d.values[f"{seat}_depth"], "posts too thick for the seat")

    def between_posts(step, name: str) -> None:
        step.derive("width", f"{seat}_width - 2 * posts_thickness")
        step.default("thickness", rules.rail_thickness, leg=post)
        step.derive("y", f"({seat}_depth - posts_thickness) / 2")
        d.require(d.values[f"{name}_thickness"] <= post, f"{name} thicker than the posts")

    if style == "panel":
        panel = d.step("back_panel", "back_panel", "across")
        between_posts(panel, "back_panel")
        panel.default("height", rules.back_panel_height, seat_top=seat_top, back_top=back_top)
        panel.derive("top", "posts_top")
        panel.done()
        d.require(d.values["back_panel_height"] <= back_top - seat_top, "back panel too tall")
    else:
        top_rail = d.step("top_rail", "rail", "across")
        between_posts(top_rail, "top_rail")
        top_rail.default("height", rules.top_rail_height, post=post)
        top_rail.derive("top", "posts_top - posts_thickness / 2")   # half a post below the top
        top_rail.done()
        lower = d.step("lower_rail", "rail", "across")
        between_posts(lower, "lower_rail")
        low_height = lower.default("height", rules.lower_rail_height, post=post)
        lower.default("top", rules.lower_rail_top, seat_top=seat_top, back_top=back_top,
                      rail_height=low_height)
        lower.done()
        opening = (d.values["top_rail_top"] - d.values["top_rail_height"]
                   - d.values["lower_rail_top"])
        d.require(opening >= 100, "no room between the back rails")
        if style == "slats":
            slats = d.step("slats", "slats", "upright_row")
            slats.default("count", rules.slat_count, clear_width=d.values["top_rail_width"])
            slats.default("width", rules.slat_width, post=post)
            slats.default("thickness", rules.slat_thickness, rail=d.values["top_rail_thickness"])
            slats.derive("bottom", "lower_rail_top")
            slats.derive("top", "top_rail_top - top_rail_height")
            slats.derive("pitch", "(top_rail_width + slats_width) / (slats_count + 1)")
            slats.derive("y", "top_rail_y")
            slats.done()
            d.require(d.values["slats_count"] >= 1, "no slats")
            d.require(d.values["slats_pitch"] - d.values["slats_width"] >= 15,
                      "slats too close together")
            d.require(d.values["slats_thickness"] <= min(d.values["top_rail_thickness"],
                                                         d.values["lower_rail_thickness"]),
                      "slats thicker than the rails")
    if arms:
        arm_posts = d.step("arm_posts", "posts", "front_corners")
        arm_posts.default("thickness", rules.leg_thickness,
                          width=d.values[f"{seat}_width"], depth=d.values[f"{seat}_depth"])
        arm_posts.derive("bottom", f"{seat}_top")
        arm_posts.default("top", rules.arm_post_top, seat_top=seat_top, back_top=back_top)
        arm_posts.derive("spread_x", f"{seat}_width")
        arm_posts.derive("spread_y", f"{seat}_depth")
        arm_posts.done()
        arm = d.step("arms", "arms", "sides")
        arm.default("height", rules.arm_height, post=post)
        arm.default("thickness", rules.arm_width, post=post)
        arm.derive("top", "arm_posts_top + arms_height")
        arm.derive("length_y", f"{seat}_depth - posts_thickness")   # front edge to the back post
        arm.derive("spread_x", f"{seat}_width")
        arm.derive("y", "-posts_thickness / 2")
        arm.done()
        d.require(d.values["arms_top"] <= back_top, "arms above the back")
        d.require(d.values["arm_posts_top"] - seat_top >= 100, "arms too low")
