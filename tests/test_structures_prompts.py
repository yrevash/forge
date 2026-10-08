"""Structures: prompts, the mentions recorded while they are written, and the option phrases."""

import random

import pytest

from forge.generators.prompts import MODEL, numbers_in
from forge.generators.structures import KINDS
from forge.generators.structures.prompts import prompt_problems, prompts_for, write_prompt
from forge.generators.structures.sampling import sample

NAMES = sorted(KINDS)


def _many(name, count=250, seed=11):
    rng = random.Random(f"{seed}:{name}")
    return [sample(KINDS[name], rng) for _ in range(count)]


def test_wording_exists_for_everything_a_kind_can_say():
    for kind in KINDS.values():
        for option, choices in kind.OPTIONS.items():
            for choice in choices[1:]:                       # every choice that is not the default
                assert kind.OPTION_WORDS[(option, choice)], (kind.NAME, option, choice)
        for (option, choice), param in kind.OPTION_COUNTS.items():
            assert choice in kind.OPTIONS[option] and param in kind.COUNT_THINGS
        assert set(kind.OVERRIDES) == set(kind.OVERRIDE_WORDS), kind.NAME
        for templates in kind.OVERRIDE_WORDS.values():
            assert all(t.count("{v}") == 1 for t in templates)
        # No digits and no number words in the wording itself: every number is a mention.
        words = [w for found in kind.OPTION_WORDS.values() for w in found]
        words += [w for found in kind.OVERRIDE_WORDS.values() for w in found]
        words += [w for found in kind.COUNT_THINGS.values() for pair in found for w in pair]
        for phrase in words:
            assert numbers_in(phrase.replace("{v}", "")) == [], phrase


@pytest.mark.parametrize("name", NAMES)
def test_prompts_pass_their_check_and_mentions_point_at_slots(name):
    kind = KINDS[name]
    texts, names_used, discarded = set(), set(), 0
    for structure in _many(name):
        prompts, thrown = prompts_for(kind, structure)
        discarded += thrown
        assert len(prompts) >= 2
        stated = structure.stated()
        for prompt in prompts:
            text = prompt["text"]
            assert prompt["model"] == MODEL == "template"
            assert prompt["level"] == structure.level
            assert prompt_problems(kind, structure, prompt) == [], text
            # Every number in the sentence belongs to the structure ...
            assert sorted(numbers_in(text)) == sorted(m["value"] for m in prompt["mentions"])
            # ... and every stated slot is in the sentence.
            assert {m["param"] for m in prompt["mentions"]} == set(stated)
            last_end = 0
            for mention in prompt["mentions"]:
                assert text[mention["start"]:mention["end"]] == mention["text"]
                assert mention["start"] >= last_end                 # in text order, no overlap
                last_end = mention["end"]
                step, slot = mention["fills"][0]
                filled = structure.steps[step].slots[slot]
                assert filled.source == "stated" and filled.value == mention["value"]
            for option in prompt["options"]:
                assert structure.choices[option["option"]] == option["choice"]
                if option["by"] == "phrase":
                    assert text[option["start"]:option["end"]] == option["text"] != ""
            texts.add(text)
            names_used |= {n for n in structure.names if n in text.lower()}
    assert len(texts) > 600                # varied wording
    assert len(names_used) >= 4            # several everyday names per kind
    assert discarded <= 5                  # a draft is thrown away only rarely


@pytest.mark.parametrize("name", NAMES)
def test_levels_say_what_they_should(name):
    kind = KINDS[name]
    said_default = False
    for structure in _many(name, 300, seed=13):
        prompt = prompts_for(kind, structure, 1)[0][0]
        expressed = {o["option"] for o in prompt["options"]}
        if structure.level == "a":
            assert prompt["options"] == []
        for option, choice in structure.choices.items():
            if choice != kind.OPTIONS[option][0]:
                assert option in expressed, (prompt["text"], option, choice)
            elif option in expressed:
                said_default = True
    assert said_default                    # "no arms" is said now and then


def test_option_phrases_are_the_kinds_own_words():
    """Without typos or case changes, a recorded phrase is one of the listed phrases."""
    kind = KINDS["chair"]
    seen = set()
    for structure in _many("chair", 400, seed=15):
        prompt = prompts_for(kind, structure, 1)[0][0]
        for option in prompt["options"]:
            if option["by"] == "phrase":
                listed = {w.lstrip("+").lower()
                          for w in kind.OPTION_WORDS[(option["option"], option["choice"])]}
                if option["text"].lower() in listed:
                    seen.add((option["option"], option["choice"]))
            else:
                assert option["param"] == "slats_count" and option["choice"] == "slats"
    assert {("arms", "yes"), ("legs", "round"), ("back", "panel"), ("stretchers", "h"),
            ("stretchers", "ring"), ("back", "rails")} <= seen


def test_the_demo_request_is_a_level_a_prompt():
    chair = KINDS["chair"].build(
        {"back": "slats", "arms": "no", "stretchers": "none", "legs": "square"},
        {"seat_width": 420.0, "seat_depth": 420.0, "seat_top": 450.0, "posts_top": 900.0})
    chair.names = ["dining chair"]
    texts = [write_prompt(KINDS["chair"], chair, random.Random(i)) for i in range(300)]
    assert all(prompt_problems(KINDS["chair"], chair, p) == [] for p in texts)
    for prompt in texts:
        assert [m["param"] for m in prompt["mentions"]].count("seat_width") == 1
        assert sorted(m["value"] for m in prompt["mentions"]) == [420.0, 420.0, 450.0, 900.0]
    # The two 420s are told apart by the builder, not by their value.
    assert any([m["param"] for m in p["mentions"]][:2] == ["seat_width", "seat_depth"]
               for p in texts)
    assert any(p["text"].lower().startswith("dining chair, seat 420 by 420, seat height 450")
               for p in texts)


def test_prompts_are_the_same_every_time():
    structure = _many("desk", 1)[0]
    assert prompts_for(KINDS["desk"], structure) == prompts_for(KINDS["desk"], structure)


# --- the check catches wrong prompts ---------------------------------------------------------

def _level_c_chair():
    return next(s for s in _many("chair", 400, seed=17)
                if s.level == "c" and s.choices["arms"] == "yes")


def test_check_catches_a_stray_number():
    structure = _level_c_chair()
    prompt = prompts_for(KINDS["chair"], structure, 1)[0][0]
    wrong = {**prompt, "text": prompt["text"] + ", 2 cushions"}
    assert any("numbers in the text" in p
               for p in prompt_problems(KINDS["chair"], structure, wrong))


def test_check_catches_a_missing_stated_slot():
    structure = _level_c_chair()
    prompt = prompts_for(KINDS["chair"], structure, 1)[0][0]
    dropped = prompt["mentions"][-1]
    text = prompt["text"][:dropped["start"]] + "some" + prompt["text"][dropped["end"]:]
    wrong = {**prompt, "text": text, "mentions": prompt["mentions"][:-1]}
    assert f"stated slot {dropped['param']} is not in the prompt" in prompt_problems(
        KINDS["chair"], structure, wrong)


def test_check_catches_a_mention_with_the_wrong_value_or_slot():
    structure = _level_c_chair()
    prompt = prompts_for(KINDS["chair"], structure, 1)[0][0]
    first = prompt["mentions"][0]
    wrong_value = {**prompt, "mentions": [{**first, "value": first["value"] + 1},
                                          *prompt["mentions"][1:]]}
    assert prompt_problems(KINDS["chair"], structure, wrong_value) != []
    wrong_slot = {**prompt, "mentions": [{**first, "fills": [[0, "thickness"]]},
                                         *prompt["mentions"][1:]]}
    assert any("wrong slot" in p for p in prompt_problems(KINDS["chair"], structure, wrong_slot))


def test_check_catches_a_wrong_or_missing_option():
    structure = _level_c_chair()
    prompt = prompts_for(KINDS["chair"], structure, 1)[0][0]
    arms = next(o for o in prompt["options"] if o["option"] == "arms")
    lying = {**prompt, "options": [{**arms, "choice": "no"}]}
    assert any("does not have" in p for p in prompt_problems(KINDS["chair"], structure, lying))
    silent = {**prompt, "options": [o for o in prompt["options"] if o["option"] != "arms"]}
    assert "arms=yes is not the default and is not said" in prompt_problems(
        KINDS["chair"], structure, silent)
