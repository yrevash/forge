"""Spec-style captions, built by code from a part's own names and numbers.

These are the plainest possible prompts: the family name and each parameter
name with underscores turned into spaces, followed by its value. Nothing here
is free-written wording. Varied, human-sounding phrasing is a separate step
(see prompts.py).

Every caption is checked before it is used: each number in the text must be a
number that belongs to the part.
"""

from __future__ import annotations

import re

NUMBER = re.compile(r"(?<![A-Za-z])-?\d+(?:\.\d+)?")
STANDARD = re.compile(r"([a-z]+)(\d+)")


def number_text(value: float) -> str:
    """13.0 -> '13', 6.8 -> '6.8'. One spelling per number, used everywhere."""
    return f"{value:g}"


def standard_name(standard: str) -> str:
    """'iso4032' -> 'ISO 4032', 'din931' -> 'DIN 931'."""
    match = STANDARD.fullmatch(standard)
    return f"{match.group(1).upper()} {match.group(2)}" if match else standard.upper()


def spec_captions(family: str, params: dict, designation: dict | None) -> list[dict]:
    """One caption that lists every dimension, plus one by designation for standard parts."""
    name = family.replace("_", " ")
    dimensions = ", ".join(
        f"{key.replace('_', ' ')} {number_text(value)}"
        + ("" if isinstance(value, int) else " deg" if "angle" in key else " mm")
        for key, value in params.items()
    )
    captions = [{"style": "spec", "variant": "dimensions", "model": "template",
                 "text": f"{name}, {dimensions}"}]
    if designation:
        size = designation["size"]
        if "length" in designation:
            size += f" x {designation['length']}"
        captions.append({"style": "spec", "variant": "designation", "model": "template",
                         "text": f"{name} {standard_name(designation['standard'])} {size}"})
    return captions


def caption_problems(text: str, params: dict, designation: dict | None) -> list[str]:
    """Numbers in the caption that do not belong to the part. Empty means verified."""
    allowed = {number_text(v) for v in params.values()}
    for key in params:
        allowed.update(re.findall(r"\d+", key))  # index in a name, e.g. "step 2 diameter"
    for value in (designation or {}).values():
        allowed.update(NUMBER.findall(value))
        allowed.update(re.findall(r"\d+(?:\.\d+)?", value))  # 'M8', '8x7x40', 'iso4032'
    return [f"number {n} is not a dimension of this part"
            for n in NUMBER.findall(text) if n not in allowed]
