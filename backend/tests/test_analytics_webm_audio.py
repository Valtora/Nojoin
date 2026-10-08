"""Delivery and overlap analytics on browser-captured recordings.

A browser capture is finalised as the WebM/Opus container MediaRecorder
produced, which libsndfile cannot open, so both analytics tiers failed on
every browser recording with "Format not recognised". They now read the audio
through soundfile_readable_audio, which decodes such a file with ffmpeg to a
16 kHz WAV: keeping the channels for delivery, downmixed for overlap.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any

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


@pytest.fixture
def scratch(tmp_path, monkeypatch) -> Path:
    """A temp dir of the test's own for the decode to write into.

    The system temp dir is shared with every other suite running on the host,
    so a before/after listing of it is not exact; this one is.
    """
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def _analysis_temp_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*_analysis.wav"))


UNREADABLE = b"\x1aE\xdf\xa3 not something libsndfile reads"
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="fakes ffmpeg with a sh script"
)


def _fake_ffmpeg(tmp_path: Path, monkeypatch, body: str) -> None:
    """Put an ``ffmpeg`` running the given sh ``body`` first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "ffmpeg"
    fake.write_text(f"#!/bin/sh\n{body}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@needs_ffmpeg
def test_delivery_is_measured_on_a_browser_webm_capture(tmp_path, scratch):
    wav = tmp_path / "capture.wav"
    local, remote = _two_source_capture(wav)
    webm = tmp_path / "capture.webm"
    _encode_webm(wav, webm)

    result = analyse_delivery(str(webm), local + remote, browser_capture=True)

    # Channels survive the decode, so each speaker keeps its capture source.
    local_speaker = result["speakers"]["rs:local"]
    remote_speaker = result["speakers"]["rs:remote"]
    assert local_speaker["capture_sources"] == ["microphone"]
    assert remote_speaker["capture_sources"] == ["system"]
    assert abs(local_speaker["median_f0_hz"] - 190) / 190 < 0.05
    assert abs(remote_speaker["median_f0_hz"] - 110) / 110 < 0.05
    # The decoded copy does not outlive the measurement.
    assert _analysis_temp_files(scratch) == []


@pytest.fixture
def overlap_inputs(monkeypatch) -> list[Any]:
    """Stub the segmentation model; record what each inference call read."""
    seen: list[Any] = []

    class FakeInference:
        def __init__(self, model, step):
            pass

        def __call__(self, path):
            seen.append(sf.info(path))
            # Two chunks of 10 frames, three local speakers, no overlap.
            return types.SimpleNamespace(data=np.zeros((2, 10, 3)))

    audio_stub = types.ModuleType("pyannote.audio")
    audio_stub.Inference = FakeInference
    monkeypatch.setitem(sys.modules, "pyannote", types.ModuleType("pyannote"))
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio_stub)
    monkeypatch.setattr(
        segmentation_refinement, "load_segmentation_model", lambda device, token: None
    )
    return seen


@needs_ffmpeg
def test_overlap_is_measured_on_a_browser_webm_capture(
    tmp_path, scratch, overlap_inputs
):
    wav = tmp_path / "capture.wav"
    _two_source_capture(wav)
    webm = tmp_path / "capture.webm"
    _encode_webm(wav, webm)

    block = measure_audio_overlap(str(webm), hf_token=None)

    assert block["duration_ms"] == pytest.approx(60_000, abs=100)
    assert block["region_count"] == 0
    # Decoded at the model's own rate, not as a 48 kHz two-channel copy.
    (read,) = overlap_inputs
    assert (read.channels, read.samplerate) == (1, 16_000)
    assert _analysis_temp_files(scratch) == []


def test_overlap_reads_an_unreadable_container_through_the_mono_decoder(
    tmp_path, monkeypatch, overlap_inputs
):
    """Runs without ffmpeg: the 16 kHz decoder is replaced by one that writes it."""
    container = tmp_path / "capture.webm"
    container.write_bytes(UNREADABLE)
    decoded = np.zeros(16_000 * 30, dtype=np.float32)

    def decode(source: str, target: str, *, mono: bool, timeout: float) -> None:
        assert source == str(container)
        assert mono is True
        assert timeout > 0
        sf.write(target, decoded, 16_000)

    monkeypatch.setattr(audio_preprocessing, "convert_to_16k_wav", decode)

    block = measure_audio_overlap(str(container), hf_token=None)

    assert block["duration_ms"] == 30_000
    assert [(i.channels, i.samplerate) for i in overlap_inputs] == [(1, 16_000)]


@posix_only
@pytest.mark.parametrize("mono", [False, True])
def test_a_hung_ffmpeg_is_killed_and_reported(tmp_path, monkeypatch, scratch, mono):
    """A real child that never finishes: killed at the timeout, reaped, cleaned up."""
    pid_file = tmp_path / "ffmpeg.pid"
    _fake_ffmpeg(tmp_path, monkeypatch, f'echo $$ > "{pid_file}"\nexec sleep 30')
    monkeypatch.setattr(audio_preprocessing, "ANALYSIS_DECODE_TIMEOUT_S", 0.5)
    container = tmp_path / "capture.webm"
    container.write_bytes(UNREADABLE)

    started = time.monotonic()
    with pytest.raises(AudioFormatError):
        with soundfile_readable_audio(str(container), mono=mono):
            pass

    assert time.monotonic() - started < 10
    # The child is gone, not merely abandoned: killed and reaped.
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert _analysis_temp_files(scratch) == []


@needs_ffmpeg
def test_a_webm_is_decoded_to_16_khz_keeping_its_channels(tmp_path, scratch):
    wav = tmp_path / "capture.wav"
    _two_source_capture(wav)
    webm = tmp_path / "capture.webm"
    _encode_webm(wav, webm)

    with soundfile_readable_audio(str(webm)) as readable:
        info = sf.info(readable)
        decoded = readable

    assert decoded != str(webm)
    # Opus decodes at 48 kHz; delivery reads it at the rate it was validated at.
    assert (info.channels, info.samplerate) == (2, 16_000)
    assert info.duration == pytest.approx(60.0, abs=0.1)
    assert _analysis_temp_files(scratch) == []


@posix_only
@pytest.mark.parametrize("mono", [False, True])
def test_the_decode_writes_rf64_rather_than_a_broken_header_past_4_gib(
    tmp_path, monkeypatch, scratch, mono
):
    """A plain WAV header cannot describe more than 4 GiB. Without ``-rf64 auto``
    ffmpeg writes a broken one and exits 0, and the reader silently gets only
    the first 4 GiB. A >4 GiB fixture is too big for CI, so this reads the
    command line a fake ffmpeg was given."""
    argv_file = tmp_path / "argv"
    _fake_ffmpeg(tmp_path, monkeypatch, f'printf "%s\\n" "$@" > "{argv_file}"\nexit 1')
    container = tmp_path / "capture.webm"
    container.write_bytes(UNREADABLE)

    with pytest.raises(AudioFormatError):
        with soundfile_readable_audio(str(container), mono=mono):
            pass

    argv = argv_file.read_text().splitlines()
    pairs = set(zip(argv, argv[1:]))
    assert ("-rf64", "auto") in pairs
    assert ("-ar", "16000") in pairs
    assert (("-ac", "1") in pairs) is mono


@posix_only
@pytest.mark.parametrize("mono", [False, True])
def test_ffmpeg_stderr_that_is_not_utf8_is_still_an_audio_format_error(
    tmp_path, monkeypatch, scratch, mono
):
    _fake_ffmpeg(tmp_path, monkeypatch, "printf '\\377\\376 broken\\n' >&2\nexit 1")
    container = tmp_path / "capture.webm"
    container.write_bytes(UNREADABLE)

    with pytest.raises(AudioFormatError, match="broken"):
        with soundfile_readable_audio(str(container), mono=mono):
            pass

    assert _analysis_temp_files(scratch) == []


@pytest.mark.parametrize("mono", [False, True])
def test_a_missing_ffmpeg_is_an_audio_format_error(
    tmp_path, monkeypatch, scratch, mono
):
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    # Otherwise it finds the host's ffmpeg in a well-known location.
    monkeypatch.setattr("backend.utils.audio.ensure_ffmpeg_in_path", lambda: None)
    container = tmp_path / "capture.webm"
    container.write_bytes(UNREADABLE)

    with pytest.raises(AudioFormatError):
        with soundfile_readable_audio(str(container), mono=mono):
            pass

    assert _analysis_temp_files(scratch) == []


@needs_ffmpeg
def test_audio_ffmpeg_cannot_decode_raises_and_leaves_no_temp_file(tmp_path, scratch):
    broken = tmp_path / "broken.webm"
    broken.write_bytes(b"not a media file")

    with pytest.raises(AudioFormatError):
        with soundfile_readable_audio(str(broken)):
            pass

    assert _analysis_temp_files(scratch) == []


def _aged(path: Path, hours: float) -> Path:
    path.write_bytes(b"audio")
    stamp = time.time() - hours * 60 * 60
    os.utime(path, (stamp, stamp))
    return path


def test_a_decode_first_reclaims_analysis_wavs_a_killed_worker_stranded(
    tmp_path, monkeypatch, scratch
):
    """The daily sweep runs on the io lane and cannot see this lane's /tmp."""
    stranded = _aged(scratch / "tmpold123_analysis.wav", 7)
    in_use = _aged(scratch / "tmpnew456_analysis.wav", 1)
    other_scratch = [
        _aged(scratch / "tmpabc789_vad.wav", 48),
        _aged(scratch / "tmpdef012_preprocessed.wav", 48),
        _aged(scratch / "notes_analysis.txt", 48),
    ]
    container = tmp_path / "capture.webm"
    container.write_bytes(UNREADABLE)

    def decode(source: str, target: str, *, mono: bool, timeout: float) -> None:
        sf.write(target, np.zeros(16_000, dtype=np.float32), 16_000)

    monkeypatch.setattr(audio_preprocessing, "convert_to_16k_wav", decode)

    with soundfile_readable_audio(str(container), mono=True):
        pass

    assert not stranded.exists()
    assert in_use.exists()
    assert all(path.exists() for path in other_scratch)
    assert _analysis_temp_files(scratch) == [in_use]


def test_a_file_soundfile_reads_is_used_in_place(tmp_path, monkeypatch):
    wav = tmp_path / "meeting.wav"
    write_wav(wav, [voiced_tone(150.0, 2.0)])

    def no_decode(*args, **kwargs):
        raise AssertionError("a WAV must not be decoded again")

    monkeypatch.setattr(audio_preprocessing, "convert_to_16k_wav", no_decode)

    with soundfile_readable_audio(str(wav)) as readable:
        assert readable == str(wav)


def test_delivery_reads_an_unreadable_container_through_the_decoder(
    tmp_path, monkeypatch
):
    """Runs without ffmpeg: the decoder is replaced by one that writes the WAV."""
    wav = tmp_path / "capture.wav"
    local, remote = _two_source_capture(wav)
    container = tmp_path / "capture.webm"
    container.write_bytes(UNREADABLE)

    def decode(source: str, target: str, *, mono: bool, timeout: float) -> None:
        assert source == str(container)
        assert mono is False
        shutil.copyfile(wav, target)

    monkeypatch.setattr(audio_preprocessing, "convert_to_16k_wav", decode)

    result = analyse_delivery(str(container), local + remote, browser_capture=True)

    assert result["speakers"]["rs:local"]["capture_sources"] == ["microphone"]
    assert result["speakers"]["rs:remote"]["capture_sources"] == ["system"]
