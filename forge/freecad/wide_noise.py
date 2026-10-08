"""The THIRD mistake mix ("v3"): everything of the second (noise.py), plus three additions.

Why (7 Oct 2026). On parts from outside its training generator the first Forge-S1 model
  - called a correct 41.5 mm hexagon wrong, because no training number had a decimal there;
  - barely noticed two plan items swapped;
  - never recovered from a wrong choice of its own, where mistakes come several at a time.
The second mix has no mistake for any of the three. This file adds them, as options of the
new recorder (wide_play.py). noise.py is not changed, so the `_v2` slices read as before.

1. ORDER mistakes: a builder who works on the wrong plan item.
       order_later_first   at the start of plan item k, a LATER item j is built instead
       order_swapped       item j is built, then item k: the two in swapped order
   The whole recipe of the wrong item is issued, one command per step, each with chance
   ORDER_NEXT of going on. Its first commands are often exactly the teacher's (every cut
   starts with "select the XY plane, new sketch on the top face"): those are carried out as
   the teacher's own, and the mistake begins at the first command that differs.
   WHAT THE TEACHER SAYS. The teacher demands the plan's order (teacher.py walks the plan
   in order). From the first command that differs it says `undo`, until the document is a
   beginning of the plan again, then builds item k, then item j. That is so even when the
   two items do not touch and either order gives the same solid; how often that is the
   case is measured by wide_order_proof.py.

2. DECIMAL slips: wrong numbers that differ from the right one only in the decimals
   (`decimal_slip`, a flavour of wrong_argument): 41.5 typed as 41, 42, 41.05, 4.15, 415,
   41.6. With numbers on grids the model could not learn that 41.5 and 41 are different
   numbers; with free numbers it must.

3. BURSTS: in a labelled share of the sessions (header `burst: true`) a carried-out mistake
   is followed, with chance BURST_START, by 1 to BURST_MOST more wrong commands in a row
   before the teacher gets a turn. A model's own mistakes come like that: the state after
   one wrong choice is unfamiliar, and the next choice is wrong more often.
"""

from __future__ import annotations

import random

from forge.freecad import noise
from forge.freecad.catalogue import COMMANDS
from forge.freecad.noise import Moment, is_target
from forge.freecad.recipes import flatten, recipe
from forge.freecad.teacher import Script, same

MIX = "v3"
ORDER_KINDS = ("order_later_first", "order_swapped")
ORDER_CHANCE = 0.22     # of the wrong commands drawn at the start of a plan item
ORDER_NEXT = 0.94       # the chance, per command, that the wrong item's recipe goes on
ORDER_NEIGHBOUR = 0.6   # the wrong item is the very next one (else: any later one)
DECIMAL_CHANCE = 0.1    # of the wrong commands drawn at an on-plan step
BURST_SHARE = 0.2       # of the sessions with noise
BURST_LEVELS = (0.2, 0.3, 0.4)
BURST_START = 0.5
BURST_MOST = 5

Command = tuple[str, dict]


def decimal_slips(rng: random.Random, value: float) -> list[float]:
    """Wrong values that are the right one with its decimals mistyped."""
    value = float(value)
    whole, text = float(int(value)), repr(round(abs(value), 4))
    found = [whole, whole + (1.0 if value >= 0 else -1.0), round(value, 1), value * 10, value / 10,
             value + rng.choice((0.1, -0.1, 0.01, -0.01, 0.05, -0.05, 0.5, -0.5))]
    digits = text.split(".")[1]
    if len(digits) >= 2 and digits[0] != digits[1]:         # 7.25 -> 7.52
        swapped = float(f"{text.split('.')[0]}.{digits[1]}{digits[0]}{digits[2:]}")
        found.append(swapped if value >= 0 else -swapped)
    if digits != "0":                                       # 41.5 -> 41.05
        deeper = float(f"{text.split('.')[0]}.0{digits}")
        found.append(deeper if value >= 0 else -deeper)
    return [round(other, 4) for other in found if not same(other, value)]


def _decimal_slip(rng: random.Random, m: Moment) -> tuple[str, dict, str] | None:
    """A teacher command with one number wrong in its decimals."""
    choices = [(target, arg) for target in m.advice.targets for arg in target.args
               if COMMANDS[target.command]["args"][arg] in ("mm", "pos")]
    if not choices:
        return None
    target, arg = rng.choice(choices)
    kind = COMMANDS[target.command]["args"][arg]
    values = [value for value in decimal_slips(rng, target.args[arg]) if kind == "pos" or value > 0]
    if not values:
        return None
    return target.command, {**target.args, arg: rng.choice(values)}, "decimal_slip"


def order_mistake(rng: random.Random, m: Moment, script: Script,
                  ) -> tuple[str, list[Command]] | None:
    """(kind, the commands of the wrong build), at the start of a plan item with a later one."""
    item = m.advice.active
    if not m.advice.on_plan or not m.item_start or item is None or item == 0 \
            or item + 1 >= len(m.plan):
        return None
    later = item + 1 if rng.random() < ORDER_NEIGHBOUR else rng.randrange(item + 1, len(m.plan))
    kind = rng.choice(ORDER_KINDS)
    items = [later] if kind == "order_later_first" else [later, item]
    commands = [(command.name, dict(command.args)) for index in items
                for command in flatten(recipe(m.plan[index], m.context), rng)]
    return kind, commands


def wrong_command(rng: random.Random, m: Moment) -> tuple[str, dict, str, str | None] | None:
    """One wrong command of the third mix: a decimal slip sometimes, else the second mix's."""
    if m.advice.on_plan and rng.random() < DECIMAL_CHANCE:
        made = _decimal_slip(rng, m)
        if made is not None and not is_target(made[:2], m.advice):
            return made[0], made[1], "wrong_argument", made[2]
    return noise.wrong_command_with_detail(rng, m)
