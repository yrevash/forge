"""Shared by the tests/test_system1_*.py files: sampled composed parts, as session rows."""

import random

from forge.generators import FAMILIES

COMPOSED = ("composed_block", "composed_cylinder", "composed_hex", "composed_ring")


def sample_parts(per_family: int, seed: object = "system1", features=None) -> list:
    """`per_family` random parts of each composed family, the same ones every run."""
    parts = []
    for name in COMPOSED:
        rng = random.Random(f"{seed}:{name}")
        for _ in range(per_family):
            parts.append(FAMILIES[name].sample(rng, features) if features
                         else FAMILIES[name].sample(rng))
    return parts


def as_row(part, long: bool = False) -> dict:
    """A generator Part as the light row that sessions and splits read."""
    return {"id": part.id, "family": part.family, "params": part.params,
            "geom_fingerprint": f"test-{part.id}", "source": f"gen:{part.family}",
            "license": "forge", "generator_version": "test", "long": long}
