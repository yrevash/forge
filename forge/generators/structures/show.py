"""Print structures in plain words: the prompt with its numbers marked, then every step.

Run:    uv run python -m forge.generators.structures.show [--kinds chair] [--count 1] [--seed 0]
        uv run python -m forge.generators.structures.show --formulas     (every formula, per kind)
"""

from __future__ import annotations

import argparse
import random

from forge.generators.structures import KINDS
from forge.generators.structures.draft import Structure
from forge.generators.structures.prompts import prompts_for
from forge.generators.structures.sampling import sample


def marked(prompt: dict) -> str:
    """The prompt text with [n] after each number and {option=choice} after each option phrase."""
    notes = [(m["end"], f"[{i}]") for i, m in enumerate(prompt["mentions"])]
    notes += [(o["end"], "{" + f"{o['option']}={o['choice']}" + "}")
              for o in prompt["options"] if o["by"] == "phrase"]
    text = prompt["text"]
    for position, note in sorted(notes, reverse=True):
        text = text[:position] + note + text[position:]
    return text


def describe(structure: Structure, prompt: dict) -> str:
    """One structure as text: where every slot of every step gets its number."""
    where = {m["param"]: i for i, m in enumerate(prompt["mentions"])}
    head = f"{structure.kind} {structure.id}  level {structure.level}  variant {structure.choices}"
    lines = [head, f"prompt: {marked(prompt)}"]
    lines += [f"  option by count: {o['option']}={o['choice']} <- {o['param']}"
              for o in prompt["options"] if o["by"] == "count"]
    for number, step in enumerate(structure.steps, start=1):
        lines.append(f"step {number}: {step.name}  (role {step.role}, placed {step.placement}, "
                     f"{len(step.parts)} {step.shape}{'es' if step.shape == 'box' else 's'})")
        for name, slot in step.slots.items():
            if slot.source == "stated":
                source = f"mention [{where[slot.param]}]"
                if slot.rule:
                    source += f"  (the rule {slot.rule} would give {slot.default_value:g})"
            elif slot.source == "default":
                args = ", ".join(f"{k}={v:g}" for k, v in slot.args.items())
                source = f"default  {slot.rule}({args})"
            else:
                source = f"arithmetic  {slot.formula}"
            lines.append(f"    {name:9s} = {slot.value:<8g} {source}")
    return "\n".join(lines)


def formulas(count: int = 400) -> str:
    """Every arithmetic formula each kind uses, gathered from random structures."""
    lines = []
    for name, kind in KINDS.items():
        rng = random.Random(f"formulas:{name}")
        seen: dict[str, str] = {}
        for _ in range(count):
            for step in sample(kind, rng).steps:
                for slot in step.slots.values():
                    if slot.source == "arithmetic":
                        seen.setdefault(f"{slot.param} = {slot.formula}", step.placement)
        lines.append(f"\n{name}")
        lines += [f"  {text}" for text in sorted(seen)]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--kinds", nargs="*", default=list(KINDS))
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--formulas", action="store_true")
    args = parser.parse_args()
    if args.formulas:
        print(formulas())
        return
    for name in args.kinds:
        rng = random.Random(f"show:{args.seed}:{name}")
        for _ in range(args.count):
            structure = sample(KINDS[name], rng)
            print(describe(structure, prompts_for(KINDS[name], structure, 1)[0][0]), "\n")


if __name__ == "__main__":
    main()
