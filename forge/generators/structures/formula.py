"""Tiny arithmetic formulas, written as text: "seat_top - seat_thickness".

A structure has many numbers that nobody states and no rule chooses: they
follow from other numbers (a leg is as long as the seat is high, minus the
seat's thickness). Each of those is kept as a FORMULA STRING, for three reasons:

- the row can show the model's teacher exactly where the number comes from;
- the very same text is written into the CadQuery program, so the program
  reads like something a person would write;
- evaluating the text here gives the expected value without running the program.

Only numbers, names, + - * / and brackets are allowed. The text is parsed with
Python's own parser (`ast`) and walked by hand; nothing is ever `eval`-ed.
"""

from __future__ import annotations

import ast
import operator
import re
from collections.abc import Callable, Mapping

Number = float | int

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub,
           ast.Mult: operator.mul, ast.Div: operator.truediv}
_NAME = re.compile(r"\b[a-z_][a-z0-9_]*\b")


class BadFormula(ValueError):
    """The text uses something other than numbers, names, + - * / and brackets."""


def evaluate(formula: str, values: Mapping[str, Number]) -> Number:
    """Work out a formula. The operations run in the same order Python would run them,
    so the answer is bit-for-bit what the generated program computes."""
    def walk(node: ast.AST) -> Number:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise BadFormula(f"{node.id!r} in {formula!r} is not a known number")
            return values[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -walk(node.operand)
        raise BadFormula(f"{formula!r} uses something that is not plain arithmetic")
    return walk(ast.parse(formula, mode="eval"))


def names_in(formula: str) -> list[str]:
    """The names a formula reads, in order of appearance."""
    return _NAME.findall(formula)


def rename(formula: str, new_name: Callable[[str], str]) -> str:
    """The same formula with every name replaced: 'top - height' -> 'aprons_top - aprons_height'."""
    return _NAME.sub(lambda match: new_name(match.group()), formula)
