"""Recipes against the reference: FreeCAD must build what our engine says. Skipped without FreeCAD."""

import random

import pytest

from forge.freecad import build
from forge.freecad.build import against_stored, build_steps
from forge.freecad.client import FreeCADClient
from forge.freecad.locate import freecad_available
from forge.freecad.probes import failed, probes_of
from forge.freecad.recipes import Context
from forge.sandbox import Sandbox
from forge.system1.steps import FEATURES, STARTS, TREATMENTS, Step, steps_of
from tests.system1_helpers import sample_parts

pytestmark = pytest.mark.skipif(not freecad_available(), reason="FreeCAD is not installed")


@pytest.fixture(scope="module")
def fc():
    with FreeCADClient() as client:
        yield client


def test_sampled_parts_match_the_reference_engine_step_by_step(fc):
    """Fresh generator parts, normal and long; dimensions in a random order; every kind seen."""
    parts = sample_parts(12, seed="freecad") + sample_parts(3, seed="freecad-long",
                                                             features=(6, 12))
    seen = set()
    for part in parts:
        result = build_steps(fc, steps_of(part.family, part.params), random.Random(part.id))
        assert result.problems == [], part.id
        assert all(ok for _, ok in result.steps)
        seen |= {kind for kind, _ in result.steps}
        final = result.snapshot
        assert final["solid"]["solids"] == 1 and final["solid"]["valid"]
        assert all(item["valid"] for item in final["items"])
        assert all(item["dof"] == 0 for item in final["items"] if item["type"] == "sketch")
    assert seen == set(STARTS) | set(TREATMENTS) | set(FEATURES)       # all 20 step kinds


def test_freecad_matches_the_cadquery_kernel(fc):
    """One part per base, built by FreeCAD and by the generator's own CadQuery program."""
    with Sandbox(timeout=30) as sandbox:
        for part in sample_parts(1, seed="freecad-kernel"):
            result = build_steps(fc, steps_of(part.family, part.params))
            assert result.problems == []
            reply = sandbox.run(part.code)
            assert reply["status"] == "ok"
            assert against_stored(result.snapshot["solid"], reply["measure"]) == []


def test_probes_catch_a_feature_in_the_wrong_place(fc):
    """A mirrored hole and a turned slot keep the volume and the bounding box; probes see them."""
    base = Step("block", {"length": 80.0, "width": 40.0, "height": 10.0})
    hole = {"diameter": 6.0, "x": 20.0, "y": 5.0}
    slot = {"length": 16.0, "width": 4.0, "depth": 3.0, "angle": 0.0, "x": -15.0, "y": -5.0}
    wanted = [base, Step("hole", hole), Step("slot", slot)]
    wrong = [base, Step("hole", {**hole, "x": -20.0}), Step("slot", slot)]
    turned = [base, Step("hole", hole), Step("slot", {**slot, "angle": 90.0})]

    def misses(built: list[Step]) -> list[str]:
        assert build_steps(fc, built, probe=False).problems == []      # sizes are all fine
        probes = probes_of(wanted)
        return failed(probes, fc.probe([point for point, _, _ in probes]))

    assert misses(wanted) == []
    assert any("hole" in text for text in misses(wrong))
    assert any("slot" in text for text in misses(turned))


def test_a_wrong_number_is_reported_by_the_step_check(fc, monkeypatch):
    """Building at a different height than the reference expects is named at that step."""
    steps = [Step("cylinder", {"diameter": 50.0, "height": 12.0}),
             Step("boss", {"diameter": 10.0, "height": 6.0, "x": 0.0, "y": 0.0})]
    assert build_steps(fc, steps).problems == []
    # Same plan, but the recipe is fed a context that puts the boss 3 mm too low.
    monkeypatch.setattr(build, "context_of", lambda steps: Context("cylinder", 12.0, 9.0))
    problems = build_steps(fc, steps).problems
    assert problems and problems[0].startswith("after boss: bbox Z")
