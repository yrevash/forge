"""Prompts with their mentions, and the plan: which mention fills which slot of which step.

A MENTION is one number as it stands in the sentence:

    {"start": 14, "end": 16, "text": "60", "value": 60.0,
     "param": "base_length", "fills": [[0, "length"]]}

`fills` lists the (step number, slot) pairs this mention fills. It is a list
because one mention may fill two slots; in version 1 every composed prompt says
every parameter exactly once, so it holds one pair, or none for a number that
fills nothing (the "2" in the spec caption's "hole 2 diameter 6 mm").

Nothing here searches a text for a value. For natural prompts the builder in
forge/generators/prompts.py records each number as it writes it. For the spec
caption this file writes the caption itself, piece by piece, and checks the
result is the caption `forge.generators.captions` gives.

The PLAN is the part's steps (steps.py) with `sources` filled in: for every
slot, the index of the mention that fills it. It is what the teacher follows.
"""

from __future__ import annotations

import random
import re

from forge.generators.captions import number_text, spec_captions
from forge.generators.prompts import composed_prompt_with_mentions
from forge.system1.steps import Number, Step, slot_params

# Prompt variants in the order sessions use them: `--prompts-per-part 2` takes the first
# two. Names and random seeds are those of forge.generators.prompts.natural_prompts, so
# "request-0" here is the very text dataset v1 holds for the part. The spec caption comes
# last: it is the least like something a person would type.
VARIANTS = ("request-0", "compact-0", "request-1", "compact-1", "request-2", "spec")


class Unaligned(ValueError):
    """A slot of the plan has no mention: the prompt does not state that number."""


def natural_prompt(family: str, params: dict[str, Number], part_id: str,
                   variant: str) -> tuple[str, list[dict]]:
    """A natural prompt ('request-0', 'compact-1', ...) and the mentions its builder recorded."""
    level, number = variant.split("-")
    rng = random.Random(f"{part_id}:{level}:{number}")    # the seed natural_prompts uses
    return composed_prompt_with_mentions(level, family, params, rng)


def spec_prompt(family: str, params: dict[str, Number]) -> tuple[str, list[dict]]:
    """The canonical spec caption and its mentions.

    The caption lists every parameter in order as "<name words> <value> <unit>".
    It is written here one piece at a time so that each number's position is
    known as it is written. A feature's number inside a name ("hole 2 diameter")
    is a number in the text too, so it is a mention, with no parameter.
    """
    text = family.replace("_", " ") + ", "
    mentions = []
    for position, (key, value) in enumerate(params.items()):
        if position:
            text += ", "
        words = key.replace("_", " ")
        for index in re.finditer(r"\d+", words):
            mentions.append({"start": len(text) + index.start(), "end": len(text) + index.end(),
                             "text": index.group(), "value": float(index.group()), "param": None})
        text += words + " "
        number = number_text(value)
        mentions.append({"start": len(text), "end": len(text) + len(number), "text": number,
                         "value": float(value), "param": key})
        text += number + ("" if isinstance(value, int) else " deg" if "angle" in key else " mm")
    canonical = spec_captions(family, params, None)[0]["text"]
    if text != canonical:
        raise AssertionError(f"spec caption drifted from captions.py:\n{text}\n{canonical}")
    return text, mentions


def align(family: str, params: dict[str, Number], mentions: list[dict]) -> list[Step]:
    """Fill in each mention's `fills`, and return the plan: the steps with their sources.

    Raises `Unaligned` if some slot has no mention at all.
    """
    walk = slot_params(family, params)
    where = {name: (index, slot) for index, (_, slots) in enumerate(walk)
             for slot, name in slots.items()}
    sources: list[dict[str, int]] = [{} for _ in walk]
    for number, mention in enumerate(mentions):
        mention["fills"] = []
        if mention["param"] is None:
            continue
        index, slot = where[mention["param"]]
        if mention["value"] != float(params[mention["param"]]):
            raise AssertionError(f"mention {mention} does not carry its parameter's value")
        mention["fills"].append([index, slot])
        # If a number were said twice, the first mention is the one the plan points at.
        sources[index].setdefault(slot, number)
    missing = [name for name, (index, slot) in where.items() if slot not in sources[index]]
    if missing:
        raise Unaligned(f"no mention for {missing}")
    # Sources are listed in the step's own slot order, whatever order the sentence used.
    return [Step(kind, {slot: params[name] for slot, name in slots.items()},
                 {slot: sources[index][slot] for slot in slots})
            for index, (kind, slots) in enumerate(walk)]


def prompt_and_plan(family: str, params: dict[str, Number], part_id: str,
                    variant: str) -> tuple[str, list[dict], list[Step]]:
    """One prompt of a part: its text, its mentions, and the plan aligned to them."""
    if variant == "spec":
        text, mentions = spec_prompt(family, params)
    else:
        text, mentions = natural_prompt(family, params, part_id, variant)
    return text, mentions, align(family, params, mentions)
