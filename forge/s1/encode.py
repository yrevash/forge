"""Turn one view into rows of facts (the model's input), and a label into a target set.

    encoded = encode(example.view)          # only the view: the label side cannot get in
    target  = target_bits(encoded.candidates, example.label)

ROWS. One row per thing, in this order:
    plan rows      one per plan item
    item rows      one per object of the document (body, sketch, pad, ...)
    shape rows     one per shape drawn in a sketch
    session row    what is open, what is selected, whether undo is possible
    solid row      the measured solid (or "there is none")
    cand rows      one per valid command; the model gives each of these one score

FACTS. A row is a bag of facts; the model adds their vectors up (model.py).
    word     a closed-vocabulary word                  "item.type=pad", "cand.command=undo"
    number   a field and its value                     ("pad.length", 16.0)
    link     a role and the row it points at           ("item.sketch", object 1)

HOW A WRONG NUMBER BECOMES VISIBLE. Every size in the document is compared, by plain
value equality, with every number of the plan. The result goes in as link facts:
    ("eq:shape.length:length", plan item 0)    this shape's length equals item 0's length slot
    ("half:shape.x:circle_diameter", item 3)   this x is half of item 3's circle diameter
or as the word "nomatch:shape.length" when it equals no plan number at all. So a sketch
that says 42 where the plan says 24 differs from the right one by a fact, not by a float.
The other direction: a plan slot whose value some document size equals gets the word
"plan.used:<slot>".

WHICH PLAN ITEM IS BEING BUILT. Each recipe makes exactly one "primary" feature (a pad,
pocket, hole, fillet, chamfer or wall). So the k-th primary feature of the document, its
sketch and its copies get the link ("item.ordinal", plan item k): the same index the k-th
plan item has. On plan they line up; off plan they do not. This is computed from the
document alone.

Nothing here knows which commands are right. This file uses only the view.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.freecad.load import DIMENSIONS
from forge.s1.vocab import (
    ALL_ROLES,
    ALL_WORDS,
    BOOL_FIELDS,
    HISTORY,
    MATCH_FIELDS,
    MAX_CANDIDATES,
    MAX_DOC_ITEMS,
    MAX_PLAN_ITEMS,
    MAX_SHAPE_INDEX,
    NUM_FIELD_ID,
    NUM_FIELDS,
    POINTER_FIELDS,
    PRIMARY_TYPES,
    ROLE_ID,
    WORD_FIELDS,
    WORD_ID,
)

MATCH_DIGITS = 6        # two numbers are equal when they agree to a millionth (teacher.TOLERANCE)
_MATCHED = frozenset(MATCH_FIELDS)


@dataclass
class Encoded:
    """One view as flat lists of facts. `*_row` says which row a fact belongs to."""

    n_rows: int = 0
    third: bool = False     # the third model's extra facts (history, first of a row)
    n_plan: int = 0         # how many plan rows there are (they are the first rows)
    candidates: list[str] = field(default_factory=list)     # command names; the LAST rows
    word_row: list[int] = field(default_factory=list)
    word_id: list[int] = field(default_factory=list)
    num_row: list[int] = field(default_factory=list)
    num_field: list[int] = field(default_factory=list)
    num_value: list[float] = field(default_factory=list)
    link_row: list[int] = field(default_factory=list)
    link_role: list[int] = field(default_factory=list)
    link_target: list[int] = field(default_factory=list)    # a plan item index or an object index

    def new_row(self, kind: str) -> int:
        row = self.n_rows
        self.n_rows += 1
        self.word(row, f"row:{kind}")
        return row

    def word(self, row: int, word: str) -> None:
        self.word_row.append(row)
        self.word_id.append(WORD_ID[word])

    def number(self, row: int, name: str, value: float) -> None:
        self.num_row.append(row)
        self.num_field.append(NUM_FIELD_ID[name])
        self.num_value.append(float(value))

    def link(self, row: int, role: str, target: int) -> None:
        self.link_row.append(row)
        self.link_role.append(ROLE_ID[role])
        self.link_target.append(target)


def object_index(name: str) -> int:
    """ "#3" -> 3: the place of an object in the document (load.opaque_ids)."""
    return int(name[1:])


class PlanNumbers:
    """The plan's numbers, ready to answer "which plan slots hold this value?"."""

    def __init__(self, plan: list[dict]) -> None:
        self.by_value: dict[float, list[tuple[int, str]]] = {}
        for index, item in enumerate(plan):
            for slot, value in item["slots"].items():
                self.by_value.setdefault(round(float(value), MATCH_DIGITS), []).append((index, slot))
        self.used: set[tuple[int, str]] = set()     # (plan item, slot) some document size equals
        # Third model: where the first hole of a row of holes belongs, -(count - 1) * spacing / 2
        self.first: dict[float, list[int]] = {}
        for index, item in enumerate(plan):
            slots = item["slots"]
            if "count" in slots and "spacing" in slots:
                place = -(slots["count"] - 1) * slots["spacing"] / 2
                self.first.setdefault(round(place, MATCH_DIGITS), []).append(index)

    def first_of_row(self, value: float) -> list[int]:
        return self.first.get(round(float(value), MATCH_DIGITS), [])

    def equal_to(self, value: float) -> list[tuple[int, str]]:
        return self.by_value.get(round(float(value), MATCH_DIGITS), [])

    def double_of(self, value: float) -> list[tuple[int, str]]:
        """Plan slots that hold twice the SIZE of this value (the sign is ignored: a block of
        length 85 runs from x = -42.5 to +42.5). Zero is left out: it is its own double."""
        if round(float(value), MATCH_DIGITS) == 0:
            return []
        return self.by_value.get(round(2 * abs(float(value)), MATCH_DIGITS), [])


def _size(enc: Encoded, row: int, name: str, value: float, plan: PlanNumbers,
          of_document: bool = True) -> None:
    """A number that is a size: the number itself, and what it equals in the plan."""
    enc.number(row, name, value)
    if name not in _MATCHED:
        return
    equal, half = plan.equal_to(value), plan.double_of(value)
    for index, slot in equal:
        enc.link(row, f"eq:{name}:{slot}", index)
        if of_document:
            plan.used.add((index, slot))
    for index, slot in half:
        enc.link(row, f"half:{name}:{slot}", index)
    first = plan.first_of_row(value) if enc.third and name in ("shape.x", "shape.y") else []
    for index in first:
        enc.link(row, f"first:{name}", index)
    if not equal and not half and not first:
        enc.word(row, f"nomatch:{name}")


def _fields(enc: Encoded, row: int, scope: str, numbers_as: str, fields: dict,
            plan: PlanNumbers) -> None:
    """The fields of one thing as facts. Each value is nothing, a yes/no, a word, a pointer
    to an object, or a number; which one is decided by the field's name and the value."""
    for name, value in fields.items():
        if value is None:
            enc.word(row, f"{scope}.{name}=None")
        elif name in BOOL_FIELDS or name in WORD_FIELDS:
            enc.word(row, f"{scope}.{name}={value}")
        elif name in POINTER_FIELDS:
            enc.link(row, f"{scope}.{name}", object_index(value))
        else:
            _size(enc, row, f"{numbers_as}.{name}", value, plan)


def ordinals(items: list[dict]) -> dict[str, int]:
    """Object name -> which plan item it would belong to if the document were on plan.

    The k-th primary feature gets k. A sketch gets the number of the feature that uses it,
    and a sketch nothing uses yet gets the next free number (it is being worked on). A
    pattern or mirror gets the number of the feature it copies. A body gets none."""
    found: dict[str, int] = {}
    for item in items:
        if item["type"] in PRIMARY_TYPES:
            found[item["name"]] = len(found)
    primaries = len(found)
    for item in items:
        if item["type"] == "sketch":
            found[item["name"]] = found.get(item["used_by"], primaries)
        elif "of" in item and item["of"] in found:
            found[item["name"]] = found[item["of"]]
    return found


def encode(view: dict, third: bool = False) -> Encoded:
    """The model's input for one step. Uses the view and nothing else.
    `third`: also write the third model's facts: the previous commands of
    view["history"] on the session row, and "first of a row" links on sketch positions."""
    plan_items, snapshot, valid = view["plan"], view["snapshot"], view["valid"]
    items, session, solid = snapshot["items"], snapshot["session"], snapshot["solid"]
    if len(plan_items) > MAX_PLAN_ITEMS or len(items) > MAX_DOC_ITEMS \
            or len(valid) > MAX_CANDIDATES:
        raise ValueError(f"view too large: {len(plan_items)} plan items, {len(items)} objects, "
                         f"{len(valid)} valid commands")
    enc = Encoded(third=third, n_plan=len(plan_items))
    plan = PlanNumbers(plan_items)

    plan_rows = []
    for index, item in enumerate(plan_items):
        row = enc.new_row("plan")
        plan_rows.append(row)
        enc.word(row, f"plan.kind={item['kind']}")
        enc.link(row, "plan.self", index)
        for slot, value in item["slots"].items():
            enc.number(row, f"plan.{slot}", value)

    ordinal = ordinals(items)
    shapes = []         # (object index of the sketch, its ordinal, place in the sketch, shape, newest?)
    for index, item in enumerate(items):
        row = enc.new_row("item")
        enc.word(row, f"item.type={item['type']}")
        own = {name: value for name, value in item.items() if name not in ("type", "shapes")}
        _fields(enc, row, "item", item["type"], own, plan)
        if item["name"] in ordinal:
            enc.link(row, "item.ordinal", min(ordinal[item["name"]], MAX_PLAN_ITEMS - 1))
        for place, shape in enumerate(item.get("shapes", ())):
            shapes.append((index, ordinal[item["name"]], place, shape,
                           place == len(item["shapes"]) - 1))

    for sketch, number, place, shape, newest in shapes:
        row = enc.new_row("shape")
        enc.word(row, f"shape.shape={shape['shape']}")
        enc.word(row, f"shape.index={min(place, MAX_SHAPE_INDEX)}")
        if newest:
            enc.word(row, "shape.newest=True")      # dimension commands act on this one
        enc.link(row, "shape.sketch", sketch)
        enc.link(row, "shape.ordinal", min(number, MAX_PLAN_ITEMS - 1))
        for name in ("sides", "first"):
            if shape.get(name) is not None:
                enc.number(row, f"shape.{name}", shape[name])
        for name in DIMENSIONS:
            if name not in shape:
                continue
            if shape[name] is None:
                enc.word(row, f"shape.{name}=None")
            elif name in shape["fixed"]:
                enc.word(row, f"shape.fixed:{name}")
                _size(enc, row, f"shape.{name}", shape[name], plan)
            else:
                # Not given yet: the value is the rough size FreeCAD drew, so it is shown
                # but not compared with the plan.
                enc.word(row, f"shape.free:{name}")
                enc.number(row, f"shape.{name}", shape[name])

    row = enc.new_row("session")
    selection = session["selection"]
    _fields(enc, row, "session", "session",
            {name: value for name, value in session.items() if name != "selection"}, plan)
    if selection is None:
        enc.word(row, "session.selection=None")
    else:
        enc.word(row, f"sel.type={selection['type']}")
        rest = {name: value for name, value in selection.items() if name != "type"}
        if selection["type"] == "plane":            # a plane's name is a word ("XY")
            enc.word(row, f"sel.name={rest.pop('name')}")
        _fields(enc, row, "sel", "sel", rest, plan)
    if third:       # prev1 is the command carried out last, prev2 the one before it
        history = list(reversed(view.get("history", ())))
        for k in range(1, HISTORY + 1):
            if k > len(history):
                enc.word(row, f"prev{k}=None")
            else:
                enc.word(row, f"prev{k}.command={history[k - 1]['command']}")
                enc.word(row, f"prev{k}.status={history[k - 1]['status']}")

    row = enc.new_row("solid")
    if solid is None:
        enc.word(row, "solid=None")
    else:
        enc.word(row, f"solid.valid={solid['valid']}")
        enc.number(row, "solid.volume", solid["volume"])
        enc.number(row, "solid.solids", solid["solids"])
        low, high = solid["bbox"][:3], solid["bbox"][3:]
        for axis, a, b in zip("xyz", low, high, strict=True):
            _size(enc, row, f"solid.{axis}min", a, plan, of_document=False)
            _size(enc, row, f"solid.{axis}max", b, plan, of_document=False)
            _size(enc, row, f"solid.d{axis}", b - a, plan, of_document=False)

    for index, slot in sorted(plan.used):
        enc.word(plan_rows[index], f"plan.used:{slot}")

    for command in valid:
        row = enc.new_row("cand")
        enc.word(row, f"cand.command={command}")
        enc.candidates.append(command)
    return enc


def target_bits(candidates: list[str], label: list[dict]) -> int:
    """The teacher's set as bits: bit k is set when candidate k is an accepted command."""
    accepted = {target["command"] for target in label}
    missing = accepted - set(candidates)
    if missing or not accepted:
        raise ValueError(f"the label must be a non-empty subset of the valid commands: {missing}")
    return sum(1 << k for k, command in enumerate(candidates) if command in accepted)


# --- the way back: facts -> view ------------------------------------------------------------------
# Not used by the model. It proves that the facts hold every value of the view (the round-trip
# test, tests/test_s1_encoding.py), and it lets you print what the model is given.

def _value(text: str) -> object:
    return {"None": None, "True": True, "False": False}.get(text, text)


def facts_by_row(enc: Encoded) -> list[dict]:
    """Per row: its words, its numbers {field: value} and its links [(role, target)]."""
    rows = [{"words": [], "numbers": {}, "links": []} for _ in range(enc.n_rows)]
    for row, word in zip(enc.word_row, enc.word_id, strict=True):
        rows[row]["words"].append(ALL_WORDS[word])
    for row, name, value in zip(enc.num_row, enc.num_field, enc.num_value, strict=True):
        rows[row]["numbers"][NUM_FIELDS[name]] = value
    for row, role, target in zip(enc.link_row, enc.link_role, enc.link_target, strict=True):
        rows[row]["links"].append((ALL_ROLES[role][0], target))
    return rows


def _plain_fields(facts: dict, scope: str) -> dict:
    """The fields of one scope back from a row's facts (words, pointers and numbers)."""
    found = {}
    for word in facts["words"]:
        if word.startswith(f"{scope}.") and "=" in word:
            name, value = word[len(scope) + 1:].split("=")
            found[name] = _value(value)
    for role, target in facts["links"]:
        if role.startswith(f"{scope}.") and role.split(".")[1] in POINTER_FIELDS:
            found[role.split(".")[1]] = f"#{target}"
    return found


def decode(enc: Encoded) -> dict:
    """The view these facts were made from."""
    plan, items, valid, session, solid = [], [], [], None, None
    for facts in facts_by_row(enc):
        kind = facts["words"][0].removeprefix("row:")
        numbers = {name.split(".")[1]: value for name, value in facts["numbers"].items()}
        if kind == "plan":
            plan.append({"kind": _plain_fields(facts, "plan")["kind"], "slots": numbers})
        elif kind == "item":
            items.append({**_plain_fields(facts, "item"), **numbers})
            if items[-1]["type"] == "sketch":
                items[-1]["shapes"] = []
        elif kind == "shape":
            words = facts["words"]
            shape = {"shape": _plain_fields(facts, "shape")["shape"], **numbers,
                     "fixed": sorted(w.split(":")[1] for w in words if w.startswith("shape.fixed:"))}
            shape.update({w[6:-5]: None for w in words
                          if w.endswith("=None") and w.startswith("shape.")})
            sketch = dict(facts["links"])["shape.sketch"]
            items[sketch]["shapes"].append(shape)
        elif kind == "session":
            session = _plain_fields(facts, "session")
            if "selection" not in session:
                session["selection"] = {**_plain_fields(facts, "sel"), **numbers}
        elif kind == "solid":
            if "solid=None" not in facts["words"]:
                solid = {"volume": numbers["volume"], "solids": numbers["solids"],
                         "valid": _plain_fields(facts, "solid")["valid"],
                         "bbox": [numbers[f"{axis}{end}"] for end in ("min", "max")
                                  for axis in "xyz"]}
        else:
            valid.append(_plain_fields(facts, "cand")["command"])
    return {"plan": plan, "snapshot": {"items": items, "session": session, "solid": solid},
            "valid": valid}
