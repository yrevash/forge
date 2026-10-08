"""The CAD kernel as the judge for pairs arithmetic cannot decide (contact.py returns None).

The bodies in question are written as a CadQuery program (program.py) and run in
forge.sandbox with `structure=True`; forge/assembly_geometry.py then reports which
parts share volume and which touch. Nothing is executed in this process.

One sandbox is started the first time it is needed and kept warm, so a plan that needs
the kernel for several lines pays the start-up once.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.resolve.bodies import Body
from forge.resolve.program import write_program
from forge.sandbox import Sandbox

JUDGE_TIMEOUT = 60.0


class KernelFailed(Exception):
    """The kernel could not build the bodies (a feature that removes the whole part ...)."""


@dataclass
class View:
    """What the kernel saw for one group of bodies."""

    overlaps: dict[frozenset[str], float] = field(default_factory=dict)   # pair -> shared mm^3
    touching: set[frozenset[str]] = field(default_factory=set)
    solids: dict[str, int] = field(default_factory=dict)                  # body -> solid count


class KernelJudge:
    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self._sandbox = sandbox
        self._owns_sandbox = sandbox is None
        self.calls = 0

    def look(self, bodies: list[Body]) -> View:
        """Build these bodies and report overlaps and touches among them."""
        if self._sandbox is None:
            self._sandbox = Sandbox(timeout=JUDGE_TIMEOUT)     # starts on first use
        self.calls += 1
        reply = self._sandbox.run(write_program(bodies), structure=True)
        if reply.get("status") != "ok":
            raise KernelFailed(f"{reply.get('status')}: {reply.get('error')}")
        measured = reply["measure"]["structure"]
        return View(
            overlaps={frozenset((a, b)): volume for a, b, volume in measured["overlaps"]},
            touching={frozenset(pair) for pair in measured["touching"]},
            solids={part["name"]: part["n_solids"] for part in measured["parts"]},
        )

    def close(self) -> None:
        if self._sandbox is not None and self._owns_sandbox:
            self._sandbox.close()
            self._sandbox = None
