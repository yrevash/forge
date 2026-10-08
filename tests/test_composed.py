"""Composed parts: random structure, exact arithmetic, feature-by-feature prompts."""

import math
import random
import re

import pytest

from forge.generators import FAMILIES
from forge.generators.base import check
from forge.generators.families.composed import TREATMENTS, _bare_base, _treated_volume
from forge.generators.prompts import composed_features, natural_prompts, prompt_problems
from forge.sandbox import Sandbox

COMPOSED = sorted(name for name in FAMILIES if name.startswith("composed_"))
FEATURE_KEY = re.compile(r"([a-z_]+?)_(\d+)_([a-z_]+)")


def _treatment(part):
    return next((t for t in TREATMENTS["block"] if f"base_{t}" in part.params), None)


def test_there_is_a_family_per_base():
    assert COMPOSED == ["composed_block", "composed_cylinder", "composed_hex", "composed_ring"]


# --- the closed-form volumes, each against a second, independent derivation ---------------

def test_chamfered_block_matches_the_prismatoid_formula():
    base = _bare_base(random.Random(1), "block")
    length, width, height = (base.params[k] for k in ("base_length", "base_width", "base_height"))
    c = 1.5
    layer = c / 6 * (length * width + (length - 2 * c) * (width - 2 * c)
                     + 4 * (length - c) * (width - c))
    assert _treated_volume(base, "top_chamfer", c) == pytest.approx(
        length * width * (height - c) + layer, rel=1e-12)


def test_filleted_cylinder_matches_pappus():
    """The ring cut away by a fillet is a corner area revolved about the axis (Pappus)."""
    base = _bare_base(random.Random(2), "cylinder")
    radius, height, r = base.params["base_diameter"] / 2, base.params["base_height"], 2.0
    corner_area = r * r * (1 - math.pi / 4)
    centroid = radius - r + r / (6 * (1 - math.pi / 4))
    expected = math.pi * radius**2 * height - 2 * math.pi * centroid * corner_area
    assert _treated_volume(base, "top_fillet_radius", r) == pytest.approx(expected, rel=1e-12)


def test_chamfered_ring_matches_pappus():
    """Two triangles revolved: one on the outer edge, one on the inner edge."""
    base = _bare_base(random.Random(3), "ring")
    outer, inner = base.params["base_outer_diameter"] / 2, base.params["base_inner_diameter"] / 2
    height, c = base.params["base_height"], 1.0
    removed = 2 * math.pi * (c * c / 2) * ((outer - c / 3) + (inner + c / 3))
    expected = math.pi * (outer**2 - inner**2) * height - removed
    assert _treated_volume(base, "top_chamfer", c) == pytest.approx(expected, rel=1e-12)


def test_shelled_hexagon_is_outer_minus_inner_hexagon():
    base = _bare_base(random.Random(4), "hex")
    flats, height, t = base.params["base_across_flats"], base.params["base_height"], 2.0
    outer_area = math.sqrt(3) / 2 * flats**2
    inner_area = math.sqrt(3) / 2 * (flats - 2 * t) ** 2
    expected = outer_area * height - inner_area * (height - t)
    assert _treated_volume(base, "wall_thickness", t) == pytest.approx(expected, rel=1e-12)


# --- the kernel agrees, for every base with every edge treatment --------------------------

def test_every_base_and_treatment_builds_what_it_claims():
    wanted = {(f"composed_{base}", t) for base, ts in TREATMENTS.items() for t in (*ts, None)}
    rng = random.Random(21)
    chosen = {}
    for _ in range(4000):
        for name in COMPOSED:
            part = FAMILIES[name].sample(rng)
            chosen.setdefault((name, _treatment(part)), part)
        if set(chosen) == wanted:
            break
    assert set(chosen) == wanted
    with Sandbox(timeout=30) as sandbox:
        for key, part in chosen.items():
            reply = sandbox.run(part.code)
            assert reply["status"] == "ok", (key, reply)
            assert check(part, reply["measure"]) == [], (key, part.params)


# --- structure ----------------------------------------------------------------------------

def _structure(part):
    kinds = {}
    for key in part.params:
        match = FEATURE_KEY.fullmatch(key)
        if match and not key.startswith("base_"):
            kinds[int(match.group(2))] = match.group(1)
    return kinds


@pytest.mark.parametrize("name", COMPOSED)
def test_features_are_numbered_in_build_order(name):
    rng = random.Random(8)
    for _ in range(100):
        part = FAMILIES[name].sample(rng)
        kinds = _structure(part)
        assert sorted(kinds) == list(range(1, len(kinds) + 1))
        assert 1 <= len(kinds) + (_treatment(part) is not None) <= 5
        # The program builds and combines the features in that same order.
        positions = [part.code.index(f"({kinds[i]}_{i})") for i in sorted(kinds)]
        assert positions == sorted(positions)
        for key in part.params:
            assert key.startswith("base_") or FEATURE_KEY.fullmatch(key), key


def test_structure_varies_widely():
    rng = random.Random(9)
    seen, sizes, repeated = set(), set(), 0
    for i in range(1200):
        part = FAMILIES[COMPOSED[i % 4]].sample(rng)
        kinds = _structure(part)
        sequence = tuple(kinds[k] for k in sorted(kinds))
        seen.add((part.family, _treatment(part), sequence))
        sizes.add(len(sequence))
        repeated += len(set(sequence)) < len(sequence)
    assert len(seen) > 600          # far more structures than a list of recipes could hold
    assert sizes == {1, 2, 3, 4, 5}
    assert repeated > 100           # the same kind twice on one part is common


def test_a_shelled_base_takes_only_bosses():
    rng = random.Random(10)
    shelled = 0
    for i in range(1500):
        part = FAMILIES[COMPOSED[i % 3]].sample(rng)   # the ring is never shelled
        if "base_wall_thickness" in part.params:
            shelled += 1
            assert set(_structure(part).values()) <= {"boss", "pad", "boss_pair"}
    assert shelled > 20


# --- prompts ------------------------------------------------------------------------------

@pytest.mark.parametrize("name", COMPOSED)
def test_prompts_list_features_and_are_complete(name):
    rng = random.Random(12)
    texts = set()
    for _ in range(150):
        part = FAMILIES[name].sample(rng)
        prompts = natural_prompts(part.family, part.params, None, part.id,
                                  {"compact": 3, "request": 3})
        assert prompts and all(p["model"] == "template" for p in prompts)
        for prompt in prompts:
            assert prompt_problems(prompt["text"], part.params, None, complete=True) == [], \
                prompt["text"]
            # The feature-by-feature builder never falls back to "hole 1 diameter 6".
            assert not re.search(r"\b(hole|boss|pad|pocket|slot|polar|row|pair) \d (diameter|height|depth|count)",
                                 prompt["text"].lower()), prompt["text"]
            texts.add(prompt["text"])
    assert len(texts) > 800


def test_parameters_read_back_as_a_feature_list():
    params = {"base_diameter": 50.0, "base_height": 20.0, "base_top_chamfer": 1.0,
              "polar_1_count": 4, "polar_1_hole_diameter": 5.0, "polar_1_circle_diameter": 30.0,
              "hole_2_diameter": 12.0, "hole_2_x": 0.0, "hole_2_y": 0.0}
    base, features = composed_features(params)
    assert base == {"diameter": 50.0, "height": 20.0, "top_chamfer": 1.0}
    assert features == [("polar", {"count": 4, "hole_diameter": 5.0, "circle_diameter": 30.0}),
                        ("hole", {"diameter": 12.0, "x": 0.0, "y": 0.0})]
