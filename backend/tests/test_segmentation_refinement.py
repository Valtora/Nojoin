"""Phase F5: unit tests for the frame-level segmentation refinement helpers.

These tests cover the deterministic pure-Python helpers
(``_runs_from_frames`` and ``_best_match_to_recording_speakers``) plus the
``refine_utterance_via_segmentation`` orchestrator with the pyannote model
substituted by a synthetic ndarray.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from backend.processing import segmentation_refinement
from backend.processing.segmentation_refinement import (
    SEGMENTATION_REFINEMENT_TIE_BREAK_MARGIN,
    SegmentationTurnRow,
    _best_match_to_recording_speakers,
    _runs_from_frames,
    refine_utterance_via_segmentation,
)


@dataclass
class _FakeSpeaker:
    id: int
    embedding: list[float]


@dataclass
class _FakeUtterance:
    public_id: str
    start_ms: int
    end_ms: int


def test_runs_from_frames_splits_two_disjoint_speakers() -> None:
    # 100 frames spanning 1000 ms (10 ms each). Speaker 0 active on the
    # first half, speaker 1 active on the second half.
    frames = np.zeros((100, 2), dtype=float)
    frames[:50, 0] = 1.0
    frames[50:, 1] = 1.0

    runs = _runs_from_frames(frames, span_start_ms=2000, span_end_ms=3000)

    assert len(runs) == 2
    runs.sort(key=lambda run: run["local_speaker_idx"])
    assert runs[0]["local_speaker_idx"] == 0
    assert runs[0]["start_ms"] == 2000
    assert 2480 <= runs[0]["end_ms"] <= 2520
    assert runs[1]["local_speaker_idx"] == 1
    assert 2480 <= runs[1]["start_ms"] <= 2520
    assert runs[1]["end_ms"] == 3000


def test_runs_from_frames_bridges_small_gaps_and_drops_short_runs() -> None:
    # 200 frames spanning 2000 ms (10 ms each).
    frames = np.zeros((200, 1), dtype=float)
    # Active 0-700 ms, idle 700-770 ms (gap of 70 ms < 80 ms threshold),
    # active 770-1500 ms.
    frames[0:70, 0] = 1.0
    frames[77:150, 0] = 1.0
    # Spurious 100 ms blip at the end (>= 250 ms required) — should be dropped.
    frames[160:170, 0] = 1.0

    runs = _runs_from_frames(frames, span_start_ms=0, span_end_ms=2000)

    assert len(runs) == 1
    assert runs[0]["start_ms"] == 0
    assert runs[0]["end_ms"] == 1500


def test_runs_from_frames_returns_empty_for_inactive_signal() -> None:
    frames = np.zeros((40, 2), dtype=float)
    assert _runs_from_frames(frames, span_start_ms=0, span_end_ms=400) == []


def test_best_match_returns_top_two_scores_in_order() -> None:
    speakers = [
        _FakeSpeaker(id=1, embedding=[1.0, 0.0]),
        _FakeSpeaker(id=2, embedding=[0.0, 1.0]),
        _FakeSpeaker(id=3, embedding=[0.5, 0.5]),
    ]
    best, second, best_score, second_score = _best_match_to_recording_speakers(
        [1.0, 0.0], speakers
    )
    assert best.id == 1
    assert second.id == 3
    assert pytest.approx(best_score, abs=1e-3) == 1.0
    assert pytest.approx(second_score, abs=1e-3) == 0.707


def test_best_match_skips_speakers_without_embedding() -> None:
    speakers = [
        _FakeSpeaker(id=1, embedding=[]),
        _FakeSpeaker(id=2, embedding=[0.0, 1.0]),
    ]
    best, _, best_score, _ = _best_match_to_recording_speakers([0.0, 1.0], speakers)
    assert best.id == 2
    assert pytest.approx(best_score, abs=1e-3) == 1.0


def test_refine_utterance_returns_empty_when_fewer_than_two_speakers() -> None:
    speaker = _FakeSpeaker(id=1, embedding=[1.0, 0.0])
    utterance = _FakeUtterance(public_id="u1", start_ms=0, end_ms=2000)
    rows = refine_utterance_via_segmentation(
        "/tmp/fake.wav",
        utterance=utterance,
        recording_speakers=[speaker],
        device_str="cpu",
        hf_token=None,
    )
    assert rows == []


def test_refine_utterance_returns_empty_for_short_utterances(monkeypatch: Any) -> None:
    called = {"value": False}

    def _fail_inference(*_args: Any, **_kwargs: Any) -> np.ndarray:
        called["value"] = True
        raise AssertionError("inference should not be invoked")

    monkeypatch.setattr(
        segmentation_refinement, "_run_segmentation_inference", _fail_inference
    )

    rows = refine_utterance_via_segmentation(
        "/tmp/fake.wav",
        utterance=_FakeUtterance(public_id="u-short", start_ms=0, end_ms=400),
        recording_speakers=[
            _FakeSpeaker(id=1, embedding=[1.0, 0.0]),
            _FakeSpeaker(id=2, embedding=[0.0, 1.0]),
        ],
        device_str="cpu",
        hf_token=None,
    )
    assert rows == []
    assert called["value"] is False


def test_refine_utterance_produces_split_turn_rows(monkeypatch: Any) -> None:
    # Synthetic frame matrix: 100 frames over a 2000 ms utterance — local
    # speaker 0 active 0-1000 ms, local speaker 1 active 1000-2000 ms.
    frames = np.zeros((100, 2), dtype=float)
    frames[:50, 0] = 1.0
    frames[50:, 1] = 1.0

    monkeypatch.setattr(
        segmentation_refinement,
        "_run_segmentation_inference",
        lambda *_args, **_kwargs: frames,
    )

    embedding_calls: list[tuple[Any, list[tuple[float, float]]]] = []

    def _fake_extract(audio_path: str, segments: list[tuple[float, float]], **_kw: Any):
        embedding_calls.append((audio_path, segments))
        # Segments are in absolute recording-time seconds. The span covers
        # 5.0-7.0 s, so local 0 runs sit before 6.0 s and local 1 runs sit
        # after — pick the matching embedding accordingly.
        mid_s = sum((start + end) / 2.0 for start, end in segments) / len(segments)
        if mid_s < 6.0:
            return [1.0, 0.0]
        return [0.0, 1.0]

    monkeypatch.setattr(
        segmentation_refinement, "extract_embedding_for_segments", _fake_extract
    )

    speakers = [
        _FakeSpeaker(id=11, embedding=[1.0, 0.0]),
        _FakeSpeaker(id=22, embedding=[0.0, 1.0]),
    ]
    utterance = _FakeUtterance(public_id="u-split", start_ms=5000, end_ms=7000)

    rows = refine_utterance_via_segmentation(
        "/tmp/fake.wav",
        utterance=utterance,
        recording_speakers=speakers,
        device_str="cpu",
        hf_token=None,
    )

    assert len(rows) == 2
    assert all(isinstance(row, SegmentationTurnRow) for row in rows)
    rows.sort(key=lambda row: row.start_ms)
    assert rows[0].matched_recording_speaker_id == 11
    assert rows[0].start_ms == 5000
    assert rows[0].end_ms == 6000
    assert rows[1].matched_recording_speaker_id == 22
    assert rows[1].start_ms == 6000
    assert rows[1].end_ms == 7000


def test_refine_utterance_tie_breaker_runs_when_margin_below_threshold(
    monkeypatch: Any,
) -> None:
    frames = np.zeros((100, 2), dtype=float)
    frames[:50, 0] = 1.0
    frames[50:, 1] = 1.0
    monkeypatch.setattr(
        segmentation_refinement,
        "_run_segmentation_inference",
        lambda *_args, **_kwargs: frames,
    )

    speakers = [
        _FakeSpeaker(id=11, embedding=[1.0, 0.0, 0.0]),
        _FakeSpeaker(id=22, embedding=[0.0, 1.0, 0.0]),
    ]

    call_log: list[list[tuple[float, float]]] = []

    def _fake_extract(_audio: str, segments: list[tuple[float, float]], **_kw: Any):
        call_log.append(list(segments))
        # First (aggregate) call returns a near-tie embedding so the tie-break
        # path runs; the centred re-embed then returns a clear win for the
        # *other* speaker, which must flip the assignment.
        if len(call_log) == 1:
            return [0.70, 0.71, 0.0]
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(
        segmentation_refinement, "extract_embedding_for_segments", _fake_extract
    )

    speaker_zero_runs = [{"local_speaker_idx": 0, "start_ms": 0, "end_ms": 1000}]
    best, score = segmentation_refinement._match_local_speaker(
        "/tmp/fake.wav",
        runs_for_local=speaker_zero_runs,
        recording_speakers=speakers,
        device_str="cpu",
        hf_token=None,
    )
    # First-pass margin is ~0.01 < 0.05 so the tie-breaker runs and produces
    # a clean win for speaker 11, which must override the aggregate winner.
    assert len(call_log) == 2
    assert best is not None
    assert best.id == 11
    assert score == pytest.approx(1.0, abs=1e-3)
    assert SEGMENTATION_REFINEMENT_TIE_BREAK_MARGIN == pytest.approx(0.05)
