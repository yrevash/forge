"""Repair data: verified parts turned into "wrong program + what is wrong + the fix".

Why: a model only uses error feedback well if it was trained on that exact
format. So before the
harness can retry with feedback, examples in that format have to exist.

How, in four steps, all done by code:

1. MUTATE   one realistic mistake is applied to the text of a verified program
            (a wrong number, a missing feature, a wrong count, a wrong
            operation, a misplaced feature, or something that stops it running).
2. EXECUTE  the mutated program runs in the sandbox. It is kept only if it is
            really wrong: it fails, or it builds a solid that measures
            differently from the correct part.
3. FEEDBACK a verifier message is written from the outcome and the wrong
            program's own text, using only what the harness will have at
            inference time (see `problems`).
4. ROW      wrong program, feedback and the correct program are written with
            full provenance. Prompts are attached later (see `repair_pairs`).

Run:    uv run python -m forge.data.mistakes --per-part 2 --limit-parts 300 --workers 2
Input:  data/generated/<family>.jsonl   (only families with a <family>.done marker)
Output: data/mistakes/<family>.jsonl           the repair rows
        data/mistakes/<family>.rejects.jsonl   every mutation not kept, with the reason
        runs/<date>-repair-mistakes/           config, command, metrics
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import random
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from forge.generators.base import BBOX_TOLERANCE_MM, VOLUME_RELATIVE_TOLERANCE, num
from forge.generators.prompts import numbers_in
from forge.runs import PROJECT_ROOT, log_metrics, start_run
from forge.sandbox import Sandbox

IN_DIR = PROJECT_ROOT / "data" / "generated"
OUT_DIR = PROJECT_ROOT / "data" / "mistakes"

# The feedback sentences below are fixed templates; rows say so.
FEEDBACK_MODEL = "template"


def mistake_version() -> str:
    """A hash of this file: changes whenever a mutation or a feedback sentence could."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 1. A program as text: the named parameters at the top, then the body
# ---------------------------------------------------------------------------

PARAM_LINE = re.compile(r"^([A-Za-z_]\w*) = (-?\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class Program:
    """A canonical program split into three runs of lines.

    `head` is everything before the parameters (the import), `names` and
    `values` are the "name = value" lines, `body` is the rest. Values stay as
    text, so an untouched line is written back exactly as it was read.
    """

    head: tuple[str, ...]
    names: tuple[str, ...]
    values: tuple[str, ...]
    body: tuple[str, ...]

    def text(self, values: tuple[str, ...] | None = None, body: tuple[str, ...] | None = None,
             names: tuple[str, ...] | None = None) -> str:
        names = self.names if names is None else names
        values = self.values if values is None else values
        body = self.body if body is None else body
        params = [f"{n} = {v}" for n, v in zip(names, values, strict=True)]
        return "\n".join([*self.head, *params, *body])

    def floats(self) -> list[int]:
        """Positions of the parameters that are lengths or angles written with a decimal point."""
        return [i for i, v in enumerate(self.values) if "." in v]

    def with_value(self, i: int, value: float) -> str | None:
        """The program with parameter `i` set to `value`; None if that changes nothing."""
        new = str(value) if isinstance(value, int) else num(value)
        if new == self.values[i] or float(new) == float(self.values[i]):
            return None
        return self.text(values=(*self.values[:i], new, *self.values[i + 1:]))

    def with_line(self, i: int, line: str | None) -> str:
        """The program with body line `i` replaced, or removed when `line` is None."""
        middle = () if line is None else (line,)
        return self.text(body=(*self.body[:i], *middle, *self.body[i + 1:]))


def parse_program(code: str) -> Program:
    """Split a program at its first block of "name = number" lines."""
    lines = code.split("\n")
    start = next((i for i, line in enumerate(lines) if PARAM_LINE.match(line)), len(lines))
    end = start
    while end < len(lines) and PARAM_LINE.match(lines[end]):
        end += 1
    pairs = [PARAM_LINE.match(line).groups() for line in lines[start:end]]
    return Program(tuple(lines[:start]), tuple(p[0] for p in pairs), tuple(p[1] for p in pairs),
                   tuple(lines[end:]))


# ---------------------------------------------------------------------------
# 2. The mistakes. Each operator makes ONE change, or returns None when the
#    program has nothing for it to change.
# ---------------------------------------------------------------------------

def _sites(program: Program, pattern: str) -> list[tuple[int, re.Match]]:
    """Every match of `pattern` in the body, as (line number, match)."""
    return [(i, m) for i, line in enumerate(program.body) for m in re.finditer(pattern, line)]


def _replace(program: Program, site: tuple[int, re.Match], new: str, group: int = 0) -> str:
    """The program with one matched piece of a body line swapped for `new`."""
    i, match = site
    line = program.body[i]
    return program.with_line(i, line[:match.start(group)] + new + line[match.end(group):])


def _pick(rng: random.Random, options: list) -> object | None:
    return rng.choice(options) if options else None


# --- a wrong number --------------------------------------------------------

def swap_digits(program: Program, rng: random.Random) -> str | None:
    """Two neighbouring digits change places: 52.0 becomes 25.0, 16.25 becomes 16.52."""
    options = []
    for i in program.floats():
        text = program.values[i]
        for j in range(len(text) - 1):
            a, b = text[j], text[j + 1]
            if a.isdigit() and b.isdigit() and a != b:
                swapped = float(text[:j] + b + a + text[j + 2:])
                if swapped > 0:
                    options.append((i, swapped))
    choice = _pick(rng, options)
    return program.with_value(*choice) if choice else None


def _grid_step(value: float) -> float:
    """The coarsest usual step this value sits on: 13.0 -> 1, 6.5 -> 0.5, 16.25 -> 0.25."""
    for step in (1.0, 0.5, 0.25, 0.1, 0.05):
        if abs(value / step - round(value / step)) < 1e-9:
            return step
    return 0.01


def off_by_step(program: Program, rng: random.Random) -> str | None:
    """One value is one grid step too big or too small: 6.5 becomes 6.0 or 7.0."""
    options = []
    for i in program.floats():
        value = float(program.values[i])
        step = _grid_step(abs(value))
        options += [(i, v) for v in (value - step, value + step) if v * value > 0]
    choice = _pick(rng, options)
    return program.with_value(*choice) if choice else None


def times_ten(program: Program, rng: random.Random) -> str | None:
    """A slipped decimal point: one value is ten times too big or too small."""
    options = [(i, float(program.values[i]) * factor)
               for i in program.floats() for factor in (10.0, 0.1)]
    choice = _pick(rng, options)
    return program.with_value(*choice) if choice else None


def other_parameter_value(program: Program, rng: random.Random) -> str | None:
    """One parameter gets the value that belongs to another one."""
    floats = program.floats()
    options = [(i, float(program.values[j])) for i in floats for j in floats
               if float(program.values[i]) != float(program.values[j])]
    choice = _pick(rng, options)
    return program.with_value(*choice) if choice else None


def diameter_radius_value(program: Program, rng: random.Random) -> str | None:
    """A diameter is given the radius (halved), or a radius is given the diameter (doubled)."""
    options = []
    for i in program.floats():
        if "diameter" in program.names[i]:
            options.append((i, float(program.values[i]) / 2))
        elif "radius" in program.names[i]:
            options.append((i, float(program.values[i]) * 2))
    choice = _pick(rng, options)
    return program.with_value(*choice) if choice else None


def diameter_used_as_radius(program: Program, rng: random.Random) -> str | None:
    """`.circle(hole_diameter / 2)` loses its `/ 2`, so the diameter is used as a radius."""
    site = _pick(rng, _sites(program, r"\b\w*diameter( / 2)\b"))
    return _replace(program, site, "", group=1) if site else None


# --- a missing feature or step ---------------------------------------------

CHAINED_CALL = re.compile(r"^\s+\.(\w+)\(.*\)$")
# Calls that add a feature to an existing solid, and the calls that only choose
# where the next feature goes. Dropping a feature takes its "where" lines with it.
FEATURE_CALLS = {"hole", "cboreHole", "cskHole", "fillet", "chamfer", "shell", "cutBlind",
                 "cutThruAll"}
SETUP_CALLS = {"faces", "edges", "vertices", "workplane", "rarray", "polarArray", "pushPoints",
               "center", "rect", "circle", "polygon", "slot2D"}


def _call_name(line: str) -> str | None:
    match = CHAINED_CALL.match(line)
    return match.group(1) if match else None


def drop_feature(program: Program, rng: random.Random) -> str | None:
    """A hole, fillet, chamfer, shell or blind cut is left out, with the lines that placed it."""
    ends = [i for i, line in enumerate(program.body) if _call_name(line) in FEATURE_CALLS]
    end = _pick(rng, ends)
    if end is None:
        return None
    start = end
    while start > 0 and _call_name(program.body[start - 1]) in SETUP_CALLS:
        start -= 1
    return program.text(body=(*program.body[:start], *program.body[end + 1:]))


def drop_boolean(program: Program, rng: random.Random) -> str | None:
    """One `.cut(x)` or `.union(x)` is left out, so that pocket, hole or boss never appears."""
    site = _pick(rng, _sites(program, r"\.(?:cut|union)\(\w+\)"))
    if site is None:
        return None
    i, match = site
    if program.body[i].strip() == match.group(0):  # the call has a line to itself
        return program.with_line(i, None)
    return _replace(program, site, "")


def drop_list_item(program: Program, rng: random.Random) -> str | None:
    """One point is left out of a list: a hole position, or a corner of an outline."""
    items = [i for i, line in enumerate(program.body) if re.match(r"^\s+\(.*\),$", line)]
    i = _pick(rng, items)
    return program.with_line(i, None) if i is not None else None


# --- a wrong count ---------------------------------------------------------

def wrong_count(program: Program, rng: random.Random) -> str | None:
    """A whole-number count (holes, sides) is one or two off."""
    options: list[tuple] = []
    for i, (name, value) in enumerate(zip(program.names, program.values, strict=True)):
        if "." not in value and "angle" not in name:
            options += [("param", i, int(value) + d) for d in (-2, -1, 1, 2) if int(value) + d >= 1]
    for site in _sites(program, r"\.polygon\((\d+)\b"):
        sides = int(site[1].group(1))
        options += [("body", site, sides + d) for d in (-2, -1, 1, 2) if sides + d >= 3]
    choice = _pick(rng, options)
    if choice is None:
        return None
    if choice[0] == "param":
        return program.with_value(choice[1], choice[2])
    return _replace(program, choice[1], str(choice[2]), group=1)


# --- a wrong operation -----------------------------------------------------

BOOLEAN_SWAPS = {"cut": "union", "union": "cut", "cutBlind": "extrude"}


def swap_boolean(program: Program, rng: random.Random) -> str | None:
    """Material is added where it should be removed, or removed where it should be added."""
    site = _pick(rng, _sites(program, r"\.(cutBlind|cut|union)\("))
    return _replace(program, site, BOOLEAN_SWAPS[site[1].group(1)], group=1) if site else None


def extrude_wrong_parameter(program: Program, rng: random.Random) -> str | None:
    """An extrusion takes its length from the wrong parameter."""
    value = dict(zip(program.names, program.values, strict=True))
    floats = [program.names[i] for i in program.floats()]
    options = [(site, other) for site in _sites(program, r"\.extrude\(([A-Za-z_]\w*)")
               if site[1].group(1) in floats
               for other in floats if float(value[other]) != float(value[site[1].group(1)])]
    choice = _pick(rng, options)
    return _replace(program, choice[0], choice[1], group=1) if choice else None


# --- a sign or position error ----------------------------------------------

def _split_arguments(text: str) -> list[str]:
    """Split "a, (b, c), d" at the commas that are not inside brackets."""
    parts, depth, current = [], 0, ""
    for ch in text:
        depth += ch in "(["
        depth -= ch in ")]"
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    return [*parts, current]


def _negate(expression: str) -> str:
    expression = expression.strip()
    if re.fullmatch(r"-[\w.]+", expression):
        return expression[1:]
    if re.fullmatch(r"[\w.]+", expression):
        return f"-{expression}"
    return f"-({expression})"


def flip_parameter_sign(program: Program, rng: random.Random) -> str | None:
    """A position parameter gets the wrong sign: boss_y = -16.25 becomes 16.25."""
    options = [i for i in program.floats()
               if float(program.values[i]) != 0
               and (program.values[i].startswith("-")
                    or re.search(r"(_x|_y|_z|offset)$", program.names[i]))]
    i = _pick(rng, options)
    return program.with_value(i, -float(program.values[i])) if i is not None else None


# Calls whose arguments place a feature: (how the line starts, how it ends).
OFFSET_CALLS = ((r"\s*\.workplane\(offset=", ")"), (r"\s*\.center\(", ")"),
                (r"\s*\.translate\(\(", "))"), (r"\s*\.cutBlind\(", ")"))


def flip_offset_sign(program: Program, rng: random.Random) -> str | None:
    """One offset in the body gets the wrong sign: a workplane offset, a centre, a translation."""
    options = []
    for i, line in enumerate(program.body):
        for start, end in OFFSET_CALLS:
            match = re.match(start, line)
            if match and line.endswith(end):
                arguments = _split_arguments(line[match.end():-len(end)])
                options += [(i, match.group(0), end, arguments, k)
                            for k, a in enumerate(arguments) if a.strip() != "0"]
    choice = _pick(rng, options)
    if choice is None:
        return None
    i, start, end, arguments, k = choice
    arguments = [a.strip() for a in arguments]
    arguments[k] = _negate(arguments[k])
    return program.with_line(i, start + ", ".join(arguments) + end)


def drop_offset(program: Program, rng: random.Random) -> str | None:
    """`.workplane(offset=...)` loses its offset, so the feature starts at the wrong height."""
    site = _pick(rng, _sites(program, r"\.workplane\((offset=.+)\)$"))
    return _replace(program, site, "", group=1) if site else None


def drop_centered(program: Program, rng: random.Random) -> str | None:
    """`centered=(True, True, False)` is left out, so the box no longer sits on the XY plane."""
    site = _pick(rng, _sites(program, r", centered=\([^)]*\)"))
    return _replace(program, site, "") if site else None


def wrong_plane(program: Program, rng: random.Random) -> str | None:
    """A sketch is drawn on the wrong plane, e.g. XZ instead of XY."""
    options = [(site, plane) for site in _sites(program, r'Workplane\("(XY|XZ|YZ)"\)')
               for plane in ("XY", "XZ", "YZ") if plane != site[1].group(1)]
    choice = _pick(rng, options)
    return _replace(program, choice[0], choice[1], group=1) if choice else None


# --- a program that does not run, or leaves no result -----------------------

def _misspellings(name: str) -> list[str]:
    """Wrong spellings of a method name, made by rule: wrong case, snake_case, a lost letter."""
    cased = []
    if any(ch.isupper() for ch in name):
        cased = [name.lower(), re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()]
    lost = [name[:i] + name[i + 1:] for i in range(1, len(name))]
    return [s for s in dict.fromkeys(cased + lost) if s != name]


def misspell_method(program: Program, rng: random.Random) -> str | None:
    """A CadQuery method name is misspelt: `cboreHole` as `cborehole`, `extrude` as `extrde`."""
    site = _pick(rng, _sites(program, r"\.([A-Za-z]\w*)\("))
    if site is None:
        return None
    name = site[1].group(1)
    spellings = _misspellings(name)
    cased = [s for s in spellings if len(s) >= len(name)]
    pool = cased if cased and rng.random() < 0.5 else spellings
    return _replace(program, site, rng.choice(pool), group=1) if pool else None


def undefined_name(program: Program, rng: random.Random) -> str | None:
    """A name is used that was never defined: its line is missing, or one use is misspelt."""
    used = [i for i, name in enumerate(program.names) if _sites(program, rf"\b{name}\b")]
    i = _pick(rng, used)
    if i is None:
        return None
    name = program.names[i]
    if rng.random() < 0.5 or name[:-1] in program.names or len(name) < 3:
        keep = [k for k in range(len(program.names)) if k != i]
        return program.text(names=tuple(program.names[k] for k in keep),
                            values=tuple(program.values[k] for k in keep))
    return _replace(program, rng.choice(_sites(program, rf"\b{name}\b")), name[:-1])


def missing_bracket(program: Program, rng: random.Random) -> str | None:
    """One closing bracket is missing."""
    site = _pick(rng, _sites(program, r"\)$"))
    return _replace(program, site, "") if site else None


def no_result(program: Program, rng: random.Random) -> str | None:
    """The finished part is stored under another name, so `result` is never set."""
    taken = set(re.findall(r"[A-Za-z_]\w*", program.text()))
    names = [n for n in ("part", "solid", "shape", "model", "body") if n not in taken]
    site = _pick(rng, _sites(program, r"^(result) = "))
    return _replace(program, site, rng.choice(names), group=1) if site and names else None


@dataclass(frozen=True)
class Operator:
    name: str
    group: str
    # What the mutated program must do to be kept: "builds" a (different) solid,
    # "fails" to produce one, or "any" of the two.
    expect: str
    apply: Callable[[Program, random.Random], str | None]


OPERATORS: dict[str, Operator] = {op.name: op for op in (
    Operator("swap_digits", "wrong_number", "any", swap_digits),
    Operator("off_by_step", "wrong_number", "any", off_by_step),
    Operator("times_ten", "wrong_number", "any", times_ten),
    Operator("other_parameter_value", "wrong_number", "any", other_parameter_value),
    Operator("diameter_radius_value", "wrong_number", "any", diameter_radius_value),
    Operator("diameter_used_as_radius", "wrong_number", "any", diameter_used_as_radius),
    Operator("drop_feature", "missing_feature", "builds", drop_feature),
    Operator("drop_boolean", "missing_feature", "builds", drop_boolean),
    Operator("drop_list_item", "missing_feature", "builds", drop_list_item),
    Operator("wrong_count", "wrong_count", "any", wrong_count),
    Operator("swap_boolean", "wrong_operation", "any", swap_boolean),
    Operator("extrude_wrong_parameter", "wrong_operation", "any", extrude_wrong_parameter),
    Operator("flip_parameter_sign", "position", "any", flip_parameter_sign),
    Operator("flip_offset_sign", "position", "any", flip_offset_sign),
    Operator("drop_offset", "position", "any", drop_offset),
    Operator("drop_centered", "position", "any", drop_centered),
    Operator("wrong_plane", "position", "any", wrong_plane),
    Operator("misspell_method", "does_not_run", "fails", misspell_method),
    Operator("undefined_name", "does_not_run", "fails", undefined_name),
    Operator("missing_bracket", "does_not_run", "fails", missing_bracket),
    Operator("no_result", "does_not_run", "fails", no_result),
)}
GROUPS = tuple(dict.fromkeys(op.group for op in OPERATORS.values()))


def mutations(part_id: str, code: str, seed: int = 0) -> list[tuple[str, str]]:
    """Every mistake that applies to this program, as (operator name, wrong program).

    Deterministic: the same part and seed give the same list in the same order.
    The order takes one operator from each group in turn (groups and operators
    shuffled per part), so the first few entries cover different kinds of
    mistake. Each operator has its own random stream, so adding an operator
    later does not change what the others produce.
    """
    program = parse_program(code)
    order = random.Random(f"{seed}:{part_id}:order")
    by_group = []
    for group in order.sample(GROUPS, len(GROUPS)):
        names = [op.name for op in OPERATORS.values() if op.group == group]
        by_group.append(order.sample(names, len(names)))
    found, seen = [], {code}
    for turn in range(max(len(names) for names in by_group)):
        for names in by_group:
            if turn >= len(names):
                continue
            wrong = OPERATORS[names[turn]].apply(
                program, random.Random(f"{seed}:{part_id}:{names[turn]}"))
            if wrong is not None and wrong not in seen:
                seen.add(wrong)
                found.append((names[turn], wrong))
    return found


# ---------------------------------------------------------------------------
# 3. Is the mutated program really wrong?
# ---------------------------------------------------------------------------

def same_solid(measure: dict, correct: dict) -> bool:
    """Do two measurements describe the same solid, within the generator tolerances?

    Compares volume, surface area, bounding box, round faces and the face and
    edge counts. Two different solids can still agree on all of these (a pocket
    mirrored to the other side of a symmetric block); such a mutation is thrown
    away, because no check we have could tell it from the correct part.
    """
    def close(a: float, b: float) -> bool:
        return abs(a - b) <= VOLUME_RELATIVE_TOLERANCE * abs(b)

    return (close(measure["volume"], correct["volume"])
            and close(measure["area"], correct["area"])
            and all(abs(a - b) <= BBOX_TOLERANCE_MM
                    for a, b in zip(measure["bbox"], correct["bbox"], strict=True))
            and measure["cylinders"] == correct["cylinders"]
            and measure["n_faces"] == correct["n_faces"]
            and measure["n_edges"] == correct["n_edges"])


# ---------------------------------------------------------------------------
# 4. Feedback: what a verifier can say WITHOUT knowing the correct program
# ---------------------------------------------------------------------------

# Plain size names that, on a correct part, are always a bounding-box extent,
# or stack along Z with another stated dimension: a boss of `boss_height` on a
# block of `height`, a screw of `length` under a head of `head_height`.
OVERALL_NAMES = ("length", "width", "height", "depth")
STACKING_ENDINGS = ("height", "length", "thickness")
# Diameters that are not a cylinder on the finished part, so cannot be checked:
# a countersink is a cone, a bolt circle is a layout, a polygon has corners.
UNMEASURABLE_DIAMETERS = ("countersink", "circle", "corner", "top_")
# A count is checked against the round faces of the diameter that shares its
# prefix: `hole_count` with `hole_diameter`, `polar_1_count` with `polar_1_hole_diameter`.
COUNTED_DIAMETERS = ("diameter", "hole_diameter")
MAX_ERROR_CHARS = 200


def _n(value: float) -> str:
    """A number as the feedback writes it: 3 decimals at most, no trailing zeros."""
    text = f"{round(float(value), 3):.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _near(value: float, others: list[float]) -> bool:
    return any(abs(value - other) <= BBOX_TOLERANCE_MM for other in others)


def program_text_problems(stated: dict[str, float | int], code: str) -> list[str]:
    """Stated dimensions that the candidate program itself sets to another value.

    Inputs, and nothing else: the dimensions the PROMPT states, and the text of
    the CANDIDATE program. Only its "name = number" lines are read, and only
    names the prompt states; a name the program does not define is left alone,
    because a program may be right under other names. Each sentence starts with
    "Program text:" so it can be told apart from what was measured.
    """
    program = parse_program(code)
    written = dict(zip(program.names, program.values, strict=True))
    return [f"Program text: `{name} = {written[name]}`, but the prompt states "
            f"{name.replace('_', ' ')} {_n(value)}."
            for name, value in stated.items()
            if name in written and abs(float(written[name]) - float(value)) > 1e-9]


def problems(stated: dict[str, float | int], outcome: dict, code: str | None = None) -> list[str]:
    """What is visibly wrong with a candidate program, one sentence per problem.

    Inputs, and nothing else:
      stated   the dimensions the PROMPT states, as {name: value}. These are the
               part's intended values; the prompt already gave them to the model.
               Leave out anything the prompt does not say.
      outcome  what `Sandbox.run` returned for the CANDIDATE program: its status,
               its error, or its measurements.
      code     the text of the CANDIDATE program (optional). When given, its own
               "name = number" lines are compared with the stated values
               (`program_text_problems`).

    The correct program and its measurements are never used, so the harness can
    call this at inference time and get the same sentences the model was trained
    on. Every number in the output is a stated value, a measurement of the
    candidate, or a number written in the candidate program.

    Checks on a part that built:
      - it is one valid solid, and it sits on the XY plane (our convention);
      - a stated length / width / height / depth is one of the bounding-box
        extents, or stacks on another stated height or length to reach the Z one;
      - a stated diameter exists as a round face, or as the X and Y extents,
        or (a ring of round section, which has no cylinder) adds up with another
        stated diameter to the X and Y extents;
      - a stated count matches the number of round faces of its stated diameter
        (`hole_count` with `hole_diameter`), unless another stated diameter has
        the same value.
    Other stated dimensions (a wall thickness, a pocket position) cannot be
    checked from measurements alone; an empty list means "nothing visibly
    wrong", not "correct".
    """
    written = program_text_problems(stated, code) if code is not None else []
    return _outcome_problems(stated, outcome, written)


def _outcome_problems(stated: dict, outcome: dict, written: list[str]) -> list[str]:
    """The sentences of `problems`; `written` are the program-text ones, placed before
    the closing "Measured:" line."""
    status = outcome["status"]
    if status == "error":
        message = str(outcome.get("error") or "").strip().split("\n")[0]
        line = outcome.get("line")
        located = re.search(r" \(<program>, line (\d+)\)$", message)  # syntax errors
        if located:
            message, line = message[:located.start()], int(located.group(1))
        where = f" on line {line}" if line else ""
        kind = outcome.get("error_type") or "Error"
        return [f"The program failed{where}: {kind}: {message[:MAX_ERROR_CHARS]}", *written]
    if status == "no_result":
        if "did not set" in str(outcome.get("error")):
            return ["The program ran but did not set `result`.", *written]
        return [f"The program ran but `result` is not a solid: {outcome.get('error')}.",
                *written]
    if status == "timeout":
        return ["The program did not finish within the time limit.", *written]
    if status != "ok":
        return ["The program crashed.", *written]

    measure = outcome["measure"]
    extents = [float(e) for e in measure["bbox"]]
    round_faces = {float(d): count for d, count in measure["cylinders"].items()}
    lengths = {k: float(v) for k, v in stated.items() if not isinstance(v, int)}
    found = []

    if measure.get("n_solids", 1) != 1:
        found.append(f"The result is {measure['n_solids']} separate solids, not one.")
    elif not measure.get("one_valid_solid", True):
        found.append("The result is not a valid solid.")
    lowest = measure.get("bbox_min", [0.0, 0.0, 0.0])[2]
    if abs(lowest) > BBOX_TOLERANCE_MM:
        found.append(f"The part does not sit on the XY plane: its lowest point is at "
                     f"z = {_n(lowest)}.")

    box = " x ".join(_n(e) for e in extents)
    for name, value in stated.items():
        label = name.replace("_", " ")
        if isinstance(value, int):
            prefix = name[:-len("count")] if name.endswith("_count") else None
            diameter = next((stated[prefix + d] for d in COUNTED_DIAMETERS
                             if prefix and prefix + d in stated), None)
            shared = diameter is not None and sum(
                k.endswith("diameter") and _near(float(v), [float(diameter)])
                for k, v in stated.items()) > 1
            if diameter is not None and not shared:
                faces = sum(c for d, c in round_faces.items() if _near(d, [float(diameter)]))
                # No face at all of that diameter is the diameter's problem, said below.
                if faces not in (0, value):
                    seen = "1 round face" if faces == 1 else f"{faces} round faces"
                    found.append(f"{label} {value} is stated, but {seen} of diameter "
                                 f"{_n(diameter)} {'was' if faces == 1 else 'were'} measured.")
        elif name in OVERALL_NAMES:
            stacked = [v for k, v in lengths.items()
                       if k != name and k.endswith(STACKING_ENDINGS)]
            if not _near(value, extents) and not _near(extents[2] - value, stacked):
                found.append(f"{label} {_n(value)} is stated, but the bounding box "
                             f"measures {box}.")
        elif (name == "diameter" or name.endswith("_diameter")) and not any(
                word in name for word in UNMEASURABLE_DIAMETERS):
            # A round outline about Z with no cylindrical wall (a cone's base)
            # still shows as equal X and Y extents.
            outline = _near(value, extents[:1]) and _near(value, extents[1:2])
            # A ring of round section lying flat (an O-ring) has no cylinder at
            # all: X = Y = inner + 2 x section, and Z = section.
            others = [v for k, v in lengths.items() if k != name and k.endswith("diameter")]
            ring = not round_faces and _near(extents[0], extents[1:2]) and any(
                (_near(value, extents[2:]) and _near(other + 2 * value, extents[:1]))
                or (_near(other, extents[2:]) and _near(value + 2 * other, extents[:1]))
                for other in others)
            if not _near(value, list(round_faces)) and not outline and not ring:
                found.append(f"{label} {_n(value)} is stated, but no round face of "
                             f"diameter {_n(value)} was measured.")

    found += written
    if found:
        faces = ", ".join(f"{_n(d)} ({c})" for d, c in sorted(round_faces.items())) or "none"
        found.append(f"Measured: bounding box {box} mm; round face diameters (count): {faces}.")
    return found


def feedback(stated: dict[str, float | int], outcome: dict, code: str | None = None) -> str:
    """The verifier message for a candidate program. Same inputs and rules as `problems`."""
    return "\n".join(problems(stated, outcome, code)) or "No problem found."


# ---------------------------------------------------------------------------
# 5. Pairing repairs with prompts (called by the dataset build)
# ---------------------------------------------------------------------------

REPAIR_PAIR_FIELDS = (
    "id", "repair_id", "part_id", "prompt", "wrong_code", "feedback", "code", "family",
    "params", "designation", "mistake", "mistake_group",
    # Provenance required on every training row:
    "source", "license", "generator_version", "mistake_version", "caption_style",
    "caption_variant", "caption_model", "feedback_model", "geom_fingerprint", "split")


def stated_params(prompt: str, params: dict) -> dict:
    """The parameters whose value appears as a number in the prompt text."""
    numbers = numbers_in(prompt)
    return {name: value for name, value in params.items()
            if any(abs(n - float(value)) < 1e-9 for n in numbers)}


def repair_pairs(repair: dict, prompts: list[dict], split: str) -> list[dict]:
    """Training rows for one repair: (prompt, wrong program, feedback) -> correct program.

    `repair`   a row of data/mistakes/<family>.jsonl.
    `prompts`  the verified prompts of the SAME part, as the dataset build makes
               them: dicts with `text`, `style`, `variant`, `model`.
    `split`    the split the build gave that part. A repair inherits it, so the
               wrong and correct program of a test part never reach training.

    The feedback is written again for each prompt, from only the dimensions that
    prompt states, the wrong program's outcome and the wrong program's own text. A prompt that says "M8 nut" does not state a thickness, so
    its feedback cannot mention one. If nothing is visibly wrong given what the
    prompt says, no row is made: the harness would not ask for a repair either.
    No row is made either when the same check would complain about the CORRECT
    part for that prompt (a prompt giving a block's height but not the height of
    the boss on top of it): feedback that is wrong about the right answer would
    teach the model to ignore feedback.
    All values are strings, like the pair files the build already writes.
    """
    rows = []
    correct = {"status": "ok", "measure": repair["correct_measure"]}
    for prompt in prompts:
        stated = stated_params(prompt["text"], repair["params"])
        found = problems(stated, repair["wrong_outcome"], repair["wrong_code"])
        if not found or problems(stated, correct, repair["correct_code"]):
            continue
        rows.append({
            "id": hashlib.sha1(
                f"{repair['id']}:{prompt['variant']}".encode()).hexdigest()[:16],
            "repair_id": repair["id"], "part_id": repair["part_id"], "prompt": prompt["text"],
            "wrong_code": repair["wrong_code"], "feedback": "\n".join(found),
            "code": repair["correct_code"], "family": repair["family"],
            "params": json.dumps(repair["params"]),
            "designation": json.dumps(repair["designation"]),
            "mistake": repair["mistake"], "mistake_group": repair["mistake_group"],
            "source": repair["source"], "license": repair["license"],
            "generator_version": repair["generator_version"],
            "mistake_version": repair["mistake_version"],
            "caption_style": prompt["style"], "caption_variant": prompt["variant"],
            "caption_model": prompt["model"], "feedback_model": repair["feedback_model"],
            "geom_fingerprint": repair["geom_fingerprint"], "split": split,
        })
    return rows


# ---------------------------------------------------------------------------
# 6. The job: mutate, execute, keep what is really wrong, log the rest
# ---------------------------------------------------------------------------

_local = threading.local()
_sandboxes: list[Sandbox] = []
_lock = threading.Lock()


def _sandbox(timeout: float) -> Sandbox:
    """One warm sandbox per worker thread."""
    if not hasattr(_local, "sandbox"):
        _local.sandbox = Sandbox(timeout=timeout)
        with _lock:
            _sandboxes.append(_local.sandbox)
    return _local.sandbox


def _outcome(reply: dict) -> dict:
    """The parts of a sandbox reply worth storing (and all that `problems` reads)."""
    kept = {k: reply[k] for k in ("status", "error_type", "error", "line") if k in reply}
    if reply["status"] == "ok":
        kept["measure"] = reply["measure"]
    return kept


def repairs_for_part(part: dict, sandbox: Sandbox, per_part: int, seed: int,
                     version: str) -> tuple[list[dict], list[dict]]:
    """Repair rows for one verified part, and the reject log for everything not kept.

    Mutations are tried in order until `per_part` of them are both really wrong
    and visibly wrong to `problems`. A mutation that is really wrong but that
    the feedback cannot see is still written, marked `detected: false`: it is no
    use for repair training, but it shows what the verifier misses.
    """
    def reject(operator: str | None, reason: str, detail: object = None) -> dict:
        return {"part_id": part["id"], "family": part["family"], "mistake": operator,
                "reason": reason, "detail": detail}

    original = sandbox.run(part["code"])
    if original["status"] != "ok" or not same_solid(original["measure"], part["measured"]):
        return [], [reject(None, "original_not_reproduced", original.get("error"))]
    # If the verifier complains about the CORRECT part, its complaints mean
    # nothing for this part, so only mutations that fail to run are usable.
    false_alarm = problems(part["params"], original, part["code"])

    rows, rejects, wanted = [], [], per_part
    if false_alarm:
        rejects.append(reject(None, "verifier_flags_correct_part", false_alarm[0]))
    for name, wrong in mutations(part["id"], part["code"], seed):
        if wanted == 0:
            break
        operator = OPERATORS[name]
        reply = sandbox.run(wrong)
        status = reply["status"]
        if status in ("timeout", "crash"):
            rejects.append(reject(name, status, reply.get("error")))  # not reproducible
        elif status == "ok" and operator.expect == "fails":
            rejects.append(reject(name, "built_but_should_fail"))
        elif status != "ok" and operator.expect == "builds":
            rejects.append(reject(name, "failed_but_should_build", reply.get("error")))
        elif status == "ok" and same_solid(reply["measure"], original["measure"]) and all(
                abs(a - b) <= BBOX_TOLERANCE_MM for a, b in
                zip(reply["measure"]["bbox_min"], original["measure"]["bbox_min"], strict=True)):
            rejects.append(reject(name, "same_solid"))
        elif status == "ok" and false_alarm:
            rejects.append(reject(name, "verifier_flags_correct_part"))
        else:
            outcome = _outcome(reply)
            found = problems(part["params"], outcome, wrong)
            rows.append({
                "id": hashlib.sha1(
                    f"{part['id']}:{name}:{wrong}".encode()).hexdigest()[:16],
                "part_id": part["id"], "family": part["family"],
                "mistake": name, "mistake_group": operator.group,
                "wrong_code": wrong, "feedback": "\n".join(found) or "No problem found.",
                "detected": bool(found), "correct_code": part["code"],
                "wrong_outcome": outcome, "correct_measure": original["measure"],
                "params": part["params"], "designation": part["designation"],
                "source": part["source"], "license": part["license"],
                "generator_version": part["generator_version"], "mistake_version": version,
                "feedback_model": FEEDBACK_MODEL, "geom_fingerprint": part["geom_fingerprint"],
            })
            wanted -= bool(found)
    return rows, rejects


def selection_point(part_id: str, seed: int) -> tuple[float, str]:
    """Where a part falls in its family's keep-order: a hash of its id and the seed.

    The dataset build uses the same order for `max_parts_per_family` (it keeps
    the parts with the smallest points). Sampling in that order here means the
    parts that get repairs are parts the build keeps, as long as both use the
    same seed and this job's --limit-parts is not above the build's cap.
    """
    text = f"{seed}:cap:{part_id}"
    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16) / 16**12, part_id


def sample_parts(path: Path, limit: int | None, seed: int) -> list[dict]:
    """Up to `limit` rows of a family file: those with the smallest `selection_point`.

    Read as a stream; only the chosen lines are held in memory.
    """
    chosen: list[tuple] = []  # a heap of (-point, id, line number, line): largest point on top
    with path.open() as f:
        for number, line in enumerate(f):
            if limit is None:
                chosen.append((0.0, "", number, line))
                continue
            point, part_id = selection_point(json.loads(line)["id"], seed)
            item = (-point, part_id, number, line)
            if len(chosen) < limit:
                heapq.heappush(chosen, item)
            elif limit > 0 and item > chosen[0]:
                heapq.heapreplace(chosen, item)
    return [json.loads(line) for _, _, _, line in sorted(chosen, key=lambda item: item[2])]


def finished_families(in_dir: Path = IN_DIR) -> Iterator[str]:
    """Families whose generation is complete (generate.py leaves a .done marker)."""
    for marker in sorted(in_dir.glob("*.done")):
        if (in_dir / f"{marker.stem}.jsonl").exists():
            yield marker.stem


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--per-part", type=int, default=2,
                        help="repairs wanted per part that the feedback can see")
    parser.add_argument("--limit-parts", type=int, default=None, help="parts per family")
    parser.add_argument("--families", nargs="*", default=None)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--in-dir", type=Path, default=IN_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR,
                        help="the dataset build reads data/mistakes/; use another folder "
                             "for a trial that must not enter the dataset")
    args = parser.parse_args()
    in_dir, out_dir = args.in_dir, args.out_dir

    finished = list(finished_families(in_dir))
    families = finished if args.families is None else args.families
    version = mistake_version()
    out_dir.mkdir(parents=True, exist_ok=True)
    run_dir = start_run("repair-mistakes", {
        "in_dir": str(in_dir), "out_dir": str(out_dir),
        "per_part": args.per_part, "limit_parts": args.limit_parts, "families": families,
        "seed": args.seed, "timeout": args.timeout, "workers": args.workers,
        "mistake_version": version,
    })

    counts: dict[str, Counter] = {name: Counter() for name in OPERATORS}
    totals: Counter = Counter()

    def work(part: dict) -> tuple[list[dict], list[dict]]:
        return repairs_for_part(part, _sandbox(args.timeout), args.per_part, args.seed, version)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for family in families:
            if family not in finished:
                print(f"{family:24s} skipped: no {family}.done in {in_dir} yet", flush=True)
                continue
            started = time.time()
            source_stamp = (in_dir / f"{family}.done").read_text()
            stamp = (f"{version} per_part={args.per_part} limit_parts={args.limit_parts} "
                     f"seed={args.seed} from={source_stamp}")
            marker = out_dir / f"{family}.done"
            out_path = out_dir / f"{family}.jsonl"
            rejects_path = out_dir / f"{family}.rejects.jsonl"
            if marker.exists() and marker.read_text() == stamp:
                # Already done with exactly these settings: count it from the files.
                rows = [json.loads(line) for line in out_path.open()]
                rejects = [json.loads(line) for line in rejects_path.open()]
                note = "(already done, skipped)"
            else:
                marker.unlink(missing_ok=True)
                rows, rejects = [], []
                parts = sample_parts(in_dir / f"{family}.jsonl", args.limit_parts, args.seed)
                for part_rows, part_rejects in pool.map(work, parts):
                    rows += part_rows
                    rejects += part_rejects
                out_path.write_text("".join(json.dumps(r) + "\n" for r in rows))
                rejects_path.write_text("".join(json.dumps(r) + "\n" for r in rejects))
                marker.write_text(stamp)
                note = f"{round(time.time() - started, 1)}s"

            family_counts: Counter = Counter()
            for row in rows:
                counts[row["mistake"]]["kept"] += 1
                counts[row["mistake"]]["kept_detected"] += row["detected"]
                family_counts["kept"] += 1
                family_counts["kept_detected"] += row["detected"]
            for item in rejects:
                if item["mistake"] is None:
                    family_counts[item["reason"]] += 1
                else:
                    counts[item["mistake"]][item["reason"]] += 1
                    family_counts["discarded"] += 1
            totals.update(family_counts)
            print(f"{family:24s} {family_counts['kept']:6d} kept "
                  f"({family_counts['kept_detected']} the feedback can see) "
                  f"{family_counts['discarded']:6d} discarded  {note}", flush=True)
            log_metrics(run_dir, family=family, **family_counts)

    for sandbox in _sandboxes:
        sandbox.close()

    reasons = ("same_solid", "failed_but_should_build", "built_but_should_fail",
               "verifier_flags_correct_part", "timeout", "crash")
    print(f"\n{'mistake':26s}{'generated':>10s}{'kept':>7s}{'seen':>7s}{'same':>7s}{'other':>7s}")
    for name, c in counts.items():
        discarded = sum(c[r] for r in reasons)
        generated = c["kept"] + discarded
        print(f"{name:26s}{generated:10d}{c['kept']:7d}{c['kept_detected']:7d}"
              f"{c['same_solid']:7d}{discarded - c['same_solid']:7d}")
        log_metrics(run_dir, mistake=name, group=OPERATORS[name].group, generated=generated,
                    kept=c["kept"], kept_detected=c["kept_detected"],
                    **{f"discarded_{r}": c[r] for r in reasons})
    print("generated = programs executed; kept = really wrong; seen = kept and the feedback "
          "names a problem;\nsame = discarded, measures the same as the correct part; "
          "other = discarded for another reason (see the rejects files)")
    print(f"\ntotal: {totals['kept']} kept, {totals['kept_detected']} usable for repair training, "
          f"{totals['discarded']} discarded; parts whose correct program the verifier "
          f"flags: {totals['verifier_flags_correct_part']}; parts not reproduced: "
          f"{totals['original_not_reproduced']} (mistake version {version})")
    log_metrics(run_dir, final=True, **totals)


if __name__ == "__main__":
    main()
