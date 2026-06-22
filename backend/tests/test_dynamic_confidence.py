"""Unit tests for dynamic speaker suggestion confidence."""

from __future__ import annotations

from backend.utils.speaker_name_suggestions import (
    build_mapping_based_speaker_suggestions,
)


def _make_segments() -> list[dict]:
    return [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "Hello everyone"},
    ]


def test_confidence_without_embedding_scores() -> None:
    result = build_mapping_based_speaker_suggestions(
        {"SPEAKER_00": "Alice"},
        segments=_make_segments(),
        eligible_labels=["SPEAKER_00"],
        embedding_similarity_scores=None,
    )

    assert len(result.suggestions) == 1
    assert result.suggestions[0].confidence == 0.50


def test_confidence_with_high_embedding_similarity() -> None:
    result = build_mapping_based_speaker_suggestions(
        {"SPEAKER_00": "Alice"},
        segments=_make_segments(),
        eligible_labels=["SPEAKER_00"],
        embedding_similarity_scores={"SPEAKER_00": 0.85},
    )

    assert len(result.suggestions) == 1
    suggestion = result.suggestions[0]
    assert suggestion.confidence == 0.70
    assert suggestion.confidence > 0.50


def test_confidence_with_moderate_embedding_similarity() -> None:
    result = build_mapping_based_speaker_suggestions(
        {"SPEAKER_00": "Alice"},
        segments=_make_segments(),
        eligible_labels=["SPEAKER_00"],
        embedding_similarity_scores={"SPEAKER_00": 0.60},
    )

    assert len(result.suggestions) == 1
    suggestion = result.suggestions[0]
    expected = round(0.60 * 0.85, 4)
    assert abs(suggestion.confidence - expected) < 0.01
    assert suggestion.confidence > 0.40
    assert suggestion.confidence < 0.70


def test_confidence_with_low_embedding_similarity() -> None:
    result = build_mapping_based_speaker_suggestions(
        {"SPEAKER_00": "Alice"},
        segments=_make_segments(),
        eligible_labels=["SPEAKER_00"],
        embedding_similarity_scores={"SPEAKER_00": 0.30},
    )

    assert len(result.suggestions) == 1
    suggestion = result.suggestions[0]
    assert suggestion.confidence == 0.40


def test_confidence_with_missing_label_in_scores() -> None:
    result = build_mapping_based_speaker_suggestions(
        {"SPEAKER_00": "Alice"},
        segments=_make_segments(),
        eligible_labels=["SPEAKER_00"],
        embedding_similarity_scores={"SPEAKER_99": 0.90},
    )

    assert len(result.suggestions) == 1
    assert result.suggestions[0].confidence == 0.50


def test_transcript_mention_overrides_embedding_confidence() -> None:
    segments = [
        {
            "start": 0.0,
            "end": 5.0,
            "speaker": "SPEAKER_00",
            "text": "Hi, I'm Alice speaking",
        },
    ]

    result = build_mapping_based_speaker_suggestions(
        {"SPEAKER_00": "Alice"},
        segments=segments,
        eligible_labels=["SPEAKER_00"],
        embedding_similarity_scores={"SPEAKER_00": 0.40},
    )

    assert len(result.suggestions) == 1
    suggestion = result.suggestions[0]
    assert suggestion.confidence >= 0.72
    assert (
        "transcript_name_mention" in suggestion.signals
        or "self_introduction" in suggestion.signals
    )
