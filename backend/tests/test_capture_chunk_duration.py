"""A capture chunk's stored duration is the length of the samples it holds."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from backend.utils.recording_audio_sync import build_recording_audio_chunk_fields


def _silent_wav(path: Path, frames: int, sample_rate_hz: int) -> Path:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(b"\x00\x00" * frames)
    return path


@pytest.mark.parametrize(
    ("frames", "sample_rate_hz", "duration_ms"),
    [
        # Each is a whole number of milliseconds, which dividing and then
        # multiplying in floats and truncating stored one short.
        (16016, 16000, 1001),
        (48048, 48000, 1001),
        (88641, 44100, 2010),
        # Nearest millisecond otherwise: 1000.9375 ms.
        (16015, 16000, 1001),
    ],
)
def test_a_chunk_stores_the_millisecond_its_samples_last(
    tmp_path: Path, frames: int, sample_rate_hz: int, duration_ms: int
) -> None:
    storage = _silent_wav(tmp_path / "1.wav", frames, sample_rate_hz)

    fields = build_recording_audio_chunk_fields(
        sequence=1, source_kind="browser", storage_path=storage
    )

    assert fields["duration_ms"] == duration_ms
    assert fields["absolute_end_ms"] == duration_ms
    assert fields["sample_rate_hz"] == sample_rate_hz
