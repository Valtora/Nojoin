from __future__ import annotations

from types import SimpleNamespace

import backend.worker.tasks as tasks_module


class _ExecResult:
    def __init__(self, first_value):
        self._first_value = first_value

    def first(self):
        return self._first_value


class _FakeSession:
    def __init__(self, recording, transcript, user):
        self.recording = recording
        self.transcript = transcript
        self.user = user
        self.added = []
        self.commit_count = 0

    def get(self, model, identifier):
        if model is tasks_module.Recording and identifier == self.recording.id:
            return self.recording
        if model is tasks_module.User and identifier == self.user.id:
            return self.user
        return None

    def exec(self, statement):
        return _ExecResult(self.transcript)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commit_count += 1

    def close(self):
        return None


def test_refresh_meeting_edge_task_returns_idle_without_llm_when_disabled(monkeypatch):
    recording = SimpleNamespace(
        id=1,
        status=tasks_module.RecordingStatus.UPLOADING,
        user_id=7,
    )
    transcript = SimpleNamespace(
        meeting_edge_status=tasks_module.MEETING_EDGE_STATUS_READY,
        meeting_edge_error_message="previous failure",
        meeting_edge_payload={"summary": "Existing guidance"},
        meeting_edge_source_signature="abc123",
        segments=[{"text": "This would normally be enough signal."}],
        meeting_edge_focus=None,
        user_notes=None,
    )
    user = SimpleNamespace(id=7, settings={"enable_meeting_edge": False})
    session = _FakeSession(recording, transcript, user)

    monkeypatch.setattr(tasks_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tasks_module,
        "resolve_llm_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Meeting Edge LLM config should not be resolved when disabled")
        ),
    )

    task = tasks_module.refresh_meeting_edge_task
    original_session = task._session
    task._session = session
    try:
        result = task.run(1)
    finally:
        task._session = original_session

    assert result is None
    assert transcript.meeting_edge_status == tasks_module.MEETING_EDGE_STATUS_IDLE
    assert transcript.meeting_edge_error_message is None
    assert transcript.meeting_edge_payload == {"summary": "Existing guidance"}
    assert session.commit_count == 1