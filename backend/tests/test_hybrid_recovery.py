"""Recovery regressions for lost identities, partial gaps and runtime release."""

from __future__ import annotations

import sys
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from sqlmodel import Session

from backend.processing import nemotron_model
from backend.processing.live_diarization import hybrid_runtime, state, step
from backend.processing.live_diarization.engine import LiveLabelConfig
from backend.processing.live_diarization.files import (
    BANK_FILENAME,
    DISABLED_FILENAME,
    STATE_FILENAME,
)
from backend.processing.live_diarization.hybrid import HybridLiveState
from backend.processing.live_diarization.hybrid_state import load_hybrid, save_hybrid
from backend.processing.live_diarization.nemotron import NemotronStream
from backend.tests.live_label_fakes import SAMPLE_RATE, SpeakerTrackModel, speaker_audio
from backend.tests.nemotron_fakes import ObservedRuntime
from backend.tests.test_live_speaker_labels_db import _apply, _database, _patch_step
from backend.utils.canonical_pipeline import (
    serialize_canonical_utterances,
    update_utterance_speaker,
)
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision


def run_at(live_dir: Path, start_s: float = 0.0) -> step.LiveLabelRun:
    return step.LiveLabelRun(
        recording_id=1,
        sequence=2,
        live_dir=live_dir,
        audio=speaker_audio([(0, 4, 2)], 4),
        audio_start_s=start_s,
        next_text_start_s=start_s + 4,
    )


def test_lost_checkpoint_without_bank_preserves_published_native_labels_and_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database()
    model = SpeakerTrackModel()
    _patch_step(monkeypatch, database, model)
    _apply(
        database,
        {1000: LiveLabelDecision("LIVE_PROVISIONAL_2", locked=True, share=0.9)},
    )
    with Session(database) as session:
        update_utterance_speaker(
            session,
            recording_id=1,
            utterance_public_id="line-b",
            new_speaker_name="Ada",
        )
        session.commit()
        before = serialize_canonical_utterances(session, 1)
    assert not (tmp_path / BANK_FILENAME).exists()
    assert not (tmp_path / STATE_FILENAME).exists()
    step.run_live_speaker_labels(run_at(tmp_path))
    with Session(database) as session:
        assert serialize_canonical_utterances(session, 1) == before
    assert (tmp_path / DISABLED_FILENAME).exists()
    assert not (tmp_path / STATE_FILENAME).exists()
    assert model.calls == 0


def test_orphan_bank_disables_before_database_or_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = tmp_path / BANK_FILENAME
    original = np.arange(256, dtype=np.float32).tobytes()
    bank.write_bytes(original)

    def forbidden_history(recording_id: int) -> bool:
        pytest.fail("An orphan bank already proves the identity state was lost")

    monkeypatch.setattr(step, "has_label_history", forbidden_history)
    assert step._load_or_disable(run_at(tmp_path), "cpu") is None
    assert bank.read_bytes() == original
    assert (tmp_path / DISABLED_FILENAME).exists()


def test_empty_saved_capture_without_bank_resumes_without_history_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = LiveLabelConfig()
    engine = HybridLiveState(
        state.new_engine(tmp_path, config), None, speaker_engine="classic"
    )
    engine.recording_id = 1
    save_hybrid(tmp_path, engine)
    assert not (tmp_path / BANK_FILENAME).exists()

    def forbidden_history(recording_id: int) -> bool:
        pytest.fail("A valid empty snapshot needs no published-identity query")

    monkeypatch.setattr(step, "has_label_history", forbidden_history)
    restored = step._load_or_disable(run_at(tmp_path), "cpu")
    assert (
        restored is not None
        and restored.capture_generation == engine.capture_generation
    )
    assert restored.speaker_engine == "classic"
    assert not (tmp_path / DISABLED_FILENAME).exists()


def test_first_capture_without_checkpoint_bank_or_label_events_can_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(step, "has_label_history", lambda recording_id: False)
    engine = step._load_or_disable(run_at(tmp_path), "cpu")
    assert engine is not None and engine.consumed_n == 0
    assert engine.reason == "cpu"
    assert not (tmp_path / DISABLED_FILENAME).exists()


def test_gap_flush_allocates_the_first_shadow_identity_before_save(
    tmp_path: Path,
) -> None:
    config = LiveLabelConfig()
    engine = HybridLiveState(
        state.new_engine(tmp_path, config),
        NemotronStream(ObservedRuntime()),
        fingerprints=("assets", "runtime"),
    )
    engine.recording_id = 1
    model = SpeakerTrackModel()
    engine.feed(speaker_audio([(0, 1.5, 1)], 1.5), 0, model)
    assert engine.shadow.units.count == 0 and not engine.translation
    # No complete packet follows the gap: only the partial flush can found a unit.
    engine.feed(np.zeros(100, np.float32), 2 * SAMPLE_RATE, model)
    assert engine.shadow.units.count > 0
    labels = set(engine.units.label) - {engine.shadow.held_slot}
    assert labels and labels.issubset(engine.translation)
    assert all(engine.translation[label] >= 8 for label in labels)
    save_hybrid(tmp_path, engine)
    restored = load_hybrid(tmp_path, config, recording_id=1)
    assert restored is not None and restored.reason == "audio_gap"
    assert restored.translation == engine.translation


def test_runtime_mismatch_drops_local_reference_before_cuda_cache_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MismatchRuntime(ObservedRuntime):
        asset_fingerprint = "different-assets"
        runtime_fingerprint = "different-runtime"

    engine = HybridLiveState(
        state.new_engine(tmp_path, LiveLabelConfig()),
        NemotronStream(ObservedRuntime()),
        fingerprints=("assets", "runtime"),
    )
    references: list[weakref.ReferenceType[Any]] = []
    emptied: list[bool] = []
    monkeypatch.setattr(nemotron_model, "_RUNTIMES", {})

    def load_runtime(*, device: str) -> MismatchRuntime:
        runtime = MismatchRuntime()
        references.append(weakref.ref(runtime))
        nemotron_model._RUNTIMES[device] = runtime
        return runtime

    def empty_cache() -> None:
        assert references and references[0]() is None
        emptied.append(True)

    monkeypatch.setattr(nemotron_model, "load_nemotron_model", load_runtime)
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True, empty_cache=empty_cache)
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    hybrid_runtime.bind_capture(engine, device="cuda")
    assert emptied == [True]
    assert engine.stream is None and engine.reason == "state_incompatible"
