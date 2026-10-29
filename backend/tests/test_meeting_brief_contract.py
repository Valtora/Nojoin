"""The generated gist is plain prose and shares the unified response contract."""

from __future__ import annotations

import json

import pytest

from backend.utils.meeting_brief import (
    build_meeting_brief_prompt,
    parse_meeting_brief_response,
)
from backend.utils.meeting_intelligence import (
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


@pytest.mark.parametrize("gist", [None, "", " \n ", [], 42, True, "x" * 401])
def test_unified_response_rejects_an_invalid_gist(gist: object) -> None:
    with pytest.raises(MeetingIntelligenceContractError, match="gist"):
        parse_automatic_meeting_intelligence_response(_payload(gist=gist))


def test_unified_response_rejects_a_missing_gist() -> None:
    payload = json.loads(_payload())
    del payload["gist"]
    with pytest.raises(MeetingIntelligenceContractError, match="gist"):
        parse_automatic_meeting_intelligence_response(json.dumps(payload))


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
        {"title": "Title", "gist": "x" * 401},
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
