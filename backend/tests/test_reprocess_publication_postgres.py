"""Reprocess serialises before reset and preserves the publication identity."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select

from backend.api.v1.endpoints.recordings import helpers, routes_actions
from backend.models.notes_template import NotesTemplate
from backend.models.pipeline import ProcessingRunKind, TranscriptUtterance
from backend.models.recording import Recording, RecordingStatus
from backend.models.user import User
from backend.tests.test_canonical_publication_fence import (
    SCHEMA,
    Database,
)
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.constants import _load_transcript


async def _reset(
    engine: Engine,
    recording_id: int,
    waiting: threading.Event,
    *,
    route: bool = False,
    metadata: bool = False,
) -> None:
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": SCHEMA, "lock_timeout": "3s"}},
    )

    def entering_fence(
        connection: Any, cursor: Any, statement: str, *args: Any
    ) -> None:
        if "FROM transcripts" in statement and "FOR UPDATE" in statement:
            waiting.set()

    event.listen(async_engine.sync_engine, "before_cursor_execute", entering_fence)
    try:
        async with AsyncSession(async_engine, expire_on_commit=False) as session:
            if route:
                recording = await session.get(Recording, recording_id)
                assert recording is not None
                body: dict[str, Any] = {"transcription_backend": "whisper"}
                if metadata:
                    body.update(max_speakers=3, transcription_language="fr")
                await routes_actions.reprocess_recording(
                    recording.public_id,
                    routes_actions.ReprocessRequest(**body),
                    db=session,
                    current_user=User(
                        id=recording.user_id,
                        username="fence-owner",
                        hashed_password="x",
                    ),
                )
            else:
                await helpers._reset_generated_recording_state(session, recording_id)
                await session.commit()
    finally:
        await async_engine.dispose()


def _processed(engine: Engine, recording_id: int) -> None:
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        assert recording is not None
        recording.status = RecordingStatus.PROCESSED
        session.add(recording)
        session.commit()


def test_reset_waits_without_holding_the_editors_lines(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    waiting = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor, Session(engine) as editor:
        editor.execute(text("SET lock_timeout = '1s'"))
        _load_transcript(editor, recording_id, lock=True)
        future = executor.submit(
            lambda: asyncio.run(_reset(engine, recording_id, waiting))
        )
        try:
            assert waiting.wait(5), "Reset never reached the Transcript fence"
            update_utterance_text(
                editor,
                recording_id=recording_id,
                utterance_public_id="fence-a",
                text="human correction before reset",
            )
            editor.commit()
        finally:
            editor.rollback()
        future.result(timeout=10)
    with Session(engine) as session:
        assert (
            session.exec(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.recording_id == recording_id
                )
            ).first()
            is None
        )


def test_route_metadata_does_not_autoflush_before_the_fence(
    publication_database: Database,
    stub_celery_dispatch: Any,
) -> None:
    engine, recording_id, _ = publication_database
    _processed(engine, recording_id)
    waiting = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor, Session(engine) as publisher:
        publisher.execute(text("SET lock_timeout = '1s'"))
        _load_transcript(publisher, recording_id, lock=True)
        future = executor.submit(
            lambda: asyncio.run(
                _reset(engine, recording_id, waiting, route=True, metadata=True)
            )
        )
        try:
            assert waiting.wait(5), "Route never reached the Transcript fence"
            # The optional metadata UPDATE takes NO KEY UPDATE, so an insert's
            # FK KEY SHARE alone cannot detect this particular lock inversion.
            publisher.execute(
                select(Recording).where(Recording.id == recording_id).with_for_update()
            ).scalar_one()
            publisher.commit()
        finally:
            publisher.rollback()
        future.result(timeout=10)
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        assert recording is not None
        assert (recording.max_speakers, recording.transcription_language) == (3, "fr")
        assert recording.status == RecordingStatus.QUEUED
    assert len(stub_celery_dispatch) == 1


def _seed_generated_products(
    engine: Engine, recording_id: int, status: RecordingStatus
) -> int:
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        assert recording is not None
        recording.status = status
        session.add(recording)
        template = NotesTemplate(name="Previous template", sections="Previous sections")
        session.add(template)
        session.flush()
        transcript = _load_transcript(session, recording_id, lock=True)
        assert transcript is not None and transcript.id is not None
        transcript.notes_template_id = template.id
        for field in (
            "notes",
            "notes_template_sections",
            "meeting_edge_focus",
            "meeting_edge_error_message",
            "meeting_edge_source_signature",
            "error_message",
            "analytics_error_message",
            "analytics_ai_error_message",
        ):
            setattr(transcript, field, "old generated result")
        for field in (
            "language_detection",
            "meeting_edge_payload",
            "analytics_payload",
        ):
            setattr(transcript, field, {"old": True})
        for field in (
            "notes_status",
            "meeting_edge_status",
            "analytics_status",
            "analytics_ai_status",
        ):
            setattr(transcript, field, "completed")
        transcript.speaker_name_suggestions = [{"old": True}]
        transcript.settling_state = {"eligible": True, "epoch": 4}
        transcript.notes_stale_documents = True
        transcript.transcript_status = (
            "error" if status == RecordingStatus.ERROR else "completed"
        )
        session.add(transcript)
        session.commit()
        transcript_id = transcript.id
        session.execute(
            text("UPDATE transcripts SET change_sequence=50 WHERE id=:id"),
            {"id": transcript_id},
        )
        session.commit()
        return transcript_id


@pytest.mark.parametrize("status", [RecordingStatus.PROCESSED, RecordingStatus.ERROR])
def test_reprocess_and_retry_keep_sequence_after_a_concurrent_edit(
    publication_database: Database,
    stub_celery_dispatch: Any,
    monkeypatch: pytest.MonkeyPatch,
    status: RecordingStatus,
) -> None:
    engine, recording_id, _ = publication_database
    transcript_id = _seed_generated_products(engine, recording_id, status)
    monkeypatch.setattr(routes_actions, "_recording_has_audio", lambda recording: True)
    waiting = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor, Session(engine) as editor:
        _load_transcript(editor, recording_id, lock=True)
        future = executor.submit(
            lambda: asyncio.run(_reset(engine, recording_id, waiting, route=True))
        )
        try:
            assert waiting.wait(5), "Reprocess never reached the Transcript fence"
            update_utterance_text(
                editor,
                recording_id=recording_id,
                utterance_public_id="fence-a",
                text="edit accepted before reprocess",
            )
            editor.commit()
        finally:
            editor.rollback()
        future.result(timeout=10)
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None, "Reprocess deleted the publication identity"
        assert (transcript.id, transcript.change_sequence) == (transcript_id, 52)
        assert transcript.text is None and transcript.segments == []
        assert transcript.transcript_status == "pending"
        assert transcript.notes_status == "pending"
        assert transcript.meeting_edge_status == "idle"
        assert (
            transcript.analytics_status == transcript.analytics_ai_status == "pending"
        )
        assert transcript.speaker_name_suggestions == []
        assert transcript.notes_stale_documents is False
        for field in (
            "notes",
            "notes_template_id",
            "notes_template_sections",
            "meeting_edge_focus",
            "meeting_edge_error_message",
            "meeting_edge_source_signature",
            "error_message",
            "analytics_error_message",
            "analytics_ai_error_message",
            "language_detection",
            "meeting_edge_payload",
            "analytics_payload",
            "settling_state",
        ):
            assert getattr(transcript, field) is None, field
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[{"start": 0, "end": 1, "text": "new processing result"}],
            run_kind=ProcessingRunKind.FINALIZE,
            source="finalize",
        )
        session.commit()
        session.refresh(transcript)
        assert (transcript.id, transcript.change_sequence) == (transcript_id, 53)
    assert len(stub_celery_dispatch) == 1


def test_reset_publishes_when_projection_was_already_empty(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE transcripts SET text=NULL, segments='[]', transcript_status='pending', "
                "change_sequence=50 WHERE recording_id=:id"
            ),
            {"id": recording_id},
        )
    asyncio.run(_reset(engine, recording_id, threading.Event()))
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None and transcript.change_sequence == 51
        assert session.exec(select(TranscriptUtterance)).first() is None
