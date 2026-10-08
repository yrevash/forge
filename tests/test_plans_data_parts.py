"""Single parts as plans (the kernel is used by one small test only)."""

import json

import pytest

from forge.plan import parse_plan
from forge.plans_data import parts
from forge.system1.parts import GENERATED_DIR

BLOCK = {"base_length": 160.0, "base_width": 90.0, "base_height": 22.0,
         "hole_pair_1_diameter": 18.5, "hole_pair_1_x": 12.0, "hole_pair_1_y": 15.0,
         "pocket_2_length": 22.0, "pocket_2_width": 12.0, "pocket_2_depth": 3.5,
         "pocket_2_x": -40.0, "pocket_2_y": 0.0,
         "row_3_count": 3, "row_3_hole_diameter": 5.0, "row_3_spacing": 10.0, "row_3_y": 0.0}


def rows(base: str, count: int) -> list[dict]:
    path = GENERATED_DIR / f"composed_{base}.jsonl"
    if not path.exists():
        pytest.skip("data/generated is not on this machine")
    with open(path) as f:
        return [json.loads(line) for _, line in zip(range(count), f, strict=False)]


def test_a_block_becomes_a_base_line_and_one_line_per_step() -> None:
    lines, turned, kinds = parts.plan_lines("composed_block", BLOCK)
    assert lines == [
        "part: box 160 by 90 by 22, on ground",
        "on part: pair of holes diameter 18.5, at (12, 15)",
        "on part: pocket length 22, width 12, depth 3.5, at (-40, 0)",
        "on part: row of holes count 3, hole diameter 5, spacing 10",
        "done",
    ]
    assert not turned and kinds == ["hole_pair", "pocket", "row"]
    assert parse_plan("\n".join(lines) + "\n").accepted


def test_edge_distances_say_the_same_place() -> None:
    lines, _, _ = parts.plan_lines("composed_block", BLOCK, edges=True)
    assert lines[1] == "on part: pair of holes diameter 18.5, 68 from part's right, 30 from part's back"
    assert lines[2] == "on part: pocket length 22, width 12, depth 3.5, 40 from part's left"
    assert parse_plan("\n".join(lines) + "\n").accepted


def test_a_hex_part_is_written_turned_and_a_pair_on_it_cannot_be_said() -> None:
    hexagon = {"base_across_flats": 88.0, "base_height": 33.0, "hole_1_diameter": 6.5,
               "hole_1_x": 16.0, "hole_1_y": 0.0, "slot_2_length": 43.0, "slot_2_width": 12.5,
               "slot_2_depth": 2.5, "slot_2_angle": 90.0, "slot_2_x": 0.0, "slot_2_y": 0.0}
    lines, turned, _ = parts.plan_lines("composed_hex", hexagon)
    assert turned
    assert lines[0] == "part: prism 6 by 88 by 33, on ground"
    assert lines[1] == "on part: hole diameter 6.5, at (0, 16)"          # (x, y) -> (-y, x)
    assert lines[2] == "on part: slot length 43, width 12.5, depth 2.5, angle 0"
    with pytest.raises(parts.NotExpressible):
        parts.plan_lines("composed_hex", {"base_across_flats": 88.0, "base_height": 33.0,
                                          "hole_pair_1_diameter": 6.0, "hole_pair_1_x": 20.0,
                                          "hole_pair_1_y": 0.0})


@pytest.mark.parametrize("base", ["block", "cylinder"])
def test_stored_parts_convert_and_resolve_to_the_stored_frame(base: str) -> None:
    for part in rows(base, 25):
        made = parts.convert(part)
        assert made.ok, made.reason
        assert made.plan.accepted and made.resolution.complete
        frame = made.resolution.bodies[0].frame
        assert all(abs(frame.size[k] - part["measured"]["bbox"][k]) < 1e-3 for k in range(3))


def test_a_boss_on_a_ring_is_reported_not_forced() -> None:
    ring = {"base_outer_diameter": 55.0, "base_inner_diameter": 17.0, "base_height": 18.0,
            "boss_1_diameter": 8.0, "boss_1_height": 10.0, "boss_1_x": -4.0, "boss_1_y": 20.0}
    made = parts.convert({"id": "00", "family": "composed_ring", "params": ring})
    assert not made.ok and "does not fit" in made.reason


def test_the_plan_builds_the_stored_solid_on_the_kernel() -> None:
    from forge.sandbox import Sandbox
    with Sandbox(timeout=120) as sandbox:
        for part in rows("block", 2) + rows("hex", 6)[-1:]:
            made = parts.convert(part)
            if not made.ok:
                continue
            assert parts.check_measurements(part, made.resolution, sandbox) == []
            assert parts.check_same_solid(part, made.resolution, made.turned, sandbox) == []
