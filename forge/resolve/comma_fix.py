"""One mechanical repair for plans written against Draft 1: commas between sizes become `by`.

    seat: cylinder 320, 30, on ground      ->      seat: cylinder 320 by 30, on ground

Draft 1 showed sizes joined with commas; Draft 2 says `by`. This is applied IN MEMORY
only, by `build.py --fix-commas`, to measure what the old tuning plans would do. It
never rewrites a plan file, and it changes nothing else about a line.
"""

from __future__ import annotations

import re

from forge.plan import clauses
from forge.plan import grammar as g

_TURNING = re.compile(r"\b(standing|lying|pointing|flat)\b.*$")


def fix_line(raw: str) -> str:
    cleaned = g.clean(raw)
    if ":" not in cleaned or cleaned.startswith("on "):
        return raw                          # a feature or control line: sizes are slots there
    name, _, body = raw.partition(":")
    pieces = [piece.strip() for piece in body.split(",")]
    head, rest = pieces[0], pieces[1:]
    # Pieces that are bare sizes (perhaps followed by turning words) belong to the shape.
    while rest and clauses.looks_like_a_size(_TURNING.sub("", g.clean(rest[0])).strip() or "?"):
        head, rest = f"{head} by {rest[0]}", rest[1:]
    return f"{name}: " + ", ".join([head, *rest])


def fix_commas(text: str) -> str:
    return "\n".join(fix_line(line) if line.strip() else line for line in text.splitlines())
