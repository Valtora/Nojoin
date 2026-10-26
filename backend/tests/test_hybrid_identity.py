"""Slot recycling, display continuity and checkpoint corruption regressions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backend.processing.live_diarization import state
from backend.processing.live_diarization.engine import LiveLabelConfig
from backend.processing.live_diarization.hybrid import HybridLiveState
from backend.processing.live_diarization.hybrid_state import load_hybrid, save_hybrid
from backend.processing.live_diarization.nemotron import NemotronStream
from backend.tests.live_label_fakes import SpeakerTrackModel, speaker_audio
from backend.tests.nemotron_fakes import ObservedRuntime


def switched(path: Path) -> HybridLiveState:
    engine = HybridLiveState(
        state.new_engine(path, LiveLabelConfig()), NemotronStream(ObservedRuntime())
    )
    engine.feed(speaker_audio([(0, 8, 1)], 8), 0, SpeakerTrackModel())
    engine.fallback("audio_gap", engine.consumed_n)
    return engine


def test_recycled_slot_gets_fresh_display_after_restart(tmp_path: Path) -> None:
    engine = switched(tmp_path)
    old = engine.translation[0]
    # A re-cluster moves all slot-0 units to slot 1 and retires centroid 0.
    engine.units.close_all(engine.bank)
    engine.units.label[:] = [1] * engine.units.count
    engine.clustering.reset_centers({1: np.eye(256)[0]})
    engine._allocate_labels()
    assert 0 not in engine.translation
    save_hybrid(tmp_path, engine)
    restored = load_hybrid(tmp_path, engine.config, recording_id=0)
    assert restored is not None
    fresh_floor = restored.next_display_id + 1
    # The production allocator reuses 0, without reusing its old display.
    restored.feed(
        speaker_audio([(0, 6, 2)], 6), restored.consumed_n, SpeakerTrackModel()
    )
    assert restored.units.label[-1] == 0
    assert restored.translation[0] > old
    decision = restored.label_line(12, 14)
    assert int(decision.speaker.rsplit("_", 1)[1]) >= fresh_floor


def test_retire_and_reuse_between_publications_cannot_inherit_translation(
    tmp_path: Path,
) -> None:
    engine = switched(tmp_path)
    old = engine.translation[0]
    engine.units.close_all(engine.bank)
    engine.units.label[:] = [1] * engine.units.count
    engine.clustering.reset_centers({1: np.eye(256)[0]})
    # No display update between retirement and the next founding.
    engine.feed(speaker_audio([(0, 6, 2)], 6), 8 * 16000, SpeakerTrackModel())
    assert engine.units.label[-1] == 0
    assert engine.translation[0] > old


def test_existing_locked_unit_witness_keeps_display_during_cluster_growth(
    tmp_path: Path,
) -> None:
    engine = switched(tmp_path)
    engine.units.locked[:] = [True] * engine.units.count
    before = engine.label_line(6, 7)
    assert before.locked
    save_hybrid(tmp_path, engine)
    restored = load_hybrid(tmp_path, engine.config, recording_id=0)
    assert restored is not None
    engine = restored
    engine.feed(speaker_audio([(0, 2, 1)], 2), 8 * 16000, SpeakerTrackModel())
    assert engine.label_line(6, 7) == before
    assert engine.translation[0] == 8


def test_damaged_continuity_is_rejected_before_bank_truncation(tmp_path: Path) -> None:
    engine = switched(tmp_path)
    save_hybrid(tmp_path, engine)
    engine.bank.append(np.ones(256, np.float32))
    before = engine.bank.path.read_bytes()
    snapshot = state.read_snapshot(tmp_path, engine.config)
    assert snapshot is not None
    meta, arrays = snapshot
    arrays["hybrid_display_labels"] = np.full(engine.units.count, 1, np.int64)
    state.write_snapshot(tmp_path, meta, arrays)
    with pytest.raises(state.LiveLabelStateError, match="display label continuity"):
        load_hybrid(tmp_path, engine.config, recording_id=0)
    assert engine.bank.path.read_bytes() == before


class LifetimeModel:
    """A reliable singleton voice each cycle, merged away by default AHC.

    The main voice has many reliable units. The one-second novel voice has
    only one, so classic's min_units=2 rule merges it into the main cluster.
    Each following cycle therefore founds a new cluster in the retired slot.
    """

    def __init__(self) -> None:
        rng = np.random.default_rng(71)
        vectors = rng.normal(size=(170, 256))
        self.voices = (vectors / np.linalg.norm(vectors, axis=1)[:, None]).astype(
            np.float32
        )

    def __call__(self, windows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        speakers = windows[:, -1].astype(int)
        return (
            np.ones((len(windows), 293, 1), np.float32),
            self.voices[speakers, None, :],
        )


def record_labels(engine: Any) -> list[tuple[str, tuple[int, ...]]]:
    """Observe each completed step and re-cluster without changing either."""
    events: list[tuple[str, tuple[int, ...]]] = []
    step, recluster = engine._step, engine._maybe_recluster

    def observe_step(*args: Any) -> None:
        step(*args)
        events.append(("step", tuple(engine.units.label)))

    def observe_recluster(*args: Any) -> None:
        recluster(*args)
        events.append(("packet", tuple(engine.units.label)))

    engine._step = observe_step
    engine._maybe_recluster = observe_recluster
    return events


def test_full_shadow_history_matches_classic_past_127_lifetime_clusters(
    tmp_path: Path,
) -> None:
    classic_dir = tmp_path / "classic"
    classic_dir.mkdir()
    classic = state.new_engine(classic_dir, LiveLabelConfig())
    engine = HybridLiveState(
        state.new_engine(tmp_path, LiveLabelConfig()), NemotronStream(ObservedRuntime())
    )
    engine.fallback("channel_cap", 0)
    history = record_labels(engine.shadow)
    control = record_labels(classic)
    model = LifetimeModel()
    founded = 0
    displays: list[int] = []
    for cycle in range(160):
        for packet in range(5):
            audio = np.ones(32000, np.float32)
            if packet == 3:
                audio[16000:] = cycle + 2
            start = (cycle * 5 + packet) * 32000
            result = engine.feed(audio, start, model)
            classic.feed(audio, start, model)
            founded += result.founded
            assert history == control
            if packet == 3:
                displays.append(engine.translation[1])
        assert not engine.clustering.active[1]
    assert founded == 161
    assert len(set(displays)) == 160
    assert engine.next_display_id > 160
