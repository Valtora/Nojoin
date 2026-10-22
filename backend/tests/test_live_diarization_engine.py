"""The live label engine on a scripted meeting (``live_label_fakes``)."""

from __future__ import annotations

import numpy as np
import pytest

from backend.processing.live_diarization import state as state_module
from backend.processing.live_diarization.clustering import StepAssignment
from backend.processing.live_diarization.engine import (
    LiveLabelConfig,
    LiveLabelEngine,
    Piece,
    _Window,
)
from backend.processing.live_diarization.lines import LabelTimeline
from backend.processing.live_diarization.recluster import UnitBank
from backend.processing.live_diarization.state import (
    LiveLabelStateError,
    load_engine,
    new_engine,
    save_engine,
)
from backend.tests.live_label_fakes import SAMPLE_RATE, SpeakerTrackModel, speaker_audio

CONFIG = LiveLabelConfig()
PACKET = int(2.0 * SAMPLE_RATE)


def _feed_all(engine: LiveLabelEngine, audio: np.ndarray, model, start_n: int = 0):
    return engine.feed(audio, start_n, model)


def _feed_in_runs(
    live_dir, audio: np.ndarray, model, start_n: int = 0
) -> LiveLabelEngine:
    """2 s at a time, saving and reloading the engine between runs like the lane."""
    for offset in range(0, len(audio), PACKET):
        engine = load_engine(live_dir, CONFIG) or new_engine(live_dir, CONFIG)
        engine.feed(audio[offset : offset + PACKET], start_n + offset, model)
        save_engine(live_dir, engine)
    engine = load_engine(live_dir, CONFIG)
    assert engine is not None
    return engine


def _labels_between(engine: LiveLabelEngine, start: float, end: float) -> set[int]:
    timeline = LabelTimeline.from_engine(engine)
    inside = (timeline.start >= start) & (timeline.end <= end)
    return set(timeline.label[inside].tolist())


def test_a_returning_speaker_gets_the_label_they_had(tmp_path):
    audio = speaker_audio([(0, 20, 1), (20, 40, 2), (60, 80, 1)], 82)
    engine = _feed_in_runs(tmp_path, audio, SpeakerTrackModel())

    first = _labels_between(engine, 1, 19)
    other = _labels_between(engine, 21, 39)
    back = _labels_between(engine, 61, 79)
    assert len(first) == 1 and len(other) == 1
    assert first != other
    assert back == first


def test_a_voice_too_short_to_found_a_speaker_is_held_not_forced(tmp_path):
    # Speakers 1 and 2 both have centroids. Speaker 3 says 0.8 s between two
    # stretches of speaker 1: diart would force it onto speaker 2's free
    # centroid. Here it shows no label.
    audio = speaker_audio(
        [(0, 10, 1), (10, 20, 2), (20, 25, 1), (25, 25.8, 3), (25.8, 26.0, 1)], 26
    )
    engine = LiveLabelEngine(CONFIG, UnitBank(tmp_path / "bank", CONFIG.embedding_dim))
    _feed_all(engine, audio, SpeakerTrackModel())

    timeline = LabelTimeline.from_engine(engine)
    assert _labels_between(engine, 25.0, 25.8) == {-1}
    assert timeline.label_line(25.0, 25.8).speaker == "UNKNOWN"
    assert (
        timeline.label_line(10.5, 19.5).speaker
        != timeline.label_line(20.5, 24.5).speaker
    )


def test_a_held_voice_is_back_filled_once_it_founds_a_speaker(tmp_path):
    # Speaker 4 starts at 30 s. Its first 3 s are too short a share of the 5 s
    # window to found a centroid and are held; once it does, the held pieces
    # still in the window take its label.
    audio = speaker_audio([(0, 30, 1), (30, 45, 4)], 45)
    engine = LiveLabelEngine(CONFIG, UnitBank(tmp_path / "bank", CONFIG.embedding_dim))
    _feed_all(engine, audio, SpeakerTrackModel())

    later = _labels_between(engine, 36, 44)
    assert len(later) == 1 and -1 not in later
    assert _labels_between(engine, 30.0, 32.5) == later
    assert later != _labels_between(engine, 1, 29)


def test_labels_lock_after_two_unchanged_reclusters(tmp_path):
    audio = speaker_audio([(0, 50, 1)], 50)
    model = SpeakerTrackModel()
    engine = LiveLabelEngine(CONFIG, UnitBank(tmp_path / "bank", CONFIG.embedding_dim))

    _feed_all(engine, audio[: 15 * SAMPLE_RATE], model)
    one_pass = LabelTimeline.from_engine(engine).label_line(1.0, 4.0)
    _feed_all(engine, audio[15 * SAMPLE_RATE :], model, start_n=15 * SAMPLE_RATE)
    two_passes = LabelTimeline.from_engine(engine).label_line(1.0, 4.0)

    assert one_pass.speaker == two_passes.speaker == "LIVE_PROVISIONAL_1"
    assert one_pass.locked is False
    assert two_passes.locked is True


def test_saving_and_reloading_every_run_changes_nothing(tmp_path):
    audio = speaker_audio(
        [(0, 12, 1), (12, 13, 3), (13, 30, 2), (30, 31, 5), (31, 48, 1), (50, 64, 2)],
        66,
    )
    whole = LiveLabelEngine(CONFIG, UnitBank(tmp_path / "bank", CONFIG.embedding_dim))
    _feed_all(whole, audio, SpeakerTrackModel())
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    in_runs = _feed_in_runs(runs_dir, audio, SpeakerTrackModel())

    assert [(p.start, p.end, p.unit, p.open) for p in in_runs.pieces] == [
        (p.start, p.end, p.unit, p.open) for p in whole.pieces
    ]
    assert in_runs.units.label == whole.units.label
    assert in_runs.units.locked == whole.units.locked
    assert np.array_equal(in_runs.clustering.centers, whole.clustering.centers)


def test_a_long_gap_skips_the_model_and_keeps_the_speakers(tmp_path):
    model = SpeakerTrackModel()
    engine = LiveLabelEngine(CONFIG, UnitBank(tmp_path / "bank", CONFIG.embedding_dim))
    _feed_all(engine, speaker_audio([(0, 20, 1)], 20), model)
    windows_before = model.windows

    # The lane resumed 10 minutes later: only the new audio is scored.
    _feed_all(engine, speaker_audio([(0, 10, 1)], 10), model, start_n=620 * SAMPLE_RATE)

    assert model.windows - windows_before == 20
    assert _labels_between(engine, 621, 629) == _labels_between(engine, 1, 19)


def test_audio_already_seen_is_not_scored_twice(tmp_path):
    model = SpeakerTrackModel()
    engine = LiveLabelEngine(CONFIG, UnitBank(tmp_path / "bank", CONFIG.embedding_dim))
    audio = speaker_audio([(0, 6, 1)], 6)
    _feed_all(engine, audio[: 4 * SAMPLE_RATE], model)
    # The next run's clip starts with the carried buffer, 1 s before the end.
    _feed_all(engine, audio[3 * SAMPLE_RATE :], model, start_n=3 * SAMPLE_RATE)

    assert model.windows == 12
    assert engine.audio_end_n == 6 * SAMPLE_RATE


def test_an_engine_saved_after_every_run_matches_one_that_never_stopped(tmp_path):
    # The state keeps only founded centroids and open units' sums; whatever it
    # leaves out must be zero, or the saved engine would drift from this one.
    audio = speaker_audio([(0, 9, 1), (9, 14, 2), (14, 15, 3), (15, 30, 1)], 31)
    whole = LiveLabelEngine(CONFIG, UnitBank(tmp_path / "bank", CONFIG.embedding_dim))
    _feed_all(whole, audio, SpeakerTrackModel())
    (tmp_path / "runs").mkdir()
    runs = _feed_in_runs(tmp_path / "runs", audio, SpeakerTrackModel())

    assert runs.pieces == whole.pieces
    assert runs.units.label == whole.units.label
    assert runs.units.locked == whole.units.locked
    assert np.array_equal(runs.clustering.active, whole.clustering.active)
    assert np.array_equal(runs.clustering.centers, whole.clustering.centers)
    assert np.array_equal(runs.units.open_sum_all, whole.units.open_sum_all)
    assert np.array_equal(runs.bank.read(), whole.bank.read())


def test_the_state_reaches_the_disk_before_it_replaces_the_old_one(
    tmp_path, monkeypatch
):
    engine = new_engine(tmp_path, CONFIG)
    _feed_all(engine, speaker_audio([(0, 4, 1)], 4), SpeakerTrackModel())
    calls = []
    real_fsync, real_replace = state_module.os.fsync, state_module.os.replace
    monkeypatch.setattr(
        state_module.os, "fsync", lambda fd: calls.append("fsync") or real_fsync(fd)
    )
    monkeypatch.setattr(
        state_module.os,
        "replace",
        lambda src, dst: calls.append("replace") or real_replace(src, dst),
    )

    save_engine(tmp_path, engine)

    # A power loss after the rename must not leave an empty state behind it.
    assert calls == ["fsync", "replace"]


def test_a_bank_row_written_after_the_last_save_is_cut_on_load(tmp_path):
    engine = new_engine(tmp_path, CONFIG)
    _feed_all(engine, speaker_audio([(0, 12, 1)], 12), SpeakerTrackModel())
    save_engine(tmp_path, engine)
    rows = engine.bank.rows
    engine.bank.append(np.ones(CONFIG.embedding_dim, np.float32))

    reloaded = load_engine(tmp_path, CONFIG)

    assert reloaded is not None
    assert reloaded.bank.rows == rows
    # The next unit's row lands where the state expects it.
    marker = np.full(CONFIG.embedding_dim, 7.0, np.float32)
    assert reloaded.bank.append(marker) == rows
    assert np.array_equal(reloaded.bank.read()[rows], marker)


def test_a_state_from_another_configuration_is_refused(tmp_path):
    engine = new_engine(tmp_path, CONFIG)
    _feed_all(engine, speaker_audio([(0, 4, 1)], 4), SpeakerTrackModel())
    save_engine(tmp_path, engine)

    with pytest.raises(LiveLabelStateError):
        load_engine(tmp_path, LiveLabelConfig(backfill_cover=0.6))
    (tmp_path / "speaker_labels.npz").write_bytes(b"not a state")
    with pytest.raises(LiveLabelStateError):
        load_engine(tmp_path, CONFIG)


@pytest.mark.parametrize(("covered", "filled"), [(0.4, False), (0.6, True)])
def test_a_held_piece_is_back_filled_only_when_mostly_covered(
    tmp_path, covered, filled
):
    engine = LiveLabelEngine(CONFIG, UnitBank(tmp_path / "bank", CONFIG.embedding_dim))
    engine.pieces = [Piece(start=7.0, end=8.0, unit=0, open=True)]
    frames = 293
    middles = 5.0 + (np.arange(frames) + 0.5) * CONFIG.window_s / frames
    in_piece = np.where((middles >= 7.0) & (middles < 8.0))[0]
    segmentation = np.zeros((frames, 3), np.float32)
    segmentation[in_piece[: int(round(covered * len(in_piece)))], 0] = 1.0
    window = _Window(
        end_n=10 * SAMPLE_RATE,
        step=19,
        segmentation=segmentation,
        embeddings=np.zeros((3, CONFIG.embedding_dim), np.float32),
    )

    count = engine._backfill(window, StepAssignment(pairs=[(0, 4)]), {4: 9}, 1)

    assert count == int(filled)
    assert engine.pieces[0].unit == (9 if filled else 0)
    assert engine.pieces[0].open is not filled
