"""Tests for the post-diarization per-speaker stats metric.

The metric exists so an over-clustering report can be triaged from a user's
logs: it is what distinguishes genuine over-clustering (several clusters each
holding substantial speech) from clusters the phantom filter nearly caught.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.worker.tasks.pipeline import (
    _diarization_speaker_stats,
    _record_diarization_speaker_stats,
)


@dataclass
class FakeSegment:
    duration: float


class FakeAnnotation:
    """Minimal stand-in for a pyannote Annotation."""

    def __init__(self, tracks: list[tuple[float, str]]) -> None:
        self._tracks = tracks

    def itertracks(self, yield_label: bool = False):
        for index, (duration, label) in enumerate(self._tracks):
            yield FakeSegment(duration), f"track_{index}", label


def test_stats_aggregate_duration_and_count_per_label() -> None:
    annotation = FakeAnnotation(
        [
            (10.0, "SPEAKER_00"),
            (5.5, "SPEAKER_01"),
            (20.0, "SPEAKER_00"),
            (1.25, "SPEAKER_02"),
        ]
    )

    stats = _diarization_speaker_stats(annotation)

    assert stats == [
        {"speaker": "SPEAKER_00", "total_speech_s": 30.0, "segment_count": 2},
        {"speaker": "SPEAKER_01", "total_speech_s": 5.5, "segment_count": 1},
        {"speaker": "SPEAKER_02", "total_speech_s": 1.25, "segment_count": 1},
    ]


def test_stats_are_ordered_longest_speaking_first() -> None:
    annotation = FakeAnnotation(
        [(1.0, "SPEAKER_00"), (50.0, "SPEAKER_01"), (10.0, "SPEAKER_02")]
    )

    labels = [entry["speaker"] for entry in _diarization_speaker_stats(annotation)]

    assert labels == ["SPEAKER_01", "SPEAKER_02", "SPEAKER_00"]


def test_metric_reports_four_substantial_clusters(monkeypatch) -> None:
    """The shape of the reported failure: two people, four sizeable clusters."""
    recorded: list[dict] = []
    monkeypatch.setattr(
        "backend.worker.tasks.pipeline.record_pipeline_metric",
        lambda **kwargs: recorded.append(kwargs) or {},
    )
    annotation = FakeAnnotation(
        [
            (1800.0, "SPEAKER_00"),
            (1500.0, "SPEAKER_01"),
            (1200.0, "SPEAKER_02"),
            (900.0, "SPEAKER_03"),
        ]
    )

    _record_diarization_speaker_stats(77, annotation)

    assert len(recorded) == 1
    assert recorded[0]["stage"] == "final_diarization_speaker_stats"
    assert recorded[0]["recording_id"] == 77
    payload = recorded[0]["payload"]
    assert payload["speaker_count"] == 4
    assert [entry["total_speech_s"] for entry in payload["speakers"]] == [
        1800.0,
        1500.0,
        1200.0,
        900.0,
    ]


def test_metric_failure_never_aborts_finalize(monkeypatch) -> None:
    """Best-effort: a broken annotation must not crash the pipeline."""

    class ExplodingAnnotation:
        def itertracks(self, yield_label: bool = False):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "backend.worker.tasks.pipeline.record_pipeline_metric",
        lambda **kwargs: {},
    )

    _record_diarization_speaker_stats(1, ExplodingAnnotation())
