"""Coverage is independent of label stability on every live diariser route."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backend.models.live_settling_public import public_transcript_segment
from backend.models.pipeline import TranscriptUtterance
from backend.processing.live_diarization import evidence
from backend.processing.live_diarization.hybrid_lines import prepare_label_line
from backend.processing.live_diarization.lines import LabelTimeline
from backend.processing.live_persistence import _mark_pending_speakers
from backend.tests.test_hybrid_publication import _engine
from backend.tests.test_live_settling_evidence import _snapshot
from backend.utils.canonical_pipeline.finalization import (
    _final_confidence_payload,
    _settled_final_confidence_payload,
)
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.canonical_pipeline.live_settling_projection import word_decisions
from backend.utils.canonical_pipeline.preservation import protected_final_payload
from backend.utils.config_manager import config_manager


@pytest.mark.parametrize("route", ["classic", "native", "fallback"])
def test_partial_and_missing_coverage_never_assign_an_overlapping_speaker(
    tmp_path: Path, route: str
) -> None:
    engine = _engine(tmp_path, route)
    if route == "native":
        engine.native_complete_frame = 150
    else:
        engine.shadow.next_end_n = 2 * 16000
        engine.native_complete_frame = 0
    decide = prepare_label_line(engine)
    for start, end in [(1.0, 2.0), (3.0, 4.0)]:
        assert decide(start, end) == LiveLabelDecision("UNKNOWN", False, None, True)


def test_covered_held_speech_is_unknown_even_before_its_label_locks() -> None:
    timeline = LabelTimeline(
        np.array([1.0]), np.array([2.0]), np.array([-1]), np.array([False]), 3.0
    )
    assert timeline.label_line(1, 2) == LiveLabelDecision("UNKNOWN", False, 0.0)
    assert timeline.label_line(2, 3) == LiveLabelDecision("UNKNOWN", True, None)


@pytest.mark.parametrize("native", [False, True])
def test_accepted_snapshot_returns_pending_for_partial_or_missing_coverage(
    native: bool,
) -> None:
    meta, arrays = _snapshot(native=native)
    snapshot = evidence._from_snapshot(meta, arrays)
    for start, end in [(2900, 3100), (4000, 4500)]:
        assert snapshot.decide(start, end) == LiveLabelDecision(
            "UNKNOWN", False, None, True
        )


def test_native_silence_matches_accepted_snapshot_unknown(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "native")
    engine.posteriors[:] = 0
    assert prepare_label_line(engine)(1, 2) == LiveLabelDecision("UNKNOWN", True, None)
    meta, arrays = _snapshot(native=True)
    arrays["hybrid_posteriors"][:] = 0
    assert evidence._from_snapshot(meta, arrays).decide(1000, 2000) == (
        LiveLabelDecision("UNKNOWN", True, None)
    )


@pytest.mark.parametrize(
    "device,enabled", [("cuda", True), ("cuda:0", True), ("cpu", False)]
)
def test_whole_line_publication_marks_only_cuda_unknowns_pending(
    monkeypatch: pytest.MonkeyPatch, device: str, enabled: bool
) -> None:
    monkeypatch.setattr(config_manager, "get", lambda *_args: "auto")
    segments: list[dict[str, Any]] = [
        {"speaker": "UNKNOWN"},
        {"speaker": "LIVE_PROVISIONAL_1"},
    ]
    _mark_pending_speakers(segments, {"_settling_device": device})
    assert (
        bool(public_transcript_segment(segments[0]).get("diarisation_pending"))
        is enabled
    )
    assert "diarisation_pending" not in public_transcript_segment(segments[1])


@pytest.mark.parametrize("mode", ["legacy", "settled", "protected"])
def test_final_and_human_protected_payloads_remove_live_pending(
    mode: str,
) -> None:
    payload = {"live_speaker_label": {"diarisation_pending": True, "locked": False}}
    final_segment = {"start": 0.0, "end": 1.0, "text": "Hello"}
    final = (
        protected_final_payload(final_segment, payload)
        if mode == "protected"
        else _settled_final_confidence_payload(final_segment, payload, matched=True)
        if mode == "settled"
        else _final_confidence_payload(final_segment, payload, matched=True)
    )
    segment = public_transcript_segment(
        {"speaker": "UNKNOWN", "diarisation_pending": True, "confidence_payload": final}
    )
    assert "diarisation_pending" not in segment


def test_untimed_atom_uses_observed_bounds_for_pending_without_inventing_word_times() -> (
    None
):
    atom = {
        "id": "untimed",
        "word": " tail",
        "start_ms": None,
        "end_ms": None,
        "observed_audio_start_ms": 2000,
        "observed_audio_end_ms": 4000,
    }
    words = [{"id": "timed", "word": "Hello", "start_ms": 1000, "end_ms": 1500}, atom]

    def decide(_start: int, end: int) -> LiveLabelDecision:
        return (
            LiveLabelDecision("UNKNOWN", False, None, True)
            if end > 3000
            else LiveLabelDecision("A", False, 1.0)
        )

    decisions = word_decisions(words, [], decide, speaker_enabled=True)
    assert decisions == [
        LiveLabelDecision("A", False, 1.0),
        LiveLabelDecision("UNKNOWN", False, None, True),
    ]
    row = TranscriptUtterance(
        recording_id=1,
        public_id="line",
        sort_key="0",
        start_ms=2000,
        end_ms=4000,
        speaker_label="UNKNOWN",
        text=" tail",
        confidence_payload={
            "asr_segments": [{"words": [atom]}],
            "live_speaker_label": {"locked": False, "diarisation_pending": True},
        },
    )
    assert word_decisions([atom], [row], None, speaker_enabled=True)[
        0
    ].diarisation_pending
    covered = word_decisions(
        words,
        [],
        lambda _start, _end: LiveLabelDecision("A", False, 1.0),
        speaker_enabled=True,
    )
    assert covered == [
        LiveLabelDecision("A", False, 1.0),
        LiveLabelDecision("A", False, 1.0),
    ]
    assert (
        atom["id"] == "untimed" and atom["start_ms"] is None and atom["end_ms"] is None
    )


def test_covered_untimed_atom_does_not_inherit_pending_from_a_later_neighbour() -> None:
    words = [
        {
            "id": "covered",
            "word": "Hello",
            "start_ms": None,
            "end_ms": None,
            "observed_audio_start_ms": 100,
            "observed_audio_end_ms": 900,
        },
        {"id": "pending", "word": " tail", "start_ms": 1100, "end_ms": 1400},
    ]

    def decide(_start: int, end: int) -> LiveLabelDecision:
        if end > 1000:
            return LiveLabelDecision("UNKNOWN", False, None, True)
        return LiveLabelDecision("A", False, 1.0)

    assert word_decisions(words, [], decide, speaker_enabled=True) == [
        LiveLabelDecision("UNKNOWN", False, None),
        LiveLabelDecision("UNKNOWN", False, None, True),
    ]
