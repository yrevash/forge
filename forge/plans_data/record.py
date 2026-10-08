"""One plan as a stored record: its two halves (the text, the resolver's exact result) and its labels.

    record = make_record(source, text, plan, resolution, origin)

The record is plain JSON. `solution_of(text)` re-derives the resolver's half from the
text alone, so the audit can compare it with what is stored.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict

from forge.plan import parse_plan
from forge.plan.model import Line, Plan
from forge.plans_data import config, coverage
from forge.resolve import contact
from forge.resolve import space as sp
from forge.resolve.bodies import Body
from forge.resolve.judge import KernelFailed, KernelJudge
from forge.resolve.resolver import Resolution, resolve_plan

ROUND = 6          # decimals kept for stored numbers: a millionth of a millimetre


def _r(value: float) -> float:
    rounded = round(float(value), ROUND)
    return 0.0 if rounded == 0 else rounded


def _vec(v) -> list[float]:
    return [_r(x) for x in v]


def _lean(item):
    """Drop empty fields so a stored line shows only what was written."""
    if isinstance(item, dict):
        return {k: _lean(v) for k, v in item.items() if v not in (None, [], {}, "")}
    if isinstance(item, (list, tuple)):
        return [_lean(v) for v in item]
    return item


def line_dict(line: Line) -> dict:
    """The reader's structured line as JSON (forge/plan/README.md: "What a line becomes")."""
    return _lean(asdict(line))


def body_dict(body: Body, touching: set[frozenset[str]]) -> dict:
    frame = body.frame
    touches = sorted(next(iter(pair - {body.name})) for pair in touching
                     if body.name in pair and len(pair) == 2)
    made = {
        "name": body.name, "owner": body.owner, "line": body.line, "shape": body.shape,
        "sizes": {slot: _r(value) for slot, value in body.sizes.items()},
        "low": _vec(frame.low), "high": _vec(frame.high), "size": _vec(frame.size),
        "centre": _vec(body.centre), "turn": [_vec(row) for row in body.matrix],
        "touches": touches, "on_ground": contact.on_ground(body),
    }
    if body.profile:
        made["profile"] = body.profile
    if body.shape == "wedge":
        made["pointing"], made["flat"] = body.pointing, body.flat
    if body.volume is not None:
        made["volume"] = _r(body.volume)
    if body.may_overlap:
        made["may_overlap"] = sorted(body.may_overlap)
    if body.cuts:
        made["features"] = [{"kind": cut.kind, "name": cut.name, "line": cut.line,
                             "slots": {s: _r(v) for s, v in cut.slots.items()},
                             "origin": _vec(cut.origin), "normal": _vec(cut.normal),
                             "first": _vec(cut.first),
                             "spots": [_vec(spot) for spot in cut.spots],
                             **({"open": _vec(cut.open_normal)} if cut.open_normal else {})}
                            for cut in body.cuts]
    return made


def solution(resolution: Resolution) -> dict:
    """The resolver's half of a record: replies, parts, overall frame."""
    bodies = resolution.bodies
    replies = [{"line": r.number, "reply": r.reply, "echo": r.echo, "notes": list(r.notes),
                "made": list(r.made), "by_kernel": r.by_kernel} for r in resolution.replies]
    made = {"replies": [_lean(r) | {"reply": r["reply"]} for r in replies],
            "parts": [body_dict(body, resolution.touching) for body in bodies],
            "complete": resolution.complete}
    if bodies:
        whole = sp.around([body.frame for body in bodies])
        made["overall"] = {"low": _vec(whole.low), "high": _vec(whole.high),
                           "size": _vec(whole.size)}
    return made


def fingerprint(parts: list[dict], overall: dict | None) -> str:
    """Rounded part count, overall size and (where exact) volume: the geometry's fingerprint."""
    if not parts:
        return "n0"
    volume = sum(part.get("volume", 0.0) for part in parts)
    size = "x".join(f"{v:.1f}" for v in sorted(overall["size"]))
    spots = hashlib.sha1(repr(sorted((p["shape"], tuple(round(v, 2) for v in p["low"] + p["high"]),
                                      len(p.get("features", []))) for p in parts)).encode())
    return f"n{len(parts)}|b{size}|v{volume:.2f}|{spots.hexdigest()[:10]}"


def plan_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]        # an id, not a secret


def make_record(source: str, text: str, plan: Plan, resolution: Resolution,
                origin: dict, versions: dict) -> dict:
    """Join the two halves. The split is added by splits.assign."""
    solved = solution(resolution)
    replies = [reply.reply for reply in resolution.replies]
    cells = coverage.cells_of(plan.lines, replies)
    record = {
        "id": plan_id(text),
        "source": source,
        "split": None,
        "lines": text.rstrip("\n").split("\n"),
        "plan": [line_dict(line) for line in plan.lines],
        **solved,
        "negative": not resolution.complete,
        "lines_by_kernel": sum(1 for reply in resolution.replies if reply.by_kernel),
        "cells": cells,
        "geom_fingerprint": fingerprint(solved["parts"], solved.get("overall")),
        "license": config.LICENSE,
        "generator_version": versions["plans_data"],
        "caption_style": "plan",
        "caption_model": "code:forge.plans_data",
        "provenance": {"language": config.LANGUAGE, "data_version": config.DATA_VERSION,
                       **versions, **origin},
    }
    return record


def solution_of(text: str, judge: KernelJudge | None = None) -> tuple[Plan, Resolution, dict]:
    """Read and resolve a plan from its text alone (what the audit compares against)."""
    plan = parse_plan(text)
    resolution = resolve_plan(plan, judge)
    return plan, resolution, solution(resolution)


class NoKernel(KernelJudge):
    """A judge for plans that must be decided by arithmetic alone.

    Asking it is counted in `asked` and answered with KernelFailed, which the resolver
    turns into `rejected: does not fit`. The caller looks at `asked` and throws the line
    (or, in the audit, the whole plan) away: such a reply is never stored.
    """

    def __init__(self) -> None:
        super().__init__(sandbox=None)
        self.asked = 0

    def look(self, bodies: list[Body]):
        self.asked += 1
        raise KernelFailed("this plan was to be decided without the CAD kernel")

    def close(self) -> None:
        pass
