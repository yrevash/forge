"""Natural-language prompts: how people ask for a part, in many levels and varieties.

Wording in this file (names, synonyms, sentence frames) is fixed template text,
written because no permissively licensed dataset of real part requests was
found. Every prompt built here is labelled `template`, so it can be
filtered or replaced when real human phrasing becomes available. Numbers are never free-written: code fills
them in from the part, and every prompt is checked afterwards.

Levels, from least to most explicit:
  designation  standard parts only: "M8 nut", "ISO 4032 M8 hex nut"
  compact      shorthand a machinist would type: "plate 100x60x5, 4 holes dia 6"
  request      a sentence: "I need a spacer 20 long with OD 12 and ID 6"
  spec         the canonical list from captions.py (unchanged)

Composed parts (families/composed.py) are described feature by feature instead of
dimension by dimension; their builders are at the end of this file.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable

from forge.generators.captions import standard_name

MODEL = "template"

# What people call each part. The first name is the plain one.
NAMES: dict[str, list[str]] = {
    "hex_nut": ["hex nut", "nut", "hexagon nut", "hexagonal nut", "hex. nut"],
    "square_nut": ["square nut", "square-shaped nut", "sq nut", "square machine nut"],
    "plain_washer": ["washer", "flat washer", "plain washer", "round washer"],
    "hex_bolt": ["hex bolt", "bolt", "hex head bolt", "hexagon bolt", "hex screw",
                 "hex head screw"],
    "socket_head_cap_screw": ["socket head cap screw", "cap screw", "allen bolt", "allen screw",
                              "socket cap screw", "socket head screw", "SHCS"],
    "shaft_key": ["shaft key", "parallel key", "key", "machine key", "feather key"],
    "spacer": ["spacer", "round spacer", "sleeve", "bushing", "tube spacer", "distance sleeve"],
    "plate_with_holes": ["plate with holes", "perforated plate", "hole plate",
                         "plate with a grid of holes", "drilled plate"],
    "round_flange": ["flange", "round flange", "pipe flange", "bolt flange",
                     "circular flange"],
    "l_bracket": ["L bracket", "angle bracket", "L-bracket", "corner bracket",
                  "right angle bracket"],
    "mounting_plate": ["mounting plate", "base plate", "plate with corner holes",
                       "adapter plate", "rounded mounting plate"],
    "polygon_prism": ["polygon bar", "polygon prism", "polygonal bar", "prism"],
    "slotted_link": ["link", "flat link", "connecting link", "link bar", "link plate"],
    "u_channel": ["U channel", "channel", "U-channel", "U profile", "C channel"],
    "t_section": ["T section", "T bar", "T-section", "T profile", "tee bar"],
    "i_beam": ["I beam", "I-beam", "H beam", "I section", "I profile"],
    "stepped_shaft": ["stepped shaft", "shaft", "turned shaft", "step shaft"],
    "block_with_features": ["block", "machined block", "rectangular block", "base block"],
    "chamfered_block": ["chamfered block", "block with chamfered top", "bevelled block",
                        "block with chamfer"],
    "open_box": ["open box", "tray", "open-top box", "box without lid", "rectangular tray"],
    "tapered_block": ["tapered block", "truncated pyramid", "frustum block", "tapered base"],
    "counterbored_block": ["counterbored block", "block with counterbored holes",
                           "bar with counterbores", "mounting bar"],
    "angle_section": ["angle section", "angle bar", "L profile", "angle iron", "L section"],
    "cheese_head_screw": ["cheese head screw", "slotted cheese head screw", "slotted screw",
                          "cheese head machine screw"],
    "clevis_bracket": ["clevis bracket", "clevis", "clevis mount", "U bracket with pin holes",
                       "fork bracket"],
    "cone_frustum": ["cone frustum", "truncated cone", "tapered cylinder", "conical frustum",
                     "tapered plug"],
    "corner_plate": ["corner plate", "flat L plate", "L-shaped plate", "flat corner brace",
                     "corner brace"],
    "countersunk_plate": ["countersunk plate", "plate with countersunk holes",
                          "strip with countersunk holes", "countersunk mounting strip"],
    "countersunk_screw": ["countersunk screw", "flat head socket screw", "countersunk bolt",
                          "CSK screw", "flat head cap screw"],
    "countersunk_washer": ["countersunk washer", "finishing washer", "cup washer",
                           "washer with countersink"],
    "cross_plate": ["cross plate", "plus-shaped plate", "cross brace", "flat cross connector"],
    "cup": ["cup", "round cup", "cylindrical cup", "can", "round container"],
    "d_shaft": ["D shaft", "shaft with a flat", "D-shaft", "D-cut shaft", "motor shaft with flat"],
    "dovetail_slide": ["dovetail slide", "dovetail rail", "dovetail", "dovetail guide"],
    "dowel_pin": ["dowel pin", "pin", "dowel", "locating pin", "alignment pin"],
    "eccentric_cam": ["eccentric cam", "eccentric disc", "eccentric", "cam disc",
                      "offset cam"],
    "enclosure_base": ["enclosure base", "enclosure", "project box", "electronics box",
                       "box with screw posts"],
    "flanged_bushing": ["flanged bushing", "flange bushing", "flanged sleeve", "flanged bush",
                        "collar bushing"],
    "flanged_hub": ["flanged hub", "hub", "flange hub", "mounting hub", "shaft hub"],
    "gusset_plate": ["gusset plate", "gusset", "triangular plate", "corner gusset",
                     "triangle bracket"],
    "gusseted_bracket": ["gusseted bracket", "reinforced angle bracket", "ribbed bracket",
                         "bracket with gusset", "braced L bracket"],
    "hat_section": ["hat section", "hat channel", "top hat section", "omega profile",
                    "hat profile"],
    "hex_standoff": ["hex standoff", "standoff", "hex spacer", "hexagonal standoff",
                     "spacer nut"],
    "keyed_hub": ["keyed hub", "hub with keyway", "keyed bushing", "keyway hub",
                  "bushing with keyway"],
    "knob": ["knob", "round knob", "control knob", "grip knob", "handle knob"],
    "o_ring": ["o-ring", "O ring", "oring", "o ring seal", "sealing ring"],
    "perforated_strip": ["perforated strip", "strip with holes", "hole strip", "fixing strip",
                         "punched strip"],
    "pillow_block": ["pillow block", "bearing block", "plummer block", "bearing housing",
                     "shaft support block"],
    "rectangular_frame": ["rectangular frame", "frame plate", "plate with window",
                          "bezel", "gasket frame"],
    "rectangular_tube": ["rectangular tube", "box section", "hollow section", "square tube",
                         "RHS"],
    "set_screw": ["set screw", "grub screw", "socket set screw", "headless screw"],
    "shaft_collar": ["shaft collar", "collar", "locking collar", "stop collar"],
    "shoulder_screw": ["shoulder screw", "shoulder bolt", "stripper bolt",
                       "socket shoulder screw"],
    "slotted_block": ["slotted block", "block with slot", "grooved block", "block with a groove"],
    "slotted_plate": ["slotted plate", "plate with slots", "adjustment plate",
                      "plate with slotted holes"],
    "slotted_shim": ["slotted shim", "shim", "U shim", "horseshoe shim", "slotted spacer plate"],
    "square_flange": ["square flange", "square mounting flange", "square bolt flange",
                      "square bearing flange"],
    "stepped_block": ["stepped block", "step block", "L-shaped block", "block with a step"],
    "stepped_sleeve": ["stepped sleeve", "stepped bushing", "hollow stepped shaft",
                       "step bushing"],
    "t_plate": ["T plate", "flat T plate", "T-shaped plate", "T brace", "tee plate"],
    "t_slot_nut": ["T-slot nut", "T nut", "tee nut", "T slot nut", "sliding nut"],
    "v_block": ["V block", "vee block", "V-block", "block with V groove"],
    "v_pulley": ["V pulley", "V-belt pulley", "pulley", "vee pulley", "belt pulley"],
    "wedge": ["wedge", "triangular wedge", "ramp", "wedge block"],
    "z_section": ["Z section", "Z bar", "Z profile", "zed section", "Z purlin"],
    # Composed parts are named after their base shape; the features follow one by one.
    "composed_block": ["block", "rectangular block", "base block", "machined block",
                       "rectangular base", "block part"],
    "composed_cylinder": ["cylinder", "round block", "disc", "cylindrical block", "round base",
                          "puck"],
    "composed_hex": ["hex block", "hexagonal prism", "hex prism", "hexagonal block",
                     "hexagon bar"],
    "composed_ring": ["ring", "tube", "thick ring", "round tube", "annular block", "ring blank"],
}

# How people name a dimension. "adj" is the word used after the value ("5 mm thick").
LABELS: dict[str, dict] = {
    "length": {"nouns": ["length", "L", "len", "overall length"], "adj": "long"},
    "width": {"nouns": ["width", "W", "wd"], "adj": "wide"},
    "height": {"nouns": ["height", "H", "ht"], "adj": "high"},
    "thickness": {"nouns": ["thickness", "thk", "t"], "adj": "thick"},
    "depth": {"nouns": ["depth", "D"], "adj": "deep"},
    "across_flats": {"nouns": ["across flats", "AF", "A/F", "width across flats",
                               "flat to flat"]},
    "bore_diameter": {"nouns": ["bore", "bore diameter", "centre hole", "center hole",
                                "bore dia"]},
    "outer_diameter": {"nouns": ["outer diameter", "OD", "outside diameter", "outer dia"]},
    "inner_diameter": {"nouns": ["inner diameter", "ID", "inside diameter", "inner dia",
                                 "bore"]},
    "hole_diameter": {"nouns": ["hole diameter", "hole dia", "hole size", "holes"]},
    "head_height": {"nouns": ["head height", "head thickness"]},
    "head_diameter": {"nouns": ["head diameter", "head dia", "head OD"]},
    "shank_diameter": {"nouns": ["shank diameter", "thread diameter", "shank dia",
                                 "diameter"]},
    "socket_size": {"nouns": ["socket size", "hex socket", "key size", "allen size"]},
    "socket_depth": {"nouns": ["socket depth", "hex depth"]},
    "key_length": {"nouns": ["length", "key length", "L"], "adj": "long"},
    "key_width": {"nouns": ["width", "key width", "b"], "adj": "wide"},
    "key_height": {"nouns": ["height", "key height", "h"], "adj": "high"},
    "corner_radius": {"nouns": ["corner radius", "corner R", "corner fillet", "fillet radius",
                                "rounded corners"]},
    "hole_inset": {"nouns": ["hole inset", "hole edge distance", "hole offset from edge",
                             "edge distance"]},
    "spacing_x": {"nouns": ["spacing in x", "x spacing", "pitch along length", "x pitch"]},
    "spacing_y": {"nouns": ["spacing in y", "y spacing", "pitch along width", "y pitch"]},
    "hole_spacing": {"nouns": ["hole spacing", "hole pitch", "pitch", "spacing"]},
    "bolt_circle_diameter": {"nouns": ["bolt circle diameter", "bolt circle", "PCD", "BCD",
                                       "pitch circle diameter"]},
    "bolt_hole_diameter": {"nouns": ["bolt hole diameter", "bolt holes", "bolt hole dia",
                                     "bolt hole size"]},
    "base_length": {"nouns": ["base length", "base leg", "bottom length"]},
    "centre_distance": {"nouns": ["centre distance", "center distance", "hole centres",
                                  "centre to centre", "c/c"]},
    "wall_thickness": {"nouns": ["wall thickness", "wall", "walls", "wall thk"]},
    "flange_thickness": {"nouns": ["flange thickness", "flange thk", "flange"]},
    "web_thickness": {"nouns": ["web thickness", "web thk", "web"]},
    "corner_diameter": {"nouns": ["diameter across corners", "corner diameter",
                                  "across corners", "circumscribed diameter"]},
    "chamfer": {"nouns": ["chamfer", "chamfer size", "bevel", "edge chamfer"]},
    "counterbore_diameter": {"nouns": ["counterbore diameter", "counterbore", "c'bore dia",
                                       "cbore diameter"]},
    "counterbore_depth": {"nouns": ["counterbore depth", "c'bore depth", "cbore depth"]},
    "top_length": {"nouns": ["top length", "length at the top"]},
    "top_width": {"nouns": ["top width", "width at the top"]},
    "base_width": {"nouns": ["base width", "bottom width"]},
    "diameter": {"nouns": ["diameter", "dia", "Ø", "D"]},
    "side": {"nouns": ["side", "side length", "square side"]},
    "leg_a": {"nouns": ["leg a", "first leg", "leg A"]},
    "leg_b": {"nouns": ["leg b", "second leg", "leg B"]},
    "slot_width": {"nouns": ["slot width", "slot", "groove width"]},
    "slot_depth": {"nouns": ["slot depth", "groove depth"]},
    "slot_length": {"nouns": ["slot length", "slot len"]},
    "hole_pitch": {"nouns": ["hole pitch", "pitch", "hole spacing", "hole centres"]},
    "section_diameter": {"nouns": ["section diameter", "cross section", "cord diameter",
                                   "cross-section diameter"]},
    "thread_diameter": {"nouns": ["thread diameter", "thread", "thread dia"]},
    "thread_length": {"nouns": ["thread length", "threaded length"]},
    "shoulder_diameter": {"nouns": ["shoulder diameter", "shoulder dia", "shoulder"]},
    "shoulder_length": {"nouns": ["shoulder length", "shoulder len"]},
    "flange_diameter": {"nouns": ["flange diameter", "flange dia", "flange OD"]},
    "hub_diameter": {"nouns": ["hub diameter", "hub dia", "hub OD"]},
    "base_diameter": {"nouns": ["base diameter", "bottom diameter", "large diameter"]},
    "top_diameter": {"nouns": ["top diameter", "small diameter", "upper diameter"]},
    "mount_hole_diameter": {"nouns": ["mount hole diameter", "mounting hole", "mounting holes",
                                      "fixing hole dia"]},
    "mount_hole_spacing": {"nouns": ["mount hole spacing", "mounting hole centres",
                                     "hole centres"]},
    "pin_hole_diameter": {"nouns": ["pin hole diameter", "pin hole", "pin dia"]},
    "countersink_diameter": {"nouns": ["countersink diameter", "countersink", "csk dia",
                                       "c'sink diameter"]},
    "countersink_angle": {"nouns": ["countersink angle", "csk angle", "included angle"]},
    "keyway_width": {"nouns": ["keyway width", "keyway", "key width"]},
    "keyway_depth": {"nouns": ["keyway depth", "key depth"]},
    "bore_depth": {"nouns": ["bore depth", "hole depth"]},
    "bore_offset": {"nouns": ["bore offset", "eccentricity", "offset"]},
    "arm_width": {"nouns": ["arm width", "leg width", "bar width"]},
    "border_width": {"nouns": ["border width", "border", "frame width"]},
    "bolt_hole_spacing": {"nouns": ["bolt hole spacing", "hole spacing", "bolt pattern",
                                    "hole centres"]},
}

# Whole-number parameters are counts. Singular and plural of the thing counted.
COUNTS: dict[str, list[tuple[str, str]]] = {
    "holes_x": [("hole along the length", "holes along the length"), ("hole in x", "holes in x"),
                ("column", "columns")],
    "holes_y": [("hole along the width", "holes along the width"), ("hole in y", "holes in y"),
                ("row", "rows")],
    "hole_count": [("hole", "holes")],
    "bolt_hole_count": [("bolt hole", "bolt holes"), ("hole on the bolt circle",
                                                     "holes on the bolt circle")],
    "sides": [("side", "sides")],
}
COUNT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
               "ten", "eleven", "twelve"]

FRAMES = [
    "{thing}", "{thing}", "{thing}",
    "make a {thing}", "make me a {thing}", "create a {thing}", "I need a {thing}",
    "need a {thing}", "design a {thing}", "generate a {thing}", "model a {thing}",
    "can you make a {thing}", "draw a {thing}", "build a {thing}", "give me a {thing}",
    "I want a {thing}", "please make a {thing}", "CAD model of a {thing}", "{thing} please",
    "looking for a {thing}", "could you model a {thing}",
]
JOINERS = [", ", ", ", ", ", ", ", "; ", " - ", " / "]
SIZE_SEPARATORS = ["x", " x ", " by ", "×", " × ", "*", " X "]
SIZE_TRIPLES = (("length", "width", "thickness"), ("length", "width", "height"),
                ("key_width", "key_height", "key_length"))


def _number(value: float, rng: random.Random) -> str:
    text = f"{value:g}"
    if "." not in text and "e" not in text and rng.random() < 0.08:
        text += ".0"  # some people always write a decimal
    return _said(value, text)


# ---------------------------------------------------------------------------
# Recording where each number lands in a prompt
# ---------------------------------------------------------------------------
# The System 1 model points at numbers in the sentence, so its training data
# needs to know, for every number written, WHICH parameter it is. The builder
# knows that at the moment it writes the number; finding it afterwards by value
# would have to guess whenever two parameters share a value ("40 by 40").
#
# How it works. A parameter that should be tracked is passed in as a `_Tracked*`
# number: an ordinary float or int that also remembers its parameter name and a
# shared log. Every function that writes a number calls `_said`, which notes the
# written text in the log and puts one invisible marker character straight after
# it. Wording functions build many alternatives and keep one; only the markers
# of the kept text survive, so the log is read back through them. Plain floats
# and ints carry no log, so ordinary prompts are built exactly as before.

class _TrackedFloat(float):
    param: str
    log: list


class _TrackedInt(int):
    param: str
    log: list


# Markers are characters from Unicode's private-use block: they mean nothing, so
# no wording contains them, and no regular expression here treats them as a
# letter or a digit.
_MARK_FIRST, _MARK_LAST = 0xE000, 0xF8FF


def _tracked(value: float, param: str, log: list) -> float | int:
    number = _TrackedInt(value) if isinstance(value, int) else _TrackedFloat(value)
    number.param, number.log = param, log
    return number


def _said(value: float, text: str) -> str:
    """Note that `text` was written for `value`. Returns `text`, marked if the value is tracked."""
    log = getattr(value, "log", None)
    if log is None:
        return text
    log.append((value.param, text, float(value)))
    assert _MARK_FIRST + len(log) - 1 <= _MARK_LAST, "too many numbers written for one prompt"
    return text + chr(_MARK_FIRST + len(log) - 1)


def _read_marks(marked: str, log: list) -> tuple[str, list[dict]]:
    """Remove the markers; return the clean text and one mention per marker that survived."""
    pieces: list[str] = []
    length = 0
    mentions = []
    for piece in re.split(f"([{chr(_MARK_FIRST)}-{chr(_MARK_LAST)}])", marked):
        if len(piece) == 1 and _MARK_FIRST <= ord(piece) <= _MARK_LAST:
            param, text, value = log[ord(piece) - _MARK_FIRST]
            mentions.append({"start": length - len(text), "end": length, "text": text,
                             "value": value, "param": param})
        else:
            pieces.append(piece)
            length += len(piece)
    clean = "".join(pieces)
    for m in mentions:
        assert clean[m["start"]:m["end"]] == m["text"], (clean, m)
    return clean, mentions


def _label(name: str) -> dict:
    """Wording for a dimension, falling back to the parameter's own name."""
    if name in LABELS:
        return LABELS[name]
    plain = name.replace("_", " ")
    nouns = [plain]
    if plain.endswith(" diameter"):
        stem = plain[: -len(" diameter")]
        nouns += [f"{stem} dia", f"{stem} Ø"]
    if plain.endswith(" thickness"):
        nouns.append(plain[: -len("ness")])  # "boss thick" reads oddly; kept rare by order
        nouns.pop()
    return {"nouns": nouns}


class Style:
    """Choices made once per prompt, so one prompt is consistent with itself."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.unit = rng.choice(["", "", "", "mm", "mm", " mm", " mm", " mm"])
        self.diameter = rng.choice(["noun", "noun", "symbol", "dia", "dia_after"])
        self.form = rng.choice(["noun_value", "noun_value", "value_noun", "colon", "equals", "of",
                                "adjective", "adjective"])
        self.count_words = rng.random() < 0.2

    def dimension(self, name: str, value: float) -> str:
        rng, label = self.rng, _label(name)
        unit = rng.choice(["°", " deg", " degrees", " degree"]) if "angle" in name else self.unit
        number = _number(value, rng) + unit
        if any(ch.isdigit() for ch in name):
            # An index in the name ("step 2 diameter") must never sit next to the value.
            return f"{name.replace('_', ' ')}{rng.choice([' ', ': ', ' = '])}{number}"
        noun = rng.choice(label["nouns"])
        if "diameter" in name and noun.endswith("diameter") and self.diameter != "noun":
            stem = noun[: -len("diameter")].strip()
            lead = f"{stem} " if stem else ""
            if self.diameter == "symbol":
                return f"{lead}Ø{number}"
            if self.diameter == "dia":
                return f"{lead}dia {number}"
            return f"{lead}{number} dia"
        if self.form == "adjective" and "adj" in label:
            return f"{number} {label['adj']}"
        if self.form == "value_noun":
            return f"{number} {noun}"
        if self.form == "colon":
            return f"{noun}: {number}"
        if self.form == "equals":
            return f"{noun} = {number}" if rng.random() < 0.5 else f"{noun}={number}"
        if self.form == "of":
            return f"{noun} of {number}"
        return f"{noun} {number}"

    def count(self, name: str, value: int) -> str:
        singular, plural = self.rng.choice(
            COUNTS.get(name, [(name.replace("_", " "), name.replace("_", " "))]))
        if name not in COUNTS:
            return f"{singular} {value}"
        thing = singular if value == 1 else plural
        if self.count_words and value < len(COUNT_WORDS):
            return f"{COUNT_WORDS[value]} {thing}"
        return self.rng.choice([f"{value} {thing}", f"{value} {thing}", f"{value}x {thing}"])


def _dimension_phrases(params: dict, style: Style, compact_size: bool) -> list[str]:
    """One phrase per parameter. The size triple may collapse into '100x60x5'."""
    rng = style.rng
    phrases: list[str] = []
    used: set[str] = set()
    if compact_size:
        for triple in SIZE_TRIPLES:
            if all(k in params for k in triple):
                separator = rng.choice(SIZE_SEPARATORS)
                size = separator.join(_number(params[k], rng) for k in triple)
                phrases.append(size + rng.choice(["", "", "mm", " mm"]))
                used.update(triple)
                break
    rest = [k for k in params if k not in used]
    if rng.random() < 0.35:
        rng.shuffle(rest)  # people do not list dimensions in a fixed order
    for name in rest:
        if name in used:
            continue
        value = params[name]
        partner = name[:-2] + "_y" if name.endswith("_x") else None
        if partner in params and partner not in used:
            feature = name[:-2].replace("_", " ")
            x, y = _number(value, rng), _number(params[partner], rng)
            phrases.append(rng.choice([
                f"{feature} at ({x}, {y})", f"{feature} at x={x} y={y}",
                f"{feature} centre ({x}, {y})", f"{feature} position x {x}, y {y}",
                f"{feature} located at ({x}, {y})"]))
            used.update({name, partner})
            continue
        if name.endswith("_y") and name[:-2] + "_x" in params and name[:-2] + "_x" not in used:
            continue  # said together with its x when that comes up
        phrases.append(style.count(name, value) if isinstance(value, int)
                       else style.dimension(name, value))
    # Any y whose x came later in a shuffled order has been said with that x by now.
    return phrases


def _designation_text(designation: dict, rng: random.Random) -> str:
    """'M8', 'm8', 'M8x40', 'M8 x 40' and friends; the standard is added by the caller."""
    size = designation["size"]
    if rng.random() < 0.12:
        size = size.lower()
    if "length" in designation:
        length = designation["length"]
        size = rng.choice([f"{size}x{length}", f"{size} x {length}", f"{size}×{length}",
                           f"{size}X{length}"])
    return size


_AN_LETTERS = set("AEFHILMNORSX")  # letters whose names start with a vowel sound


def _articles(text: str, rng: random.Random, edits: list | None = None) -> str:
    """Turn 'a M8 nut' into 'an M8 nut'. People slip here now and then, so we do too.

    `edits`, if given, receives (position, +1) for every 'n' put in, so that the
    positions of numbers recorded before this step can be moved along."""
    def fix(match: re.Match) -> str:
        article, word = match.group(1), match.group(2)
        first = word[0]
        spelled_out = len(word) <= 4 and (word.isupper() or any(c.isdigit() for c in word)
                                          or len(word) == 1)
        vowel_sound = first.upper() in _AN_LETTERS if spelled_out else first.lower() in "aeio"
        if rng.random() < 0.06:
            vowel_sound = not vowel_sound
        if vowel_sound and edits is not None:
            edits.append((match.start() + 1, 1))
        return f"{article[0]}{'n' if vowel_sound else ''} {word}"
    return re.sub(r"\b([Aa]) ([A-Za-z0-9][\w.\-']*)", fix, text)


def _finish(text: str, rng: random.Random, passes: list | None = None) -> str:
    """Articles, casing and the occasional typo. Digits and symbols are never touched.

    `passes`, if given, receives one list of (position, change in length) per
    editing pass, in the order the passes ran; see `_moved`."""
    article_edits: list = []
    typo_edits: list = []
    before = len(text)
    text = _articles(text, rng, article_edits)
    assert len(text) == before + len(article_edits)
    roll = rng.random()
    before = len(text)
    if roll < 0.25:
        text = text[:1].upper() + text[1:]
    elif roll < 0.45:
        text = text.lower()
    elif roll < 0.48:
        text = text.upper()
    # Changing case never changes the length of anything these prompts contain,
    # so recorded positions stay valid.
    assert len(text) == before
    if rng.random() < 0.08:
        text = _typo(text, rng, typo_edits)
    if rng.random() < 0.06:
        text += rng.choice([".", " thanks", ", thanks", "?"]) if not text.endswith("please") else ""
    if passes is not None:
        passes += [article_edits, typo_edits]
    return text


def _moved(position: int, passes: list) -> int:
    """Where a position in the text ends up after the editing passes of `_finish`.

    Each pass lists its edits as positions in the text as it was before that
    pass. An edit moves everything after it; it never falls inside a number."""
    for edits in passes:
        position += sum(change for at, change in edits if at < position)
    return position


def _typo(text: str, rng: random.Random, edits: list | None = None) -> str:
    """Swap, drop or double one letter inside one longer word.

    `edits`, if given, receives (position, change in length) when a letter is
    dropped or doubled."""
    # Number words are left alone: a typo there would lose a count ("thee holes").
    words = [m for m in re.finditer(r"[A-Za-z]{5,}", text)
             if m.group().lower() not in COUNT_WORDS]
    if not words:
        return text
    word = rng.choice(words)
    letters = list(word.group())
    i = rng.randrange(1, len(letters) - 1)
    kind = rng.choice(["swap", "drop", "double"])
    if kind == "swap":
        letters[i], letters[i + 1 if i + 1 < len(letters) else i - 1] = (
            letters[i + 1 if i + 1 < len(letters) else i - 1], letters[i])
    elif kind == "drop":
        del letters[i]
    else:
        letters.insert(i, letters[i])
    if edits is not None and kind != "swap":
        edits.append((word.start() + i, -1 if kind == "drop" else 1))
    return text[: word.start()] + "".join(letters) + text[word.end():]


def designation_prompt(family: str, designation: dict, rng: random.Random) -> str:
    name = rng.choice(NAMES.get(family, [family.replace("_", " ")]))
    size = _designation_text(designation, rng)
    standard = standard_name(designation["standard"])
    pattern = rng.choice([
        "{size} {name}", "{size} {name}", "{size} {name}", "{name} {size}", "{name} {size}",
        "{name}, {size}", "{standard} {size} {name}", "{name} {standard} {size}",
        "{size} {name} {standard}", "{name} {size} ({standard})", "{standard} {name} {size}",
        "{name} size {size}",
    ])
    thing = pattern.format(size=size, name=name, standard=standard)
    return _finish(rng.choice(FRAMES).format(thing=thing), rng)


def compact_prompt(family: str, params: dict, rng: random.Random) -> str:
    name = rng.choice(NAMES.get(family, [family.replace("_", " ")]))
    style = Style(rng)
    phrases = _dimension_phrases(params, style, compact_size=True)
    joiner = rng.choice(JOINERS)
    text = name + rng.choice([" ", ", ", ": ", " - "]) + joiner.join(phrases)
    if style.unit == "" and rng.random() < 0.3:
        text += rng.choice([" (mm)", ", all in mm", " mm", ", dimensions in mm"])
    return _finish(text, rng)


def request_prompt(family: str, params: dict, rng: random.Random) -> str:
    name = rng.choice(NAMES.get(family, [family.replace("_", " ")]))
    style = Style(rng)
    phrases = _dimension_phrases(params, style, compact_size=rng.random() < 0.5)
    if len(phrases) > 1 and rng.random() < 0.6:
        listed = ", ".join(phrases[:-1]) + rng.choice([" and ", ", and ", ", "]) + phrases[-1]
    else:
        listed = ", ".join(phrases)
    link = rng.choice([" with ", ", ", " - ", ": ", " that is ", " having "])
    text = rng.choice(FRAMES).format(thing=name) + link + listed
    if style.unit == "" and rng.random() < 0.3:
        text += rng.choice([" (all mm)", ", all dimensions in mm", ". Units are mm",
                            ", in millimetres"])
    return _finish(text, rng)


# Per-family prompt builders: family -> {level: builder(family, params, rng) -> text}.
# Most families are described well enough by listing their dimensions. A family whose
# parameters are a list of features needs its own wording; it registers a builder here.
OVERRIDES: dict[str, dict[str, Callable[[str, dict, random.Random], str]]] = {}


def natural_prompts(family: str, params: dict, designation: dict | None, part_id: str,
                    counts: dict[str, int]) -> list[dict]:
    """Prompts for one part: `counts` says how many of each level to try for.

    The same part always gets the same prompts (the random stream is seeded by
    the part's id). Duplicate texts are dropped.
    """
    builders = {
        "designation": (lambda r: designation_prompt(family, designation, r)) if designation
        else None,
        "compact": lambda r: compact_prompt(family, params, r),
        "request": lambda r: request_prompt(family, params, r),
    }
    # A family may bring its own builder for a level (see OVERRIDES below).
    for level, build in OVERRIDES.get(family, {}).items():
        builders[level] = lambda r, build=build: build(family, params, r)
    prompts, seen = [], set()
    for level, wanted in counts.items():
        build = builders.get(level)
        if build is None:
            continue
        for i in range(wanted):
            text = build(random.Random(f"{part_id}:{level}:{i}"))
            if text not in seen:
                seen.add(text)
                prompts.append({"style": level, "variant": f"{level}-{i}", "model": MODEL,
                                "text": text})
    return prompts


# ---------------------------------------------------------------------------
# Checking a natural prompt against its part
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"(?:(?<=\s)|(?<=\()|(?<=:)|(?<==)|^)-\d+(?:\.\d+)?|\d+(?:\.\d+)?")
_WORD_NUMBERS = {word: i for i, word in enumerate(COUNT_WORDS)}


def numbers_in(text: str) -> list[float]:
    found = [float(n) for n in _NUMBER.findall(text)]
    found += [float(_WORD_NUMBERS[w]) for w in re.findall(r"[a-z]+", text.lower())
              if w in _WORD_NUMBERS]
    return found


def prompt_problems(text: str, params: dict, designation: dict | None,
                    complete: bool) -> list[str]:
    """What is wrong with a prompt. Empty means verified.

    Always: every number in the text must be a number of this part. With
    `complete`, every dimension of the part must also appear in the text, so
    the prompt carries enough to rebuild the part.
    """
    allowed = [float(v) for v in params.values()]
    for key in params:
        allowed += [float(n) for n in re.findall(r"\d+", key)]
    for value in (designation or {}).values():
        allowed += [float(n) for n in re.findall(r"\d+(?:\.\d+)?", value)]

    found = numbers_in(text)
    problems = [f"number {n:g} is not a dimension of this part"
                for n in found if not any(abs(n - a) < 1e-9 for a in allowed)]
    if complete:
        problems += [f"{key} = {value:g} is missing"
                     for key, value in params.items()
                     if not any(abs(n - float(value)) < 1e-9 for n in found)]
    return problems


# ---------------------------------------------------------------------------
# Composed parts: one phrase per feature
# ---------------------------------------------------------------------------
# A composed part's parameters are `base_*` plus `<kind>_<n>_<field>` for the n-th
# feature in build order (families/composed.py). Reading them back as a list of
# features lets a prompt say "a 6 mm through hole at (10, 5), a round boss ..."
# the way a person would, instead of "hole 1 diameter 6, hole 1 x 10, ...".
#
# Rules that keep these prompts checkable:
# - every number comes from a parameter, and every parameter is said once;
# - no digits or number words in the wording itself ("a pair", never "two").

_FEATURE_KEY = re.compile(r"([a-z_]+?)_(\d+)_([a-z_]+)")
_TREATMENTS = ("corner_radius", "top_chamfer", "top_fillet_radius", "wall_thickness")
_ARROWS = [" → ", " → ", " -> ", " -> ", " => ", " > "]


def composed_features(params: dict) -> tuple[dict, list[tuple[str, dict]]]:
    """Split parameters into the base's own and one (kind, fields) per feature, in build order."""
    base = {k[len("base_"):]: v for k, v in params.items() if k.startswith("base_")}
    features: dict[int, tuple[str, dict]] = {}
    for key, value in params.items():
        match = _FEATURE_KEY.fullmatch(key)
        if match and not key.startswith("base_"):
            kind, index, name = match.group(1), int(match.group(2)), match.group(3)
            features.setdefault(index, (kind, {}))[1][name] = value
    return base, [features[i] for i in sorted(features)]


class FeatureStyle(Style):
    """Style, plus the forms a feature list needs: points, sizes, diameters before a noun."""

    def __init__(self, rng: random.Random) -> None:
        super().__init__(rng)
        self.point = rng.choice(["at ({x}, {y})", "at ({x}, {y})", "at ({x}, {y})",
                                 "at x={x} y={y}", "at x={x}, y={y}", "at x {x}, y {y}",
                                 "centred at ({x}, {y})", "centered at ({x}, {y})",
                                 "@ ({x}, {y})"])
        self.separator = rng.choice(SIZE_SEPARATORS)

    def one(self, *options: str) -> str:
        return self.rng.choice(options)

    def bare(self, value: float) -> str:
        return _number(value, self.rng)

    def mm(self, value: float) -> str:
        return _number(value, self.rng) + self.unit

    def dia(self, value: float) -> str:
        """A diameter said after its noun: 'hole Ø6', 'hole dia 6', 'hole 6 dia'."""
        number = self.mm(value)
        return {"symbol": f"Ø{number}", "dia": f"dia {number}",
                "dia_after": f"{number} dia"}.get(self.diameter, f"diameter {number}")

    def dia_before(self, value: float) -> str:
        """A diameter said before its noun: 'Ø6 hole', '6 mm hole', '6 dia hole'."""
        number = self.mm(value)
        if self.diameter == "symbol":
            return f"Ø{number}"
        # A bare "6 hole" would be unclear, so the plain form needs a unit.
        return self.one(number, f"{number} dia") if self.unit else f"{number} dia"

    def size(self, *values: float) -> str:
        return self.separator.join(self.bare(v) for v in values) + self.unit

    def at(self, x: float, y: float) -> str:
        return self.point.format(x=self.bare(x), y=self.bare(y))

    def degrees(self, value: float) -> str:
        return self.bare(value) + self.one("°", " deg", " degrees")

    def many(self, count: int, things: str, plain: bool = False) -> str:
        if self.count_words and count < len(COUNT_WORDS):
            return f"{_said(count, COUNT_WORDS[count])} {things}"
        digits = _said(count, f"{count}")
        return f"{digits} {things}" if plain or self.rng.random() < 0.7 else f"{digits}x {things}"


def _a(text: str) -> str:
    """'a' or 'an' in front of a phrase that may start with a number: 'an 8 mm', 'a 12 mm'."""
    if re.match(r"8|11(\D|$)|18(\D|$)", text):
        return f"an {text}"
    return f"a {text}"


# Each function returns one feature as a noun phrase. A leading "~" marks where an
# article may go ("~round boss" -> "a round boss" or "round boss").

def _say_hole(f: dict, s: FeatureStyle, at: str) -> str:
    d = f["diameter"]
    return s.one(f"~through hole {s.dia(d)} {at}", f"{s.dia_before(d)} through hole {at}",
                 f"~hole {s.dia(d)} {at}, through", f"{s.dia_before(d)} hole right through {at}",
                 f"~thru hole {s.dia(d)} {at}")


def _say_blind_hole(f: dict, s: FeatureStyle, at: str) -> str:
    d, depth = f["diameter"], s.mm(f["depth"])
    return s.one(f"~blind hole {s.dia(d)}, {depth} deep, {at}",
                 f"{s.dia_before(d)} blind hole {depth} deep {at}",
                 f"~flat-bottomed hole {s.dia(d)} {at}, depth {depth}",
                 f"{s.dia_before(d)} hole {at}, {depth} deep (blind)")


def _say_counterbore(f: dict, s: FeatureStyle, at: str) -> str:
    hole, wide, depth = f["hole_diameter"], f["diameter"], s.mm(f["depth"])
    return s.one(
        f"~counterbored hole {at}: {s.dia(hole)} through, counterbore {s.dia(wide)}, {depth} deep",
        f"{s.dia_before(hole)} through hole {at} with {_a(s.dia_before(wide))} counterbore "
        f"{depth} deep",
        f"~c'bored hole {s.dia(hole)} {at}, c'bore {s.dia(wide)} x {depth} deep",
        f"~counterbore {s.dia(wide)}, {depth} deep, over {_a(s.dia_before(hole))} through hole {at}")


def _say_boss(f: dict, s: FeatureStyle, at: str) -> str:
    d, h = f["diameter"], s.mm(f["height"])
    return s.one(f"~round boss {s.dia(d)} x {h} high {at}",
                 f"~cylindrical boss, {s.dia(d)}, {h} tall, {at}",
                 f"{s.dia_before(d)} round boss {h} high {at}",
                 f"~round post {s.dia(d)} standing {h} high {at}",
                 f"~boss {s.dia(d)}, height {h}, {at}")


def _say_pad(f: dict, s: FeatureStyle, at: str) -> str:
    length, width, h = f["length"], f["width"], f["height"]
    return s.one(f"~rectangular boss {s.size(length, width, h)} {at}",
                 f"~rectangular pad {s.size(length, width)}, {s.mm(h)} high, {at}",
                 f"~raised rectangular block {s.mm(length)} long, {s.mm(width)} wide, "
                 f"{s.mm(h)} high {at}",
                 f"{s.size(length, width)} rectangular boss {s.mm(h)} tall {at}")


def _say_pocket(f: dict, s: FeatureStyle, at: str) -> str:
    length, width, depth = f["length"], f["width"], s.mm(f["depth"])
    return s.one(f"~rectangular pocket {s.size(length, width)}, {depth} deep, {at}",
                 f"{s.size(length, width)} pocket {depth} deep {at}",
                 f"~rectangular recess {s.mm(length)} long by {s.mm(width)} wide, depth {depth}, "
                 f"{at}",
                 f"~pocket {s.size(length, width, f['depth'])} {at}")


def _say_slot(f: dict, s: FeatureStyle, at: str) -> str:
    length, width, depth = f["length"], f["width"], s.mm(f["depth"])
    angle = s.degrees(f["angle"])
    along = "X" if f["angle"] == 0 else "Y"
    turned = s.one(f"along {along} ({angle})", f"angle {angle}", f"at {angle} to the X axis",
                   f"running in {along}, angle {angle}")
    return s.one(f"~slot {s.mm(length)} long, {s.mm(width)} wide, {depth} deep, {at}, {turned}",
                 f"~round-ended slot {s.size(length, width)} overall, {depth} deep, {at}, {turned}",
                 f"{s.size(length, width)} slot {at}, depth {depth}, {turned}",
                 f"~slot {s.mm(width)} wide and {s.mm(length)} long end to end, {depth} deep, "
                 f"{at}, {turned}")


def _say_polar(f: dict, s: FeatureStyle, at: str) -> str:
    n, d, circle = f["count"], f["hole_diameter"], f["circle_diameter"]
    return s.one(f"{s.many(n, 'holes')} {s.dia(d)} on {_a(s.dia_before(circle))} circle",
                 f"{s.many(n, 'through holes')} {s.dia(d)} equally spaced on PCD {s.mm(circle)}",
                 f"~bolt circle {s.dia(circle)} with {s.many(n, 'holes', plain=True)} {s.dia(d)}",
                 f"~polar pattern of {s.many(n, 'holes', plain=True)}, {s.dia(d)}, on "
                 f"{_a(s.dia_before(circle))} pitch circle",
                 f"{s.many(n, 'holes')} {s.dia(d)} evenly spaced around the centre, "
                 f"pitch circle {s.dia(circle)}")


def _say_row(f: dict, s: FeatureStyle, at: str) -> str:
    n, d, pitch, y = f["count"], f["hole_diameter"], s.mm(f["spacing"]), s.bare(f["y"])
    return s.one(f"~row of {s.many(n, 'holes', plain=True)} {s.dia(d)} along X, {pitch} pitch, "
                 f"centred, at y = {y}",
                 f"{s.many(n, 'holes')} {s.dia(d)} in a line along X, spaced {pitch}, centred on "
                 f"the block, y={y}",
                 f"~linear pattern of {s.many(n, 'through holes', plain=True)}, {s.dia(d)}, "
                 f"spacing {pitch}, along the length, centred, at y {y}",
                 f"{s.many(n, 'through holes')} {s.dia(d)} at {pitch} centres in a centred row "
                 f"along X, y = {y}")


def _say_hole_pair(f: dict, s: FeatureStyle, at: str) -> str:
    d, x, y = f["diameter"], s.bare(f["x"]), s.bare(f["y"])
    return s.one(f"~pair of {s.dia_before(d)} through holes at x = ±{x}, y = {y}",
                 f"~mirrored pair of holes {s.dia(d)} at x=+/-{x}, y={y}",
                 f"{s.dia_before(d)} through hole {at}, mirrored about the YZ plane",
                 f"through holes {s.dia(d)} left and right of centre, x = ±{x}, y = {y}",
                 f"~through hole {s.dia(d)} {at} and its mirror image across the YZ plane")


_SAY: dict[str, Callable[[dict, FeatureStyle, str], str]] = {
    "hole": _say_hole, "blind_hole": _say_blind_hole, "counterbore": _say_counterbore,
    "boss": _say_boss, "pad": _say_pad, "pocket": _say_pocket, "slot": _say_slot,
    "polar": _say_polar, "row": _say_row, "hole_pair": _say_hole_pair,
    "pocket_pair": _say_pocket, "boss_pair": _say_boss,
}
_VERBS = {"hole": ["drill", "add", "cut"], "blind_hole": ["drill", "add", "cut"],
          "counterbore": ["add", "drill", "cut"], "boss": ["add", "put"], "pad": ["add", "put"],
          "pocket": ["mill", "cut", "add"], "slot": ["mill", "cut", "add"],
          "polar": ["drill", "add"], "row": ["drill", "add"], "hole_pair": ["drill", "add"],
          "pocket_pair": ["mill", "cut", "add"], "boss_pair": ["add"]}


def _feature_phrase(kind: str, f: dict, s: FeatureStyle, shelled: bool) -> str:
    at = s.at(f["x"], f["y"]) if "x" in f else ""
    text = _SAY[kind](f, s, at)
    if kind in ("pocket_pair", "boss_pair"):
        text += s.one(", mirrored about the YZ plane", ", mirrored across the YZ plane",
                      " and its mirror image across the YZ plane",
                      ", plus a mirrored copy on the other side of the YZ plane")
    if shelled:
        # Inside a shelled base a boss stands on the cavity floor, not on the rim.
        text += s.one(" on the inside floor", ", standing on the floor of the cavity",
                      " (rising from the inside floor)")
    return text


def _treatment_phrase(base_kind: str, name: str, value: float, s: FeatureStyle,
                      command: bool) -> str:
    """An edge treatment as a noun phrase, or as an instruction when `command` is set."""
    size, radius = s.mm(value), f"R{s.bare(value)}"
    if name == "corner_radius":
        if command:
            return s.one(f"fillet the vertical edges {size}", f"round the vertical corners {radius}",
                         f"put {_a(size)} fillet on the vertical edges")
        return s.one(f"{size} fillet on the vertical edges", f"vertical edges filleted {radius}",
                     f"corners rounded to {size} radius", f"{radius} on the vertical corner edges")
    if name == "wall_thickness":
        if command:
            return s.one(f"shell it from the top leaving {size} walls and floor",
                         f"hollow it out from the top, wall thickness {size}")
        return s.one(f"hollowed from the top with {size} walls and floor",
                     f"shelled to {size} wall thickness, open at the top",
                     f"open-top shell, walls and floor {size} thick")
    edges = (s.one("both top edges (inner and outer)", "the inner and outer top edges")
             if base_kind == "ring" else
             s.one("the top edge", "the top rim") if base_kind == "cylinder" else
             s.one("the top edges", "all top edges", "the edges of the top face"))
    if name == "top_chamfer":
        if command:
            return s.one(f"chamfer {edges} {size}", f"break {edges} with {_a(size)} chamfer")
        return s.one(f"{size} chamfer on {edges}", f"{edges} chamfered {size}",
                     f"chamfer of {size} on {edges}")
    if command:
        return s.one(f"fillet {edges} {size}", f"round over {edges}, radius {size}")
    return s.one(f"{size} fillet on {edges}", f"{edges} rounded {radius}",
                 f"{radius} fillet on {edges}", f"fillet of radius {size} on {edges}")


def _base_phrase(family: str, base: dict, s: FeatureStyle) -> str:
    name = s.rng.choice(NAMES[family])
    high = s.one("high", "high", "tall", "thick")
    h = base["height"]
    if "length" in base:
        length, width = base["length"], base["width"]
        return s.one(f"{name} {s.size(length, width, h)}", f"{name} {s.size(length, width, h)}",
                     f"{s.size(length, width, h)} {name}",
                     f"{name} {s.mm(length)} long, {s.mm(width)} wide, {s.mm(h)} {high}",
                     f"{name}, length {s.mm(length)}, width {s.mm(width)}, height {s.mm(h)}")
    if "across_flats" in base:
        flats = s.mm(base["across_flats"])
        across = s.one("across flats", "across flats", "AF", "A/F")
        return s.one(f"{name} {flats} {across}, {s.mm(h)} {high}",
                     f"{name}, {across} {flats}, height {s.mm(h)}",
                     f"{flats} {across} {name} {s.mm(h)} {high}")
    if "outer_diameter" in base:
        outer, inner = base["outer_diameter"], base["inner_diameter"]
        return s.one(f"{name} OD {s.mm(outer)}, ID {s.mm(inner)}, {s.mm(h)} {high}",
                     f"{name} {s.dia(outer)} outside, {s.dia(inner)} inside, {s.mm(h)} {high}",
                     f"{name}, outer diameter {s.mm(outer)}, inner diameter {s.mm(inner)}, "
                     f"height {s.mm(h)}",
                     f"{name} with OD {s.mm(outer)} and ID {s.mm(inner)}, height {s.mm(h)}")
    d = base["diameter"]
    return s.one(f"{name} {s.dia(d)}, {s.mm(h)} {high}", f"{name} {s.dia(d)} x {s.mm(h)} {high}",
                 f"{s.dia_before(d)} {name} {s.mm(h)} {high}",
                 f"{name}, {s.dia(d)}, height {s.mm(h)}")


def _article(phrase: str, wanted: bool) -> str:
    if not phrase.startswith("~"):
        return phrase
    return _a(phrase[1:]) if wanted else phrase[1:]


def _composed_pieces(family: str, params: dict, s: FeatureStyle, steps: bool,
                     articles: float) -> tuple[str, list[str]]:
    """The base phrase and the feature phrases, in the order they will be said."""
    rng = s.rng
    base, features = composed_features(params)
    treatment = next((t for t in _TREATMENTS if t in base), None)
    shelled = treatment == "wall_thickness"
    items = []
    for kind, fields in features:
        phrase = _feature_phrase(kind, fields, s, shelled)
        if steps:
            items.append(f"{rng.choice(_VERBS[kind])} {_article(phrase, rng.random() < 0.8)}")
        else:
            items.append(_article(phrase, rng.random() < articles))
    # Features never touch each other, so now and then they are listed in another order.
    # Features are always mentioned in build order, so the program's numbering
    # (hole_1, boss_2, ...) can be read off the prompt.
    text = _base_phrase(family, base, s)
    if treatment:
        said = _treatment_phrase(family.removeprefix("composed_"), treatment, base[treatment], s,
                                 command=steps)
        if shelled:
            # The shell is said first: "on the inside floor" means nothing before it.
            items.insert(0, said)
        else:
            items.insert(rng.choice([0, len(items), len(items), len(items)]), said)
    return text, items


def _composed_ending(s: FeatureStyle) -> str:
    rng, text = s.rng, ""
    if s.unit == "" and rng.random() < 0.3:
        text += rng.choice([" (mm)", ", all in mm", ", dimensions in mm", ". Units are mm"])
    if rng.random() < 0.15:
        text += rng.choice([". Positions are measured from the centre", ", x and y from the centre",
                            " (origin at the centre of the base)"])
    return text


def composed_compact(family: str, params: dict, rng: random.Random) -> str:
    """Shorthand: 'block 60x40x10; Ø6 through hole at (10, 5); round boss ...' or an arrow list."""
    return _finish(_compact_body(family, params, rng), rng)


def _compact_body(family: str, params: dict, rng: random.Random) -> str:
    s = FeatureStyle(rng)
    base, items = _composed_pieces(family, params, s, steps=False, articles=0.25)
    if rng.random() < 0.4:
        text = rng.choice(_ARROWS).join([base, *items])
    else:
        text = base + rng.choice([", ", "; ", ": ", " with ", " - "]) + rng.choice(JOINERS).join(items)
    return text + _composed_ending(s)


def composed_request(family: str, params: dict, rng: random.Random) -> str:
    """A sentence, a step-by-step instruction, or a request followed by a bullet list."""
    return _finish(_request_body(family, params, rng), rng)


def _request_body(family: str, params: dict, rng: random.Random) -> str:
    s = FeatureStyle(rng)
    roll = rng.random()
    frame = rng.choice([f for f in FRAMES if f.endswith("{thing}")])
    if roll < 0.5:
        base, items = _composed_pieces(family, params, s, steps=False, articles=0.8)
        listed = (", ".join(items[:-1]) + rng.choice([" and ", ", and ", ", "]) + items[-1]
                  if len(items) > 1 else items[0])
        link = rng.choice([" with ", " with ", " having ", ", with ", ". It has ", ". Features: "])
        text = frame.format(thing=base) + link + listed
    elif roll < 0.8:
        base, items = _composed_pieces(family, params, s, steps=True, articles=0.8)
        opener = rng.choice(["start with a {thing}", "take a {thing}", "begin with a {thing}",
                             "make a {thing}", "base: {thing}"]).format(thing=base)
        then = rng.choice([". Then ", ", then ", "; then ", ". Next ", ". ", "; "])
        text = opener + then + then.join(items)
    else:
        base, items = _composed_pieces(family, params, s, steps=False, articles=0.5)
        bullet = rng.choice(["\n- ", "\n- ", "\n* ", "\n  - "])
        text = frame.format(thing=base) + rng.choice([":", ", with:", " with"]) + bullet \
            + bullet.join(items)
    return text + _composed_ending(s)


_COMPOSED_BODIES = {"compact": _compact_body, "request": _request_body}


def composed_prompt_with_mentions(level: str, family: str, params: dict,
                                  rng: random.Random) -> tuple[str, list[dict]]:
    """The same prompt `composed_compact` / `composed_request` would give, plus its mentions.

    A mention is one number as it stands in the text:
        {"start": 14, "end": 16, "text": "60", "value": 60.0, "param": "base_length"}
    `param` is the parameter the builder was writing at that moment, so nothing
    is matched up afterwards. Mentions are in text order, and a number word
    ("six holes") is a mention like any other.
    """
    log: list = []
    tracked = {name: _tracked(value, name, log) for name, value in params.items()}
    clean, mentions = _read_marks(_COMPOSED_BODIES[level](family, tracked, rng), log)
    passes: list = []
    text = _finish(clean, rng, passes)
    for m in mentions:
        m["start"], m["end"] = _moved(m["start"], passes), _moved(m["end"], passes)
        # Only the case can have changed ("SIX"); anything else is a bug in the bookkeeping.
        assert text[m["start"]:m["end"]].lower() == m["text"].lower(), (text, m)
        m["text"] = text[m["start"]:m["end"]]
    return text, sorted(mentions, key=lambda m: m["start"])


for _family in ("composed_block", "composed_cylinder", "composed_hex", "composed_ring"):
    OVERRIDES[_family] = {"compact": composed_compact, "request": composed_request}
