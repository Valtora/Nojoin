"""The gist rides on the title and notes without ever failing them."""

from __future__ import annotations

import json

import pytest

from backend.utils.meeting_brief import (
    build_meeting_brief_prompt,
    parse_meeting_brief_response,
)
from backend.utils.meeting_intelligence import (
    GIST_MAX_CHARS,
    MeetingIntelligenceContractError,
    parse_automatic_meeting_intelligence_response,
)


def _payload(**overrides: object) -> str:
    return json.dumps(
        {
            "speaker_mapping": {},
            "title": "Launch review",
            "notes_markdown": "## Summary\nThe team agreed to launch on Friday.",
            "gist": "  The team agreed to launch\n on Friday.  ",
            **overrides,
        }
    )


def test_unified_response_normalises_gist_whitespace() -> None:
    result = parse_automatic_meeting_intelligence_response(_payload())
    assert result.gist == "The team agreed to launch on Friday."
    assert result.title == "Launch review"
    assert result.notes_markdown.startswith("## Summary")


@pytest.mark.parametrize("gist", [None, "", " \n ", [], 42, True])
def test_unified_response_without_a_usable_gist_still_gives_title_and_notes(
    gist: object,
) -> None:
    result = parse_automatic_meeting_intelligence_response(_payload(gist=gist))
    assert result.gist is None
    assert result.title == "Launch review"
    assert result.notes_markdown.startswith("## Summary")


def test_unified_response_without_a_gist_key_still_gives_title_and_notes() -> None:
    payload = json.loads(_payload())
    del payload["gist"]
    result = parse_automatic_meeting_intelligence_response(json.dumps(payload))
    assert result.gist is None
    assert result.title == "Launch review"


def test_an_over_long_gist_is_cut_at_the_last_sentence_that_fits() -> None:
    first = "The team agreed to launch on Friday. "
    gist = first * 10 + "x" * 50
    result = parse_automatic_meeting_intelligence_response(_payload(gist=gist))
    assert result.gist is not None
    assert len(result.gist) <= GIST_MAX_CHARS
    assert result.gist.endswith("on Friday.")
    assert result.gist == (first * 10).strip()


def test_an_over_long_gist_without_a_sentence_end_is_cut_at_a_word() -> None:
    gist = "launch " * 80
    result = parse_automatic_meeting_intelligence_response(_payload(gist=gist))
    assert result.gist is not None
    assert len(result.gist) <= GIST_MAX_CHARS
    assert result.gist.endswith("launch\u2026")


def test_live_response_normalises_both_fields_together() -> None:
    title, gist = parse_meeting_brief_response(
        '```json\n{"title": " Launch\\nreview ", "gist": " Friday\\n launch. "}\n```'
    )
    assert title == "Launch review"
    assert gist == "Friday launch."


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": "Title"},
        {"title": "", "gist": "Gist."},
        {"title": "Title", "gist": []},
    ],
)
def test_live_response_rejects_an_incomplete_brief(payload: dict[str, object]) -> None:
    with pytest.raises(MeetingIntelligenceContractError):
        parse_meeting_brief_response(json.dumps(payload))


def test_live_prompt_applies_language_and_title_preference_to_the_same_call() -> None:
    prompt = build_meeting_brief_prompt(
        "Dana: Le lancement est vendredi.",
        prefer_short_titles=True,
        output_language_instruction="Write the title and gist in French.",
    )
    assert "French" in prompt
    assert "3-5 words" in prompt
    assert '"title"' in prompt
    assert '"gist"' in prompt
    assert "Dana: Le lancement est vendredi." in prompt
