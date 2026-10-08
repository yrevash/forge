"""The sketcher's drawing tools and their on-view fields. Plain Python: no FreeCAD needed.

A drawing tool asks for its numbers in PHASES. The circle tool first shows two
fields (centre x, centre y) and, once both are entered, one field (diameter).
The fields are real widgets but have no names, so the runtime names them by the
tool in use and how far it has got.
"""

from __future__ import annotations

# For each drawing tool button: the phases, each a list of field roles in widget order.
TOOL_PHASES: dict[str, list[list[str]]] = {
    "Sketcher_CreateCircle": [["x", "y"], ["diameter"]],
    "Sketcher_CreateRectangle_Center": [["x", "y"], ["length", "width"]],
    "Sketcher_CreateHexagon": [["x", "y"], ["corner_radius", "angle"]],
    # A slot is drawn from the centre of one round end to the centre of the other.
    "Sketcher_CreateSlot": [["x", "y"], ["centre_distance", "angle"], ["end_radius"]],
}


class Tool:
    """The drawing tool in use, and how far its current shape has got."""

    def __init__(self) -> None:
        self.command: str | None = None
        self.phase = 0
        self.entered: list[str] = []    # roles entered in the current phase
        self.point: dict[str, float] = {}   # the x and y typed for the shape's first point

    def start(self, command: str) -> None:
        self.command, self.phase, self.entered, self.point = command, 0, [], {}

    def stop(self) -> None:
        self.command, self.phase, self.entered, self.point = None, 0, [], {}

    def first_point(self) -> tuple[float, float] | None:
        """The shape's first point once both of its numbers are typed, else None."""
        if "x" in self.point and "y" in self.point:
            return self.point["x"], self.point["y"]
        return None

    def roles(self) -> list[str]:
        """Roles of the on-view fields showing now, in widget order ([] for an unknown tool)."""
        phases = TOOL_PHASES.get(self.command or "", [])
        return phases[self.phase] if self.phase < len(phases) else []

    def enter(self, role: str) -> str:
        """Note one entered field. Returns "more", "next_phase" or "shape_done"."""
        if role not in self.entered:
            self.entered.append(role)
        if set(self.entered) != set(self.roles()):
            return "more"
        self.entered = []
        self.phase += 1
        if self.phase < len(TOOL_PHASES[self.command]):
            return "next_phase"
        self.phase, self.point = 0, {}
        return "shape_done"
