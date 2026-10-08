"""Reading and resolving a plan one line at a time, with a way back.

The converters (structures.py, parts.py) write a candidate line, look at what the resolver
built, and take the line back if it is not what they wanted. `Session` holds the reader
and the resolver side by side and keeps them in step:

    session = Session(judge)
    reply = session.offer(text)       # read + resolve; nothing is kept yet
    session.keep()                    # the line stays
    session.drop()                    # the line never happened

A dropped line leaves no trace in either the reader or the resolver, so the finished
text reads and resolves from scratch exactly as it did here (`finish` checks that the
fresh reading gives the same lines).
"""

from __future__ import annotations

import copy

from forge.plan import echo_line, parse_plan
from forge.plan.model import Plan
from forge.plan.parser import _PlanParser
from forge.resolve.bodies import Body
from forge.resolve.judge import KernelJudge
from forge.resolve.resolver import Reply, Resolution, Resolver


class Session:
    def __init__(self, judge: KernelJudge) -> None:
        self.judge = judge
        self.resolver = Resolver(judge)
        self.reader = _PlanParser()
        self.text: list[str] = []
        self.replies: list[Reply] = []
        self._pending: tuple | None = None

    def offer(self, text: str) -> Reply:
        before = (copy.deepcopy(self.reader.state), list(self.reader.history),
                  copy.deepcopy(self.resolver.state), len(self.resolver.history),
                  len(self.resolver.stages))
        line = self.reader.read(len(self.text) + 1, text)
        reply = self.resolver.read(line)
        self._pending = (text, reply, before)
        return reply

    def keep(self) -> Reply:
        text, reply, _ = self._pending
        self.text.append(text)
        self.replies.append(reply)
        self._pending = None
        return reply

    def drop(self) -> None:
        _, _, (read_state, read_history, state, history, stages) = self._pending
        self.reader.state, self.reader.history = read_state, read_history
        self.resolver.state = state
        del self.resolver.history[history:]
        del self.resolver.stages[stages:]
        self._pending = None

    def mark(self) -> tuple:
        """Remember this point; `rollback(mark)` takes back every line kept since."""
        return (copy.deepcopy(self.reader.state), list(self.reader.history),
                copy.deepcopy(self.resolver.state), len(self.resolver.history),
                len(self.resolver.stages), len(self.text))

    def rollback(self, mark: tuple) -> None:
        read_state, read_history, state, history, stages, lines = mark
        self.reader.state, self.reader.history = read_state, read_history
        self.resolver.state = state
        del self.resolver.history[history:]
        del self.resolver.stages[stages:]
        del self.text[lines:]
        del self.replies[lines:]
        self._pending = None

    def made(self, reply: Reply) -> list[Body]:
        world = self.resolver.state.visible
        return [world.bodies[name] for name in reply.made]

    @property
    def bodies(self) -> list[Body]:
        return list(self.resolver.state.world.bodies.values())

    def finish(self) -> tuple[str, Plan, Resolution] | None:
        """The finished plan, or None if a fresh reading of the text differs from this one."""
        text = "\n".join(self.text) + "\n"
        plan = parse_plan(text)
        if [echo_line(line) for line in plan.lines] != [reply.echo for reply in self.replies]:
            return None
        world = self.resolver.state.world
        complete = self.resolver.state.done and all(reply.built for reply in self.replies)
        return text, plan, Resolution(list(self.replies), list(world.bodies.values()),
                                      set(world.touching), [], complete,
                                      self.resolver.by_arithmetic, self.resolver.by_kernel,
                                      self.judge.calls)
