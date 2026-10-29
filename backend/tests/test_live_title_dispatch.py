"""An accepted live publication requests the brief after committing its text."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.worker.tasks as tasks_module
from backend.models.recording import RecordingStatus
from backend.processing import live_transcribe as lt
from backend.tests.test_live_transcription import (
    _FakeRecording,
    _FakeSession,
    _FakeTranscript,
    _make_segment_wav,
    _patch_live_deps,
    _run_live_task,
)


@pytest.mark.parametrize("existing_gist", [None, "Already generated."])
@pytest.mark.parametrize("broker_fails", [False, True])
def test_live_brief_dispatch_happens_after_commit_and_is_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_gist: str | None,
    broker_fails: bool,
) -> None:
    temp_dir = tmp_path / "24"
    temp_dir.mkdir()
    monkeypatch.setattr(
        lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir
    )
    audio_store = _patch_live_deps(
        monkeypatch, speech_map=lambda audio: [{"start": 0.0, "end": 1.0}]
    )
    _make_segment_wav(temp_dir, 0, 2.0, audio_store)
    transcript = _FakeTranscript()
    recording = _FakeRecording(RecordingStatus.UPLOADING, transcript)
    recording.user = SimpleNamespace(settings={"live_title_delay_minutes": 1})
    recording.gist = existing_gist
    session = _FakeSession(recording)
    calls: list[int] = []

    def dispatch(recording_id: int) -> None:
        assert session.committed
        calls.append(recording_id)
        if broker_fails:
            raise RuntimeError("broker down")

    monkeypatch.setattr(
        tasks_module, "generate_live_title_task", SimpleNamespace(delay=dispatch)
    )
    _run_live_task(monkeypatch, 24, 0, session)
    assert calls == ([24] if existing_gist is None else [])
    assert len(transcript.segments) == 1
    assert lt.read_live_state(temp_dir / "live")["next_expected"] == 1
