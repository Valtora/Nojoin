"""Derived outputs cannot take ownership of the recording lifecycle."""

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.tests.test_canonical_publication_fence import Database
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils.status_manager import update_recording_status
from backend.worker import tasks


@pytest.mark.parametrize(
    "capture_status", [RecordingStatus.UPLOADING, RecordingStatus.PAUSED]
)
@pytest.mark.parametrize("transcript_status", ["pending", "processing", "completed"])
def test_notes_status_updates_preserve_capture_lifecycle(
    publication_database: Database,
    capture_status: RecordingStatus,
    transcript_status: str,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        recording.status = capture_status
        recording.processing_step = "Capturing audio"
        recording.processing_progress = 12
        transcript = session.exec(select(Transcript)).one()
        transcript.transcript_status = transcript_status
        session.add(recording)
        session.add(transcript)
        session.commit()

        for notes_status in ("generating", "completed", "error"):
            transcript.notes_status = notes_status
            session.add(transcript)
            session.commit()
            update_recording_status(session, recording_id)
            _assert_capture_unchanged(engine, recording_id, capture_status)


def _assert_capture_unchanged(
    engine: Engine, recording_id: int, status: RecordingStatus
) -> None:
    with Session(engine) as reader:
        recording = reader.get(Recording, recording_id)
        assert recording.status == status
        assert recording.processing_step == "Capturing audio"
        assert recording.processing_progress == 12
        assert recording.processing_started_at is None
        assert recording.processing_completed_at is None


@pytest.mark.parametrize(
    "notes_status", ["pending", "generating", "completed", "error"]
)
@pytest.mark.parametrize(
    ("transcript_status", "expected"),
    [
        ("pending", RecordingStatus.QUEUED),
        ("processing", RecordingStatus.PROCESSING),
        ("completed", RecordingStatus.PROCESSED),
        ("error", RecordingStatus.ERROR),
    ],
)
def test_final_transcription_drives_status_independently_of_notes(
    publication_database: Database,
    notes_status: str,
    transcript_status: str,
    expected: RecordingStatus,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        recording.status = RecordingStatus.QUEUED
        transcript = session.exec(select(Transcript)).one()
        transcript.transcript_status = transcript_status
        transcript.notes_status = notes_status
        session.add(recording)
        session.add(transcript)
        session.commit()
        update_recording_status(session, recording_id)

    with Session(engine) as reader:
        assert reader.get(Recording, recording_id).status == expected
        assert reader.exec(select(Transcript)).one().notes_status == notes_status


@pytest.mark.usefixtures("stub_text_embedding")
@pytest.mark.parametrize(
    "capture_status", [RecordingStatus.UPLOADING, RecordingStatus.PAUSED]
)
@pytest.mark.parametrize("fails", [False, True])
def test_primary_notes_worker_preserves_live_capture_on_success_and_failure(
    publication_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    capture_status: RecordingStatus,
    fails: bool,
) -> None:
    engine, recording_id, _ = publication_database
    config = SimpleNamespace(
        provider="ollama",
        merged_config={"llm_provider": "ollama"},
        missing_configuration_message=lambda: None,
    )

    class LLM:
        def generate_meeting_notes(self, *args: Any, **kwargs: Any) -> str:
            _assert_capture_unchanged(engine, recording_id, capture_status)
            with Session(engine) as reader:
                assert (
                    reader.exec(select(Transcript)).one().notes_status == "generating"
                )
            if fails:
                raise RuntimeError("Notes provider unavailable")
            return "## Summary\nLive notes."

    monkeypatch.setattr(tasks, "resolve_llm_config", lambda *a, **kw: config)
    monkeypatch.setattr(tasks, "_llm_backend_from_config", lambda _: LLM())
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        recording.status = capture_status
        recording.processing_step = "Capturing audio"
        recording.processing_progress = 12
        transcript = session.exec(select(Transcript)).one()
        transcript.transcript_status = "processing"
        transcript.notes = "Earlier notes"
        session.add(recording)
        session.add(transcript)
        session.commit()
        tasks.generate_notes_task._session = session
        try:
            tasks.generate_notes_task.run(recording_id)
        finally:
            tasks.generate_notes_task._session = None

    _assert_capture_unchanged(engine, recording_id, capture_status)
    with Session(engine) as reader:
        transcript = reader.exec(select(Transcript)).one()
        assert transcript.transcript_status == "processing"
        assert transcript.notes_status == ("error" if fails else "completed")
        assert transcript.notes == (
            "Earlier notes" if fails else "## Summary\nLive notes."
        )
        assert transcript.error_message == (
            "Notes provider unavailable" if fails else None
        )
