"""Reprocess and retry reset generated transcript products in place."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select

from backend.api.v1.endpoints.recordings import helpers, routes_actions
from backend.models.notes_template import NotesTemplate
from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording, RecordingStatus
from backend.models.user import User
from backend.tests.test_canonical_publication_fence import (
    SCHEMA,
    Database,
)
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils.canonical_pipeline.constants import _load_transcript


async def _reset(engine: Engine, recording_id: int) -> None:
    """Run the generated-state reset on its own committed transaction."""
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": SCHEMA}},
    )
    try:
        async with AsyncSession(async_engine, expire_on_commit=False) as session:
            await helpers._reset_generated_recording_state(session, recording_id)
            await session.commit()
    finally:
        await async_engine.dispose()


async def _reprocess(engine: Engine, recording_id: int) -> None:
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": SCHEMA}},
    )
    try:
        async with AsyncSession(async_engine, expire_on_commit=False) as session:
            recording = await session.get(Recording, recording_id)
            assert recording is not None
            await routes_actions.reprocess_recording(
                recording.public_id,
                routes_actions.ReprocessRequest(transcription_backend="whisper"),
                db=session,
                current_user=User(
                    id=recording.user_id, username="fence-owner", hashed_password="x"
                ),
            )
    finally:
        await async_engine.dispose()


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
        return transcript.id


@pytest.mark.parametrize("status", [RecordingStatus.PROCESSED, RecordingStatus.ERROR])
def test_reprocess_and_retry_reset_generated_products_in_place(
    publication_database: Database,
    stub_celery_dispatch: Any,
    monkeypatch: pytest.MonkeyPatch,
    status: RecordingStatus,
) -> None:
    engine, recording_id, _ = publication_database
    transcript_id = _seed_generated_products(engine, recording_id, status)
    monkeypatch.setattr(routes_actions, "_recording_has_audio", lambda recording: True)
    asyncio.run(_reprocess(engine, recording_id))
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None and transcript.id == transcript_id
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
        assert session.exec(select(TranscriptUtterance)).first() is None
    assert len(stub_celery_dispatch) == 1
