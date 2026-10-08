"""The vocabulary of the plan language (Draft 2), written as tables.

Every table here is copied from a table in that document (section numbers given).
Nothing in this file reads text; clauses.py does that using these tables.
"""

from __future__ import annotations

import re

# --- section 2: directions and faces --------------------------------------------------------
FACES = ("top", "bottom", "front", "back", "left", "right")
# The direction each face closes off, and the two faces at the ends of each direction.
DIRECTION_OF_FACE = {"top": "height", "bottom": "height", "front": "depth", "back": "depth",
                     "left": "length", "right": "length"}
FACE_PAIRS = {"height": ("bottom", "top"), "depth": ("front", "back"), "length": ("left", "right")}
DIRECTIONS = ("length", "depth", "height")
# Anchor and corner names list their words in this order: top-front-left.
FACE_ORDER = {face: index for index, face in enumerate(FACES)}

# --- section 4: shapes and the order of their sizes -----------------------------------------
SHAPES: dict[str, tuple[str, ...]] = {
    "box": ("length", "depth", "height"),
    "cylinder": ("diameter", "height"),
    "tube": ("outer diameter", "inner diameter", "height"),
    "cone": ("bottom diameter", "top diameter", "height"),
    "sphere": ("diameter",),
    "dome": ("diameter", "height"),
    "prism": ("sides", "across", "height"),
    "wedge": ("length", "depth", "height"),
    "tapered box": ("bottom length", "bottom depth", "top length", "top depth", "height"),
}
# `bar PROFILE ...`: the sizes of each profile (section 4, the bar table).
BAR = "bar"
_OPEN_PROFILE = ("profile width", "profile depth", "thickness", "length")
BAR_PROFILES: dict[str, tuple[str, ...]] = {
    "l": _OPEN_PROFILE, "t": _OPEN_PROFILE, "u": _OPEN_PROFILE, "i": _OPEN_PROFILE,
    "tube": ("outer diameter", "wall", "length"),
}
COUNT_SLOTS = frozenset({"sides"})                                   # a whole number, 3 or more
ZERO_ALLOWED_SLOTS = frozenset({"top diameter", "top length", "top depth"})   # a point or an edge

# The size that may be written `rest`, per shape: the ones a placement can leave over.
REST_SLOTS: dict[str, tuple[str, ...]] = {
    "box": ("length", "depth", "height"), "wedge": ("length", "depth", "height"),
    "cylinder": ("height",), "tube": ("height",), "cone": ("height",), "dome": ("height",),
    "prism": ("height",), "tapered box": ("height",), "bar": ("length",), "sphere": (),
}

# Turning a part. A shape either LIES (quarter turn of a long part) or POINTS (which way
# its top end faces); no shape does both, and a sphere does neither.
ORIENTATIONS = ("standing", "lying along length", "lying along depth")
LYING_SHAPES = frozenset({"box", "cylinder", "tube", "prism", "bar"})
POINTING_SHAPES = frozenset({"wedge", "cone", "dome", "tapered box"})
# A wedge keeps two full faces: the one opposite the way it points, and this one unless
# `flat FACE` says otherwise (section 4, the wedge table).
WEDGE_DEFAULT_FLAT = {"left": "bottom", "right": "bottom", "front": "bottom", "back": "bottom",
                      "up": "back", "down": "back"}
POINTINGS = ("up", "down", "left", "right", "front", "back")
FACE_POINTED_AT = {"up": "top", "down": "bottom", "left": "left", "right": "right",
                   "front": "front", "back": "back"}

DIMENSIONS = ("length", "depth", "height", "diameter")    # what `same as X's ...` may copy
SHARES = ("half", "third", "quarter", "double")

# --- section 5: placements ------------------------------------------------------------------
ON_GROUND = "on ground"
ABOVE_GROUND = "above ground"
BETWEEN = "between"
AROUND = "around"
SPANS = "spans"
# Face placements: the new part touches one outer face of X. Value: the face of the NEW
# part that does the touching (used by the `rest` check).
FACE_PLACEMENTS = {"on top of": "bottom", "under": "top", "in front of": "back",
                   "behind": "front", "left of": "right", "right of": "left"}
# Placements that name exactly one other part. Longest first, so "on top of" wins over "on".
ONE_PART_PLACEMENTS = ("on top of", "in front of", "left of", "right of", "under", "behind",
                       "inside", "through", "around")
SPANNING_SHAPES = frozenset({"box", "cylinder", "tube", "prism", "bar"})

# --- sections 6 and 7: small word lists -----------------------------------------------------
FACE_RELATIONS = ("above", "below", "inside", "beyond")
CORNER_SIDES = ("front", "back", "left", "right")
MIRRORS = ("left-right", "front-back")

# --- section 9: features --------------------------------------------------------------------
# name in a plan -> (kind in forge/system1/steps.py, slot words, may it be positioned?)
# A slot word is the STEPS slot with a space for the underscore; `wall` is `wall_thickness`.
# The STEPS slots x and y are not slots here: they are the position (`at (x, y)`).
FEATURES: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "hole": ("hole", ("diameter",), True),
    "blind hole": ("blind_hole", ("diameter", "depth"), True),
    "counterbored hole": ("counterbore", ("hole diameter", "diameter", "depth"), True),
    "boss": ("boss", ("diameter", "height"), True),
    "pad": ("pad", ("length", "width", "height"), True),
    "pocket": ("pocket", ("length", "width", "depth"), True),
    "slot": ("slot", ("length", "width", "depth", "angle"), True),
    "circle of holes": ("polar", ("count", "hole diameter", "circle diameter"), False),
    "row of holes": ("row", ("count", "hole diameter", "spacing"), True),
    "pair of holes": ("hole_pair", ("diameter",), True),
    "pair of bosses": ("boss_pair", ("diameter", "height"), True),
    "pair of pockets": ("pocket_pair", ("length", "width", "depth"), True),
    "rounded corners": ("corner_radius", ("radius",), False),
    "top chamfer": ("top_chamfer", ("size",), False),
    "top fillet": ("top_fillet", ("radius",), False),
    "hollowed out": ("shell", ("wall",), False),
}
STEPS_SLOT_OF = {"wall": "wall_thickness"}          # every other slot: spaces -> underscores
FEATURE_COUNT_SLOTS = frozenset({"count"})
FEATURE_ANY_NUMBER_SLOTS = frozenset({"angle"})     # degrees: zero or negative is fine
TOP_ONLY_FEATURES = frozenset({"top chamfer", "top fillet", "hollowed out"})
HOLLOWING_FEATURE = "hollowed out"

# --- words a part may not be called ---------------------------------------------------------
RESERVED_NAMES = frozenset({"ground", "done", "undo", "end", "group", "rest", "default"})

# --- regular-expression pieces --------------------------------------------------------------
NUM = r"(\d+(?:\.\d+)?)(?: ?mm)?"           # 40, 12.5, 40mm, 40 mm
INT = r"(\d+)"
BY = r"(?:by|x|×)"
FACE = "(" + "|".join(FACES) + ")"
OTHER_FACE = "((?:inner )?(?:" + "|".join(FACES) + "))"    # X's left, or X's inner left
# "seat's", "back posts's" and "back posts'" all point at a part.
OWNER = r"(.+?)(?:'s|')"
NAME_RE = re.compile(r"[a-z0-9][a-z0-9 -]*")
# Between two sizes: the word "by", the sign "×", or a lone "x" next to a digit or a space.
SIZE_SEPARATOR = re.compile(r"\s+by\s+|\s*×\s*|(?<=[\d\s])x(?=[\d\s])")


def clean(text: str) -> str:
    """The tolerated surface variation: capitals, extra spaces, curly apostrophes."""
    text = text.lower()
    for mark in "’‘`´":
        text = text.replace(mark, "'")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r" ([,:])", r"\1", text)      # "seat : box" -> "seat: box"


def face_words(text: str) -> list[str] | None:
    """'top-front-left' or 'front left' -> its face words in the fixed order; None if not faces.

    The words must close off different directions: 'top-bottom' is not a place on a part.
    """
    words = re.split(r"[- ]", text)
    directions = {DIRECTION_OF_FACE.get(word) for word in words}
    if None in directions or len(directions) != len(words):
        return None
    return sorted(words, key=FACE_ORDER.__getitem__)
