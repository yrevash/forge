"""Single parts as plans: a composed part (a base plus features) in plan-language lines.

    part: box 160 by 90 by 22, on ground
    on part: pair of holes diameter 18.5, at (12, 15)
    on part: pocket length 22, width 12, depth 3.5, 40 from part's left
    done

The part's steps come from forge.system1.steps; each step becomes one
line: the start is the base line, an edge treatment and every feature an `on part:` line.

What the language cannot say as the generator built it:

  * hex bases. The generator's hexagon has flat sides facing left and right; the
    language's `prism` has one flat side facing the FRONT and no part can be turned about
    its own upright axis. A hex part is therefore written turned a quarter turn
    (x, y) -> (-y, x). That is exact for single features and for a circle of 4 or 8
    holes. A `pair` is mirrored left-right in the language, so after the turn it would
    have to be mirrored front-back: not expressible. Those parts are reported, not forced.

Three levels of checking (the build says how many parts got each):

  1. every part: the reader accepts every line, the resolver builds every line, and the
     resolved frame equals the stored bounding box. The resolver asks the CAD kernel on
     every feature line whether the part is still one solid; here that question is
     answered "yes" without the kernel (`TrustStored`), because the generator's own
     kernel run already built this part with these features.
  2. a sample: the resolver's reference program is built on the kernel
     (forge.resolve.verify) and its volume, area, face and edge counts are compared with
     the stored measurements.
  3. a smaller sample: the stored program and the resolver's program are built together
     and their symmetric difference must be empty (this is the check that sees a feature
     in the wrong place).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.plans_data.session import Session
from forge.resolve.bodies import Body
from forge.resolve.judge import KernelJudge, View
from forge.resolve.program import variable, write_program
from forge.resolve.resolver import Resolution
from forge.resolve.verify import verify
from forge.sandbox import Sandbox
from forge.system1.steps import FEATURES, TREATMENTS, Step, steps_of

NAME = "part"
BBOX_TOLERANCE = 1e-3
RELATIVE = 1e-6
FEATURE_WORDS = {
    "hole": "hole", "blind_hole": "blind hole", "counterbore": "counterbored hole",
    "boss": "boss", "pad": "pad", "pocket": "pocket", "slot": "slot", "polar": "circle of holes",
    "row": "row of holes", "hole_pair": "pair of holes", "boss_pair": "pair of bosses",
    "pocket_pair": "pair of pockets",
}
TREATMENT_WORDS = {"corner_radius": "rounded corners radius {0}", "top_chamfer": "top chamfer size {0}",
                   "top_fillet": "top fillet radius {0}",
                   "shell": "hollowed out wall {0}, open at top"}
PAIRS = ("hole_pair", "boss_pair", "pocket_pair")


def num(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


class TrustStored(KernelJudge):
    """Answers "is this one part still one solid?" with yes, without the kernel (level 1)."""

    def __init__(self) -> None:
        super().__init__(sandbox=None)

    def look(self, bodies: list[Body]) -> View:
        if len(bodies) != 1:
            raise ValueError("TrustStored is only for a plan with one part")
        return View(solids={bodies[0].name: 1})

    def close(self) -> None:
        pass


class NotExpressible(Exception):
    """This part cannot be said in the language as the generator built it."""


@dataclass
class Converted:
    ok: bool
    text: str = ""
    plan: object = None
    resolution: Resolution | None = None
    reason: str = ""
    failed_kind: str = ""
    turned: bool = False
    kinds: list[str] = field(default_factory=list)


def _base_line(step: Step) -> tuple[str, tuple[float, float]]:
    """The base line and the frame's length and depth (for edge distances)."""
    s = step.slots
    if step.kind == "block":
        return f"box {num(s['length'])} by {num(s['width'])} by {num(s['height'])}", \
            (s["length"], s["width"])
    if step.kind == "cylinder":
        return f"cylinder {num(s['diameter'])} by {num(s['height'])}", (s["diameter"],) * 2
    if step.kind == "ring":
        return (f"tube {num(s['outer_diameter'])} by {num(s['inner_diameter'])} by "
                f"{num(s['height'])}"), (s["outer_diameter"],) * 2
    return f"prism 6 by {num(s['across_flats'])} by {num(s['height'])}", (0.0, 0.0)


def _position(x: float, y: float, sides: tuple[float, float], edges: bool, pair: bool) -> str:
    """Where the feature sits: nothing (centred), `at (x, y)`, or distances from the edges."""
    if x == 0 and y == 0:
        return ""
    if not edges or sides[0] <= 0:
        return f", at ({num(x)}, {num(y)})"
    said = []
    if x != 0:
        said.append(f"{num(sides[0] / 2 - abs(x))} from {NAME}'s {'right' if x > 0 else 'left'}")
    if y != 0:
        said.append(f"{num(sides[1] / 2 - abs(y))} from {NAME}'s {'back' if y > 0 else 'front'}")
    return ", " + ", ".join(said)


def plan_lines(family: str, params: dict, edges: bool = False) -> tuple[list[str], bool, list[str]]:
    """The plan lines of a composed part; whether it is written turned; its feature kinds."""
    steps = steps_of(family, params)
    start = steps[0]
    turned = start.kind == "hex"
    head, sides = _base_line(start)
    lines = [f"{NAME}: {head}, on ground"]
    shelled = False
    kinds = []
    for step in steps[1:]:
        s = dict(step.slots)
        kinds.append(step.kind)
        if step.kind in TREATMENTS:
            lines.append(f"on {NAME}: " + TREATMENT_WORDS[step.kind].format(
                num(s[TREATMENTS[step.kind][0]])))
            shelled = shelled or step.kind == "shell"
            continue
        x, y = s.pop("x", 0.0), s.pop("y", 0.0)
        if turned:
            if step.kind in PAIRS:
                raise NotExpressible(f"{step.kind} on a hex base: the pair would be mirrored "
                                     "front-back, the language mirrors a pair left-right")
            if step.kind == "polar" and s["count"] % 4:
                raise NotExpressible(f"polar with {s['count']} holes on a hex base: the first "
                                     "hole would sit at the back, the language puts it at the right")
            if step.kind == "row":
                raise NotExpressible("row on a hex base: it would run front to back")
            x, y = -y, x
            if step.kind in ("pad", "pocket"):
                s["length"], s["width"] = s["width"], s["length"]
            if step.kind == "slot":
                s["angle"] = (s["angle"] + 90.0) % 180.0
        words = FEATURE_WORDS[step.kind]
        slots = ", ".join(f"{slot.replace('_', ' ')} {num(s[slot])}"
                          for slot in FEATURES[step.kind] if slot not in ("x", "y"))
        where = _position(x, y, sides, edges and not shelled, step.kind in PAIRS)
        face = f"{NAME}'s inner bottom" if shelled else NAME
        lines.append(f"on {face}: {words} {slots}{where}")
    return [*lines, "done"], turned, kinds


def convert(part: dict, judge: KernelJudge | None = None) -> Converted:
    """Write the part as a plan and resolve it (level 1). `part`: a row of data/generated."""
    edges = int(part["id"][:2], 16) % 2 == 0        # half the parts use edge distances
    try:
        lines, turned, kinds = plan_lines(part["family"], part["params"], edges)
    except NotExpressible as why:
        return Converted(False, reason=str(why), failed_kind=str(why).split(" ")[0])
    session = Session(judge or TrustStored())
    for text in lines:
        reply = session.offer(text)
        if not reply.built:
            session.drop()
            kind = text.split(": ", 1)[1].split(" ")[0] if ": " in text else text
            return Converted(False, "\n".join(lines), reason=f"{reply.reply} on `{text}` "
                             f"({'; '.join(reply.notes)})", failed_kind=kind, turned=turned,
                             kinds=kinds)
        session.keep()
    finished = session.finish()
    if finished is None:
        return Converted(False, reason="a fresh reading differs", kinds=kinds)
    text, plan, resolution = finished
    frame = resolution.bodies[0].frame
    box = part.get("measured", {}).get("bbox")
    if box:
        want = (box[1], box[0], box[2]) if turned else tuple(box)
        if any(abs(frame.size[k] - want[k]) > BBOX_TOLERANCE for k in range(3)):
            return Converted(False, text, reason=f"frame {frame.size} is not the stored "
                             f"bounding box {want}", failed_kind="bbox", turned=turned, kinds=kinds)
    return Converted(True, text, plan, resolution, turned=turned, kinds=kinds)


# --- levels 2 and 3: the kernel ---------------------------------------------------------------

def check_measurements(part: dict, resolution: Resolution, sandbox: Sandbox) -> list[str]:
    """Level 2: build the resolver's program; compare with the stored measurements."""
    verdict = verify(resolution.bodies, resolution.touching, sandbox)
    problems = list(verdict.problems)
    if verdict.reply.get("status") != "ok":
        return problems or [f"kernel: {verdict.reply.get('status')}"]
    got, want = verdict.reply["measure"], part["measured"]
    for key in ("volume", "area"):
        if abs(got[key] - want[key]) > max(RELATIVE * want[key], 1e-6):
            problems.append(f"{key}: plan {got[key]:.4f}, stored {want[key]:.4f}")
    for key in ("n_faces", "n_edges"):
        if got.get(key) != want.get(key):
            problems.append(f"{key}: plan {got.get(key)}, stored {want.get(key)}")
    return problems


def check_same_solid(part: dict, resolution: Resolution, turned: bool, sandbox: Sandbox) -> list[str]:
    """Level 3: the stored program's solid and the plan's solid differ by nothing."""
    body = resolution.bodies[0]
    name = variable(body.name)
    turn = '.rotate((0, 0, 0), (0, 0, 1), 90)' if turned else ""
    program = "\n".join([
        part["code"],
        f"_stored = result{turn}",
        write_program([body]),
        f"_plan = {name}",
        "_only_stored = _stored.cut(_plan)",
        "_only_plan = _plan.cut(_stored)",
        ("_difference = sum(s.Volume() for s in _only_stored.solids().vals()) "
        "+ sum(s.Volume() for s in _only_plan.solids().vals())"),
        # The sandbox measures `result`: a block 1 by 1 by (1 + the difference) carries the number.
        "result = cq.Workplane('XY').box(1.0, 1.0, 1.0 + _difference)",
    ])
    reply = sandbox.run(program, timeout=120)
    if reply.get("status") != "ok":
        return [f"kernel: {reply.get('status')}: {str(reply.get('error'))[:200]}"]
    difference = reply["measure"]["volume"] - 1.0
    if difference > 1e-6 * part["measured"]["volume"]:
        return [f"the two solids differ by {difference:.6f} mm^3"]
    return []
