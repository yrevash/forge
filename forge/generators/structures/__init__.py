"""Structures: several parts placed together. `KINDS` is the registry."""

from forge.generators.structures.kinds import (
    bed,
    bench,
    cabinet,
    cart,
    chair,
    chest,
    crate,
    desk,
    fence,
    frame,
    ladder,
    pallet,
    shelf,
    standoffs,
    stool,
    table,
    toolbox,
    workbench,
)

# The first eight are the kinds of the first trial run; the rest were added on 6 Oct 2026.
KINDS = {kind.NAME: kind for kind in (
    chair, stool, bench, table, desk, shelf, frame, crate,
    bed, cabinet, chest, workbench, ladder, pallet, cart, standoffs, fence, toolbox)}
