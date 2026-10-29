from __future__ import annotations

from types import SimpleNamespace

import pytest

import backend.worker.tasks as tasks_module
from backend.models.timeline_note import TimelineNote
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.utils.meeting_edge import MeetingEdgeRequest, build_meeting_edge_prompt


@pytest.fixture(autouse=True)
def isolate_edge_guards_from_live_naming(monkeypatch: pytest.MonkeyPatch) -> None:
    """These legacy session doubles test Edge guards, not capture-fenced naming.

    Live suggestion persistence and cadence run against real database sessions
    in test_live_speaker_suggestions.py.
    """
    monkeypatch.setattr(
        "backend.worker.tasks.meeting_edge_stage.refresh_live_speaker_suggestions",
        lambda session, recording_id: None,
    )


class _ExecResult:
    def __init__(self, first_value=None, all_values=None):
        self._first_value = first_value
        self._all_values = all_values or []

    def first(self):
        return self._first_value

    def all(self):
        return list(self._all_values)


class _FakeSession:
    def __init__(self, recording, transcript, user):
        self.recording = recording
        self.transcript = transcript
        self.user = user
        self.added = []
        self.commit_count = 0
        recording.capture_epoch = getattr(recording, "capture_epoch", 0)
        transcript.recording_id = recording.id

    def get(self, model, identifier):
        if model is tasks_module.Recording and identifier == self.recording.id:
            return self.recording
        if model is tasks_module.User and identifier == self.user.id:
            return self.user
        return None

    def exec(self, statement):
        statement_text = str(statement)
        if "FROM transcripts" in statement_text:
            return _ExecResult(first_value=self.transcript)
        return _ExecResult(all_values=[])

    def execute(self, statement):
        # The live speaker names: none given.
        return _ExecResult(all_values=[])

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commit_count += 1

    def close(self):
        return None


def test_refresh_meeting_edge_task_returns_idle_without_llm_when_disabled(
    monkeypatch, fake_single_flight
):
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
    )
    user = SimpleNamespace(id=7, settings={"enable_meeting_edge": False})
    session = _FakeSession(recording, transcript, user)

    monkeypatch.setattr(tasks_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tasks_module,
        "resolve_llm_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError(
                "Meeting Edge LLM config should not be resolved when disabled"
            )
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


def _run_live_edge_refresh(monkeypatch, session, llm):
    """Run one refresh that reaches the model call with `llm`."""
    monkeypatch.setattr(tasks_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tasks_module,
        "build_transcript_segments_for_read",
        lambda *args, **kwargs: [
            {
                "start": 0.0,
                "end": 1.2,
                "speaker": "SPEAKER_00",
                "text": "Fresh agenda update",
            }
        ],
    )
    monkeypatch.setattr(tasks_module, "_has_meeting_edge_signal", lambda **kwargs: True)
    monkeypatch.setattr(
        tasks_module, "_should_refresh_meeting_edge", lambda **kwargs: True
    )
    monkeypatch.setattr(
        tasks_module, "_resolve_meeting_event_context", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        tasks_module, "build_recording_speaker_map", lambda speakers: {}
    )
    monkeypatch.setattr(
        tasks_module,
        "serialize_meeting_edge_result",
        lambda result: {"summary": result},
    )
    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda config: llm)
    monkeypatch.setattr(
        tasks_module,
        "resolve_llm_config",
        lambda *args, **kwargs: SimpleNamespace(
            provider="openai",
            model="gpt-test",
            api_url=None,
            merged_config={},
            missing_configuration_message=lambda: None,
        ),
    )

    task = tasks_module.refresh_meeting_edge_task
    original_session = task._session
    task._session = session
    try:
        result = task.run(1)
    finally:
        task._session = original_session
    return result


def test_refresh_meeting_edge_task_passes_rolling_summary_and_previous_suggestions(
    monkeypatch, fake_single_flight
):
    recording = SimpleNamespace(
        id=1,
        status=tasks_module.RecordingStatus.UPLOADING,
        user_id=7,
    )
    transcript = SimpleNamespace(
        meeting_edge_status=tasks_module.MEETING_EDGE_STATUS_READY,
        meeting_edge_error_message=None,
        meeting_edge_payload={
            "summary": "Short read.",
            "rolling_summary": "Rich running context with decisions and threads.",
            "questions": ["Previously asked question?"],
            "points": ["Previously raised point."],
        },
        meeting_edge_source_signature="old-signature",
        segments=[],
        meeting_edge_focus=None,
    )
    user = SimpleNamespace(id=7, settings={"enable_meeting_edge": True})
    session = _FakeSession(recording, transcript, user)
    captured: dict[str, object] = {}

    class FakeLLM:
        def generate_meeting_edge(self, request, timeout):
            captured["rolling_summary"] = request.rolling_summary
            captured["previous_questions"] = request.previous_questions
            captured["previous_points"] = request.previous_points
            return "Updated guidance"

    result = _run_live_edge_refresh(monkeypatch, session, FakeLLM())

    assert result is not None
    assert (
        captured["rolling_summary"]
        == "Rich running context with decisions and threads."
    )
    assert captured["previous_questions"] == ("Previously asked question?",)
    assert captured["previous_points"] == ("Previously raised point.",)


def test_stored_item_reprs_never_reach_previously_suggested():
    # Items stored before issue #652 are the Python repr of an object.
    stored_payload = {
        "questions": [
            "{'text': 'Ask Alice Example about the rollback plan.'}",
            "{'term': 'SLA', 'explanation': 'Service level agreement'}",
            "Who owns the vendor contract?",
        ],
        "points": [
            "{'text': \"Bob Sample's budget is not confirmed.\"}",
            7,
        ],
    }

    questions = tasks_module._read_meeting_edge_payload_items(
        stored_payload, "questions"
    )
    points = tasks_module._read_meeting_edge_payload_items(stored_payload, "points")
    prompt = build_meeting_edge_prompt(
        MeetingEdgeRequest(
            recent_transcript="Alice Example: we launch on Friday.",
            previous_questions=questions,
            previous_points=points,
        )
    )

    assert questions == (
        "Ask Alice Example about the rollback plan.",
        "Who owns the vendor contract?",
    )
    assert points == ("Bob Sample's budget is not confirmed.",)
    previous = prompt.split("# Previously Suggested", 1)[1].split("# User Focus", 1)[0]
    assert "- Ask Alice Example about the rollback plan." in previous
    assert "- Bob Sample's budget is not confirmed." in previous
    assert "{" not in previous


def test_should_refresh_meeting_edge_on_context_level_change():
    transcript = SimpleNamespace(
        meeting_edge_status=tasks_module.MEETING_EDGE_STATUS_READY,
        meeting_edge_source_signature="other-signature",
        meeting_edge_payload={
            "generated_at": "2099-01-01T00:00:00+00:00",
            "source_segment_count": 10,
            "source_word_count": 200,
            "focus_hash": tasks_module._hash_meeting_edge_text(None),
            "notes_hash": tasks_module._hash_meeting_edge_text("n1:1"),
            "context_level": 2,
        },
    )

    # Same context level, future generated_at, no new content: no refresh.
    assert not tasks_module._should_refresh_meeting_edge_impl(
        transcript=transcript,
        source_signature="new-signature",
        current_segment_count=10,
        current_word_count=200,
        focus_text=None,
        notes_signature="n1:1",
        context_level=2,
    )

    # Context level change forces an immediate refresh.
    assert tasks_module._should_refresh_meeting_edge_impl(
        transcript=transcript,
        source_signature="new-signature",
        current_segment_count=10,
        current_word_count=200,
        focus_text=None,
        notes_signature="n1:1",
        context_level=4,
    )

    # So does an edited note, wherever it sits in the meeting.
    assert tasks_module._should_refresh_meeting_edge_impl(
        transcript=transcript,
        source_signature="new-signature",
        current_segment_count=10,
        current_word_count=200,
        focus_text=None,
        notes_signature="n1:2",
        context_level=2,
    )


def _edge_note(public_id: str, kind: str, body: str, **anchor: int) -> TimelineNote:
    return TimelineNote(
        public_id=public_id, recording_id=1, kind=kind, body=body, **anchor
    )


def test_meeting_edge_window_carries_notes_with_their_lines():
    # Two lines more than the window holds, so lines 0 and 1 fall outside it.
    count = tasks_module.MEETING_EDGE_RECENT_SEGMENTS + 2
    segments = [
        {"start": float(i), "end": i + 0.5, "speaker": "SPEAKER_00", "text": f"L{i}"}
        for i in range(count)
    ]
    notes = RecordingTimelineNotes(
        notes=[
            _edge_note("a", "point", "before the window", anchor_ms=1_200),
            _edge_note("b", "point", "inside it", anchor_ms=2_200),
            _edge_note("c", "head", "meeting-wide"),
        ]
    )

    window = tasks_module._build_recent_meeting_edge_transcript(segments, {}, notes)

    # The note after line 1 leaves with it, and the head note is the prompt
    # section's, not the window's.
    lines = window.splitlines()
    assert lines[:3] == [
        "[00:02 - 00:02] SPEAKER_00: L2",
        "[Note — User]: inside it",
        "[00:03 - 00:03] SPEAKER_00: L3",
    ]
    assert "before the window" not in window
    assert "meeting-wide" not in window
    assert len(lines) == tasks_module.MEETING_EDGE_RECENT_SEGMENTS + 1


def test_meeting_edge_window_opens_with_early_notes_only_from_the_start():
    segments = [{"start": 5.0, "end": 6.0, "speaker": "SPEAKER_00", "text": "Hi"}]
    notes = RecordingTimelineNotes(
        notes=[_edge_note("a", "point", "before anyone spoke", anchor_ms=1_000)]
    )

    window = tasks_module._build_recent_meeting_edge_transcript(segments, {}, notes)

    assert window.splitlines() == [
        "[Note — User]: before anyone spoke",
        "[00:05 - 00:06] SPEAKER_00: Hi",
    ]


def test_meeting_edge_source_signature_includes_context_level():
    base_kwargs = {
        "recent_transcript": "Speaker A: hello",
        "focus_text": None,
        "user_notes": None,
        "config_signature": "openai:gpt-test:",
    }
    signature_level_2 = tasks_module._build_meeting_edge_source_signature(
        **base_kwargs, context_level=2
    )
    signature_level_3 = tasks_module._build_meeting_edge_source_signature(
        **base_kwargs, context_level=3
    )

    assert signature_level_2 != signature_level_3


def test_refresh_meeting_edge_task_uses_canonical_segments_when_projection_is_empty(
    monkeypatch, fake_single_flight
):
    recording = SimpleNamespace(
        id=1,
        status=tasks_module.RecordingStatus.UPLOADING,
        user_id=7,
    )
    transcript = SimpleNamespace(
        meeting_edge_status=tasks_module.MEETING_EDGE_STATUS_IDLE,
        meeting_edge_error_message=None,
        meeting_edge_payload={},
        meeting_edge_source_signature=None,
        segments=[],
        meeting_edge_focus=None,
    )
    user = SimpleNamespace(id=7, settings={"enable_meeting_edge": True})
    session = _FakeSession(recording, transcript, user)
    captured: dict[str, str] = {}

    class FakeLLM:
        def generate_meeting_edge(self, request, timeout):
            captured["recent_transcript"] = request.recent_transcript
            captured["timeout"] = str(timeout)
            return "Canonical guidance"

    monkeypatch.setattr(tasks_module, "flag_modified", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tasks_module,
        "build_transcript_segments_for_read",
        lambda *args, **kwargs: [
            {
                "start": 0.0,
                "end": 1.2,
                "speaker": "SPEAKER_00",
                "text": "Canonical agenda update",
            }
        ],
    )
    monkeypatch.setattr(tasks_module, "_has_meeting_edge_signal", lambda **kwargs: True)
    monkeypatch.setattr(
        tasks_module, "_should_refresh_meeting_edge", lambda **kwargs: True
    )
    monkeypatch.setattr(
        tasks_module, "_resolve_meeting_event_context", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        tasks_module, "build_recording_speaker_map", lambda speakers: {}
    )
    monkeypatch.setattr(
        tasks_module,
        "serialize_meeting_edge_result",
        lambda result: {"summary": result},
    )
    monkeypatch.setattr(
        tasks_module, "_llm_backend_from_config", lambda config: FakeLLM()
    )
    monkeypatch.setattr(
        tasks_module,
        "resolve_llm_config",
        lambda *args, **kwargs: SimpleNamespace(
            provider="openai",
            model="gpt-test",
            api_url=None,
            merged_config={},
            missing_configuration_message=lambda: None,
        ),
    )

    task = tasks_module.refresh_meeting_edge_task
    original_session = task._session
    task._session = session
    try:
        result = task.run(1)
    finally:
        task._session = original_session

    assert result is not None
    assert result["summary"] == "Canonical guidance"
    assert "Canonical agenda update" in captured["recent_transcript"]
    assert transcript.meeting_edge_status == tasks_module.MEETING_EDGE_STATUS_READY
    assert transcript.meeting_edge_payload["summary"] == "Canonical guidance"


def test_refresh_is_skipped_while_another_run_holds_the_guard(
    monkeypatch, fake_single_flight, caplog
):
    """A second trigger during a live meeting must not start a second LLM run.

    The staleness check reads a timestamp written only when a run finishes, and
    the signature check only suppresses an input identical to one already in
    flight, which a growing transcript never is. Four concurrent runs were
    observed on a single recording before the guard existed.
    """
    from backend.core.single_flight import KEY_PREFIX

    recording = SimpleNamespace(
        id=1,
        status=tasks_module.RecordingStatus.UPLOADING,
        user_id=7,
    )
    transcript = SimpleNamespace(
        meeting_edge_status=tasks_module.MEETING_EDGE_STATUS_UPDATING,
        meeting_edge_error_message=None,
        meeting_edge_payload={},
        meeting_edge_source_signature="abc123",
        segments=[{"text": "Plenty of signal here to justify a refresh."}],
        meeting_edge_focus=None,
    )
    user = SimpleNamespace(id=7, settings={"enable_meeting_edge": True})
    session = _FakeSession(recording, transcript, user)

    # Stand in for a run already in flight on another worker.
    fake_single_flight.keys[f"{KEY_PREFIX}meeting-edge:1"] = "in-flight-elsewhere"

    monkeypatch.setattr(
        tasks_module,
        "resolve_llm_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("A skipped refresh must not reach the LLM")
        ),
    )

    task = tasks_module.refresh_meeting_edge_task
    original_session = task._session
    task._session = session
    try:
        with caplog.at_level("INFO"):
            result = task.run(1)
    finally:
        task._session = original_session

    assert result is None
    # The skip must not touch transcript state: the in-flight run owns it.
    assert session.commit_count == 0
    assert '"stage":"meeting_edge_refresh_skipped"' in caplog.text
    assert '"reason":"already_running"' in caplog.text


def test_the_guard_is_released_so_the_next_trigger_can_run(
    monkeypatch, fake_single_flight
):
    from backend.core.single_flight import KEY_PREFIX

    recording = SimpleNamespace(
        id=1,
        status=tasks_module.RecordingStatus.UPLOADING,
        user_id=7,
    )
    transcript = SimpleNamespace(
        meeting_edge_status=tasks_module.MEETING_EDGE_STATUS_READY,
        meeting_edge_error_message=None,
        meeting_edge_payload={},
        meeting_edge_source_signature="abc123",
        segments=[{"text": "Short."}],
        meeting_edge_focus=None,
    )
    user = SimpleNamespace(id=7, settings={"enable_meeting_edge": False})
    session = _FakeSession(recording, transcript, user)

    monkeypatch.setattr(tasks_module, "flag_modified", lambda *args, **kwargs: None)

    task = tasks_module.refresh_meeting_edge_task
    original_session = task._session
    task._session = session
    try:
        task.run(1)
    finally:
        task._session = original_session

    assert f"{KEY_PREFIX}meeting-edge:1" not in fake_single_flight.keys


def _live_edge_session() -> _FakeSession:
    recording = SimpleNamespace(
        id=1, status=tasks_module.RecordingStatus.UPLOADING, user_id=7
    )
    transcript = SimpleNamespace(
        meeting_edge_status=tasks_module.MEETING_EDGE_STATUS_READY,
        meeting_edge_error_message=None,
        meeting_edge_payload={"summary": "Earlier guidance"},
        meeting_edge_source_signature="old-signature",
        segments=[],
        meeting_edge_focus=None,
    )
    user = SimpleNamespace(id=7, settings={"enable_meeting_edge": True})
    return _FakeSession(recording, transcript, user)


@pytest.mark.parametrize("call_fails", [False, True])
def test_stop_during_a_call_drops_its_outcome_and_retires_updating(
    monkeypatch, fake_single_flight, call_fails
):
    session = _live_edge_session()

    class StoppedDuringCall:
        def generate_meeting_edge(self, request, timeout):
            assert (
                session.transcript.meeting_edge_status
                == tasks_module.MEETING_EDGE_STATUS_UPDATING
            )
            session.recording.status = tasks_module.RecordingStatus.QUEUED
            if call_fails:
                raise RuntimeError("provider timed out")
            return "Guidance for a capture that has ended"

    result = _run_live_edge_refresh(monkeypatch, session, StoppedDuringCall())

    assert result is None
    transcript = session.transcript
    assert transcript.meeting_edge_payload == {"summary": "Earlier guidance"}
    assert transcript.meeting_edge_status == tasks_module.MEETING_EDGE_STATUS_READY
    assert transcript.meeting_edge_error_message is None


def test_pause_during_a_call_keeps_its_outcome(monkeypatch, fake_single_flight):
    session = _live_edge_session()

    class PausedDuringCall:
        def generate_meeting_edge(self, request, timeout):
            session.recording.status = tasks_module.RecordingStatus.PAUSED
            return "Guidance for the paused capture"

    result = _run_live_edge_refresh(monkeypatch, session, PausedDuringCall())

    assert result is not None
    assert session.transcript.meeting_edge_payload["summary"] == (
        "Guidance for the paused capture"
    )
    assert (
        session.transcript.meeting_edge_status == tasks_module.MEETING_EDGE_STATUS_READY
    )
