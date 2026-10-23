from __future__ import annotations

import json

from backend.utils.speaker_name_suggestions import (
    SpeakerInferenceSuggestion,
    SpeakerSuggestionEvidenceSpan,
    parse_speaker_inference_response,
)


def test_parsed_signals_keep_only_strings() -> None:
    response = json.dumps(
        {
            "suggestions": [
                {
                    "diarization_label": "SPEAKER_00",
                    "suggested_name": "Alice Example",
                    "confidence": 0.9,
                    "evidence_spans": [
                        {
                            "quote": "Hi, I'm Alice Example.",
                            "reason": "Self introduction.",
                        }
                    ],
                    "signals": [
                        "self_introduction",
                        {"text": "attendee_match"},
                        3,
                        None,
                        "  ",
                        "self_introduction",
                    ],
                }
            ]
        }
    )

    result = parse_speaker_inference_response(response)

    assert result.suggestions[0].signals == ("self_introduction",)


def test_a_suggestion_drops_signals_that_are_not_strings() -> None:
    suggestion = SpeakerInferenceSuggestion(
        diarization_label="SPEAKER_01",
        suggested_name="Bob Sample",
        confidence=0.8,
        evidence_spans=(
            SpeakerSuggestionEvidenceSpan(
                quote="Bob Sample here.", reason="Self introduction."
            ),
        ),
        signals=("transcript_name_mention", {"text": "attendee_match"}, 5),  # type: ignore[arg-type]
    )

    assert suggestion.signals == ("transcript_name_mention",)
