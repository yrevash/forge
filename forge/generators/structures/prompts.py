"""Prompts for structures, with their mentions and option phrases.

Wording here and in kinds/*.py (names, phrases, sentence frames) is fixed
template text, like the wording in forge/generators/prompts.py, and
every prompt is labelled `template` so it can be filtered or replaced.
Numbers are never free-written: code fills them in from the structure.

A prompt is built from four sorts of piece:

    the name            "dining chair"
    headline sizes      "seat 420 by 420", "seat height 450"       (always)
    options, in words   "with arms", "round legs", "no back"       (levels b, c)
    counts              "4 slats", "five shelves"                  (levels b, c)
    stated defaults     "legs 50 square"                           (level c)

While a prompt is written, two things are recorded (never searched for afterwards):

    mentions   every NUMBER: where it is in the text, its value, the parameter
               it states and the (step, slot) that parameter fills
    options    every OPTION PHRASE: where it is, and which choice of which
               option it expresses. A choice that a count already expresses
               ("3 legs" for a three-legged stool) is recorded as `by: count`.

The recording reuses the marker mechanism of forge/generators/prompts.py
(`_tracked`, `_said`, `_read_marks`): a marker character is put after each
number or phrase as it is written and read back once the sentence is whole.
"""

from __future__ import annotations

import random
from types import ModuleType

from forge.generators.prompts import (
    FRAMES,
    MODEL,
    FeatureStyle,
    _finish,
    _moved,
    _read_marks,
    _said,
    _tracked,
    numbers_in,
)
from forge.generators.structures.draft import Structure

PROMPTS_PER_STRUCTURE = 3
SAY_DEFAULT_CHOICE = 0.2     # how often a default choice is said anyway ("no arms")


def _option_pieces(kind: ModuleType, structure: Structure, log: list,
                   rng: random.Random) -> tuple[list[str], list[dict]]:
    """Phrases for the option choices that get said, and the choices a count expresses."""
    stated = structure.stated()
    pieces, by_count = [], []
    for option, choice in structure.choices.items():
        is_default = choice == kind.OPTIONS[option][0]
        count = kind.OPTION_COUNTS.get((option, choice))
        if count in stated:
            # "3 slats in the back" already says the back is slatted. Saying it again in
            # words gave "with back slats, with 3 slats in the back", so the count stands alone.
            by_count.append({"option": option, "choice": choice, "by": "count", "param": count})
            continue
        if is_default and ((option, choice) not in kind.OPTION_WORDS
                             or rng.random() > SAY_DEFAULT_CHOICE):
            continue                 # a default choice is usually left unsaid
        phrase = rng.choice(kind.OPTION_WORDS[(option, choice)])
        marker = _tracked(0.0, f"option:{option}={choice}", log)
        # The "+" (may follow "with") stays in front of the marked phrase.
        pieces.append(("+" if phrase.startswith("+") else "") + _said(marker, phrase.lstrip("+")))
    return pieces, by_count


def _number_pieces(kind: ModuleType, structure: Structure, v: dict, s: FeatureStyle) -> list[str]:
    """One phrase per stated count and per stated default."""
    pieces = []
    for param, sort in structure.stated().items():
        if sort == "count":
            one, several = s.rng.choice(kind.COUNT_THINGS[param])
            pieces.append("+" + s.many(v[param], one if v[param] == 1 else several, plain=True))
        elif sort == "override":
            template = s.rng.choice(kind.OVERRIDE_WORDS[param])
            pieces.append(template.format(v=s.mm(v[param])))
    return pieces


def _sentence(name: str, head: list[str], extras: list[str], rng: random.Random) -> str:
    """Put the pieces together in one of three shapes: shorthand, a request, a bullet list."""
    can_follow_with = [p[1:] for p in extras if p.startswith("+")]
    others = [p for p in extras if not p.startswith("+")]
    if len(can_follow_with) > 1 and rng.random() < 0.6:
        # "with arms, round legs and 4 slats"
        said = ["with " + ", ".join(can_follow_with[:-1]) + " and " + can_follow_with[-1]]
    else:
        said = [f"with {p}" if rng.random() < 0.6 else p for p in can_follow_with]
    said += others
    rng.shuffle(said)
    if rng.random() < 0.3:
        rng.shuffle(head)            # people do not give sizes in a fixed order
    pieces = head + said
    shape = rng.choice(["short", "short", "request", "request", "bullets"])
    if shape == "short":
        return name + rng.choice([", ", ", ", " ", ": ", " - "]) \
            + rng.choice([", ", ", ", ", ", "; "]).join(pieces)
    frame = rng.choice(FRAMES).format(thing=name)
    if shape == "request":
        return frame + rng.choice([", ", ", ", ": ", " - ", " "]) + ", ".join(pieces)
    bullet = rng.choice(["\n- ", "\n- ", "\n* "])
    return frame + rng.choice([":", ",", ""]) + bullet + bullet.join(pieces)


def write_prompt(kind: ModuleType, structure: Structure, rng: random.Random) -> dict:
    """One prompt for a structure, at the structure's own level, with mentions and options."""
    log: list = []
    v = {param: _tracked(value, param, log) for param, value in structure.params.items()}
    s = FeatureStyle(rng)
    name = rng.choice(structure.names)
    head = list(kind.say_headline(v, structure.choices, s))
    extras, by_count = [], []
    if structure.level != "a":
        extras, by_count = _option_pieces(kind, structure, log, rng)
        extras += _number_pieces(kind, structure, v, s)
    clean, found = _read_marks(_sentence(name, head, extras, rng), log)
    passes: list = []
    text = _finish(clean, rng, passes)

    mentions, options = [], by_count
    for item in sorted(found, key=lambda item: item["start"]):
        start, end = _moved(item["start"], passes), _moved(item["end"], passes)
        if item["param"].startswith("option:"):
            option, choice = item["param"].removeprefix("option:").split("=")
            options.append({"option": option, "choice": choice, "by": "phrase",
                            "start": start, "end": end, "text": text[start:end]})
        else:
            index, slot, _ = structure.slot(item["param"])
            mentions.append({"start": start, "end": end, "text": text[start:end],
                             "value": item["value"], "param": item["param"],
                             "fills": [[index, slot]]})
    return {"level": structure.level, "model": MODEL, "text": text,
            "mentions": mentions, "options": options}


def prompt_problems(kind: ModuleType, structure: Structure, prompt: dict) -> list[str]:
    """What is wrong with a prompt. Empty means verified.

    - every number in the text is a recorded mention, and every mention states
      a `stated` slot of this structure with that slot's value;
    - every `stated` slot is mentioned;
    - every option phrase names a choice this structure really has, and every
      choice that is not the default is expressed (by a phrase or by a count).
    """
    text, problems = prompt["text"], []
    stated = structure.stated()
    in_text = sorted(numbers_in(text))
    recorded = sorted(m["value"] for m in prompt["mentions"])
    if in_text != recorded:
        problems.append(f"numbers in the text {in_text} are not the recorded mentions {recorded}")
    for m in prompt["mentions"]:
        if m["param"] not in stated:
            problems.append(f"mention of {m['param']}, which is not a stated slot")
        elif float(structure.params[m["param"]]) != m["value"]:
            problems.append(f"mention of {m['param']} says {m['value']:g}")
        if numbers_in(text[m["start"]:m["end"]]) != [m["value"]]:
            problems.append(f"mention {m['text']!r} is not where it is recorded to be")
        index, slot = m["fills"][0]
        if structure.steps[index].slots[slot].param != m["param"]:
            problems.append(f"mention of {m['param']} points at the wrong slot")
    said = {m["param"] for m in prompt["mentions"]}
    problems += [f"stated slot {param} is not in the prompt" for param in stated
                 if param not in said]

    expressed = set()
    for item in prompt["options"]:
        if structure.choices.get(item["option"]) != item["choice"]:
            problems.append(f"option phrase says {item['option']}={item['choice']}, "
                            "which this structure does not have")
        if item["by"] == "phrase" and not text[item["start"]:item["end"]].strip():
            problems.append(f"option phrase for {item['option']} is empty")
        if item["by"] == "count" and item["param"] not in said:
            problems.append(f"option {item['option']} rests on a count that is not said")
        expressed.add(item["option"])
    for option, choice in structure.choices.items():
        if choice != kind.OPTIONS[option][0] and option not in expressed:
            problems.append(f"{option}={choice} is not the default and is not said")
    if structure.level == "a" and prompt["options"]:
        problems.append("a level a prompt must not state options")
    return problems


def prompts_for(kind: ModuleType, structure: Structure,
                wanted: int = PROMPTS_PER_STRUCTURE) -> tuple[list[dict], int]:
    """Up to `wanted` different verified prompts, and how many drafts were thrown away.

    The same structure always gets the same prompts: the random stream is seeded
    by its id. A draft is thrown away if it fails the check (a rare typo can turn
    a word into a number word) or repeats an earlier text.
    """
    prompts, seen, discarded = [], set(), 0
    for attempt in range(wanted * 4):
        if len(prompts) == wanted:
            break
        prompt = write_prompt(kind, structure, random.Random(f"{structure.id}:prompt:{attempt}"))
        if prompt_problems(kind, structure, prompt):
            discarded += 1
        elif prompt["text"] not in seen:
            seen.add(prompt["text"])
            prompts.append({"variant": f"{structure.level}-{len(prompts)}", **prompt})
    return prompts, discarded
