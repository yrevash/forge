"""The rule table for sizes written `default`, and for feature slots that are not written.

Status: OUR CHOICE. PLAN_LANGUAGE section 4 says a `default` size "is filled by a rule,
and reported", and does not give the rules. These are deliberately few and plain: a
missing size is taken from the other sizes on the same line, so the part keeps a
sensible proportion. They know nothing about objects (a "chair leg" has no rule here;
that is the planner's and the furniture generators' job).

Each rule is (what the reply says, how to work it out from the sizes already known).
The rules for one size are tried in order; the first one whose inputs are known wins.
When none can be worked out, the size is FALLBACK: the smaller side of the face the
placement touches, or 100 mm when the line touches no part.
"""

from __future__ import annotations

from collections.abc import Callable

FALLBACK_MM = 100.0

Rule = tuple[str, Callable[[dict], float]]


def _larger(sizes: dict, a: str, b: str) -> float:
    return max(sizes[a], sizes[b])


SIZE_RULES: dict[tuple[str, str], list[Rule]] = {
    # box and wedge: a square footprint; a slab one tenth as thick as it is long
    ("box", "length"): [("same as its depth", lambda s: s["depth"]),
                        ("same as its height", lambda s: s["height"])],
    ("box", "depth"): [("same as its length", lambda s: s["length"]),
                       ("same as its height", lambda s: s["height"])],
    ("box", "height"): [("a tenth of its longer side",
                         lambda s: _larger(s, "length", "depth") / 10)],
    # round shapes: as high as wide
    ("cylinder", "diameter"): [("same as its height", lambda s: s["height"])],
    ("cylinder", "height"): [("same as its diameter", lambda s: s["diameter"])],
    ("tube", "outer diameter"): [("1.25 times its inner diameter",
                                  lambda s: s["inner diameter"] * 1.25),
                                 ("same as its height", lambda s: s["height"])],
    ("tube", "inner diameter"): [("0.8 times its outer diameter",
                                  lambda s: s["outer diameter"] * 0.8)],
    ("tube", "height"): [("same as its outer diameter", lambda s: s["outer diameter"])],
    ("cone", "bottom diameter"): [("same as its height", lambda s: s["height"])],
    ("cone", "top diameter"): [("a point", lambda s: 0.0)],
    ("cone", "height"): [("same as its wider end",
                          lambda s: _larger(s, "bottom diameter", "top diameter"))],
    ("dome", "diameter"): [("twice its height (half a ball)", lambda s: s["height"] * 2)],
    ("dome", "height"): [("half its diameter (half a ball)", lambda s: s["diameter"] / 2)],
    ("prism", "across"): [("same as its height", lambda s: s["height"])],
    ("prism", "height"): [("same as its across size", lambda s: s["across"])],
    # tapered box: the top half the size of the bottom
    ("tapered box", "bottom length"): [("same as its bottom depth", lambda s: s["bottom depth"])],
    ("tapered box", "bottom depth"): [("same as its bottom length", lambda s: s["bottom length"])],
    ("tapered box", "top length"): [("half its bottom length", lambda s: s["bottom length"] / 2)],
    ("tapered box", "top depth"): [("half its bottom depth", lambda s: s["bottom depth"] / 2)],
    ("tapered box", "height"): [("same as its bottom length", lambda s: s["bottom length"])],
    # bars: thin walls, ten times as long as wide
    ("bar", "profile width"): [("same as its profile depth", lambda s: s["profile depth"])],
    ("bar", "profile depth"): [("same as its profile width", lambda s: s["profile width"])],
    ("bar", "thickness"): [("a tenth of its smaller profile side",
                            lambda s: min(s["profile width"], s["profile depth"]) / 10)],
    ("bar", "wall"): [("a tenth of its outer diameter", lambda s: s["outer diameter"] / 10)],
    ("bar", "length"): [("ten times its larger profile side",
                         lambda s: 10 * _larger(s, "profile width", "profile depth")),
                        ("ten times its outer diameter", lambda s: 10 * s["outer diameter"])],
}
SIZE_RULES.update({("wedge", slot): rules for (shape, slot), rules in list(SIZE_RULES.items())
                   if shape == "box"})


def fill_defaults(shape: str, sizes: dict[str, float | None], wanted: list[str],
                  fallback: float) -> list[str]:
    """Fill every slot in `wanted` in place. Returns one sentence per size, for the reply."""
    said = []
    waiting = list(wanted)
    while waiting:
        progress = False
        for slot in list(waiting):
            for words, rule in SIZE_RULES.get((shape, slot), []):
                try:
                    value = rule(sizes)
                except (KeyError, TypeError):       # an input is itself not known yet
                    continue
                if value is None:
                    continue
                sizes[slot] = float(value)
                said.append(f"default {slot} = {value:g} ({words})")
                waiting.remove(slot)
                progress = True
                break
        if not progress:
            # Nothing on the line gives a scale: fall back, then let the other rules use it.
            slot = waiting.pop(0)
            sizes[slot] = fallback
            said.append(f"default {slot} = {fallback:g} (nothing on the line sets it: the smaller "
                        "side of the face it touches, or 100)")
    return said


# --- feature slots that are not written (section 9: "filled by a rule") ----------------------
# small: the smaller side of the face; first, second: the face's two sides; under: how much
# material lies under the face.
FEATURE_RULES: dict[str, tuple[str, Callable[[dict, float, float, float, float], float]]] = {
    "diameter": ("a quarter of the face's smaller side", lambda s, small, a, b, under: small / 4),
    "hole diameter": ("a tenth of the face's smaller side",
                      lambda s, small, a, b, under: small / 10),
    "depth": ("half the material under the face", lambda s, small, a, b, under: under / 2),
    "height": ("a tenth of the face's smaller side", lambda s, small, a, b, under: small / 10),
    "length": ("half the face's first side", lambda s, small, a, b, under: a / 2),
    "width": ("half the face's second side", lambda s, small, a, b, under: b / 2),
    "angle": ("0 degrees", lambda s, small, a, b, under: 0.0),
    "count": ("4", lambda s, small, a, b, under: 4.0),
    "circle diameter": ("0.6 of the face's smaller side",
                        lambda s, small, a, b, under: small * 0.6),
    "spacing": ("twice the hole diameter", lambda s, small, a, b, under: 2 * s["hole diameter"]),
    "radius": ("a tenth of the face's smaller side", lambda s, small, a, b, under: small / 10),
    "size": ("a tenth of the face's smaller side", lambda s, small, a, b, under: small / 10),
    "wall": ("a tenth of the part's smallest side",
             lambda s, small, a, b, under: min(small, under) / 10),
}
# Two features need a different rule for a slot name they share with others.
FEATURE_RULES_FOR = {
    ("counterbored hole", "diameter"): ("twice the hole diameter",
                                        lambda s, small, a, b, under: 2 * s["hole diameter"]),
    ("counterbored hole", "depth"): ("a quarter of the material under the face",
                                     lambda s, small, a, b, under: under / 4),
    ("slot", "width"): ("a quarter of its length", lambda s, small, a, b, under: s["length"] / 4),
}


def fill_feature_slots(feature: str, slot_words: tuple[str, ...], slots: dict[str, float],
                       first: float, second: float, under: float) -> list[str]:
    """Give every unwritten slot of a feature its value, in table order. Returns the sentences."""
    said = []
    for word in slot_words:
        if word in slots:
            continue
        words, rule = FEATURE_RULES_FOR.get((feature, word)) or FEATURE_RULES[word]
        slots[word] = float(rule(slots, min(first, second), first, second, under))
        said.append(f"default {word} = {slots[word]:g} ({words})")
    return said
