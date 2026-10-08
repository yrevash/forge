"""The step vocabulary matches its written table, and params <-> steps is an exact round trip."""

import hashlib
import json
import re

import pytest

from forge.generators.prompts import natural_prompts
from forge.runs import PROJECT_ROOT
from forge.system1.steps import (
    CONTROL,
    FEATURES,
    SLOTS,
    STARTS,
    TREATMENTS,
    Step,
    group_of,
    params_of,
    slot_params,
    steps_of,
)
from tests.system1_helpers import sample_parts


def _documented_steps() -> dict[str, tuple[str, ...]]:
    """Kinds and slots as written in the table of the step document (section 2)."""
    path = PROJECT_ROOT / "docs" / "STEPS.md"
    if not path.exists():           # the document is not shipped with every copy of the code
        pytest.skip("the step document is not present")
    text = path.read_text()
    table = text[text.index("## 2. Steps"):text.index("## 3.")]
    found: dict[str, tuple[str, ...]] = {}
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or "`" not in cells[1]:
            continue
        kinds = re.findall(r"`(\w+)`", cells[1])
        slots = () if cells[2] == "none" else tuple(s.strip() for s in cells[2].split(","))
        for kind in kinds:
            found[kind] = slots
    return found


def test_the_code_has_exactly_the_kinds_and_slots_of_the_contract():
    documented = _documented_steps()
    # The contract writes the three pairs as "as the single feature".
    for pair in ("hole_pair", "boss_pair", "pocket_pair"):
        assert documented.pop(pair)[0].startswith("as the single feature")
        assert SLOTS[pair] == SLOTS[pair.removesuffix("_pair")]
    assert {kind: SLOTS[kind] for kind in documented} == documented
    assert set(SLOTS) == set(documented) | {"hole_pair", "boss_pair", "pocket_pair"}
    assert len(STARTS) == 4 and len(TREATMENTS) == 4 and len(FEATURES) == 12 and len(CONTROL) == 2


def test_groups():
    assert [group_of(k) for k in ("ring", "shell", "row", "undo")] == \
        ["start", "treatment", "feature", "control"]


def test_a_part_reads_as_start_treatment_then_features_in_build_order():
    params = {"base_diameter": 50.0, "base_height": 20.0, "base_top_fillet_radius": 1.0,
              "polar_1_count": 4, "polar_1_hole_diameter": 5.0, "polar_1_circle_diameter": 30.0,
              "hole_2_diameter": 12.0, "hole_2_x": 0.0, "hole_2_y": 0.0}
    assert steps_of("composed_cylinder", params) == [
        Step("cylinder", {"diameter": 50.0, "height": 20.0}),
        Step("top_fillet", {"radius": 1.0}),
        Step("polar", {"count": 4, "hole_diameter": 5.0, "circle_diameter": 30.0}),
        Step("hole", {"diameter": 12.0, "x": 0.0, "y": 0.0}),
    ]
    assert slot_params("composed_cylinder", params)[1] == \
        ("top_fillet", {"radius": "base_top_fillet_radius"})


def test_round_trip_on_thousands_of_parts():
    parts = sample_parts(1000) + sample_parts(150, features=(6, 12))
    assert len(parts) == 4600
    kinds = set()
    for part in parts:
        steps = steps_of(part.family, part.params)
        family, params = params_of(steps)
        assert family == part.family
        assert params == part.params
        assert list(params) == list(part.params)                 # same order as the program
        assert [type(v) for v in params.values()] == [type(v) for v in part.params.values()]
        assert steps_of(family, params) == steps
        kinds.update(step.kind for step in steps)
    assert kinds == set(SLOTS) - set(CONTROL)                    # every kind was exercised


def test_parameters_that_fit_no_step_are_refused():
    with pytest.raises(ValueError, match="fit no step"):
        steps_of("composed_block", {"base_length": 60.0, "base_width": 40.0, "base_height": 10.0,
                                    "base_draft_angle": 3.0})
    with pytest.raises(ValueError, match="not a composed family"):
        steps_of("hex_nut", {})
    with pytest.raises(ValueError, match="straight after the start"):
        params_of([Step("block", {"length": 60.0, "width": 40.0, "height": 10.0}),
                   Step("hole", {"diameter": 5.0, "x": 0.0, "y": 0.0}),
                   Step("top_chamfer", {"size": 1.0})])


# --- the refactor of composed.py and prompts.py changed nothing they produce ----------------
# Both hashes were computed with the files as they were BEFORE forge/system1 was written
# (5 Oct 2026, generator version 4dca9e0bdd16). Dataset v1 was built from that output.

def test_composed_parts_and_their_prompts_are_what_they_were_before_system1():
    parts_hash, prompts_hash = hashlib.sha256(), hashlib.sha256()
    for part in sample_parts(750, seed="pin"):
        parts_hash.update(json.dumps([part.family, part.params, part.code, part.expected_bbox,
                                      part.expected_volume,
                                      sorted(part.expected_cylinders.items())]).encode())
        for prompt in natural_prompts(part.family, part.params, None, part.id,
                                      {"compact": 2, "request": 3}):
            prompts_hash.update((prompt["variant"] + "\x00" + prompt["text"] + "\x01").encode())
    assert parts_hash.hexdigest() == \
        "172d899266730834ef8d2dd4d641121de7a02af345e30b9baf1e7eb6aa950472"
    assert prompts_hash.hexdigest() == \
        "d9f2f6713c7bf0836dea7a68105ccde7a739d307f7de47432e30ae5b11b27202"
