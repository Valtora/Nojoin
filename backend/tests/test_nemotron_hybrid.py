"""Behavioural live routing, causal identity and packet-clock regression tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backend.processing.live_diarization.engine import Piece
from backend.processing.live_diarization.hybrid import HybridLiveState
from backend.processing.live_diarization.nemotron import NemotronStream
from backend.processing.live_diarization.state import new_engine
from backend.processing.live_diarization.step import CONFIG
from backend.processing.nemotron_types import NativeWindowResult, ProbabilityBlock
from backend.tests.live_label_fakes import SpeakerTrackModel, speaker_audio
from backend.tests.nemotron_fakes import ObservedRuntime


class ActiveRuntime(ObservedRuntime):
    def infer_window(
        self, audio_window: np.ndarray, **kwargs: Any
    ) -> NativeWindowResult:
        result = super().infer_window(audio_window, **kwargs)
        return NativeWindowResult(
            np.full_like(result.probabilities, 0.8), result.speaker_cache
        )


def hybrid(path: Path, runtime: ObservedRuntime | None = None) -> HybridLiveState:
    path.mkdir(exist_ok=True)
    return HybridLiveState(
        new_engine(path, CONFIG), NemotronStream(runtime or ObservedRuntime())
    )


@pytest.mark.parametrize("delivery", [32000, 17013, 320000])
def test_backlog_and_overlapping_input_have_identical_causal_switch(
    tmp_path: Path, delivery: int
) -> None:
    audio = speaker_audio([(0, 6, 1), (6, 12, 2), (12, 20, 1)], 20)
    reference_runtime, runtime = ActiveRuntime(), ActiveRuntime()
    reference = hybrid(tmp_path / "reference", reference_runtime)
    actual = hybrid(tmp_path / "actual", runtime)
    for start in range(0, len(audio), 32000):
        reference.feed(audio[start : start + 32000], start, SpeakerTrackModel())
    model = SpeakerTrackModel()
    for start in range(0, len(audio), delivery):
        prefix = max(0, start - 791)
        actual.feed(audio[prefix : start + delivery], prefix, model)
    assert runtime.calls == reference_runtime.calls
    assert len(runtime.calls) == 5
    assert actual.route == "clustering"
    assert actual.switch_emitted_at_n == reference.switch_emitted_at_n == 64000
    assert actual.native_complete_frame == reference.native_complete_frame == 360
    assert actual.translation == reference.translation
    assert actual.consumed_n == reference.consumed_n == len(audio)
    assert actual.packet_cursor_n == reference.packet_cursor_n
    assert len(actual.pending) == len(reference.pending) == 0
    assert actual.translation[0] == 0
    # AHC can retain a different internal numeric label for the second voice;
    # the first previously unseen display identity must still be Speaker 9.
    assert actual.label_line(7, 9).speaker == "LIVE_PROVISIONAL_9"
    for interval in [(0, 1), (3, 5), (7, 9), (15, 17)]:
        assert actual.label_line(*interval) == reference.label_line(*interval)


def test_native_line_sums_raw_probabilities_with_partial_and_fractional_frames(
    tmp_path: Path,
) -> None:
    engine = hybrid(tmp_path)
    probabilities = np.zeros((100, 8), np.float32)
    probabilities[:, 0] = 0.49
    probabilities[:40, 1] = 0.9
    probabilities[99, 2] = 0.99
    engine.accept_blocks([ProbabilityBlock(0, probabilities, 32000)])
    assert engine.label_line(0, 2).speaker == "LIVE_PROVISIONAL_1"
    assert not engine.label_line(0, 2).locked
    assert engine.label_line(0, 1).locked
    assert engine.label_line(0.991, 0.999).speaker == "LIVE_PROVISIONAL_3"
    assert engine.label_line(2, 3).speaker == "UNKNOWN"
    # A nonempty all-zero vector still has an argmax: channel 0 wins the tie.
    # UNKNOWN is reserved for an interval with no emitted frames.
    engine.posteriors[:] = 0
    assert engine.label_line(0, 1).speaker == "LIVE_PROVISIONAL_1"
    # No side effects or identity allocation through callback order.
    before = copy.deepcopy(engine.translation), engine.next_display_id, engine.route
    for interval in [(4, 5), (0, 1), (0, 2)]:
        engine.label_line(*interval)
    assert before == (engine.translation, engine.next_display_id, engine.route)


def test_route_uses_float16_threshold_and_all_eight_founding_counters(
    tmp_path: Path,
) -> None:
    engine = hybrid(tmp_path)
    near = np.full((200, 8), 0.5001, np.float32)
    engine.accept_blocks([ProbabilityBlock(0, near, 32000)])
    assert engine.route == "nemotron"
    assert not engine.used_eighth_channel
    active = np.full((199, 8), 0.8, np.float32)
    engine.accept_blocks([ProbabilityBlock(200, active, 64000)])
    assert engine.used_eighth_channel
    assert engine.route == "nemotron"
    last = np.full((1, 8), 0.8, np.float32)
    last[:, 0] = 0.5
    engine.accept_blocks([ProbabilityBlock(399, last, 96000)])
    assert engine.route == "nemotron"
    last[:, 0] = 0.8
    engine.accept_blocks([ProbabilityBlock(400, last, 128000)])
    assert engine.route == "clustering"
    assert engine.reason == "channel_cap"
    assert engine.switch_emitted_at_n == 128000


def test_gap_latches_shadow_and_never_infers_synthetic_gap_audio(
    tmp_path: Path,
) -> None:
    runtime = ObservedRuntime()
    engine = hybrid(tmp_path, runtime)
    model = SpeakerTrackModel()
    engine.feed(np.ones(40000, np.float32), 0, model)
    calls = list(runtime.calls)
    engine.feed(np.ones(32000, np.float32), 40001, model)
    engine.feed(np.ones(64000, np.float32), 72001, model)
    assert runtime.calls == calls
    assert engine.route == "clustering"
    assert engine.reason == "audio_gap"
    assert engine.switch_emitted_at_n == 40000
    assert engine.stream is None
    assert engine.label_line(0, 1).speaker == "LIVE_PROVISIONAL_1"
    assert engine.label_line(3, 6).speaker == "LIVE_PROVISIONAL_9"


def test_failed_second_forward_drops_partial_packet_and_latches_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.processing.live_diarization import hybrid_runtime

    class FailingRuntime(ObservedRuntime):
        def infer_window(
            self, audio_window: np.ndarray, **kwargs: Any
        ) -> NativeWindowResult:
            if self.calls:
                raise RuntimeError("CUDA out of memory")
            return super().infer_window(audio_window, **kwargs)

    released = []
    monkeypatch.setattr(hybrid_runtime, "release_native", lambda: released.append(True))
    runtime = FailingRuntime()
    engine = hybrid(tmp_path, runtime)
    engine.feed(np.ones(128000, np.float32), 0, SpeakerTrackModel())
    assert engine.reason == "oom"
    assert engine.route == "clustering" and engine.stream is None
    assert engine.native_complete_frame == 0
    assert len(runtime.calls) == 1
    assert released == [True]
    assert engine.label_line(2, 6).speaker == "LIVE_PROVISIONAL_9"


def test_process_control_exceptions_propagate(tmp_path: Path) -> None:
    class CancelledRuntime(ObservedRuntime):
        def infer_window(
            self, audio_window: np.ndarray, **kwargs: Any
        ) -> NativeWindowResult:
            raise KeyboardInterrupt()

    engine = hybrid(tmp_path, CancelledRuntime())
    with pytest.raises(KeyboardInterrupt):
        engine.feed(np.ones(32000, np.float32), 0, SpeakerTrackModel())


def test_translation_uses_current_unit_labels_mutable_tail_and_no_future_evidence(
    tmp_path: Path,
) -> None:
    engine = hybrid(tmp_path)
    engine.shadow.feed(np.ones(128000, np.float32), 0, SpeakerTrackModel())
    # Two units, initially distinct labels, become fragments of one label at
    # the actual hand-back. Their activity supports channel 3, tie chooses 2.
    engine.units.label = [4, 4]
    engine.shadow.pieces = [Piece(0, 1, 0), Piece(1, 2, 1), Piece(1, 2, 1)]
    engine.overlap = np.array([[0, 0, 1, 0, 0, 0, 0, 0], [0] * 8], np.float64)
    engine.overlap_until_s = 1
    probabilities = np.zeros((800, 8), np.float32)
    probabilities[100:200, 3] = 0.8
    engine.accept_blocks([ProbabilityBlock(0, probabilities, 128000)])
    engine.fallback("audio_gap", 128000)
    assert engine.translation == {4: 2}
    # Frozen mapping follows numeric identity, never future relabelled evidence.
    engine.units.label = [4, 5]
    engine._allocate_labels()
    assert engine.translation == {4: 2, 5: 8}
    assert engine.next_display_id == 9


def test_retired_numeric_slots_recycle_identically_in_native_and_classic(
    tmp_path: Path,
) -> None:
    engine = hybrid(tmp_path)
    clustering = engine.clustering
    clustering.centers = np.zeros((128, 256))
    voice = np.eye(256)[0]
    assert clustering.add_center(voice) == 0
    assert clustering.add_center(voice) == 1
    clustering.reset_centers({0: voice})
    assert not clustering.active[1]
    assert clustering.add_center(np.eye(256)[1]) == 1
    classic = new_engine(tmp_path / "classic", CONFIG).clustering
    classic.centers = np.zeros((128, 256))
    assert classic.add_center(voice) == 0
    assert classic.add_center(voice) == 1
    classic.reset_centers({0: voice})
    assert classic.add_center(np.eye(256)[1]) == 1


def test_fallback_translates_the_shadow_winner_without_merging_fragment_votes(
    tmp_path: Path,
) -> None:
    engine = hybrid(tmp_path)
    engine.shadow.feed(np.ones(128000, np.float32), 0, SpeakerTrackModel())
    engine.units.label = [0, 1]
    engine.units.locked = [True, True]
    engine.shadow.pieces = [Piece(0, 1, 0), Piece(1, 2, 1)]
    engine.fallback("audio_gap", 128000)
    engine.translation = {0: 2, 1: 2}
    # Line extends beyond frozen native prefix (zero here), so shadow applies.
    decision = engine.label_line(0, 2)
    assert decision.speaker == "UNKNOWN"
    assert decision.locked


def test_backfilled_unit_receives_mutable_overlap_at_handback(tmp_path: Path) -> None:
    engine = hybrid(tmp_path)
    engine.shadow.feed(np.ones(128000, np.float32), 0, SpeakerTrackModel())
    engine.units.label = [0, 1]
    engine.shadow.pieces = [Piece(6, 7, 0, open=True)]
    probabilities = np.zeros((800, 8), np.float32)
    probabilities[600:700, 4] = 0.8
    engine.accept_blocks([ProbabilityBlock(0, probabilities, 128000)])
    engine._accumulate_stable_overlap()
    engine.shadow.pieces[0].unit = 1  # the engine's back-fill transition
    engine.fallback("audio_gap", 128000)
    assert engine.translation[0] == 8
    assert engine.translation[1] == 4


def test_restart_duplicate_source_change_latches_fallback(tmp_path: Path) -> None:
    runtime = ObservedRuntime()
    engine = hybrid(tmp_path, runtime)
    engine.feed(np.ones(40000, np.float32), 0, SpeakerTrackModel())
    calls = list(runtime.calls)
    changed = np.ones(48000, np.float32)
    changed[0] = 0.75
    engine.feed(changed, 32000, SpeakerTrackModel())
    assert engine.reason == "state_incompatible"
    assert engine.route == "clustering" and engine.stream is None
    assert runtime.calls == calls


def test_unfinished_prefix_line_keeps_native_after_handback_but_straddler_uses_shadow(
    tmp_path: Path,
) -> None:
    engine = hybrid(tmp_path)
    engine.shadow.feed(np.ones(128000, np.float32), 0, SpeakerTrackModel())
    engine.units.locked = [True] * engine.units.count
    probabilities = np.zeros((300, 8), np.float32)
    probabilities[:, 2] = 0.8
    engine.accept_blocks([ProbabilityBlock(0, probabilities, 64000)])
    assert engine.label_line(2, 4).speaker == "LIVE_PROVISIONAL_3"
    assert not engine.label_line(2, 4).locked
    engine.fallback("audio_gap", 128000)
    engine.translation[0] = 8
    prefix = engine.label_line(1, 2)
    straddler = engine.label_line(2, 4)
    assert prefix.speaker == "LIVE_PROVISIONAL_3" and prefix.locked
    assert straddler.speaker == "LIVE_PROVISIONAL_9" and straddler.locked
