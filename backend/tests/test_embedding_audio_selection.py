from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import soundfile as sf

from backend.utils.embedding_audio import select_recording_audio_for_embedding
from backend.worker.tasks.embeddings import update_speaker_embedding_task


def test_select_recording_audio_for_embedding_prefers_proxy_for_browser_capture(
    tmp_path,
):
    audio_path = tmp_path / "meeting.webm"
    proxy_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"webm")
    proxy_path.write_bytes(b"mp3")

    recording = SimpleNamespace(audio_path=str(audio_path), proxy_path=str(proxy_path))

    assert select_recording_audio_for_embedding(recording) == str(proxy_path)


@pytest.mark.parametrize("suffix", [".mkv", ".mka", ".ts", ".mts", ".mpg", ".avi"])
def test_select_recording_audio_for_embedding_prefers_proxy_for_media_containers(
    tmp_path, suffix
):
    """pyannote's segment crop returns short or empty chunks from these."""
    audio_path = tmp_path / f"meeting{suffix}"
    proxy_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"container")
    proxy_path.write_bytes(b"mp3")

    recording = SimpleNamespace(audio_path=str(audio_path), proxy_path=str(proxy_path))

    assert select_recording_audio_for_embedding(recording) == str(proxy_path)


def test_select_recording_audio_for_embedding_prefers_audio_for_wav(tmp_path):
    audio_path = tmp_path / "meeting.wav"
    proxy_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"wav")
    proxy_path.write_bytes(b"mp3")

    recording = SimpleNamespace(audio_path=str(audio_path), proxy_path=str(proxy_path))

    assert select_recording_audio_for_embedding(recording) == str(audio_path)


def test_select_recording_audio_for_embedding_falls_back_to_proxy(tmp_path):
    proxy_path = tmp_path / "meeting.mp3"
    proxy_path.write_bytes(b"mp3")

    recording = SimpleNamespace(
        audio_path=str(tmp_path / "missing.webm"), proxy_path=str(proxy_path)
    )

    assert select_recording_audio_for_embedding(recording) == str(proxy_path)


def test_update_speaker_embedding_task_prefers_proxy_for_browser_capture(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "meeting.webm"
    proxy_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"webm")
    proxy_path.write_bytes(b"mp3")

    recording = SimpleNamespace(
        id=7, audio_path=str(audio_path), proxy_path=str(proxy_path)
    )
    recording_speaker = SimpleNamespace(
        id=9,
        diarization_label="LIVE_00",
        embedding=None,
        global_speaker_id=None,
    )

    class _FakeSession:
        def __init__(self):
            self._added = []
            self._committed = False

        def get(self, model, identity):
            model_name = getattr(model, "__name__", "")
            if model_name == "Recording":
                return recording
            if model_name == "RecordingSpeaker":
                return recording_speaker
            return None

        def add(self, value):
            self._added.append(value)

        def commit(self):
            self._committed = True

        def rollback(self):
            raise AssertionError("rollback should not be called")

    captured = {}

    def fake_extract(audio, segments, device_str="cpu"):
        captured["audio"] = audio
        captured["segments"] = list(segments)
        return [0.1, 0.2]

    monkeypatch.setattr(
        "backend.processing.embedding_core.extract_embedding_for_segments",
        fake_extract,
    )

    update_speaker_embedding_task._session = _FakeSession()
    try:
        update_speaker_embedding_task.run(7, 1.0, 2.0, 9)
    finally:
        update_speaker_embedding_task._session = None

    assert captured["audio"] == str(proxy_path)
    assert captured["segments"] == [(1.0, 2.0)]
    assert recording_speaker.embedding == [0.1, 0.2]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_a_media_container_without_a_proxy_is_cropped_from_a_decoded_wav(
    monkeypatch, tmp_path
):
    """With no proxy yet, the MKV itself is never handed to pyannote's crop."""
    from backend.processing import embedding_core

    source = tmp_path / "meeting.mkv"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error"]
        + ["-f", "lavfi", "-i", "testsrc=size=64x64:rate=25:duration=4"]
        + ["-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=48000"]
        + ["-c:v", "mpeg4", "-c:a", "aac", "-ac", "2", str(source)],
        check=True,
    )
    recording = SimpleNamespace(audio_path=str(source), proxy_path=None)
    target = select_recording_audio_for_embedding(recording)
    cropped: list[tuple[str, int, int]] = []

    def fake_crop(model, audio_path, segment):
        info = sf.info(audio_path)
        cropped.append((audio_path, info.channels, info.samplerate))
        return [1.0, 0.0]

    monkeypatch.setattr(
        embedding_core, "load_embedding_model", lambda device, token: object()
    )
    monkeypatch.setattr(embedding_core, "_crop_embedding", fake_crop)
    monkeypatch.setattr(embedding_core, "_embedding_model_cache", {})

    result = embedding_core.extract_embedding_for_segments(
        target, [(0.5, 3.5)], device_str="cpu", hf_token="unused"
    )

    assert result is not None
    assert cropped
    assert {(channels, rate) for _, channels, rate in cropped} == {(1, 16_000)}
    decoded_paths = {path for path, _, _ in cropped}
    assert str(source) not in decoded_paths
    assert not any(Path(path).exists() for path in decoded_paths)
