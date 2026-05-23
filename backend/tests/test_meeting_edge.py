from __future__ import annotations

import pytest

from backend.utils.meeting_edge import (
    MeetingEdgeContractError,
    MeetingEdgeRequest,
    build_meeting_edge_prompt,
    merge_meeting_edge_concept_history,
    parse_meeting_edge_response,
    serialize_meeting_edge_result,
)


def test_build_meeting_edge_prompt_includes_focus_and_recent_transcript() -> None:
    request = MeetingEdgeRequest(
        recent_transcript="Speaker A: We need to lock the launch date this week.",
        rolling_summary="The team is converging on launch readiness.",
        focus_text="Help me pressure-test timeline risk.",
        user_notes="Need a clear owner for follow-up.",
    )

    prompt = build_meeting_edge_prompt(request)

    assert "Help me pressure-test timeline risk." in prompt
    assert "The team is converging on launch readiness." in prompt
    assert "Speaker A: We need to lock the launch date this week." in prompt
    assert "Need a clear owner for follow-up." in prompt


def test_parse_meeting_edge_response_accepts_fenced_json() -> None:
    response = """
    Here is the guidance.

    ```json
    {
      "summary": "The meeting is narrowing around launch timing.",
      "questions": ["Who owns the final go-live decision?", "  "],
      "points": ["Flag the dependency on finance approval."],
      "concepts": [
                {"term": "Go-live", "explanation": "The planned production launch date."},
                {"term": "Rollback", "explanation": "The fallback path if the launch needs to be reversed."},
                {"term": "Runbook", "explanation": "The step-by-step operational guide for the launch."}
      ]
    }
    ```
    """

    result = parse_meeting_edge_response(response)

    assert result.summary == "The meeting is narrowing around launch timing."
    assert result.questions == ("Who owns the final go-live decision?",)
    assert result.points == ("Flag the dependency on finance approval.",)
    assert serialize_meeting_edge_result(result)["concepts"] == [
        {
            "term": "Go-live",
            "explanation": "The planned production launch date.",
        },
        {
            "term": "Rollback",
            "explanation": "The fallback path if the launch needs to be reversed.",
        },
        {
            "term": "Runbook",
            "explanation": "The step-by-step operational guide for the launch.",
        }
    ]


def test_parse_meeting_edge_response_requires_at_least_one_signal_item() -> None:
    request = MeetingEdgeRequest(recent_transcript="Speaker A: We should revisit budget.")

    with pytest.raises(MeetingEdgeContractError):
        parse_meeting_edge_response(
            '{"summary": "Budget came up.", "questions": [], "points": [], "concepts": []}',
            request=request,
        )


def test_merge_meeting_edge_concept_history_preserves_prior_terms() -> None:
    previous_payload = {
        "concepts": [
            {
                "term": "Bit shifting",
                "explanation": "Moving bits left or right to align place value.",
            }
        ]
    }
    current_payload = {
        "concepts": [
            {
                "term": "Bit shifting",
                "explanation": "Aligning place value while building partial products.",
            },
            {
                "term": "Accumulator",
                "explanation": "The higher-precision running sum of partial products.",
            },
        ]
    }

    assert merge_meeting_edge_concept_history(previous_payload, current_payload) == [
        {
            "term": "Bit shifting",
            "explanation": "Aligning place value while building partial products.",
        },
        {
            "term": "Accumulator",
            "explanation": "The higher-precision running sum of partial products.",
        },
    ]