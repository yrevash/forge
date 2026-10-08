"""Mentions and alignment: recorded while the prompt is written."""

import random

import pytest

from forge.generators.captions import spec_captions
from forge.generators.prompts import (
    COUNT_WORDS,
    composed_prompt_with_mentions,
    natural_prompts,
    numbers_in,
)
from forge.system1.mentions import VARIANTS, Unaligned, align, natural_prompt, prompt_and_plan
from forge.system1.steps import steps_of
from tests.system1_helpers import sample_parts

NATURAL = [v for v in VARIANTS if v != "spec"]


def test_recording_mentions_does_not_change_one_byte_of_any_prompt():
    """4,000 parts (3,200 like training, 800 long), five prompts each."""
    parts = sample_parts(800, seed="text") + sample_parts(200, seed="text", features=(6, 12))
    compared = 0
    for part in parts:
        today = {p["variant"]: p["text"] for p in natural_prompts(
            part.family, part.params, None, part.id, {"compact": 2, "request": 3})}
        for variant in NATURAL:
            text, _ = natural_prompt(part.family, part.params, part.id, variant)
            # natural_prompts drops a text it has already produced for this part; any
            # variant it kept must be identical.
            assert variant not in today or today[variant] == text
            assert text in today.values()
            compared += 1
    assert compared == 20_000


def test_every_parameter_is_said_exactly_once_and_every_number_is_a_mention():
    words = 0
    for part in sample_parts(400, seed="cover") + sample_parts(60, seed="cover", features=(6, 12)):
        for variant in NATURAL:
            text, mentions = natural_prompt(part.family, part.params, part.id, variant)
            assert sorted(m["param"] for m in mentions) == sorted(part.params), text
            assert [m["start"] for m in mentions] == sorted(m["start"] for m in mentions)
            for m in mentions:
                assert text[m["start"]:m["end"]] == m["text"]
                assert m["value"] == float(part.params[m["param"]])
                if m["text"].lower() in COUNT_WORDS:
                    words += 1
                    assert COUNT_WORDS.index(m["text"].lower()) == m["value"]
                else:
                    assert float(m["text"]) == m["value"]
            # An independent count: the checker's own number finder sees no number the
            # builder did not record, and misses none.
            assert sorted(numbers_in(text)) == sorted(m["value"] for m in mentions), text
    assert words > 50       # number words ("six holes") do occur and are mentions


def test_mentions_survive_articles_case_changes_and_typos():
    """Prompts whose finishing touches move or re-case text still have exact positions."""
    seen = set()
    for part in sample_parts(300, seed="finish"):
        for variant in NATURAL:
            text, mentions = natural_prompt(part.family, part.params, part.id, variant)
            assert all(text[m["start"]:m["end"]] == m["text"] for m in mentions)
            seen.add("upper" if text.isupper() else "lower" if text.islower() else "mixed")
            if " an " in text:
                seen.add("an")
            if any(m["text"].isupper() and m["text"].isalpha() for m in mentions):
                seen.add("upper-case number word")
    assert seen >= {"upper", "lower", "mixed", "an", "upper-case number word"}


def test_the_plan_points_every_slot_at_a_mention_with_its_value():
    for part in sample_parts(300, seed="plan") + sample_parts(40, seed="plan", features=(6, 12)):
        for variant in VARIANTS:
            _, mentions, plan = prompt_and_plan(part.family, part.params, part.id, variant)
            assert [(s.kind, s.slots) for s in plan] == \
                [(s.kind, s.slots) for s in steps_of(part.family, part.params)]
            pointed = []
            for index, step in enumerate(plan):
                assert set(step.sources) == set(step.slots)
                for slot, source in step.sources.items():
                    assert mentions[source]["value"] == float(step.slots[slot])
                    assert [index, slot] in mentions[source]["fills"]
                    pointed.append(source)
            # Composed prompts say each number once: no mention is shared, none is spare
            # (apart from the feature numbers of the spec caption).
            assert len(pointed) == len(set(pointed))
            spare = [m for i, m in enumerate(mentions) if i not in pointed]
            assert all(m["param"] is None and m["fills"] == [] for m in spare)
            assert (variant == "spec") or not spare


def test_the_spec_caption_is_the_canonical_one_and_its_feature_numbers_fill_nothing():
    params = {"base_diameter": 50.0, "base_height": 20.0, "polar_1_count": 4,
              "polar_1_hole_diameter": 5.0, "polar_1_circle_diameter": 30.0,
              "slot_2_length": 12.0, "slot_2_width": 4.0, "slot_2_depth": 2.5,
              "slot_2_angle": 90.0, "slot_2_x": 0.0, "slot_2_y": -1.0}
    text, mentions, plan = prompt_and_plan("composed_cylinder", params, "any", "spec")
    assert text == spec_captions("composed_cylinder", params, None)[0]["text"]
    assert [m["text"] for m in mentions] == ["50", "20", "1", "4", "1", "5", "1", "30", "2", "12",
                                             "2", "4", "2", "2.5", "2", "90", "2", "0", "2", "-1"]
    assert sum(m["param"] is None for m in mentions) == 9
    assert plan[1].sources == {"count": 3, "hole_diameter": 5, "circle_diameter": 7}
    assert plan[2].sources["y"] == 19 and mentions[19]["value"] == -1.0


def test_two_slots_with_the_same_value_keep_their_own_mentions():
    """A 40 by 40 block: the plan knows which 40 is the length and which the width."""
    params = {"base_length": 40.0, "base_width": 40.0, "base_height": 40.0,
              "hole_1_diameter": 5.0, "hole_1_x": 5.0, "hole_1_y": 5.0}
    for variant in VARIANTS:
        text, mentions, plan = prompt_and_plan("composed_block", params, "square", variant)
        sources = [source for step in plan for source in step.sources.values()]
        assert len(set(sources)) == 6, text
        for step in plan:
            for slot, source in step.sources.items():
                assert mentions[source]["param"].endswith(slot)


def test_a_prompt_that_leaves_a_number_out_cannot_be_aligned():
    part = sample_parts(1, seed="missing")[0]
    _, mentions = natural_prompt(part.family, part.params, part.id, "request-0")
    with pytest.raises(Unaligned, match="no mention for"):
        align(part.family, part.params, mentions[:-1])


def test_untracked_numbers_leave_no_marker_behind():
    """The ordinary builders must never emit a marker character."""
    for part in sample_parts(100, seed="marks"):
        for prompt in natural_prompts(part.family, part.params, None, part.id,
                                      {"compact": 2, "request": 3}):
            assert not any(0xE000 <= ord(ch) <= 0xF8FF for ch in prompt["text"])
        text, _ = composed_prompt_with_mentions("compact", part.family, part.params,
                                                random.Random(1))
        assert not any(0xE000 <= ord(ch) <= 0xF8FF for ch in text)
