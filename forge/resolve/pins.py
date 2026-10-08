"""Pins: how a line fixes where the new part's frame sits, one axis at a time.

"A placement is this anchor of the new part meets that anchor of the target, plus
offsets." Along each axis that comes down to one of three statements:

    Pin(-1, v)   the part's low face  (left / front / bottom) is at v
    Pin(+1, v)   the part's high face (right / back / top) is at v
    Pin( 0, v)   the part's middle is at v

The placement writes the first pins, each alignment adds more, in the order written.
`solve` then turns the pins of one axis into (low, high):

  * size known: ONE pin decides. The last stated pin wins ("when two alignments move
    the part in the same direction, the later one wins", section 6); the placement's
    own "centred on the face" is weak and loses to anything stated.
  * size is `rest`: the last low pin and the last high pin give both ends, and the
    size is what lies between them.
  * a HARD pin (`on ground`, `down to ground`) cannot be overridden: a stated pin that
    disagrees with it is `does not fit`.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.resolve.shapes import DoesNotFit
from forge.resolve.space import TOL

AXIS_WORD = ("length", "depth", "height")


@dataclass
class Pin:
    side: int               # -1 low face, +1 high face, 0 middle
    value: float
    strong: bool = True     # False: only "centred unless the line says otherwise"
    hard: bool = False      # the ground: cannot be overridden


def _last(pins: list[Pin]) -> Pin:
    """The pin that decides: the ground, else the last stated one, else "centred"."""
    for wanted in (lambda p: p.hard, lambda p: p.strong, lambda p: p.side == 0):
        found = [pin for pin in pins if wanted(pin)]
        if found:
            return found[-1]
    return pins[-1]


def _where(pin: Pin, low: float, high: float) -> float:
    return low if pin.side < 0 else high if pin.side > 0 else (low + high) / 2


def solve(pins: list[Pin], size: float | None, axis: int) -> tuple[float, float]:
    """The low and high end of the frame along one axis."""
    word = AXIS_WORD[axis]
    if size is None:
        lows = [pin for pin in pins if pin.side < 0]
        highs = [pin for pin in pins if pin.side > 0]
        if not lows or not highs:
            raise DoesNotFit(f"`rest` along {word}: the line does not fix both ends")
        low, high = _last(lows).value, _last(highs).value
        if high - low <= TOL:
            raise DoesNotFit(f"nothing is left along {word}: {high - low:g}")
        stated = [pin for pin in lows + highs if pin.strong]
    else:
        if not pins:
            raise DoesNotFit(f"nothing says where the part sits along {word}")
        chosen = _last(pins)
        low = chosen.value - {-1: 0.0, 0: size / 2, 1: size}[chosen.side]
        high = low + size
        stated = [pin for pin in pins if pin.strong]
    if any(pin.hard for pin in pins):
        for pin in stated:
            if abs(_where(pin, low, high) - pin.value) > TOL:
                raise DoesNotFit(f"along {word} the part must sit on the ground and also at "
                                 f"{pin.value:g}; its size does not allow both")
    return low, high
