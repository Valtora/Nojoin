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


def _turn(speaker: str, text: str, start: float) -> dict:
    return {"start": start, "end": start + 2.0, "speaker": speaker, "text": text}


def _suggestion_for(label: str, name: str, segments: list[dict]):
    result = build_mapping_based_speaker_suggestions(
        {label: name},
        segments=segments,
        eligible_labels=["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"],
    )
    assert len(result.suggestions) == 1
    return result.suggestions[0]


def test_thanking_someone_by_name_is_evidence_for_the_previous_speaker() -> None:
    segments = [
        _turn("SPEAKER_00", "The budget numbers are final.", 0.0),
        _turn("SPEAKER_00", "I sent them round this morning.", 2.0),
        _turn("SPEAKER_01", "Thanks, Priya. Moving on to hiring.", 4.0),
    ]

    thanker = _suggestion_for("SPEAKER_01", "Priya", segments)
    assert "transcript_name_mention" not in thanker.signals
    assert thanker.evidence_spans == ()
    assert thanker.confidence == 0.50

    thanked = _suggestion_for("SPEAKER_00", "Priya", segments)
    assert "transcript_name_mention" in thanked.signals
    assert [span.quote for span in thanked.evidence_spans] == [
        "Thanks, Priya. Moving on to hiring."
    ]
    assert thanked.confidence == 0.72


def test_addressing_someone_by_name_is_evidence_for_the_next_speaker() -> None:
    segments = [
        _turn("SPEAKER_00", "That covers the roadmap.", 0.0),
        _turn("SPEAKER_01", "Priya, what do you think?", 2.0),
        _turn("SPEAKER_02", "Looks right to me.", 4.0),
    ]

    answerer = _suggestion_for("SPEAKER_02", "Priya", segments)
    assert "transcript_name_mention" in answerer.signals
    assert answerer.confidence == 0.72


def test_mentioning_a_colleague_throughout_is_not_evidence_for_the_speaker() -> None:
    segments = [
        _turn("SPEAKER_00", "I talked to Priya yesterday about the launch.", 0.0),
        _turn("SPEAKER_01", "Okay.", 2.0),
        _turn("SPEAKER_00", "Priya's team owns the migration.", 4.0),
        _turn("SPEAKER_00", "So as Priya said, we wait a week.", 6.0),
        _turn("SPEAKER_01", "Fine by me.", 8.0),
        _turn("SPEAKER_02", "Let's schedule it then.", 10.0),
    ]

    mentioner = _suggestion_for("SPEAKER_00", "Priya", segments)
    assert "transcript_name_mention" not in mentioner.signals
    assert mentioner.evidence_spans == ()
    assert mentioner.confidence == 0.50

    # SPEAKER_02 never speaks next to a line that names Priya.
    bystander = _suggestion_for("SPEAKER_02", "Priya", segments)
    assert "transcript_name_mention" not in bystander.signals
