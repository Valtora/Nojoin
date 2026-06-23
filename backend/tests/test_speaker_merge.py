"""Unit tests for the embedding-based speaker merge pass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

from backend.processing.speaker_merge import merge_duplicate_speakers


@dataclass
class FakeSpeaker:
    id: int
    diarization_label: str
    embedding: Optional[list[float]] = None
    merged_into_id: Optional[int] = None
    name: Optional[str] = None


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
