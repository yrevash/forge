# plan/ — the strict reader for the plan language

**The contract is the plan language (Draft 2), as read by `forge/plan/`.** This folder implements the *reading* side of it: text in, a structured list out, and for every line that is not in the language a reason and the piece of text that caused it. If this code and that file disagree, one of them is a bug. The tests run every example in the document through this reader.

Nothing here builds anything. There is no geometry and no CAD kernel, so it runs in milliseconds. It is the "paper test" of the plan language: a planner writes plans, this parser says which lines are in the vocabulary and which move is missing.

```
plan/
  grammar.py    the vocabulary as tables: shapes and their sizes, placements, faces, features
  model.py      what a parsed plan is made of (Plan, Line, Size, Placement ...) and the list of reasons
  clauses.py    read ONE piece of a part line: a size, a placement, an option, an alignment, a repetition
  features.py   read a feature line: `on X's FACE: FEATURE SLOT N, ..., POSITION`
  names.py      find the part a name points at (the plural / singular tolerance)
  checks.py     rules about one whole line that need no geometry: `rest`, turning, options, ...
  parser.py     read a whole plan, line by line: names defined earlier, sets, groups, undo, done
  echo.py       print a parsed line back in one fixed wording
  check.py      check a file or folder of plans and print / write the summary        [command]
  examples/     the four plans of PLAN_LANGUAGE §11 and §12, word for word
```

No dependency was added: the parser is hand-written (one regular expression per form in `clauses.py`, the rules about a whole line in `checks.py`, and the rules about a whole plan in `parser.py`).

## Use it

```
uv run python -m forge.plan.check forge/plan/examples --echo
```

prints every line as `ok` or `REJECTED` with the reason, then a summary, and writes the summary to `runs/plan-check/summary.json` (change with `--json PATH`; `--note TEXT` stores a sentence in it). A folder is searched for `.txt` and `.plan` files, one plan per file.

```python
from forge.plan import parse_plan, echo_line

plan = parse_plan(text)
plan.accepted                    # True only if every line is accepted and the plan ends with done
for line in plan.lines:
    line.accepted, line.rejections, line.notes
    echo_line(line)              # "seat: box 420 by 420 by 30, above ground, top at height 450"
```

## What a line becomes

| Field of `Line` | Holds |
| --- | --- |
| `kind` | `part`, `feature`, `group`, `end`, `undo`, `undo to`, `done` |
| `name`, `in_group` | the part or group this line defines; the group it sits in |
| `shape`, `profile` / `group` | one of the ten shapes (and a bar's profile), or the group being placed |
| `sizes` | one `Size` per slot in table order: `slot`, `source` (`number`, `same as`, `share`, `rest`, `default`), and the value or the part and dimension pointed at |
| `orientation`, `pointing`, `flat` | `standing` / `lying along ...`; `up` / `down` / ...; a wedge's second whole face |
| `placement` | the one placement: `kind`, the parts it names, and for `spans` the two places |
| `options` | `gap`, `sunk`, `across` |
| `alignments` | none, one or several; every distance in them is a `Size` (a number or a reference) |
| `repetition` | none or one |
| `copies` | how many parts the line makes (sets: §7 of the document); `None` when it cannot be told |
| `target`, `face`, `feature` | for a feature line: the part, the face, and the feature with its step kind, slots and position |
| `rejections` | why the line was not accepted: `reason`, `fragment`, `detail` |
| `notes` | warnings on a line that IS in the language |

## Rejection reasons (the fixed list, `model.REASONS`)

| Reason | Meaning |
| --- | --- |
| `unknown shape` | not one of the ten shapes, not a declared group, or a bar without a profile |
| `unknown placement` | a piece where the placement belongs (or starting like one) that is not a §5 form; `above ground` with no height; `between` on names that cannot be paired; a `spans` end that is not a place or is on a set |
| `unknown alignment` | a piece starting like an alignment that is not a §6 form; two faces that cannot line up |
| `unknown repetition` | a piece starting like a repetition that is not a §7 form |
| `unknown size form` | a size or distance that is not one of the §4 forms; sizes joined by commas |
| `unknown orientation` | turning words that are not in §4, or on a shape that cannot lie or point |
| `unknown feature` | not one of the sixteen §9 features, or a slot the feature does not have |
| `unknown part` | a name no earlier line defined (or one visible only inside another group) |
| `not in the language` | none of the above fits |
| `wrong number of sizes` | fewer or more sizes than the shape's row in §4; sizes on a group |
| `missing placement` | no placement on a part line |
| `more than one placement` / `more than one repetition` | |
| `duplicate name` | the name is already used, anywhere in the plan |
| `rest not defined` | `rest` on a size that may not be `rest`, or where the line does not fix both ends |
| `first part not on ground` | the first part of a plan placed against something |
| `out of place` | a valid form in the wrong position (a line after `done`, a placement that is not first, a repetition that is not last, turning words after a comma, `end` with no group, a group inside a group, `undo` with nothing built), or on a line where it cannot apply (`gap` without a face placement, `around` for a part that is not a tube, `inside` a part that is not hollow, `inset` with nothing to be inset from, `mirrored` with `on ground`, a circle with a side placement, a feature position on a feature that has none) |
| `missing done` | the plan does not end with `done` (reported on the plan, not on a line) |

The first seven and `not in the language` are the **missing moves**: their fragments are what the summary counts as "missing phrases", with part names replaced by `X` and numbers by `N`.

## Tolerances (everything else must be exact)

1. Capital letters anywhere.
2. Extra spaces, tabs and indentation; blank lines; a space before a comma or colon.
3. `by`, `x` or `×` between sizes (`10 by 20`, `10 x 20`, `10x20`, `10 × 20`), also in `grid 3 x 4`.
4. A trailing `mm` on any number (`40mm`, `40 mm`).
5. Apostrophes: `X's`, `X’s`, and after a plural `posts's` or `posts'`.
6. Singular or plural of a part name when exactly one part fits (`under leg` finds `legs`); an exact name always wins. The line gets a note saying how it was read.
7. A space for a hyphen in direction pairs: `mirrored left right`, `front left corner`, `top front edge`. The words of a place may come in any order (`left-top-front corner`); the echo puts them in the fixed order.
8. The default words written out: `standing`, `pointing up`, `centred`.

Deliberately **not** tolerated: articles (`on the ground`, `on top of the seat`), other units, number words, `;` for `,`, list numbering, a trailing comma or full stop, commas between sizes, turning words after a comma, `double of X's length`, any other order of the pieces.

## Things the reader does that the document does not spell out

- **A rejected line** changes nothing, except that its name is remembered as a "ghost": later lines that point at it are accepted with a note instead of each being rejected as `unknown part`, and the name may be written again. This keeps one mistake from being counted many times in a whole-plan check. The executor itself would answer `rejected: unknown part`.
- **Counting copies.** The reader counts how many parts each name stands for (4 for `at each corner`, the target's count for a placement on a set, 1 with `across`), so it can check `between X` (exactly two) and `between A and B` (same size, or one single). A group or a ghost has an unknown count and is not checked.
- **Hollow parts.** A `tube` is hollow; any other part becomes hollow when a `hollowed out` line on it is accepted. `inside X` and `X's inner FACE` are rejected for a part known not to be hollow.
- **Notes instead of rejections** for lines whose numbers disagree but whose words are all valid, because that is the executor's `rejected: does not fit`: `on ground` with a height and a different `top at height`; `on ground` with `bottom at height` other than 0; `down to ground` with a number for the height.
- **The direction of a `rest` size** is worked out from the turning words (a lying cylinder's height runs sideways) and checked against the directions in which the line fixes both ends. `between` is taken on trust: only geometry knows which way the gap runs.

## Tests

`tests/test_plan_language.py` (every form in the tables parses and echoes), `tests/test_plan_rejections.py` (every reason, the plan-wide rules, `rest`, the tolerances), `tests/test_plan_examples.py` (the document's four plans are accepted in full and match `examples/`; every one-line example in the document's tables is in the language; the command).
