"""Captions, caption checks and split assignment."""

import random

import pytest

from forge.data.build_dataset import assign_split, band_limits, unit_hash
from forge.generators import FAMILIES
from forge.generators.captions import caption_problems, spec_captions, standard_name

CONFIG = {
    "seed": 0,
    "holdout_families": ["square_nut"],
    "holdout_sizes": ["M12"],
    "holdout_band": [0.45, 0.55],
    "fractions": {"train": 0.9, "val": 0.05, "test_id": 0.05},
}


def _row(part, fingerprint="f"):
    return {"family": part.family, "designation": part.designation, "params": part.params,
            "geom_fingerprint": fingerprint}


def test_standard_name():
    assert standard_name("iso4032") == "ISO 4032"
    assert standard_name("din931") == "DIN 931"


def test_hex_nut_captions():
    part = next(p for p in FAMILIES["hex_nut"].standard_parts()
                if p.designation == {"standard": "iso4032", "size": "M8"})
    by_variant = {c["variant"]: c["text"]
                  for c in spec_captions(part.family, part.params, part.designation)}
    assert by_variant["designation"] == "hex nut ISO 4032 M8"
    assert by_variant["dimensions"].startswith("hex nut, across flats ")
    assert by_variant["dimensions"].endswith("bore diameter 8 mm")


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_every_caption_number_belongs_to_the_part(name):
    rng = random.Random(5)
    family = FAMILIES[name]
    for part in family.standard_parts()[:20] + [family.sample(rng) for _ in range(20)]:
        for caption in spec_captions(part.family, part.params, part.designation):
            assert caption_problems(caption["text"], part.params, part.designation) == []


def test_caption_check_catches_a_wrong_number():
    part = FAMILIES["plate_with_holes"].sample(random.Random(1))
    text = spec_captions(part.family, part.params, None)[0]["text"]
    assert caption_problems(text + ", extra 999.5 mm", part.params, None)
    wrong = text.replace(f"thickness {part.params['thickness']:g} mm", "thickness 0.123 mm")
    assert caption_problems(wrong, part.params, None)


def test_counts_have_no_unit():
    part = FAMILIES["polygon_prism"].sample(random.Random(2))
    text = spec_captions(part.family, part.params, None)[0]["text"]
    assert f"sides {part.params['sides']}," in text


def test_holdout_family_and_size():
    square = FAMILIES["square_nut"].standard_parts()[0]
    assert assign_split(_row(square), CONFIG, {}) == "test_ood_family"
    m12 = next(p for p in FAMILIES["hex_nut"].standard_parts() if p.designation["size"] == "M12")
    assert assign_split(_row(m12), CONFIG, {}) == "test_ood_params"


def test_holdout_band_uses_the_first_dimension():
    rng = random.Random(3)
    rows = [_row(FAMILIES["spacer"].sample(rng), str(i)) for i in range(400)]
    low, high = band_limits(rows, CONFIG["holdout_band"])["spacer"]
    for row in rows:
        inside = low <= row["params"]["outer_diameter"] <= high
        assert (assign_split(row, CONFIG, {"spacer": (low, high)}) == "test_ood_params") == inside


def test_same_shape_same_split():
    part = FAMILIES["spacer"].sample(random.Random(4))
    a = assign_split(_row(part, "same"), CONFIG, {})
    b = assign_split(_row(part, "same"), CONFIG, {})
    assert a == b
    assert 0 <= unit_hash("x") < 1


def test_band_skips_counts():
    rng = random.Random(6)
    rows = [_row(FAMILIES["polygon_prism"].sample(rng), str(i)) for i in range(300)]
    low, high = band_limits(rows, CONFIG["holdout_band"])["polygon_prism"]
    diameters = sorted(r["params"]["corner_diameter"] for r in rows)
    assert diameters[0] < low <= high < diameters[-1]  # a band of sizes, not of side counts


# --- natural prompts -------------------------------------------------------

from forge.generators.prompts import natural_prompts, numbers_in, prompt_problems

LEVELS = {"designation": 8, "compact": 4, "request": 4}


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_natural_prompts_are_verified(name):
    """Every number in a prompt is the part's, and no dimension is left out."""
    rng = random.Random(11)
    family = FAMILIES[name]
    for part in family.standard_parts()[:30] + [family.sample(rng) for _ in range(60)]:
        prompts = natural_prompts(part.family, part.params, part.designation, part.id, LEVELS)
        assert prompts
        for prompt in prompts:
            complete = prompt["style"] != "designation"
            assert prompt_problems(prompt["text"], part.params, part.designation, complete) == [], \
                prompt["text"]


def test_natural_prompts_are_reproducible_and_varied():
    part = FAMILIES["mounting_plate"].sample(random.Random(3))
    a = natural_prompts(part.family, part.params, None, part.id, LEVELS)
    b = natural_prompts(part.family, part.params, None, part.id, LEVELS)
    assert a == b
    assert len({p["text"] for p in a}) == len(a) >= 6
    assert all(p["model"] == "template" for p in a)


def test_prompt_check_catches_wrong_and_missing_numbers():
    params = {"length": 100.0, "width": 60.0, "thickness": 5.0, "hole_count": 4}
    good = "plate 100x60x5 with four holes"
    assert prompt_problems(good, params, None, complete=True) == []
    assert prompt_problems("plate 100x60x6 with four holes", params, None, complete=True)
    assert prompt_problems("plate 100x60 with four holes", params, None, complete=True)
    assert numbers_in("boss at (16.25, -21.25), M8x40") == [16.25, -21.25, 8.0, 40.0]


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_whole_numbers_are_counts_only(name):
    """A length is always a float; only counts are whole-number parameters."""
    part = FAMILIES[name].sample(random.Random(13))
    for key, value in part.params.items():
        if isinstance(value, int):
            assert any(word in key for word in ("count", "sides", "holes_", "teeth", "number")), key


def test_part_names_contain_no_numbers():
    """A digit or number word in a name would be mistaken for a dimension."""
    from forge.generators.prompts import NAMES

    for family, names in NAMES.items():
        for name in names:
            assert numbers_in(name) == [], (family, name)
    assert set(NAMES) == set(FAMILIES)
