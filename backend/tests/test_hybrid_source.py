"""The independent label cursor records the last drained source, including backlog."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from backend.processing.live_diarization.hybrid import HybridLiveState
from backend.processing.live_diarization.hybrid_source import update_source_cursor
from backend.processing.live_diarization.state import new_engine
from backend.processing.live_diarization.step import CONFIG, LiveLabelRun
from backend.tests.test_live_speaker_labels_lane import _lane
from backend.tests.test_live_transcription import _make_segment_wav, _run_live_task


@pytest.mark.parametrize(
    ("frames", "rate", "normalised"), [(24000, 8000, 48000), (48001, 48000, 16001)]
)
def test_source_offset_is_normalised_samples_of_the_last_sequence(
    tmp_path: Path, frames: int, rate: int, normalised: int
) -> None:
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    with wave.open(str(tmp_path / "7.wav"), "wb") as source:
        source.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        source.writeframes(np.zeros(frames, np.int16).tobytes())
    engine = HybridLiveState(new_engine(live_dir, CONFIG), None)
    run = LiveLabelRun(1, 7, live_dir, np.zeros(48000, np.float32), 20, 22)
    update_source_cursor(engine, run)
    assert engine.source_sequence == 7
    assert engine.source_sample_offset == normalised
    old = LiveLabelRun(1, 6, live_dir, np.zeros(48000, np.float32), 18, 20)
    update_source_cursor(engine, old)
    assert engine.source_sequence == 7
    assert engine.source_sample_offset == normalised


def test_asr_backlog_reports_last_drained_source_to_labels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.processing.live_diarization import step

    temp_dir, audio_store, _, session = _lane(monkeypatch, tmp_path)
    runs = []
    monkeypatch.setattr(step, "run_live_speaker_labels", runs.append)
    for sequence in (0, 1, 2):
        _make_segment_wav(temp_dir, sequence, 2, audio_store)
    _run_live_task(monkeypatch, 42, 0, session)
    assert len(runs) == 1
    assert runs[0].sequence == 2
    assert int(runs[0].audio.numel()) == 96000
