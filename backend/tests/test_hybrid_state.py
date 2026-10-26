"""Atomic hybrid restart and identity-preserving failure boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backend.processing.live_diarization import native_state, state
from backend.processing.live_diarization.engine import LiveLabelConfig
from backend.processing.live_diarization.files import BANK_FILENAME, STATE_TMP_FILENAME
from backend.processing.live_diarization.hybrid import HybridLiveState
from backend.processing.live_diarization.hybrid_state import load_hybrid, save_hybrid
from backend.processing.live_diarization.nemotron import NemotronStream
from backend.tests.live_label_fakes import SpeakerTrackModel, speaker_audio
from backend.tests.nemotron_fakes import ObservedRuntime, cache_with_token

CONFIG = LiveLabelConfig()


def make_engine(live_dir: Path, runtime: ObservedRuntime | None) -> HybridLiveState:
    engine = HybridLiveState(
        state.new_engine(live_dir, CONFIG),
        NemotronStream(runtime) if runtime is not None else None,
        speaker_engine="auto" if runtime is not None else "classic",
        fingerprints=("assets", "runtime") if runtime is not None else ("", ""),
    )
    engine.recording_id = 7
    return engine


@pytest.fixture
def numpy_decode(monkeypatch: pytest.MonkeyPatch) -> None:
    original = native_state.restore_stream

    def restore(meta: dict[str, Any], arrays: dict[str, np.ndarray]) -> NemotronStream:
        return original(
            meta, arrays, cache_factory=lambda geometry: cache_with_token(0)
        )

    monkeypatch.setattr(native_state, "restore_stream", restore)


def test_partial_packet_resume_repeats_the_same_production_calls(
    tmp_path: Path,
    numpy_decode: None,
) -> None:
    audio = speaker_audio([(0, 8, 1), (8, 14, 2)], 14)
    whole_dir = tmp_path / "whole"
    whole_dir.mkdir()
    whole_runtime = ObservedRuntime()
    whole = make_engine(whole_dir, whole_runtime)
    whole.feed(audio, 0, SpeakerTrackModel())
    runtime = ObservedRuntime()
    engine = make_engine(tmp_path, runtime)
    cut = 7 * 16000 + 179
    engine.feed(audio[:cut], 0, SpeakerTrackModel())
    engine.source_sequence = 3
    engine.source_sample_offset = 179
    save_hybrid(tmp_path, engine)
    restored = load_hybrid(tmp_path, CONFIG, recording_id=7)
    assert restored is not None and restored.stream is not None
    restored.stream.bind_runtime(runtime)
    restored.feed(audio[cut - 1000 :], cut - 1000, SpeakerTrackModel())
    assert runtime.calls == whole_runtime.calls
    assert restored.shadow.pieces == whole.shadow.pieces
    assert restored.source_sample_offset == 179
    assert restored.source_sequence == 3
    np.testing.assert_array_equal(restored.clustering.active, whole.clustering.active)
    assert restored.capture_generation == engine.capture_generation
    for start, end in [(1, 2), (7, 10), (10, 12)]:
        assert restored.label_line(start, end) == whole.label_line(start, end)


def test_invalid_translation_does_not_truncate_uncommitted_bank_rows(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path, None)
    engine.feed(speaker_audio([(0, 12, 1)], 12), 0, SpeakerTrackModel())
    save_hybrid(tmp_path, engine)
    engine.bank.append(np.ones(CONFIG.embedding_dim, np.float32))
    bank = tmp_path / BANK_FILENAME
    before = bank.read_bytes()
    snapshot = state.read_snapshot(tmp_path, CONFIG)
    assert snapshot is not None
    meta, arrays = snapshot
    meta["hybrid"]["translation"] = [[0, 1], [0, 2]]
    state.write_snapshot(tmp_path, meta, arrays)
    with pytest.raises(state.LiveLabelStateError, match="translation"):
        load_hybrid(tmp_path, CONFIG, recording_id=7)
    assert bank.read_bytes() == before


@pytest.mark.parametrize("damage", ["cache", "fingerprint"])
def test_native_only_damage_permanently_preserves_translated_shadow(
    tmp_path: Path,
    numpy_decode: None,
    damage: str,
) -> None:
    engine = make_engine(tmp_path, ObservedRuntime())
    engine.feed(speaker_audio([(0, 8, 1)], 8), 0, SpeakerTrackModel())
    save_hybrid(tmp_path, engine)
    snapshot = state.read_snapshot(tmp_path, CONFIG)
    assert snapshot is not None
    meta, arrays = snapshot
    if damage == "cache":
        arrays["native_embeds"] = arrays["native_embeds"][:, :-1]
    else:
        meta["hybrid"]["runtime_fingerprint"] = ""
    state.write_snapshot(tmp_path, meta, arrays)
    restored = load_hybrid(tmp_path, CONFIG, recording_id=7)
    assert restored is not None
    assert restored.stream is None and restored.route == "clustering"
    assert restored.reason == "state_incompatible"
    translation = restored.translation.copy()
    assert translation
    restored.feed(speaker_audio([(0, 2, 1)], 2), 8 * 16000, SpeakerTrackModel())
    save_hybrid(tmp_path, restored)
    again = load_hybrid(tmp_path, CONFIG, recording_id=7)
    assert again is not None and again.route == "clustering"
    assert again.translation == translation
    assert again.native_complete_frame == engine.native_complete_frame


def test_wrong_recording_is_rejected_before_bank_mutation(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, None)
    engine.feed(speaker_audio([(0, 12, 1)], 12), 0, SpeakerTrackModel())
    save_hybrid(tmp_path, engine)
    engine.bank.append(np.ones(CONFIG.embedding_dim, np.float32))
    size = (tmp_path / BANK_FILENAME).stat().st_size
    with pytest.raises(state.LiveLabelStateError, match="recording identity"):
        load_hybrid(tmp_path, CONFIG, recording_id=8)
    assert (tmp_path / BANK_FILENAME).stat().st_size == size


def test_invalid_shadow_index_dtype_does_not_truncate_the_bank(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, None)
    engine.feed(speaker_audio([(0, 12, 1)], 12), 0, SpeakerTrackModel())
    save_hybrid(tmp_path, engine)
    engine.bank.append(np.ones(CONFIG.embedding_dim, np.float32))
    bank = tmp_path / BANK_FILENAME
    before = bank.read_bytes()
    snapshot = state.read_snapshot(tmp_path, CONFIG)
    assert snapshot is not None
    meta, arrays = snapshot
    arrays["open_slots"] = arrays["open_slots"].astype(np.float64)
    state.write_snapshot(tmp_path, meta, arrays)
    with pytest.raises(state.LiveLabelStateError, match="open_slots dtype"):
        load_hybrid(tmp_path, CONFIG, recording_id=7)
    assert bank.read_bytes() == before


def test_legacy_snapshot_resumes_classic_namespace_without_native(
    tmp_path: Path,
) -> None:
    shadow = state.new_engine(tmp_path, CONFIG)
    shadow.feed(speaker_audio([(0, 12, 1)], 12), 0, SpeakerTrackModel())
    state.save_engine(tmp_path, shadow)
    restored = load_hybrid(tmp_path, CONFIG, recording_id=7)
    assert restored is not None and restored.stream is None
    assert restored.speaker_engine == "classic"
    assert restored.translation == {label: label for label in set(shadow.units.label)}
    decision = restored.label_line(1, 2)
    restored.feed(speaker_audio([(0, 2, 1)], 2), 12 * 16000, SpeakerTrackModel())
    save_hybrid(tmp_path, restored)
    again = load_hybrid(tmp_path, CONFIG, recording_id=7)
    assert again is not None and again.label_line(1, 2) == decision
    assert again.capture_generation == restored.capture_generation


def test_failed_atomic_replace_leaves_previous_decisions_replayable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = make_engine(tmp_path, None)
    engine.feed(speaker_audio([(0, 4, 1)], 4), 0, SpeakerTrackModel())
    save_hybrid(tmp_path, engine)
    engine.feed(speaker_audio([(0, 2, 2)], 2), 4 * 16000, SpeakerTrackModel())

    def failed_replace(source: Path, destination: Path) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(state.os, "replace", failed_replace)
    with pytest.raises(OSError, match="rename failure"):
        save_hybrid(tmp_path, engine)
    restored = load_hybrid(tmp_path, CONFIG, recording_id=7)
    assert restored is not None and restored.consumed_n == 4 * 16000
    assert (tmp_path / STATE_TMP_FILENAME).exists()


def test_hybrid_uses_existing_fsync_commit_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = make_engine(tmp_path, None)
    engine.feed(speaker_audio([(0, 12, 1)], 12), 0, SpeakerTrackModel())
    paths = {
        "bank": tmp_path / BANK_FILENAME,
        "snapshot": tmp_path / STATE_TMP_FILENAME,
        "directory": tmp_path,
    }
    calls: list[str] = []
    original_fsync, original_replace = state.os.fsync, state.os.replace

    def fsync(fd: int) -> None:
        inode = state.os.fstat(fd).st_ino
        calls.extend(
            name
            for name, path in paths.items()
            if path.exists() and path.stat().st_ino == inode
        )
        original_fsync(fd)

    def replace(source: Path, destination: Path) -> None:
        calls.append("replace")
        original_replace(source, destination)

    monkeypatch.setattr(state.os, "fsync", fsync)
    monkeypatch.setattr(state.os, "replace", replace)
    save_hybrid(tmp_path, engine)
    assert calls == ["bank", "snapshot", "replace", "directory"]


def test_gap_with_partial_packet_remains_restartable(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, ObservedRuntime())
    engine.feed(speaker_audio([(0, 3, 1)], 3), 0, SpeakerTrackModel())
    engine.feed(np.zeros(1300, np.float32), 60001, SpeakerTrackModel())
    save_hybrid(tmp_path, engine)
    restored = load_hybrid(tmp_path, CONFIG, recording_id=7)
    assert restored is not None and restored.reason == "audio_gap"
    assert restored.consumed_n == 61301
    assert restored.packet_cursor_n == 60001
    np.testing.assert_array_equal(restored.pending, np.zeros(1300, np.float32))


def test_changed_persisted_source_tail_is_rejected_before_labels_restart(
    tmp_path: Path,
) -> None:
    engine = make_engine(tmp_path, ObservedRuntime())
    engine.feed(speaker_audio([(0, 3, 1)], 3), 0, SpeakerTrackModel())
    save_hybrid(tmp_path, engine)
    snapshot = state.read_snapshot(tmp_path, CONFIG)
    assert snapshot is not None
    meta, arrays = snapshot
    arrays["hybrid_pending"][0] += 0.1
    state.write_snapshot(tmp_path, meta, arrays)
    with pytest.raises(state.LiveLabelStateError, match="source tail digest"):
        load_hybrid(tmp_path, CONFIG, recording_id=7)
