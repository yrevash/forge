"""More parts whose sizes come from the vendored standards tables.

Nothing here types a standard dimension by hand: every table value is read
through `forge.generators.tables`. Simplifications are the same as in
fasteners.py: threads are not modelled (a threaded shank is a plain cylinder at
the nominal diameter) and edges are not chamfered or rounded.

Two families mix table values with sizes of our own, and say so:
- `hex_standoff` takes its across-flats size from the hexagon nut table and its
  hole from the clearance hole table; its length is ours.
- `keyed_hub` takes its bore and keyway from the parallel key table; its outer
  diameter and length are ours.
"""

from __future__ import annotations

import math
import random

from forge.generators import tables
from forge.generators.base import (
    HEX_AREA_FACTOR,
    HEX_CORNER_FACTOR,
    Part,
    circle_area,
    program,
)
from forge.generators.families._common import (
    circle_strip_area,
    frustum_volume,
    span,
)


def _lengths(standard: str, row: dict) -> list[float]:
    """Nominal lengths this size is made in, according to the tables."""
    return [x for x in tables.nominal_lengths(standard) if row["short"] <= x <= row["long"]]


class ORing:
    NAME = "o_ring"
    DESCRIPTION = "O-ring: a ring of round cross-section (a torus), made by revolving a circle"
    TABLE = "o-ring_parameters"

    def _part(self, standard: str, row: dict) -> Part:
        inner, section = row["id"], row["w"]
        params = {"inner_diameter": inner, "section_diameter": section}
        code = program(
            params,
            """
result = (
    cq.Workplane("XZ")
    .moveTo((inner_diameter + section_diameter) / 2, section_diameter / 2)
    .circle(section_diameter / 2)
    .revolve(360, (0, 0, 0), (0, 1, 0))
)
""",
        )
        outer = inner + 2 * section
        # Pappus: a circle of area A whose centre travels a path of length L sweeps A * L.
        volume = circle_area(section) * math.pi * (inner + section)
        return Part(self.NAME, params, code, [outer, outer, section], volume,
                    designation={"standard": standard, "size": row["size"]})

    def standard_parts(self) -> list[Part]:
        return [self._part(std, row) for std, rows in tables.load(self.TABLE).items()
                for row in rows if {"id", "w"} <= row.keys()]

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class SetScrew:
    NAME = "set_screw"
    DESCRIPTION = "Set screw with flat point: a headless round screw with a hexagon socket in one end"
    TABLE = "setscrew_parameters"

    def _part(self, standard: str, row: dict, length: float) -> Part:
        s, t, d = row["s"], row["t"], tables.thread_diameter(row["size"])
        params = {"diameter": d, "length": length, "socket_size": s, "socket_depth": t}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .circle(diameter / 2)
    .extrude(length)
    .faces(">Z")
    .workplane()
    .polygon(6, socket_size, circumscribed=True)
    .cutBlind(-socket_depth)
)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[d, d, length],
            expected_volume=circle_area(d) * length - HEX_AREA_FACTOR * s * s * t,
            expected_cylinders={d: 1},
            designation={"standard": standard, "size": tables.short_size(row["size"]),
                         "length": f"{length:g}"},
        )

    def standard_parts(self) -> list[Part]:
        return [self._part(std, row, length)
                for std, rows in tables.load(self.TABLE).items()
                for row in rows if {"s", "t", "short", "long"} <= row.keys()
                for length in _lengths(std, row)
                if length > row["t"]]  # the socket must end inside the screw

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class CheeseHeadScrew:
    NAME = "cheese_head_screw"
    DESCRIPTION = "Slotted cheese head screw: a round head with a screwdriver slot, and a round shank"
    TABLE = "cheese_head_parameters"
    STANDARD = "iso1207"  # the slotted one; the table's other standards have cross recesses

    def _part(self, row: dict, length: float) -> Part:
        dk, k, n, t = row["dk"], row["k"], row["n"], row["t"]
        d = tables.thread_diameter(row["size"])
        params = {"head_diameter": dk, "head_height": k, "slot_width": n, "slot_depth": t,
                  "shank_diameter": d, "length": length}
        code = program(
            params,
            """
head = cq.Workplane("XY").circle(head_diameter / 2).extrude(head_height)
slot = cq.Workplane("XY").rect(slot_width, head_diameter).extrude(slot_depth)
shank = (
    cq.Workplane("XY")
    .workplane(offset=head_height)
    .circle(shank_diameter / 2)
    .extrude(length)
)
result = head.cut(slot).union(shank)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[dk, dk, k + length],
            # The slot removes a strip right across the round head.
            expected_volume=(circle_area(dk) * k - circle_strip_area(dk, n) * t
                             + circle_area(d) * length),
            # The slot splits the lower part of the head's round wall, so only the
            # shank's face count is asserted.
            expected_cylinders={dk: None, d: 1},
            designation={"standard": self.STANDARD, "size": tables.short_size(row["size"]),
                         "length": f"{length:g}"},
        )

    def standard_parts(self) -> list[Part]:
        return [self._part(row, length)
                for row in tables.load(self.TABLE)[self.STANDARD]
                if {"dk", "k", "n", "t", "short", "long"} <= row.keys()
                for length in _lengths(self.STANDARD, row)]

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class CountersunkScrew:
    NAME = "countersunk_screw"
    DESCRIPTION = ("Socket countersunk head screw: a 90 degree conical head with a hexagon "
                   "socket, and a round shank")
    TABLE = "countersunk_head_parameters"
    STANDARD = "iso10642"

    def _part(self, row: dict, length: float) -> Part:
        dk, k, s, t = row["dk"], row["k"], row["s"], row["t"]
        d = tables.thread_diameter(row["size"])
        params = {"head_diameter": dk, "head_height": k, "socket_size": s, "socket_depth": t,
                  "shank_diameter": d, "length": length}
        # A 90 degree cone narrows by 2 mm of diameter per 1 mm of height, so the cone
        # from head to shank is (head - shank) / 2 high. The table's head height is a
        # little more than that; the rest is a short cylindrical rim under the flat face.
        code = program(
            params,
            """
cone_height = (head_diameter - shank_diameter) / 2
rim_height = head_height - cone_height
profile = (
    cq.Workplane("XZ")
    .moveTo(0, 0)
    .lineTo(head_diameter / 2, 0)
    .lineTo(head_diameter / 2, rim_height)
    .lineTo(shank_diameter / 2, head_height)
    .lineTo(shank_diameter / 2, length)
    .lineTo(0, length)
    .close()
)
result = (
    profile.revolve(360, (0, 0, 0), (0, 1, 0))
    .faces("<Z")
    .workplane()
    .polygon(6, socket_size, circumscribed=True)
    .cutBlind(-socket_depth)
)
""",
        )
        cone = (dk - d) / 2
        rim = k - cone
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            # A countersunk screw's length includes its head.
            expected_bbox=[dk, dk, length],
            expected_volume=(circle_area(dk) * rim + frustum_volume(dk, d, cone)
                             + circle_area(d) * (length - k) - HEX_AREA_FACTOR * s * s * t),
            expected_cylinders={dk: 1, d: 1},
            designation={"standard": self.STANDARD, "size": tables.short_size(row["size"]),
                         "length": f"{length:g}"},
        )

    def _rows(self) -> list[dict]:
        # Only the 90 degree heads. The two largest sizes in the table have 60 degree
        # heads, whose cone height is not a round number; they are left out.
        return [row for row in tables.load(self.TABLE)[self.STANDARD]
                if {"a", "dk", "k", "s", "t", "short", "long"} <= row.keys()
                and row["a"] == 90.0
                and row["k"] > (row["dk"] - tables.thread_diameter(row["size"])) / 2]

    def standard_parts(self) -> list[Part]:
        return [self._part(row, length) for row in self._rows()
                for length in _lengths(self.STANDARD, row)
                if length > row["k"]]

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class ShoulderScrew:
    NAME = "shoulder_screw"
    DESCRIPTION = ("Socket head shoulder screw: a round head with a hexagon socket, a ground "
                   "shoulder and a smaller threaded end")
    TABLE = "shoulder_screw"
    STANDARD = "iso7379"

    def _part(self, row: dict, length: float) -> Part:
        ds, dk, k, b, s, t = (row[name] for name in ("ds", "dk", "k", "b", "s", "t"))
        d = tables.thread_diameter(row["size"])
        params = {"head_diameter": dk, "head_height": k, "socket_size": s, "socket_depth": t,
                  "shoulder_diameter": ds, "shoulder_length": length,
                  "thread_diameter": d, "thread_length": b}
        code = program(
            params,
            """
head = cq.Workplane("XY").circle(head_diameter / 2).extrude(head_height)
head = (
    head.faces("<Z")
    .workplane()
    .polygon(6, socket_size, circumscribed=True)
    .cutBlind(-socket_depth)
)
shoulder = head.faces(">Z").workplane().circle(shoulder_diameter / 2).extrude(shoulder_length)
result = shoulder.faces(">Z").workplane().circle(thread_diameter / 2).extrude(thread_length)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[dk, dk, k + length + b],
            expected_volume=(circle_area(dk) * k - HEX_AREA_FACTOR * s * s * t
                             + circle_area(ds) * length + circle_area(d) * b),
            expected_cylinders={dk: 1, ds: 1, d: 1},
            # The designated length of a shoulder screw is its shoulder length.
            designation={"standard": self.STANDARD, "size": tables.short_size(row["size"]),
                         "length": f"{length:g}"},
        )

    def standard_parts(self) -> list[Part]:
        needed = {"ds", "dk", "k", "b", "s", "t", "short", "long"}
        return [self._part(row, length)
                for row in tables.load(self.TABLE)[self.STANDARD] if needed <= row.keys()
                for length in _lengths(self.STANDARD, row)]

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class HexStandoff:
    NAME = "hex_standoff"
    DESCRIPTION = "Hexagon standoff: a length of hexagon bar with a clearance hole through it"
    TABLE = "hex_nut_parameters"
    STANDARD = "iso4032"
    LARGEST_THREAD = 12.0  # standoffs hold boards and panels; they are not made for big bolts

    def _sizes(self) -> list[tuple[float, float]]:
        """(across flats, clearance hole) for each thread size both tables know.

        The hexagon is the size of that thread's nut, so the same spanner fits;
        the hole is the "Normal" clearance hole for that thread.
        """
        holes = tables.clearance_holes("Normal")
        sizes = []
        for row in tables.load(self.TABLE)[self.STANDARD]:
            hole = holes.get(tables.short_size(row["size"]))
            # Keep small threads, and only sizes that leave at least 1 mm of wall at the flats.
            if (hole is not None and "s" in row
                    and tables.thread_diameter(row["size"]) <= self.LARGEST_THREAD
                    and (row["s"] - hole) / 2 >= 1.0):
                sizes.append((row["s"], hole))
        return sizes

    def standard_parts(self) -> list[Part]:
        return []  # the tables give the cross-section only; lengths are sampled

    def sample(self, rng: random.Random) -> Part:
        across_flats, hole = rng.choice(self._sizes())
        # Length is our own choice: 1 to 6 times the across-flats size, 5 mm at least,
        # whole mm.
        length = span(rng, max(5.0, math.ceil(across_flats)), math.ceil(6 * across_flats), 1.0)
        params = {"across_flats": across_flats, "length": length, "hole_diameter": hole}
        code = program(
            params,
            """
result = (
    cq.Workplane("XY")
    .polygon(6, across_flats, circumscribed=True)
    .extrude(length)
    .faces(">Z")
    .workplane()
    .hole(hole_diameter)
)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[across_flats, across_flats * HEX_CORNER_FACTOR, length],
            expected_volume=(HEX_AREA_FACTOR * across_flats**2 - circle_area(hole)) * length,
            expected_cylinders={hole: 1},
        )


class KeyedHub:
    NAME = "keyed_hub"
    DESCRIPTION = "Keyed hub: a round hub whose bore has a keyway for a parallel key"
    TABLE = "shaft_key_parameters"
    STANDARD = "din6885"

    def standard_parts(self) -> list[Part]:
        return []  # the table gives the bore and keyway only; the hub's size is sampled

    def sample(self, rng: random.Random) -> Part:
        row = rng.choice(tables.load(self.TABLE)[self.STANDARD])
        # The table lists, per shaft diameter, the key width `b` and the depth `t2`
        # of the keyway in the hub, measured from the bore.
        bore, key_width, keyway_depth = float(row["size"]), row["b"], row["t2"]
        # Metal outside the keyway: a quarter to a half of the bore, at least 3 mm.
        wall = span(rng, max(3.0, math.ceil(0.25 * bore)), max(4.0, math.ceil(0.5 * bore)), 1.0)
        outer = math.ceil(bore + 2 * keyway_depth) + 2 * wall
        # Hubs are 0.8 to 2 bore diameters long.
        length = span(rng, max(4.0, math.ceil(0.8 * bore)), math.ceil(2 * bore), 1.0)
        params = {"outer_diameter": outer, "length": length, "bore_diameter": bore,
                  "keyway_width": key_width, "keyway_depth": keyway_depth}
        code = program(
            params,
            """
hub = (
    cq.Workplane("XY")
    .circle(outer_diameter / 2)
    .circle(bore_diameter / 2)
    .extrude(length)
)
keyway = cq.Workplane("XY").box(
    bore_diameter / 2 + keyway_depth, keyway_width, length, centered=(False, True, False)
)
result = hub.cut(keyway)
""",
        )
        # The keyway cutter is a rectangle from the axis out past the bore. What it
        # removes beyond the bore is the rectangle minus the part already inside the
        # bore, which is half of the circle's area within a strip as wide as the key.
        keyway_area = (key_width * (bore / 2 + keyway_depth)
                       - circle_strip_area(bore, key_width) / 2)
        area = circle_area(outer) - circle_area(bore) - keyway_area
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[outer, outer, length],
            expected_volume=area * length,
            expected_cylinders={outer: 1, bore: None},
        )


STANDARD_PART_FAMILIES = (
    ORing(), SetScrew(), CheeseHeadScrew(), CountersunkScrew(), ShoulderScrew(),
    HexStandoff(), KeyedHub(),
)
