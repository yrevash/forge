"""Every example in the plan language document (Draft 3) is read by the reader and built.

Five checks tie the document to the code:
  1. the whole plans in the document (chair, robot dog, rocket, every form once) are
     accepted in full by the strict reader, line by line;
  2. forge/plan/examples/ holds those plans word for word;
  3. the resolver builds each of them and every check passes (no overlap, every part
     touching, one body on the ground, kernel measurements equal to the arithmetic);
  4. the "every form once" plan really uses every form;
  5. every one-line example anywhere in the document is in the language (the only
     thing allowed to be wrong with it on its own is that its parts are not defined).
If the document changes, these tests say what the code no longer agrees with.

Check 3 is the only one that needs the CAD kernel (one warm sandbox, about 25 s).
"""

import json
import re
from pathlib import Path

import pytest

from forge.plan import echo_line, parse_plan
from forge.plan import grammar as g
from forge.plan.check import main, phrase_pattern
from forge.resolve import resolve_plan
from forge.resolve import space as sp
from forge.resolve.judge import KernelJudge
from forge.resolve.verify import verify
from forge.sandbox import Sandbox

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "forge" / "plan" / "examples"
_DOCUMENT_PATH = ROOT / "docs" / "PLAN_LANGUAGE.md"
if not _DOCUMENT_PATH.exists():       # the document is not shipped with every copy of the code
    pytest.skip("the plan language document is not present", allow_module_level=True)
DOCUMENT = _DOCUMENT_PATH.read_text()
# The fenced blocks that are whole plans, by the name of their first part.
PLANS = {block.split(":")[0]: block
         for block in re.findall(r"```\n(.*?)```", DOCUMENT, re.DOTALL)
         if block.rstrip().endswith("done")}
FILES = {"seat": "chair.txt", "body": "robot_dog.txt", "first stage": "rocket.txt",
         "base": "every_form.txt"}
# What each plan builds: how many solids, and the overall length, depth, height.
BUILT = {"seat": (16, (420, 420, 900)), "body": (18, (250, 480, 580)),
         "first stage": (10, (870, 870, 7100)), "base": (82, (650, 460, 700))}


def test_the_document_has_the_four_plans():
    assert set(PLANS) == set(FILES)


def test_the_document_says_which_draft_it_is():
    assert "**Status:** Draft 3, 6 Oct 2026." in DOCUMENT


# --- (a) the reader accepts every example completely ----------------------------------------

@pytest.mark.parametrize("first_part", list(FILES))
def test_plan_in_the_document_is_accepted_in_full(first_part):
    plan = parse_plan(PLANS[first_part])
    for line in plan.lines:
        assert line.accepted, (line.number, line.text, line.rejections)
        assert not line.notes, (line.number, line.text, line.notes)
    assert plan.accepted


@pytest.mark.parametrize(("first_part", "file"), list(FILES.items()))
def test_example_files_are_the_documents_plans_word_for_word(first_part, file):
    assert (EXAMPLES / file).read_text() == PLANS[first_part]


def test_the_examples_folder_holds_nothing_else():
    assert sorted(path.name for path in EXAMPLES.iterdir()) == sorted(FILES.values())


@pytest.mark.parametrize("first_part", list(FILES))
def test_the_documents_plans_are_written_in_the_canonical_wording(first_part):
    """What a planner copies from the document is exactly what the executor echoes."""
    for line in parse_plan(PLANS[first_part]).lines:
        # Two things the echo evens out: "posts' top" is echoed as "posts's top", and
        # `centred` (the default position of a feature) is not echoed.
        written = line.text.strip().replace("s' ", "s's ").replace(", centred", "")
        assert echo_line(line) == written


# --- (b) the resolver builds every example and every check passes ---------------------------

@pytest.fixture(scope="module")
def sandbox():
    with Sandbox() as box:
        yield box


@pytest.mark.parametrize("first_part", list(FILES))
def test_plan_in_the_document_builds_and_passes_every_check(first_part, sandbox):
    done = resolve_plan(parse_plan(PLANS[first_part]), KernelJudge(sandbox))
    rejected = [(reply.number, reply.text, reply.reply, reply.notes)
                for reply in done.replies if not reply.built]
    assert done.complete, rejected
    # Build the reference program in the sandbox; compare the kernel with the arithmetic.
    verdict = verify(done.bodies, done.touching, sandbox)
    assert verdict.passed, verdict.problems
    parts, size = BUILT[first_part]
    assert len(done.bodies) == parts
    whole = sp.around([body.frame for body in done.bodies])
    assert whole.size == pytest.approx(size, abs=1e-6)
    assert whole.low[2] == pytest.approx(0)                 # it stands on the ground


# --- the four plans, piece by piece ---------------------------------------------------------

def test_chair_line_by_line():
    plan = parse_plan(PLANS["seat"])
    assert [(line.name, line.placement.kind, line.copies) for line in plan.lines[:-1]] == [
        ("seat", "above ground", 1),
        ("front legs", "under", 2),
        ("back legs", "under", 2),
        ("back posts", "on top of", 2),    # on the seat, so inside its outline
        ("side aprons", "between", 2),     # one per pair: front leg with its back leg
        ("front apron", "between", 1),     # `between X`: X is a set of two
        ("back apron", "between", 1),
        ("top rail", "between", 1),
        ("lower rail", "between", 1),
        ("slats", "between", 3),
    ]
    aprons = plan.lines[4]
    assert aprons.placement.parts == ["front legs", "back legs"]
    assert (aprons.alignments[0].face, aprons.alignments[0].other_face) == ("top", "bottom")
    posts = plan.lines[3]
    assert [size.source for size in posts.sizes] == ["number", "number", "rest"]
    assert (posts.repetition.kind, posts.repetition.side) == ("corners", "back")


def test_robot_dog_line_by_line():
    plan = parse_plan(PLANS["body"])
    assert [line.kind for line in plan.lines] == [
        "part", "group", "part", "part", "part", "end", *["part"] * 6, "done"]
    upper = plan.lines[2]
    assert (upper.in_group, upper.placement) == ("leg", None)
    front_legs = plan.lines[6]
    assert front_legs.group == "leg"
    # The offset is what brings the upper leg against the body: a group is placed by
    # its frame, and the frame is as wide as the foot.
    assert [a.kind for a in front_legs.alignments] == ["down to ground", "flush", "inset",
                                                       "offset"]
    assert front_legs.repetition.direction == "left-right"


def test_rocket_line_by_line():
    plan = parse_plan(PLANS["first stage"])
    fins, band = plan.lines[5], plan.lines[6]
    assert (fins.shape, fins.pointing, fins.copies) == ("wedge", "left", 4)
    assert (fins.repetition.outward, fins.repetition.diameter) == (True, None)
    assert (band.shape, band.placement.kind) == ("tube", "around")


def test_every_form_plan_uses_every_form():
    """Section 12 says the plan uses every form at least once. Count them."""
    lines = parse_plan(PLANS["base"]).lines
    parts = [line for line in lines if line.kind == "part"]
    features = [line.feature for line in lines if line.kind == "feature"]
    placed = [line for line in parts if line.placement]

    assert {line.kind for line in lines} == {"part", "feature", "group", "end", "undo",
                                             "undo to", "done"}
    assert {line.shape for line in parts if line.shape} == set(g.SHAPES) | {g.BAR}
    assert {line.profile for line in parts if line.profile} == set(g.BAR_PROFILES)
    assert any(line.group for line in parts)
    assert {line.orientation for line in parts} == set(g.ORIENTATIONS)
    assert {line.pointing for line in parts} >= {"up", "down", "left", "right"}
    assert any(line.flat for line in parts)
    assert {line.placement.kind for line in placed} == {
        g.ON_GROUND, g.ABOVE_GROUND, *g.ONE_PART_PLACEMENTS, g.BETWEEN, g.SPANS}
    assert {len(line.placement.parts) for line in placed
            if line.placement.kind == g.BETWEEN} == {1, 2}
    # The three kinds of place a span may end on: a face, an edge, a corner.
    places = [place for line in placed for place in line.placement.anchors or []]
    assert {len(str(place).split("-")) for place in places} == {1, 2, 3}
    assert {option.kind for line in parts for option in line.options} == {"gap", "sunk",
                                                                          "across"}
    alignments = [a for line in parts for a in line.alignments]
    assert {a.kind for a in alignments} == {
        "flush", "face offset", "inset", "offset", "top at height", "bottom at height",
        "down to ground", "same axis"}
    assert {a.relation for a in alignments if a.relation} == set(g.FACE_RELATIONS)
    assert any(a.other_face.startswith("inner ") for a in alignments if a.other_face)
    repetitions = [line.repetition for line in parts if line.repetition]
    assert {r.kind for r in repetitions} == {
        "each corner", "corners", "corner", "evenly spaced", "spread", "grid", "around",
        "mirrored"}
    assert {(r.diameter is not None, r.outward) for r in repetitions if r.kind == "around"} \
        == {(True, False), (True, True), (False, True)}
    assert {r.direction for r in repetitions if r.kind == "mirrored"} == set(g.MIRRORS)
    sizes = [size for line in parts for size in line.sizes]
    assert {size.source for size in sizes} == {"number", "same as", "share", "rest", "default"}
    assert {size.share for size in sizes if size.source == "share"} == set(g.SHARES)
    assert {feature.name for feature in features} == set(g.FEATURES)
    assert {feature.position for feature in features} == {"centred", "edges", "at"}
    assert any(feature.open_at for feature in features)
    assert any(line.kind == "feature" and line.face.startswith("inner ") for line in lines)


# --- the one-line examples, in tables and in the text ---------------------------------------

def one_line_examples() -> list[str]:
    """Backticked whole lines outside the fenced plans: `name: shape ...`, `on X: feature ...`.

    Skipped: the forms written with capital placeholders (`on X: FEATURE`), the lines
    that hold `...`, and the replies (`rejected: does not fit`).
    """
    found = []
    for text in re.sub(r"```.*?```", "", DOCUMENT, flags=re.DOTALL).splitlines():
        for span in re.findall(r"`([^`]+)`", text):
            whole_line = re.match(r"[a-z][a-z ']*: ", span) and not span.startswith("rejected")
            if whole_line and "..." not in span and not re.search(r"[A-Z]{2}", span):
                found.append(span)
    return list(dict.fromkeys(found))


def test_the_document_holds_a_fair_number_of_examples():
    assert len(one_line_examples()) >= 100


@pytest.mark.parametrize("example", one_line_examples())
def test_one_line_example_is_in_the_language(example):
    plan = parse_plan(f"ground part: box 1 by 1 by 1, on ground\n{example}\ndone\n")
    reasons = {r.reason for r in plan.lines[1].rejections}
    assert reasons <= {"unknown part"}, plan.lines[1].rejections


# --- the command ----------------------------------------------------------------------------

def test_phrase_pattern():
    assert phrase_pattern("top flush with seat's bottom", ["seat"]) == "top flush with X's bottom"
    assert phrase_pattern("between back posts", ["back posts", "posts"]) == "between X"
    assert phrase_pattern("tilted 15 degrees", []) == "tilted N degrees"


def test_command_prints_a_summary_and_writes_json(tmp_path, capsys):
    out = tmp_path / "summary.json"
    assert main([str(EXAMPLES), "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "plans fully accepted   4 of 4" in printed
    assert "lines accepted         110 of 110" in printed
    summary = json.loads(out.read_text())
    assert summary["rejections_by_reason"] == {}
    assert [entry["plan"] for entry in summary["per_plan"]] == [
        "chair.txt", "every_form.txt", "robot_dog.txt", "rocket.txt"]


def test_command_counts_reasons_and_missing_phrases(tmp_path, capsys):
    (tmp_path / "a.plan").write_text("base: box 10 by 10 by 10, on ground\n"
                                     "lid: cylinder 10, 5, on top of base\n"
                                     "knob: sphere 4, glued to lid\ndone\n")
    (tmp_path / "b.txt").write_text("base: box 10 by 10 by 10, on ground\ndone\n")
    out = tmp_path / "s.json"
    assert main([str(tmp_path), "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "plans fully accepted   1 of 2" in printed
    assert "lines accepted         4 of 6" in printed
    summary = json.loads(out.read_text())
    assert summary["rejections_by_reason"] == {"unknown size form": 1, "unknown placement": 1}
    assert summary["missing_phrases"] == {"unknown size form: cylinder N, N": 1,
                                          "unknown placement: glued to X": 1}
    assert main([str(tmp_path / "nothing-here")]) == 2
