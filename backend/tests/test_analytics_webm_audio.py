"""Delivery and overlap analytics on browser-captured recordings.

A browser capture is finalised as the WebM/Opus container MediaRecorder
produced, which libsndfile cannot open, so both analytics tiers failed on
every browser recording with "Format not recognised". They now read the audio
through soundfile_readable_audio, which decodes such a file with ffmpeg.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.core.exceptions import AudioFormatError
from backend.processing import audio_preprocessing, segmentation_refinement
from backend.processing.audio_overlap import measure_audio_overlap
from backend.processing.audio_preprocessing import soundfile_readable_audio
from backend.processing.delivery_descriptors import (
    MIN_UTTERANCES_PER_SPEAKER,
    analyse_delivery,
)
from backend.tests.test_delivery_descriptors import (
    SAMPLE_RATE,
    utterances_for,
    voiced_tone,
    write_wav,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is not installed"
)


def _two_source_capture(path: Path) -> tuple[list, list]:
    """A browser-layout recording: shared audio on channel 0, microphone on 1."""
    duration_s = 60.0
    system = np.zeros(int(SAMPLE_RATE * duration_s), dtype=np.float32)
    microphone = np.zeros_like(system)
    local = utterances_for("rs:local", MIN_UTTERANCES_PER_SPEAKER)
    remote = utterances_for("rs:remote", MIN_UTTERANCES_PER_SPEAKER, start_ms=30_000)
    for utterance in local:
        start = int(utterance.start_ms * SAMPLE_RATE / 1000)
        tone = voiced_tone(190.0, 2.0, amplitude=0.4)
        microphone[start : start + tone.size] = tone
    for utterance in remote:
        start = int(utterance.start_ms * SAMPLE_RATE / 1000)
        tone = voiced_tone(110.0, 2.0, amplitude=0.4)
        system[start : start + tone.size] = tone
    write_wav(path, [system, microphone])
    return local, remote


def _encode_webm(source: Path, target: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source)]
        + ["-c:a", "libopus", "-b:a", "128k", str(target)],
        check=True,
    )


def _analysis_temp_files() -> set[str]:
    return {
        name
        for name in os.listdir(tempfile.gettempdir())
        if name.endswith("_analysis.wav")
    }


@needs_ffmpeg
def test_delivery_is_measured_on_a_browser_webm_capture(tmp_path):
    wav = tmp_path / "capture.wav"
    local, remote = _two_source_capture(wav)
    webm = tmp_path / "capture.webm"
    _encode_webm(wav, webm)
    temp_before = _analysis_temp_files()

    result = analyse_delivery(str(webm), local + remote, browser_capture=True)

    # Channels survive the decode, so each speaker keeps its capture source.
    local_speaker = result["speakers"]["rs:local"]
    remote_speaker = result["speakers"]["rs:remote"]
    assert local_speaker["capture_sources"] == ["microphone"]
    assert remote_speaker["capture_sources"] == ["system"]
    assert abs(local_speaker["median_f0_hz"] - 190) / 190 < 0.05
    assert abs(remote_speaker["median_f0_hz"] - 110) / 110 < 0.05
    # The decoded copy does not outlive the measurement.
    assert _analysis_temp_files() == temp_before


@needs_ffmpeg
def test_overlap_is_measured_on_a_browser_webm_capture(tmp_path, monkeypatch):
    """The segmentation model is stubbed; what is under test is reading the file."""
    wav = tmp_path / "capture.wav"
    _two_source_capture(wav)
    webm = tmp_path / "capture.webm"
    _encode_webm(wav, webm)
    seen: list[str] = []

    class FakeInference:
        def __init__(self, model, step):
            pass

        def __call__(self, path):
            sf.info(path)
            seen.append(path)
            # Two chunks of 10 frames, three local speakers, no overlap.
            return types.SimpleNamespace(data=np.zeros((2, 10, 3)))

    pyannote_stub = types.ModuleType("pyannote")
    audio_stub = types.ModuleType("pyannote.audio")
    audio_stub.Inference = FakeInference
    monkeypatch.setitem(sys.modules, "pyannote", pyannote_stub)
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio_stub)
    monkeypatch.setattr(
        segmentation_refinement, "load_segmentation_model", lambda device, token: None
    )

    block = measure_audio_overlap(str(webm), hf_token=None)

    assert block["duration_ms"] == pytest.approx(60_000, abs=100)
    assert block["region_count"] == 0
    assert len(seen) == 1 and seen[0] != str(webm)


@needs_ffmpeg
def test_a_webm_is_decoded_with_its_rate_and_channels(tmp_path):
    wav = tmp_path / "capture.wav"
    _two_source_capture(wav)
    webm = tmp_path / "capture.webm"
    _encode_webm(wav, webm)

    with soundfile_readable_audio(str(webm)) as readable:
        info = sf.info(readable)
        decoded = readable

    assert decoded != str(webm)
    assert info.channels == 2
    # Opus always decodes at 48 kHz; the point is that nothing downsamples it.
    assert info.samplerate == 48_000
    assert info.duration == pytest.approx(60.0, abs=0.1)
    assert not os.path.exists(decoded)


@needs_ffmpeg
def test_audio_ffmpeg_cannot_decode_raises_and_leaves_no_temp_file(tmp_path):
    broken = tmp_path / "broken.webm"
    broken.write_bytes(b"not a media file")
    temp_before = _analysis_temp_files()

    with pytest.raises(AudioFormatError):
        with soundfile_readable_audio(str(broken)):
            pass

    assert _analysis_temp_files() == temp_before


def test_a_file_soundfile_reads_is_used_in_place(tmp_path, monkeypatch):
    wav = tmp_path / "meeting.wav"
    write_wav(wav, [voiced_tone(150.0, 2.0)])

    def no_decode(*args, **kwargs):
        raise AssertionError("a WAV must not be decoded again")

    monkeypatch.setattr(audio_preprocessing, "convert_to_wav", no_decode)

    with soundfile_readable_audio(str(wav)) as readable:
        assert readable == str(wav)


def test_delivery_reads_an_unreadable_container_through_the_decoder(
    tmp_path, monkeypatch
):
    """Runs without ffmpeg: the decoder is replaced by one that writes the WAV."""
    wav = tmp_path / "capture.wav"
    local, remote = _two_source_capture(wav)
    container = tmp_path / "capture.webm"
    container.write_bytes(b"\x1aE\xdf\xa3 not something libsndfile reads")

    def decode(source: str, target: str) -> bool:
        assert source == str(container)
        shutil.copyfile(wav, target)
        return True

    monkeypatch.setattr(audio_preprocessing, "convert_to_wav", decode)

    result = analyse_delivery(str(container), local + remote, browser_capture=True)

    assert result["speakers"]["rs:local"]["capture_sources"] == ["microphone"]
    assert result["speakers"]["rs:remote"]["capture_sources"] == ["system"]
