"""One speed-up for generation: resolver snapshots share bodies instead of copying them.

The resolver copies its whole state before every line (`copy.deepcopy`), so that a
rejected line changes nothing. Most of that cost is copying `Body` objects, which are
never changed after they are made (forge/resolve/bodies.py says so: a feature makes a
new Body with `replace`). `share_bodies()` tells `deepcopy` to reuse a Body instead of
copying it. It is switched on only inside the generation workers of this package; the
audit runs without it and re-derives the same results, which is the check that it is safe.
"""

from __future__ import annotations

from forge.resolve.bodies import Body


def _same(self: Body, memo: dict) -> Body:
    return self


def share_bodies() -> None:
    Body.__deepcopy__ = _same       # type: ignore[attr-defined]


def copy_bodies() -> None:
    """Back to the resolver as written: every snapshot copies every body."""
    if "__deepcopy__" in vars(Body):
        del Body.__deepcopy__
