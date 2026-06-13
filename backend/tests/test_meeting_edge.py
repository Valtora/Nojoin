from __future__ import annotations

from backend.utils.meeting_edge import (
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
        context_level=1,
    )

    prompt = build_meeting_edge_prompt(request)

    assert "Help me pressure-test timeline risk." in prompt
    assert "The team is converging on launch readiness." in prompt
    assert "Speaker A: We need to lock the launch date this week." in prompt
    assert "Need a clear owner for follow-up." in prompt
    assert "Absolutely do not explain common business" in prompt


def test_build_meeting_edge_prompt_includes_previous_suggestions() -> None:
    request = MeetingEdgeRequest(
        recent_transcript="Speaker A: Let's review the budget line items.",
        previous_questions=("Who owns the final go-live decision?",),
        previous_points=("Flag the dependency on finance approval.",),
    )

    prompt = build_meeting_edge_prompt(request)

    assert "Questions already suggested:" in prompt
    assert "- Who owns the final go-live decision?" in prompt
    assert "Points already suggested:" in prompt
    assert "- Flag the dependency on finance approval." in prompt


def test_build_meeting_edge_prompt_handles_no_previous_suggestions() -> None:
    request = MeetingEdgeRequest(
        recent_transcript="Speaker A: Let's review the budget line items.",
    )

    prompt = build_meeting_edge_prompt(request)

    assert "No suggestions have been made yet." in prompt
    assert "rolling_summary" in prompt


def test_parse_meeting_edge_response_reads_rolling_summary() -> None:
    response = (
        '{"summary": "Budget review is underway.",'
        ' "rolling_summary": "The team reviewed Q3 spend, agreed to cut travel by 10%,'
        ' and left headcount open pending finance input.",'
        ' "questions": ["What is the finance deadline?"],'
        ' "points": [], "concepts": []}'
    )

    result = parse_meeting_edge_response(response)

    assert result.rolling_summary is not None
    assert result.rolling_summary.startswith("The team reviewed Q3 spend")
    assert serialize_meeting_edge_result(result)["rolling_summary"] == result.rolling_summary


def test_parse_meeting_edge_response_tolerates_missing_rolling_summary() -> None:
    response = (
        '{"summary": "Budget review is underway.",'
        ' "questions": ["What is the finance deadline?"],'
        ' "points": [], "concepts": []}'
    )

    result = parse_meeting_edge_response(response)

    assert result.rolling_summary is None
    assert serialize_meeting_edge_result(result)["rolling_summary"] is None


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


def test_parse_meeting_edge_response_accepts_summary_only_payload() -> None:
    request = MeetingEdgeRequest(recent_transcript="Speaker A: We should revisit budget.")

    result = parse_meeting_edge_response(
        '{"summary": "Budget came up.", "questions": [], "points": [], "concepts": []}',
        request=request,
    )

    assert result.summary == "Budget came up."
    assert result.questions == ()
    assert result.points == ()
    assert result.concepts == ()


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


def test_merge_meeting_edge_concept_history_can_reset_history() -> None:
    previous_payload = {
        "concepts": [
            {
                "term": "API",
                "explanation": "A common software interface term.",
            }
        ]
    }
    current_payload = {
        "concepts": [
            {
                "term": "Consensus protocol",
                "explanation": "The coordination rules distributed nodes use to agree on state.",
            }
        ]
    }

    assert merge_meeting_edge_concept_history(
        previous_payload,
        current_payload,
        reset_history=True,
    ) == [
        {
            "term": "Consensus protocol",
            "explanation": "The coordination rules distributed nodes use to agree on state.",
        }
    ]


def test_are_singular_plural() -> None:
    from backend.utils.meeting_edge import _are_singular_plural
    assert _are_singular_plural("choke point", "choke points")
    assert _are_singular_plural("choke points", "choke point")
    assert _are_singular_plural("anticipatory price rise", "anticipatory price rises")
    assert _are_singular_plural("process", "processes")
    assert _are_singular_plural("subsidy", "subsidies")
    assert not _are_singular_plural("choke", "point")
    assert not _are_singular_plural("rise", "raise")


def test_are_equivalent_concept_terms_acronym_and_hyphen_variants() -> None:
    from backend.utils.meeting_edge import _are_equivalent_concept_terms

    assert _are_equivalent_concept_terms("LLM", "Large Language Model")
    assert _are_equivalent_concept_terms("Large Language Model", "LLM")
    assert _are_equivalent_concept_terms("real-time", "real time")
    assert _are_equivalent_concept_terms("Real Time", "real-time")
    assert not _are_equivalent_concept_terms("LLM", "Low Latency")
    assert not _are_equivalent_concept_terms("API", "Application Performance")
    assert not _are_equivalent_concept_terms("rise", "raise")


def test_merge_meeting_edge_concept_history_deduplicates_acronyms() -> None:
    previous_payload = {
        "concepts": [
            {
                "term": "Large Language Model",
                "explanation": "A neural network trained on large text corpora.",
            }
        ]
    }
    current_payload = {
        "concepts": [
            {
                "term": "LLM",
                "explanation": "A model that generates text from learned patterns.",
            }
        ]
    }

    result = merge_meeting_edge_concept_history(previous_payload, current_payload)

    assert len(result) == 1
    assert result[0]["term"] == "LLM"
    assert result[0]["explanation"] == "A model that generates text from learned patterns."


def test_merge_meeting_edge_concept_history_deduplicates_plurals() -> None:
    previous_payload = {
        "concepts": [
            {
                "term": "Choke points",
                "explanation": "Points of congestion in speech processing.",
            },
            {
                "term": "Anticipatory price rise",
                "explanation": "A price hike in anticipation of inflation.",
            }
        ]
    }
    current_payload = {
        "concepts": [
            {
                "term": "Choke point",
                "explanation": "A point of congestion in speech processing.",
            },
            {
                "term": "Anticipatory price rises",
                "explanation": "A price hike in anticipation of inflation.",
            },
            {
                "term": "Subsidies",
                "explanation": "Financial support structures.",
            }
        ]
    }

    result = merge_meeting_edge_concept_history(previous_payload, current_payload)
    assert len(result) == 3
    # Check that they were resolved to their singular (shorter) forms: "Choke point", "Anticipatory price rise", "Subsidies" (no singular provided for Subsidies, so it remains plural)
    terms = [item["term"] for item in result]
    assert "Choke point" in terms
    assert "Anticipatory price rise" in terms
    assert "Subsidies" in terms
    assert "Choke points" not in terms
    assert "Anticipatory price rises" not in terms
