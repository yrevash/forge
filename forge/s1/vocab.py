"""The closed vocabularies of the encoding: words, number fields, link roles.

A view (forge/freecad/load.py) is turned into ROWS, and a row is a bag of FACTS.
There are three kinds of fact, and each kind has its own small dictionary here:

    word     "item.plane=XY", "cand.command=pad", "shape.fixed:length"   -> WORDS
    number   ("pad.length", 16.0), ("solid.volume", 115600.0)            -> NUM_FIELDS
    link     ("item.sketch", object 3), ("eq:pad.length:height", plan item 0)  -> ROLES

Everything is built from the lists load.py already whitelists, in a fixed order, so the
ids are the same on every machine. `vocab_hash()` goes into the data manifest and into
every checkpoint; a checkpoint is only used with data that has the same hash.

A word, field or role that is not listed here raises KeyError in encode.py. Nothing is
dropped silently.
"""

from __future__ import annotations

import hashlib
import json

from forge.freecad.catalogue import (
    AXES,
    COMMANDS,
    EDGE_RULES,
    FACE_RULES,
    PLANES,
    SKETCH_AXES,
)
from forge.freecad.load import (
    COMMON,
    DIMENSIONS,
    ITEM_FIELDS,
    SELECTION_FIELDS,
    SELECTION_TYPES,
    SESSION_FIELDS,
    SHAPES,
)
from forge.system1.steps import SLOTS

ROW_TYPES = ("plan", "item", "shape", "session", "solid", "cand")

# Every slot name a plan item can have, once ("length", "width", "x", ...).
SLOT_NAMES = tuple(sorted({slot for slots in SLOTS.values() for slot in slots}))

# --- which field holds what --------------------------------------------------------------------
# A field of the view is a yes/no, a word of a short list, a pointer to an object, or a number.
BOOL_FIELDS = ("valid", "active", "closed", "through_all", "document", "finished", "can_undo")
WORD_FIELDS = {"plane": PLANES, "axis": (*AXES, *SKETCH_AXES), "rule": (*EDGE_RULES, *FACE_RULES),
               "direction": AXES}
# Fields whose value is an object of the document ("#3"). Same list as load.LINK_FIELDS.
POINTER_FIELDS = ("name", "body", "tip", "sketch", "on", "of", "used_by", "active_body",
                  "open_sketch")
# Counts of things, not sizes: comparing them with the plan's numbers would only make noise.
NOT_SIZES = ("dof", "n_geometry", "n_constraints", "edges", "faces")
# A feature that a plan item builds by itself. The k-th of these belongs to plan item k when
# the document is on plan (forge/freecad/recipes.py: every recipe makes exactly one).
PRIMARY_TYPES = ("pad", "pocket", "hole", "revolution", "fillet", "chamfer", "thickness")
SOLID_NUMBERS = ("volume", "solids", "xmin", "ymin", "zmin", "xmax", "ymax", "zmax",
                 "dx", "dy", "dz")
SOLID_SIZES = SOLID_NUMBERS[2:]         # the bounding box and its extents
MAX_SHAPE_INDEX = 7                     # a sketch's 8th and later shapes share one index word

ITEM_FIELD_NAMES = tuple(dict.fromkeys((*COMMON, *(f for fs in ITEM_FIELDS.values() for f in fs))))


def _field_words(scope: str, fields: tuple[str, ...]) -> list[str]:
    """The words one scope's fields can become: =None for all, =True/False, =<word>."""
    words = []
    for field in fields:
        words.append(f"{scope}.{field}=None")
        if field in BOOL_FIELDS:
            words += [f"{scope}.{field}=True", f"{scope}.{field}=False"]
        words += [f"{scope}.{field}={word}" for word in WORD_FIELDS.get(field, ())]
    return words


def _build_words() -> tuple[str, ...]:
    words = ["<pad>"]
    words += [f"row:{name}" for name in ROW_TYPES]
    words += [f"plan.kind={kind}" for kind in SLOTS]
    words += [f"plan.used:{slot}" for slot in SLOT_NAMES]
    words += [f"item.type={kind}" for kind in ITEM_FIELDS]
    words += _field_words("item", tuple(f for f in ITEM_FIELD_NAMES if f != "type"))
    words += [f"shape.shape={shape}" for shape in SHAPES]
    words += [f"shape.index={k}" for k in range(MAX_SHAPE_INDEX + 1)]
    words += ["shape.newest=True"]
    for dimension in DIMENSIONS:
        words += [f"shape.fixed:{dimension}", f"shape.free:{dimension}", f"shape.{dimension}=None"]
    words += _field_words("session", (*SESSION_FIELDS, "can_undo", "selection"))
    words += [f"sel.type={kind}" for kind in SELECTION_TYPES]
    words += [f"sel.name={plane}" for plane in PLANES]
    words += _field_words("sel", tuple(f for f in SELECTION_FIELDS if f != "type"))
    words += ["solid=None", "solid.valid=True", "solid.valid=False"]
    words += [f"cand.command={command}" for command in COMMANDS]
    return tuple(dict.fromkeys(words))


def _build_num_fields() -> tuple[str, ...]:
    fields = [f"plan.{slot}" for slot in SLOT_NAMES]
    for kind, names in ITEM_FIELDS.items():
        fields += [f"{kind}.{name}" for name in names
                   if name not in BOOL_FIELDS and name not in WORD_FIELDS
                   and name not in POINTER_FIELDS]
    fields += [f"shape.{name}" for name in ("sides", "first", *DIMENSIONS)]
    fields += ["sel.count"]
    fields += [f"solid.{name}" for name in SOLID_NUMBERS]
    return tuple(fields)


NUM_FIELDS = _build_num_fields()
# Number fields that are sizes or positions: these are compared with the plan's numbers.
MATCH_FIELDS = tuple(field for field in NUM_FIELDS
                     if not field.startswith(("plan.", "sel."))
                     and field.split(".")[1] not in (*NOT_SIZES, "sides", "first")
                     and field not in ("solid.volume", "solid.solids"))
# A size that equals no plan number gets the word "nomatch:<field>": the 42 that should be 24.
WORDS = (*_build_words(), *(f"nomatch:{field}" for field in MATCH_FIELDS))
# How a number can equal a plan number: exactly, or as its half (a radius against a
# diameter, a bounding box that runs from -length/2 to +length/2).
RELATIONS = ("eq", "half")


def _build_roles() -> tuple[tuple[str, str], ...]:
    """(role name, which table its target is in: "plan" items or "doc" objects)."""
    roles = [("plan.self", "plan"), ("item.ordinal", "plan"), ("shape.ordinal", "plan")]
    roles += [(f"item.{field}", "doc") for field in POINTER_FIELDS if field in ITEM_FIELD_NAMES]
    roles += [("shape.sketch", "doc")]
    roles += [(f"session.{field}", "doc") for field in SESSION_FIELDS if field in POINTER_FIELDS]
    roles += [(f"sel.{field}", "doc") for field in SELECTION_FIELDS if field in POINTER_FIELDS]
    roles += [(f"{relation}:{field}:{slot}", "plan")
              for relation in RELATIONS for field in MATCH_FIELDS for slot in SLOT_NAMES]
    return tuple(roles)


ROLES = _build_roles()
ROLE_NAMES = tuple(name for name, _ in ROLES)

# --- the third model's additions -----------------------------------------------------
# They come AFTER the lists above, so every id of the first two models stays what it was and
# `vocab_hash()` (which those models' checkpoints carry) does not change.
HISTORY = 2                             # how many previous commands the view can show
STATUSES = ("ok", "rejected", "error", "timeout", "crash")      # load.STATUSES
THIRD_WORDS = tuple(word for k in range(1, HISTORY + 1) for word in (
    f"prev{k}=None", *(f"prev{k}.command={command}" for command in COMMANDS),
    *(f"prev{k}.status={status}" for status in STATUSES)))
# "this x is where the first hole of plan item i's row belongs": -(count - 1) * spacing / 2.
THIRD_ROLES = (("first:shape.x", "plan"), ("first:shape.y", "plan"))
# Number fields that count things. Their exact value matters (dof 0 or 1, 6 sides); every
# other number is a size or a position, which the third model reads by its relations to
# the plan and only coarsely by its magnitude (forge/s1/third/model.py).
COUNT_NAMES = (*NOT_SIZES, "sides", "first", "count", "solids")

ALL_WORDS = (*WORDS, *THIRD_WORDS)
ALL_ROLES = (*ROLES, *THIRD_ROLES)
WORD_ID = {word: i for i, word in enumerate(ALL_WORDS)}
NUM_FIELD_ID = {field: i for i, field in enumerate(NUM_FIELDS)}
ROLE_ID = {name: i for i, (name, _) in enumerate(ALL_ROLES)}

# How many plan items and objects one view may have. encode.py raises above these.
MAX_PLAN_ITEMS = 32
MAX_DOC_ITEMS = 64
MAX_CANDIDATES = 32     # the target set is stored as 32 bits, one per candidate


def as_json() -> dict:
    """Everything the training code needs to know about the vocabularies (it never imports
    this file, so that it runs without the rest of forge)."""
    return {"words": list(WORDS), "num_fields": list(NUM_FIELDS),
            "roles": [list(role) for role in ROLES],
            "max_plan_items": MAX_PLAN_ITEMS, "max_doc_items": MAX_DOC_ITEMS}


def vocab_hash() -> str:
    return hashlib.sha256(json.dumps(as_json(), sort_keys=True).encode()).hexdigest()[:12]


def third_json() -> dict:
    """The third model's vocabularies: the lists above plus its additions and its heads."""
    from forge.s1.third.bindings import KINDS, MAX_ARGS
    return {"words": list(ALL_WORDS), "num_fields": list(NUM_FIELDS),
            "roles": [list(role) for role in ALL_ROLES],
            "max_plan_items": MAX_PLAN_ITEMS, "max_doc_items": MAX_DOC_ITEMS,
            "kinds": list(KINDS), "max_args": MAX_ARGS,
            "num_is_count": [field.split(".")[1] in COUNT_NAMES for field in NUM_FIELDS],
            "history_words": [len(WORDS), len(ALL_WORDS)]}


def third_hash() -> str:
    return hashlib.sha256(json.dumps(third_json(), sort_keys=True).encode()).hexdigest()[:12]
