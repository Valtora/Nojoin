"""Unit tests for the embedding-based speaker merge pass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

from backend.processing.embedding_core import EMBEDDING_METHOD_VERSION
from backend.processing.speaker_merge import merge_duplicate_speakers


@dataclass
class FakeSpeaker:
    id: int
    diarization_label: str
    embedding: Optional[list[float]] = None
    merged_into_id: Optional[int] = None
    name: Optional[str] = None
    local_name: Optional[str] = None
    global_speaker_id: Optional[int] = None
    embedding_version: Optional[int] = EMBEDDING_METHOD_VERSION


def _make_embedding(value: float, dim: int = 4) -> list[float]:
    return [value] * dim


def _fake_session_with_speakers(speakers: list[FakeSpeaker]) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = list(speakers)
    session.get.side_effect = lambda cls, pk: next(
        (s for s in speakers if s.id == pk), None
    )
    return session


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_merge_speakers_above_threshold(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.99)
        ),
        FakeSpeaker(
            id=3, diarization_label="SPEAKER_02", embedding=_make_embedding(-1.0)
        ),
    ]
    mock_counts.return_value = {1: 10, 2: 5, 3: 8}
    session = _fake_session_with_speakers(speakers)

    segments = [
        {"speaker": "SPEAKER_01", "overlapping_speakers": []},
        {"speaker": "SPEAKER_00", "overlapping_speakers": ["SPEAKER_01"]},
    ]

    merge_pairs = merge_duplicate_speakers(
        session, recording_id=1, threshold=0.70, segments=segments
    )

    assert len(merge_pairs) == 1
    merged_id, survivor_id = merge_pairs[0]
    assert survivor_id == 1
    assert merged_id == 2
    assert speakers[1].merged_into_id == 1
    assert segments[0]["speaker"] == "SPEAKER_00"
    assert segments[1]["overlapping_speakers"] == ["SPEAKER_00"]


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_no_merge_below_threshold(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(-1.0)
        ),
    ]
    mock_counts.return_value = {1: 10, 2: 5}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert merge_pairs == []
    assert speakers[0].merged_into_id is None
    assert speakers[1].merged_into_id is None


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_skips_speakers_without_embeddings(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_01", embedding=None),
    ]
    mock_counts.return_value = {1: 10}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert merge_pairs == []


def test_single_speaker_returns_empty() -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
    ]
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert merge_pairs == []


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_transitive_merge(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.98)
        ),
        FakeSpeaker(
            id=3, diarization_label="SPEAKER_02", embedding=_make_embedding(0.95)
        ),
    ]
    mock_counts.return_value = {1: 10, 2: 5, 3: 3}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert len(merge_pairs) == 2
    merged_ids = {pair[0] for pair in merge_pairs}
    survivor_ids = {pair[1] for pair in merge_pairs}
    assert merged_ids == {2, 3}
    assert survivor_ids == {1}


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_survivor_has_most_utterances(mock_counts: MagicMock) -> None:
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.99)
        ),
    ]
    mock_counts.return_value = {1: 3, 2: 20}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    assert len(merge_pairs) == 1
    merged_id, survivor_id = merge_pairs[0]
    assert survivor_id == 2
    assert merged_id == 1


# --- observability ----------------------------------------------------------
# A pass that merged nothing used to emit no log line at all, making it
# indistinguishable from a pass that never ran. These lock that shut.


def _captured_metrics(monkeypatch) -> list[dict]:
    events: list[dict] = []
    monkeypatch.setattr(
        "backend.processing.speaker_merge.record_pipeline_metric",
        lambda **kwargs: events.append(kwargs) or kwargs,
    )
    return events


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_metric_is_emitted_even_when_nothing_merges(
    mock_counts: MagicMock, monkeypatch
) -> None:
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(-1.0)
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=7, threshold=0.70) == []

    assert len(events) == 1
    payload = events[0]["payload"]
    assert events[0]["stage"] == "speaker_merge_pass"
    assert payload["reason"] is None
    assert payload["merged_pair_count"] == 0
    # The near-miss score is the whole point: it separates "same voice, missed"
    # from "genuinely different voices".
    assert len(payload["pairs"]) == 1
    assert payload["pairs"][0]["cosine"] == -1.0
    assert payload["pairs"][0]["merged"] is False


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_metric_explains_why_a_pass_could_not_run(
    mock_counts: MagicMock, monkeypatch
) -> None:
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(id=2, diarization_label="SPEAKER_01", embedding=None),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=7) == []

    payload = events[0]["payload"]
    assert payload["reason"] == "fewer_than_two_speakers_with_embeddings"
    assert payload["skipped_no_embedding"] == 1
    assert payload["eligible_speaker_count"] == 1


# --- survivor selection -----------------------------------------------------


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_survivor_falls_back_to_speech_duration_without_utterances(
    mock_counts: MagicMock,
) -> None:
    """On the import path no utterance rows exist yet when this pass runs.

    Without speech duration the tiebreak was effectively arbitrary, so a
    two-minute fragment could win over a fifty-minute cluster.
    """
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.99)
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(
        session,
        recording_id=1,
        threshold=0.70,
        speech_seconds={"SPEAKER_00": 90.0, "SPEAKER_01": 3000.0},
    )

    assert merge_pairs == [(1, 2)]


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_identified_speaker_survives_over_a_larger_anonymous_one(
    mock_counts: MagicMock,
) -> None:
    """A merge must never discard a resolved person for an anonymous cluster."""
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_00",
            embedding=_make_embedding(1.0),
            global_speaker_id=42,
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=_make_embedding(0.99)
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(
        session,
        recording_id=1,
        threshold=0.70,
        speech_seconds={"SPEAKER_00": 10.0, "SPEAKER_01": 5000.0},
    )

    assert merge_pairs == [(2, 1)]


# --- merges the score must not be allowed to force ---------------------------


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_speakers_resolved_to_different_people_are_never_merged(
    mock_counts: MagicMock, monkeypatch
) -> None:
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_00",
            embedding=_make_embedding(1.0),
            global_speaker_id=10,
        ),
        FakeSpeaker(
            id=2,
            diarization_label="SPEAKER_01",
            embedding=_make_embedding(1.0),
            global_speaker_id=20,
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=1, threshold=0.70) == []

    pair = events[0]["payload"]["pairs"][0]
    assert pair["cosine"] == 1.0
    assert pair["merged"] is False
    assert pair["blocked_by"] == "distinct_global_speakers"


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_speakers_manually_named_differently_are_never_merged(
    mock_counts: MagicMock,
) -> None:
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_00",
            embedding=_make_embedding(1.0),
            local_name="Alice",
        ),
        FakeSpeaker(
            id=2,
            diarization_label="SPEAKER_01",
            embedding=_make_embedding(1.0),
            local_name="Bob",
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=1, threshold=0.70) == []


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_embeddings_from_different_methods_are_never_compared(
    mock_counts: MagicMock, monkeypatch
) -> None:
    """A cross-version cosine score is not a similarity and must not merge."""
    events = _captured_metrics(monkeypatch)
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_00",
            embedding=_make_embedding(1.0),
            embedding_version=EMBEDDING_METHOD_VERSION,
        ),
        FakeSpeaker(
            id=2,
            diarization_label="SPEAKER_01",
            embedding=_make_embedding(1.0),
            embedding_version=EMBEDDING_METHOD_VERSION - 1,
        ),
    ]
    mock_counts.return_value = {}
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=1, threshold=0.70) == []

    payload = events[0]["payload"]
    assert payload["skipped_version_mismatch"] == 1
    assert payload["reason"] == "fewer_than_two_speakers_with_embeddings"
