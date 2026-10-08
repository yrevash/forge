"""The arithmetic and table helpers the newer families rely on."""

import math
import random

import pytest

from forge.generators import FAMILIES, tables
from forge.generators.families import _common
from forge.generators.families.turned import countersunk_hole_volume


def test_span_stays_inside_its_limits():
    rng = random.Random(0)
    values = {_common.span(rng, 3.0, 4.3, 0.5) for _ in range(200)}
    assert values == {3.0, 3.5, 4.0}                      # never rounds up past 4.3
    assert _common.span(rng, 5.0, 2.0, 0.5) == 5.0        # empty range falls back to `low`


def test_frustum_volume_matches_cylinder_and_cone():
    assert _common.frustum_volume(10, 10, 7) == pytest.approx(math.pi * 25 * 7)
    assert _common.frustum_volume(10, 0, 6) == pytest.approx(math.pi * 25 * 6 / 3)


def test_circle_strip_area_limits():
    # A strip as wide as the circle covers all of it; a thin strip is about width * diameter.
    assert _common.circle_strip_area(10, 10) == pytest.approx(math.pi * 25)
    assert _common.circle_strip_area(10, 0.001) == pytest.approx(0.001 * 10, rel=1e-6)


def test_circle_segment_area_limits():
    # A flat cut to the centre removes half the circle.
    assert _common.circle_segment_area(10, 5) == pytest.approx(math.pi * 25 / 2)
    assert _common.circle_segment_area(10, 0) == pytest.approx(0.0)


def test_count_cylinders_adds_shared_diameters():
    assert _common.count_cylinders((8.0, 1), (5.0, 4), (8.0, 2)) == {8.0: 3, 5.0: 4}
    assert _common.count_cylinders((8.0, 1), (8.0, None)) == {8.0: None}


def test_countersunk_hole_volume_at_90_degrees():
    # 90 degrees: the cone is as deep as it is wide beyond the hole, here 2 mm.
    hole, countersink, thickness = 4.0, 8.0, 5.0
    cone = math.pi * 2 / 12 * (64 + 32 + 16)
    expected = math.pi * 4 * thickness + cone - math.pi * 4 * 2
    assert countersunk_hole_volume(hole, countersink, 90, thickness) == pytest.approx(expected)


def test_clearance_holes_are_metric_only():
    holes = tables.clearance_holes("Normal")
    assert all(size.startswith("M") for size in holes)
    with (tables.TABLE_DIR / "clearance_hole_sizes.csv").open() as f:
        row = next(line for line in f if line.startswith("M8,"))
    assert holes["M8"] == float(row.split(",")[2])


def test_table_with_a_blank_size_heading_loads():
    rows = tables.load("cheese_head_parameters")["iso1207"]
    assert all(row["size"].startswith("M") for row in rows)


def test_new_standard_families_read_their_tables():
    ring = tables.load("o-ring_parameters")["iso3601"][0]
    part = FAMILIES["o_ring"].standard_parts()[0]
    assert part.params == {"inner_diameter": ring["id"], "section_diameter": ring["w"]}

    rows = {r["size"]: r for r in tables.load("setscrew_parameters")["iso4026"]}
    screw = next(p for p in FAMILIES["set_screw"].standard_parts()
                 if p.designation["size"] == "M8")
    assert screw.params["socket_size"] == rows["M8-1.25"]["s"]
    assert screw.params["socket_depth"] == rows["M8-1.25"]["t"]

    keys = {r["size"]: r for r in tables.load("shaft_key_parameters")["din6885"]}
    hub = FAMILIES["keyed_hub"].sample(random.Random(3))
    row = keys[f"{hub.params['bore_diameter']:g}"]
    assert (hub.params["keyway_width"], hub.params["keyway_depth"]) == (row["b"], row["t2"])


def test_countersunk_screws_are_all_90_degree_heads():
    family = FAMILIES["countersunk_screw"]
    assert family._rows() and all(row["a"] == 90.0 for row in family._rows())
