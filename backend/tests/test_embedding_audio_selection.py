from __future__ import annotations

from types import SimpleNamespace

from backend.worker.tasks.embeddings import update_speaker_embedding_task
from backend.utils.embedding_audio import select_recording_audio_for_embedding


def test_select_recording_audio_for_embedding_prefers_proxy_for_browser_capture(tmp_path):
    audio_path = tmp_path / "meeting.webm"
    proxy_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"webm")
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

    recording = SimpleNamespace(audio_path=str(tmp_path / "missing.webm"), proxy_path=str(proxy_path))

    assert select_recording_audio_for_embedding(recording) == str(proxy_path)


def test_update_speaker_embedding_task_prefers_proxy_for_browser_capture(
    monkeypatch,
    tmp_path,
):
    audio_path = tmp_path / "meeting.webm"
    proxy_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"webm")
    proxy_path.write_bytes(b"mp3")

    recording = SimpleNamespace(id=7, audio_path=str(audio_path), proxy_path=str(proxy_path))
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
