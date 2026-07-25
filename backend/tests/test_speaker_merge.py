"""Unit tests for the embedding-based speaker merge pass."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.processing.speaker_merge import (
    MAX_RECORDED_PAIR_SCORES,
    SKIP_INSUFFICIENT_EMBEDDINGS,
    SKIP_SINGLE_ACTIVE_SPEAKER,
    merge_duplicate_speakers,
)


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


@pytest.fixture
def merge_metrics(monkeypatch) -> list[dict]:
    """Capture every ``speaker_merge_pass`` metric emitted by the pass."""
    recorded: list[dict] = []

    def _capture(*, stage: str, recording_id=None, payload=None, **kwargs) -> dict:
        event = {
            "stage": stage,
            "recording_id": recording_id,
            "payload": payload or {},
        }
        recorded.append(event)
        return event

    monkeypatch.setattr(
        "backend.processing.speaker_merge.record_pipeline_metric", _capture
    )
    return recorded


def _only_metric(recorded: list[dict]) -> dict:
    assert len(recorded) == 1, f"expected exactly one metric, got {len(recorded)}"
    assert recorded[0]["stage"] == "speaker_merge_pass"
    return recorded[0]["payload"]


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


# --- Observability regression tests -------------------------------------------------
#
# A pass that merges nothing used to emit no log output at all, which made it
# indistinguishable from a pass that never ran. These pin the metric to every
# exit path so an over-clustering report stays diagnosable.


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_metric_records_every_below_threshold_pair(
    mock_counts: MagicMock, merge_metrics: list[dict]
) -> None:
    """The no-merge case is the one that was previously invisible."""
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=[1.0, 0.0, 0.0, 0.0]
        ),
        FakeSpeaker(
            id=2, diarization_label="SPEAKER_01", embedding=[0.0, 1.0, 0.0, 0.0]
        ),
        FakeSpeaker(
            id=3, diarization_label="SPEAKER_02", embedding=[0.0, 0.0, 1.0, 0.0]
        ),
        FakeSpeaker(
            id=4, diarization_label="SPEAKER_03", embedding=[0.0, 0.0, 0.0, 1.0]
        ),
    ]
    mock_counts.return_value = {1: 10, 2: 9, 3: 8, 4: 7}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=42, threshold=0.70)

    assert merge_pairs == []
    payload = _only_metric(merge_metrics)
    assert merge_metrics[0]["recording_id"] == 42
    assert payload["skip_reason"] is None
    assert payload["merged_count"] == 0
    assert payload["speaker_count"] == 4
    assert payload["eligible_count"] == 4
    # Four speakers -> all six pairs scored and reported, none above threshold.
    assert payload["pairs_considered"] == 6
    assert payload["pairs_omitted"] == 0
    assert len(payload["pairs"]) == 6
    assert all(pair["above_threshold"] is False for pair in payload["pairs"])
    assert {(pair["a_id"], pair["b_id"]) for pair in payload["pairs"]} == {
        (1, 2),
        (1, 3),
        (1, 4),
        (2, 3),
        (2, 4),
        (3, 4),
    }


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_metric_records_merges_and_scores(
    mock_counts: MagicMock, merge_metrics: list[dict]
) -> None:
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

    merge_pairs = merge_duplicate_speakers(session, recording_id=7, threshold=0.70)

    payload = _only_metric(merge_metrics)
    assert payload["merged_count"] == 1
    assert payload["merged_pairs"] == [[2, 1]]
    assert payload["threshold"] == 0.70
    assert payload["pairs_considered"] == 3
    above = [pair for pair in payload["pairs"] if pair["above_threshold"]]
    assert [(pair["a_id"], pair["b_id"]) for pair in above] == [(1, 2)]
    # Pairs are ordered highest-score-first so truncation drops only the
    # least interesting ones.
    scores = [pair["score"] for pair in payload["pairs"]]
    assert scores == sorted(scores, reverse=True)
    assert merge_pairs == [(2, 1)]


def test_metric_flags_insufficient_embeddings(merge_metrics: list[dict]) -> None:
    """Multiple speakers but no voiceprints: the safety net is inactive."""
    speakers = [
        FakeSpeaker(id=1, diarization_label="SPEAKER_00", embedding=None),
        FakeSpeaker(id=2, diarization_label="SPEAKER_01", embedding=None),
        FakeSpeaker(
            id=3, diarization_label="SPEAKER_02", embedding=_make_embedding(1.0)
        ),
    ]
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=9, threshold=0.70)

    assert merge_pairs == []
    payload = _only_metric(merge_metrics)
    assert payload["skip_reason"] == SKIP_INSUFFICIENT_EMBEDDINGS
    assert payload["speaker_count"] == 3
    assert payload["eligible_count"] == 1
    assert payload["pairs"] == []


def test_metric_flags_single_active_speaker(merge_metrics: list[dict]) -> None:
    """One speaker is the expected case, not an anomaly."""
    speakers = [
        FakeSpeaker(
            id=1, diarization_label="SPEAKER_00", embedding=_make_embedding(1.0)
        ),
    ]
    session = _fake_session_with_speakers(speakers)

    assert merge_duplicate_speakers(session, recording_id=3, threshold=0.70) == []

    payload = _only_metric(merge_metrics)
    assert payload["skip_reason"] == SKIP_SINGLE_ACTIVE_SPEAKER
    assert payload["speaker_count"] == 1
    assert payload["eligible_count"] == 1


def test_insufficient_embeddings_logs_a_warning(merge_metrics: list[dict], caplog):
    speakers = [
        FakeSpeaker(id=1, diarization_label="SPEAKER_00", embedding=None),
        FakeSpeaker(id=2, diarization_label="SPEAKER_01", embedding=None),
    ]
    session = _fake_session_with_speakers(speakers)

    with caplog.at_level(logging.WARNING, logger="backend.processing.speaker_merge"):
        merge_duplicate_speakers(session, recording_id=11, threshold=0.70)

    assert any(
        "merge pass cannot run" in record.message.lower()
        or "cannot run" in record.getMessage().lower()
        for record in caplog.records
    )


@patch("backend.processing.speaker_merge._count_utterances_per_speaker")
def test_metric_truncates_pair_list_but_keeps_near_misses(
    mock_counts: MagicMock, merge_metrics: list[dict]
) -> None:
    """Truncation is score-ordered, so a near-miss is never the pair dropped."""
    # 10 mutually dissimilar speakers -> 45 pairs, above MAX_RECORDED_PAIR_SCORES.
    speakers = [
        FakeSpeaker(
            id=index + 1,
            diarization_label=f"SPEAKER_{index:02d}",
            embedding=[1.0 if position == index else 0.0 for position in range(10)],
        )
        for index in range(10)
    ]
    # Make one pair a near-miss: speaker 10 sits close to speaker 1 but below 0.70.
    speakers[9].embedding = [0.6, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8]
    mock_counts.return_value = {index + 1: 1 for index in range(10)}
    session = _fake_session_with_speakers(speakers)

    merge_pairs = merge_duplicate_speakers(session, recording_id=5, threshold=0.70)

    assert merge_pairs == []
    payload = _only_metric(merge_metrics)
    assert payload["pairs_considered"] == 45
    assert payload["pairs_recorded"] == MAX_RECORDED_PAIR_SCORES
    assert payload["pairs_omitted"] == 45 - MAX_RECORDED_PAIR_SCORES
    assert len(payload["pairs"]) == MAX_RECORDED_PAIR_SCORES
    # The near-miss survives truncation because it is the highest-scoring pair.
    top_pair = payload["pairs"][0]
    assert (top_pair["a_id"], top_pair["b_id"]) == (1, 10)
    assert 0.55 < top_pair["score"] < 0.70


def test_metric_payload_carries_no_speaker_names(merge_metrics: list[dict]) -> None:
    """These lines get pasted into public issue reports."""
    speakers = [
        FakeSpeaker(
            id=1,
            diarization_label="SPEAKER_00",
            embedding=_make_embedding(1.0),
            name="Ada Lovelace",
        ),
        FakeSpeaker(
            id=2,
            diarization_label="SPEAKER_01",
            embedding=_make_embedding(-1.0),
            name="Alan Turing",
        ),
    ]
    session = _fake_session_with_speakers(speakers)

    with patch("backend.processing.speaker_merge._count_utterances_per_speaker") as m:
        m.return_value = {1: 4, 2: 4}
        merge_duplicate_speakers(session, recording_id=1, threshold=0.70)

    serialized = json.dumps(_only_metric(merge_metrics))
    assert "Ada Lovelace" not in serialized
    assert "Alan Turing" not in serialized
