"""Fastener families whose dimensions come from standards tables.

Simplifications, the same for every fastener here: threads are not modelled
(a threaded hole is a plain hole at the nominal thread diameter, a threaded
shank is a plain cylinder) and edges are not chamfered.
"""

from __future__ import annotations

import random

from forge.generators import tables
from forge.generators.base import (
    HEX_AREA_FACTOR,
    HEX_CORNER_FACTOR,
    Part,
    circle_area,
    program,
)


class HexNut:
    NAME = "hex_nut"
    DESCRIPTION = "Hexagon nut: a hexagonal prism with a centre hole"
    TABLE = "hex_nut_parameters"

    def _part(self, standard: str, row: dict) -> Part:
        s, m, d = row["s"], row["m"], tables.thread_diameter(row["size"])
        code = program(
            {"across_flats": s, "thickness": m, "bore_diameter": d},
            """
result = (
    cq.Workplane("XY")
    .polygon(6, across_flats, circumscribed=True)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .hole(bore_diameter)
)
""",
        )
        return Part(
            family=self.NAME,
            params={"across_flats": s, "thickness": m, "bore_diameter": d},
            code=code,
            expected_bbox=[s, s * HEX_CORNER_FACTOR, m],  # flats face X, corners on Y
            expected_volume=(HEX_AREA_FACTOR * s * s - circle_area(d)) * m,
            expected_cylinders={d: 1},
            designation={"standard": standard, "size": tables.short_size(row["size"])},
        )

    def standard_parts(self) -> list[Part]:
        return [self._part(std, row) for std, rows in tables.load(self.TABLE).items()
                for row in rows if "s" in row and "m" in row]

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class SquareNut:
    NAME = "square_nut"
    DESCRIPTION = "Square nut: a square prism with a centre hole"
    TABLE = "square_nut_parameters"

    def _part(self, standard: str, row: dict) -> Part:
        s, m, d = row["s"], row["m"], tables.thread_diameter(row["size"])
        code = program(
            {"across_flats": s, "thickness": m, "bore_diameter": d},
            """
result = (
    cq.Workplane("XY")
    .rect(across_flats, across_flats)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .hole(bore_diameter)
)
""",
        )
        return Part(
            family=self.NAME,
            params={"across_flats": s, "thickness": m, "bore_diameter": d},
            code=code,
            expected_bbox=[s, s, m],
            expected_volume=(s * s - circle_area(d)) * m,
            expected_cylinders={d: 1},
            designation={"standard": standard, "size": tables.short_size(row["size"])},
        )

    def standard_parts(self) -> list[Part]:
        return [self._part(std, row) for std, rows in tables.load(self.TABLE).items()
                for row in rows]

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class PlainWasher:
    NAME = "plain_washer"
    DESCRIPTION = "Plain washer: a flat ring"
    TABLE = "plain_washer_parameters"

    def _part(self, standard: str, row: dict) -> Part:
        d1, d2, h = row["d1"], row["d2"], row["h"]
        code = program(
            {"outer_diameter": d2, "inner_diameter": d1, "thickness": h},
            """
result = (
    cq.Workplane("XY")
    .circle(outer_diameter / 2)
    .circle(inner_diameter / 2)
    .extrude(thickness)
)
""",
        )
        return Part(
            family=self.NAME,
            params={"outer_diameter": d2, "inner_diameter": d1, "thickness": h},
            code=code,
            expected_bbox=[d2, d2, h],
            expected_volume=(circle_area(d2) - circle_area(d1)) * h,
            expected_cylinders={d1: 1, d2: 1},
            designation={"standard": standard, "size": row["size"]},
        )

    def standard_parts(self) -> list[Part]:
        return [self._part(std, row) for std, rows in tables.load(self.TABLE).items()
                for row in rows if {"d1", "d2", "h"} <= row.keys()]

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class HexBolt:
    NAME = "hex_bolt"
    DESCRIPTION = "Hexagon head bolt: a hexagonal head with a round shank"
    TABLE = "hex_head_parameters"

    def _part(self, standard: str, row: dict, length: float) -> Part:
        s, k, d = row["s"], row["k"], tables.thread_diameter(row["size"])
        params = {"across_flats": s, "head_height": k, "shank_diameter": d, "length": length}
        code = program(
            params,
            """
head = cq.Workplane("XY").polygon(6, across_flats, circumscribed=True).extrude(head_height)
result = head.faces(">Z").workplane().circle(shank_diameter / 2).extrude(length)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[s, s * HEX_CORNER_FACTOR, k + length],  # flats face X
            expected_volume=HEX_AREA_FACTOR * s * s * k + circle_area(d) * length,
            expected_cylinders={d: 1},
            designation={"standard": standard, "size": tables.short_size(row["size"]),
                         "length": f"{length:g}"},
        )

    def _lengths(self, standard: str, row: dict) -> list[float]:
        """Nominal lengths this size is made in, according to the tables."""
        try:
            nominal = tables.nominal_lengths(standard)
        except KeyError:
            return []
        return [x for x in nominal if row["short"] <= x <= row["long"]]

    def standard_parts(self) -> list[Part]:
        return [self._part(std, row, length)
                for std, rows in tables.load(self.TABLE).items()
                for row in rows if {"s", "k", "short", "long"} <= row.keys()
                for length in self._lengths(std, row)]

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class SocketHeadCapScrew:
    NAME = "socket_head_cap_screw"
    DESCRIPTION = "Socket head cap screw: a round head with a hexagon socket, and a round shank"
    TABLE = "socket_head_cap_parameters"

    def _part(self, standard: str, row: dict, length: float) -> Part:
        dk, k, s, t = row["dk"], row["k"], row["s"], row["t"]
        d = tables.thread_diameter(row["size"])
        params = {"head_diameter": dk, "head_height": k, "socket_size": s, "socket_depth": t,
                  "shank_diameter": d, "length": length}
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
result = head.faces(">Z").workplane().circle(shank_diameter / 2).extrude(length)
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[dk, dk, k + length],
            expected_volume=(circle_area(dk) * k - HEX_AREA_FACTOR * s * s * t
                             + circle_area(d) * length),
            expected_cylinders={dk: 1, d: 1},
            designation={"standard": standard, "size": tables.short_size(row["size"]),
                         "length": f"{length:g}"},
        )

    def _lengths(self, standard: str, row: dict) -> list[float]:
        try:
            nominal = tables.nominal_lengths(standard)
        except KeyError:
            return []
        return [x for x in nominal if row["short"] <= x <= row["long"]]

    def standard_parts(self) -> list[Part]:
        return [self._part(std, row, length)
                for std, rows in tables.load(self.TABLE).items()
                for row in rows
                if {"dk", "k", "s", "t", "short", "long"} <= row.keys()
                and row["size"].startswith("M")
                for length in self._lengths(std, row)]

    def sample(self, rng: random.Random) -> Part:
        return rng.choice(self.standard_parts())


class ShaftKey:
    NAME = "shaft_key"
    DESCRIPTION = "Parallel key: a rectangular bar with a standard cross-section"
    TABLE = "shaft_key_parameters"

    def _sections(self) -> list[tuple[str, float, float]]:
        """Distinct (standard, width, height) cross-sections in the table."""
        seen = []
        for std, rows in tables.load(self.TABLE).items():
            for row in rows:
                section = (std, row["b"], row["h"])
                if section not in seen:
                    seen.append(section)
        return seen

    def _part(self, standard: str, b: float, h: float, length: float) -> Part:
        params = {"key_length": length, "key_width": b, "key_height": h}
        code = program(
            params,
            """
result = cq.Workplane("XY").box(key_length, key_width, key_height, centered=(True, True, False))
""",
        )
        return Part(
            family=self.NAME,
            params=params,
            code=code,
            expected_bbox=[length, b, h],
            expected_volume=length * b * h,
            designation={"standard": standard, "size": f"{b:g}x{h:g}x{length:g}"},
        )

    def standard_parts(self) -> list[Part]:
        return []  # the table gives cross-sections only; lengths are sampled

    def sample(self, rng: random.Random) -> Part:
        std, b, h = rng.choice(self._sections())
        # Length is our own choice (the table has no length series): 2 to 10 widths, whole mm.
        length = float(rng.randint(round(2 * b), round(10 * b)))
        return self._part(std, b, h, length)
