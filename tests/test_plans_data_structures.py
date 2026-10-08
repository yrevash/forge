"""Structures as plans: every kind in the registry converts and resolves to the same parts."""

import random

import pytest

from forge.generators.structures import KINDS
from forge.generators.structures.sampling import sample
from forge.plans_data.structures import Converter, compare, convert


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_a_kind_converts_to_a_plan_the_resolver_builds_the_same(kind: str) -> None:
    rng = random.Random(f"test:{kind}")
    converted = 0
    for _ in range(4):
        structure = sample(KINDS[kind], rng)
        made = convert(structure)
        if not made.ok:
            continue
        converted += 1
        assert made.plan.accepted
        assert made.resolution.complete
        assert len(made.resolution.bodies) == len(structure.solids)
        assert compare(structure, made.resolution, made.shift) == []
    assert converted >= 1, f"no {kind} converted"


def test_the_plain_chair_reads_like_the_chair_of_the_language_document() -> None:
    rng = random.Random("test:chair")
    structure = sample(KINDS["chair"], rng, level="a")
    made = convert(structure)
    assert made.ok
    lines = made.text.splitlines()
    assert lines[0].startswith("seat: box") and "above ground, top at height" in lines[0]
    assert any("under seat, down to ground" in line and "corners of seat" in line for line in lines)
    assert any(line.startswith("slats: ") and "evenly spaced along length" in line for line in lines)
    assert lines[-1] == "done"
    assert "above ground" not in "\n".join(lines[1:])       # no coordinates by the back door


def test_compare_notices_a_moved_part() -> None:
    structure = sample(KINDS["table"], random.Random("test:table"))
    made = convert(structure)
    assert made.ok
    assert compare(structure, made.resolution, (made.shift[0] + 5.0, made.shift[1])) != []


def test_no_part_is_named_like_a_copy_of_a_set() -> None:
    rng = random.Random("test:stool-three")
    for _ in range(30):
        structure = sample(KINDS["stool"], rng)
        if structure.choices.get("leg_count") == "three":
            converter = Converter(structure)
            made = converter.convert()
            assert made.ok, made.reason
            assert len(made.resolution.bodies) == len(structure.solids)
            return
    pytest.skip("no three-legged stool in thirty draws")
