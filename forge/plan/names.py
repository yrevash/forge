"""Finding the part a name points at, with the one tolerance about names: plural or singular.

"under leg" finds a part called "legs", and "seat's" finds "seats", as long as exactly
one part fits. Two parts that both fit ("leg" and "legs" both exist and the plan says
"legz") is not guessed at.
"""

from __future__ import annotations

from collections.abc import Iterable


def singular(name: str) -> str:
    """Drop one trailing "s" from the last word: "back posts" -> "back post"."""
    return name[:-1] if len(name) > 1 and name.endswith("s") else name


def find(written: str, names: Iterable[str]) -> str | None:
    """The name in `names` that `written` points at, or None."""
    names = list(names)
    if written in names:
        return written
    close = [name for name in names if singular(name) == singular(written)]
    return close[0] if len(close) == 1 else None
