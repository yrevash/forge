"""Every family must build exactly the part its own arithmetic says it builds."""

import random

import pytest

from forge.generators import FAMILIES, tables
from forge.generators.base import check
from forge.sandbox import Sandbox


@pytest.fixture(scope="module")
def sandbox():
    with Sandbox(timeout=30) as sb:
        yield sb


def _parts(family, n=8):
    rng = random.Random(1234)
    return family.standard_parts()[:n] + [family.sample(rng) for _ in range(n)]


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_family_builds_what_it_claims(name, sandbox):
    for part in _parts(FAMILIES[name]):
        reply = sandbox.run(part.code)
        assert reply["status"] == "ok", (part.params, reply)
        assert check(part, reply["measure"]) == [], part.params


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_sampling_is_reproducible(name):
    a = [FAMILIES[name].sample(random.Random(7)).params for _ in range(3)]
    b = [FAMILIES[name].sample(random.Random(7)).params for _ in range(3)]
    assert a == b


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_program_is_canonical(name):
    part = FAMILIES[name].sample(random.Random(0))
    assert part.code.startswith("import cadquery as cq\n")
    assert "result = " in part.code
    for parameter in part.params:
        assert f"\n{parameter} = " in part.code  # every dimension is a named parameter


def test_check_catches_a_wrong_part(sandbox):
    """The verifier itself must fail when the part is wrong (a mutation test)."""
    part = FAMILIES["hex_nut"].standard_parts()[0]
    wrong = part.code.replace("thickness = ", "thickness = 0.5 + ")
    reply = sandbox.run(wrong)
    assert reply["status"] == "ok"
    problems = check(part, reply["measure"])
    assert any("bbox Z" in p for p in problems)
    assert any("volume" in p for p in problems)


def test_hex_nut_values_come_from_the_table():
    rows = {r["size"]: r for r in tables.load("hex_nut_parameters")["iso4032"]}
    row = rows["M8-1.25"]
    part = next(p for p in FAMILIES["hex_nut"].standard_parts()
                if p.designation == {"standard": "iso4032", "size": "M8"})
    assert part.params == {"across_flats": row["s"], "thickness": row["m"], "bore_diameter": 8.0}


def test_thread_diameter():
    assert tables.thread_diameter("M8-1.25") == 8.0
    assert tables.thread_diameter("M1.6-0.35") == 1.6
    assert tables.thread_diameter("M10") == 10.0
    with pytest.raises(ValueError):
        tables.thread_diameter("#10-32")
