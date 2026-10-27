"""Blocked real PostgreSQL inference cannot resurrect closed or reset guidance."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session

import backend.worker.tasks as tasks
from backend.models.recording import Recording, RecordingStatus
from backend.services.timeline_note_service import RecordingTimelineNotes
from backend.tests.test_automatic_meeting_intelligence_worker import _sample_llm_config
from backend.tests.test_canonical_publication_fence import Database
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.tests.test_capture_master_publication import CaptureHarness, _stop
from backend.tests.test_capture_master_publication import (
    capture_harness as capture_harness,
)
from backend.tests.test_reprocess_retention_api import _reset
from backend.utils.status_manager import update_recording_status
from backend.worker.tasks import meeting_edge_stage as stage
from backend.worker.tasks.intelligence_source import load_intelligence_source
from backend.worker.tasks.vad_stage import _complete_without_speech


def _reset_running_edge(engine: Any, recording_id: int, *, settled: bool) -> None:
    with Session(engine) as session:
        transcript = load_intelligence_source(session, recording_id)
        revision = transcript.change_sequence
        assert transcript.meeting_edge_status == "updating"
        assert transcript.meeting_edge_source_signature
    asyncio.run(_reset(engine, recording_id))
    with Session(engine) as session:
        transcript = load_intelligence_source(session, recording_id)
        assert (
            (transcript.change_sequence > revision)
            if settled
            else (transcript.change_sequence == revision)
        )
        assert transcript.meeting_edge_status == "ready"
        assert transcript.meeting_edge_source_signature is None


def _transition_during_inference(
    engine: Any, recording_id: int, transition: str
) -> None:
    if transition in {"actual_reset", "settled_reset"}:
        _reset_running_edge(engine, recording_id, settled=transition == "settled_reset")
        return
    with Session(engine) as session:
        transcript = load_intelligence_source(session, recording_id)
        original_revision = transcript.change_sequence
        assert transcript.meeting_edge_status == "updating"
        assert transcript.meeting_edge_source_signature
        recording = session.get(Recording, recording_id)
        if transition in {"processed", "error"}:
            transcript.notes_status = "completed"
            transcript.meeting_edge_error_message = "Old timeout"
            session.add(transcript)
            update_recording_status(session, recording_id)
            assert recording.status == (
                RecordingStatus.PROCESSED
                if transition == "processed"
                else RecordingStatus.ERROR
            )
            assert transcript.meeting_edge_error_message is None
            assert transcript.meeting_edge_status == "ready"
        elif transition == "retry":
            # A retry may retain identical transcript words and revision.
            transcript.meeting_edge_source_signature = None
            transcript.meeting_edge_status = "ready"
            session.add(transcript)
        elif transition == "terminal_status":
            # Status must fence a writer independently of signature clearing.
            recording.status = RecordingStatus.PROCESSED
            transcript.meeting_edge_status = "ready"
            session.add(recording)
            session.add(transcript)
        else:
            recording.status = RecordingStatus.PAUSED
            session.add(recording)
        session.commit()
        assert transcript.change_sequence == original_revision


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize(
    "transition",
    [
        "processed",
        "error",
        "terminal_status",
        "retry",
        "actual_reset",
        "settled_reset",
        "paused",
    ],
)
def test_blocked_edge_completion_obeys_terminal_status_and_reset_signature(
    publication_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
    transition: str,
) -> None:
    engine, recording_id, _ = publication_database
    entered, release = threading.Event(), threading.Event()
    retained = {
        "summary": "Retained guidance",
        "concept_history": [{"text": "Earlier question"}],
    }
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        recording.status = (
            RecordingStatus.UPLOADING
            if transition == "paused"
            else RecordingStatus.PROCESSING
        )
        transcript = load_intelligence_source(session, recording_id)
        transcript.meeting_edge_payload = retained
        transcript.meeting_edge_focus = "Keep the budget visible"
        transcript.transcript_status = "error" if transition == "error" else "completed"
        transcript.notes_status = "generating"
        if transition == "settled_reset":
            transcript.settling_state = {"settled": True}
        session.add(recording)
        session.add(transcript)
        session.commit()
    if transition in {"actual_reset", "settled_reset"}:
        asyncio.run(_reset(engine, recording_id))

    class Model:
        def generate_meeting_edge(self, *args: Any, **kwargs: Any) -> Any:
            entered.set()
            assert release.wait(10), "Inference was never released"
            if fails:
                raise RuntimeError("Late inference failure")
            return SimpleNamespace()

    monkeypatch.setattr(stage, "is_meeting_edge_enabled", lambda settings: True)
    monkeypatch.setattr(
        stage, "load_recording_timeline_notes", lambda *args: RecordingTimelineNotes()
    )
    monkeypatch.setattr(tasks, "_has_meeting_edge_signal", lambda **kwargs: True)
    monkeypatch.setattr(
        stage, "resolve_llm_config", lambda *args, **kwargs: _sample_llm_config()
    )
    monkeypatch.setattr(tasks, "_llm_backend_from_config", lambda config: Model())
    monkeypatch.setattr(
        tasks,
        "serialize_meeting_edge_result",
        lambda result: {"summary": "Late guidance"},
    )

    def infer() -> Any:
        with Session(engine, expire_on_commit=False) as session:
            return stage._refresh_meeting_edge(
                SimpleNamespace(session=session), recording_id
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(infer)
        try:
            assert entered.wait(10), "The real task never entered inference"
            _transition_during_inference(engine, recording_id, transition)
        finally:
            release.set()
        result = future.result(timeout=10)
    with Session(engine) as session:
        transcript = load_intelligence_source(session, recording_id)
        assert transcript.meeting_edge_focus == "Keep the budget visible"
        if transition == "paused":
            assert transcript.meeting_edge_status == ("error" if fails else "ready")
            assert transcript.meeting_edge_error_message == (
                "Late inference failure" if fails else None
            )
            if not fails:
                assert result["summary"] == "Late guidance"
        else:
            assert result is None
            assert transcript.meeting_edge_payload == retained
            assert transcript.meeting_edge_error_message is None
            assert transcript.meeting_edge_status == "ready"


@pytest.mark.parametrize("boundary", ["stop", "no_speech"])
@pytest.mark.parametrize("useful", [False, True])
def test_closure_clears_only_transient_edge_state(
    capture_harness: CaptureHarness, boundary: str, useful: bool
) -> None:
    engine, recording_id, _, _, _ = capture_harness
    payload = {"questions": ["Who owns this?"]} if useful else {"generated_at": "old"}
    with Session(engine) as session:
        transcript = load_intelligence_source(session, recording_id)
        transcript.meeting_edge_payload = payload
        transcript.meeting_edge_focus = "Keep this focus"
        transcript.meeting_edge_error_message = "Old timeout"
        transcript.meeting_edge_status = "error"
        session.add(transcript)
        session.commit()
    if boundary == "stop":
        _stop(capture_harness)
    else:
        with Session(engine) as session:
            _complete_without_speech(session, session.get(Recording, recording_id))
    with Session(engine) as session:
        transcript = load_intelligence_source(session, recording_id)
        assert transcript.meeting_edge_error_message is None
        assert transcript.meeting_edge_status == ("ready" if useful else "idle")
        assert transcript.meeting_edge_payload == payload
        assert transcript.meeting_edge_focus == "Keep this focus"
