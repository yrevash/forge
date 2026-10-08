"""Build the reference program in the sandbox and compare the kernel with the resolver.

The resolver's numbers are arithmetic (frames from hulls, volumes from formulas, contact
from contact.py). The measured numbers come from running the generated CadQuery program
in forge.sandbox with `structure=True`. Different code produced the two, so agreement
is evidence that the program builds what the plan says.

Checked (the tolerances are the ones single parts use, forge/generators/base.py):
  * the program ran; the number of parts and their names
  * every part is exactly one solid
  * every part's frame, and its volume where the resolver states one (no features)
  * the overall frame
  * no two parts overlap, other than the pairs the plan declared (`sunk`, a span's ends)
  * every part touches another part or the ground
  * the parts form one group, and it reaches the ground
  * the kernel found exactly the touching pairs the resolver recorded
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.resolve import contact
from forge.resolve import space as sp
from forge.resolve.bodies import Body
from forge.resolve.program import write_program
from forge.sandbox import Sandbox

BBOX_TOLERANCE_MM = 1e-3            # same values as forge/generators/base.py
VOLUME_RELATIVE_TOLERANCE = 1e-6
MAX_LISTED = 6
BUILD_TIMEOUT = 300.0


@dataclass
class Verdict:
    problems: list[str] = field(default_factory=list)
    program: str = ""
    reply: dict = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.problems


def compare(bodies: list[Body], touching: set[frozenset[str]], reply: dict) -> list[str]:
    """Every difference between the resolver's arithmetic and the kernel's measurement."""
    if reply.get("status") != "ok":
        return [f"{reply.get('status')}: {reply.get('error')}"]
    measure = reply["measure"]
    measured = measure["structure"]
    problems: list[str] = []
    names = [body.name for body in bodies]
    got_names = [part["name"] for part in measured["parts"]]
    if measured["n_parts"] != len(bodies):
        problems.append(f"part count: measured {measured['n_parts']}, expected {len(bodies)}")
    if got_names != names:
        problems.append(f"part names differ: missing {sorted(set(names) - set(got_names))[:6]}, "
                        f"unexpected {sorted(set(got_names) - set(names))[:6]}")

    by_name = {part["name"]: part for part in measured["parts"]}
    part_problems = []
    for body in bodies:
        got = by_name.get(body.name)
        if got is None:
            continue
        if got["n_solids"] != 1:
            part_problems.append(f"{body.name} is {got['n_solids']} solids, expected 1")
        want = body.volume
        if want is not None and abs(got["volume"] - want) > VOLUME_RELATIVE_TOLERANCE * want:
            part_problems.append(f"{body.name} volume: measured {got['volume']:.4f}, "
                                 f"expected {want:.4f}")
        frame = body.frame
        for axis in range(3):
            if (abs(got["bbox_min"][axis] - frame.low[axis]) > BBOX_TOLERANCE_MM
                    or abs(got["bbox_max"][axis] - frame.high[axis]) > BBOX_TOLERANCE_MM):
                part_problems.append(
                    f"{body.name} {'XYZ'[axis]}: measured {got['bbox_min'][axis]:.4f} to "
                    f"{got['bbox_max'][axis]:.4f}, expected {frame.low[axis]:.4f} to "
                    f"{frame.high[axis]:.4f}")
    problems += part_problems[:MAX_LISTED]

    whole = sp.around([body.frame for body in bodies])
    for axis in range(3):
        low, size = measure["bbox_min"][axis], measure["bbox"][axis]
        if (abs(low - whole.low[axis]) > BBOX_TOLERANCE_MM
                or abs(size - whole.size[axis]) > BBOX_TOLERANCE_MM):
            problems.append(f"overall {'XYZ'[axis]}: measured from {low:.4f}, size {size:.4f}; "
                            f"expected from {whole.low[axis]:.4f}, size {whole.size[axis]:.4f}")

    declared = {frozenset((body.name, other)) for body in bodies for other in body.may_overlap}
    problems += [f"{a} and {b} overlap by {volume:.3f} mm^3"
                 for a, b, volume in measured["overlaps"] if frozenset((a, b)) not in declared
                 ][:MAX_LISTED]

    # Two parts that share volume are joined, also when one lies wholly inside the other
    # (a part sunk deeper than its own size): the kernel's distance test alone misses that.
    found = {frozenset(pair) for pair in measured["touching"]}
    found |= {frozenset((a, b)) for a, b, _ in measured["overlaps"]}
    grounded = {name for name in got_names
                if abs(by_name[name]["bbox_min"][2]) <= BBOX_TOLERANCE_MM}
    touched = {name for pair in found for name in pair}
    problems += [f"{name} touches nothing" for name in got_names
                 if name not in touched and name not in grounded][:MAX_LISTED]
    groups = contact.joined_groups(got_names, found)
    if len(groups) != 1:
        problems.append(f"the parts form {len(groups)} separate groups, expected 1")
    if not grounded:
        problems.append("no part reaches the ground")

    unseen = [sorted(pair) for pair in touching - found]
    extra = [sorted(pair) for pair in found - touching]
    if unseen:
        problems.append(f"joints the resolver recorded and the kernel did not find: "
                        f"{unseen[:MAX_LISTED]}")
    if extra:
        problems.append(f"touching pairs the kernel found and the resolver did not record: "
                        f"{extra[:MAX_LISTED]}")
    return problems


def verify(bodies: list[Body], touching: set[frozenset[str]], sandbox: Sandbox,
           comments: dict[str, str] | None = None, export: bool = False) -> Verdict:
    """Run the reference program for these bodies in `sandbox` and compare."""
    program = write_program(bodies, comments, export=export)
    reply = sandbox.run(program, timeout=BUILD_TIMEOUT, structure=True)
    return Verdict(compare(bodies, touching, reply), write_program(bodies, comments), reply,
                   reply.get("seconds", 0.0))
