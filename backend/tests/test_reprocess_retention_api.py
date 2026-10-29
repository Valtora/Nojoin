"""Retained publications survive queue/retry and API reads cannot mix versions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import Engine, delete
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select

from backend.api.v1.endpoints.recordings import (
    helpers,
    routes_actions,
    routes_query,
)
from backend.api.v1.endpoints.transcripts import routes_notes, routes_utterances
from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import RecordingSpeaker
from backend.models.user import User
from backend.tests.test_canonical_publication_fence import SCHEMA, Database
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils.canonical_pipeline import (
    refresh_transcript_projection_from_canonical,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.reprocess_publication import (
    reprocess_publication_pending,
)


def _seed_previous(engine: Engine, recording_id: int) -> int:
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        recording.status = RecordingStatus.PROCESSED
        recording.name = "Previous generated title"
        transcript = _load_transcript(session, recording_id, lock=True)
        transcript.complete_transcription()
        transcript.notes = "Previous generated notes"
        transcript.notes_status = "completed"
        transcript.meeting_edge_focus = "User-entered focus"
        transcript.meeting_edge_payload = {"history": [{"previous": True}]}
        transcript.meeting_edge_status = "ready"
        speaker = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label="SPEAKER_00",
            name="Previous person",
        )
        session.add(speaker)
        session.flush()
        for line in session.exec(select(TranscriptUtterance)).all():
            line.recording_speaker_id = speaker.id
            line.speaker_label = "SPEAKER_00"
            session.add(line)
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        return int(speaker.id)


async def _reset(engine: Engine, recording_id: int) -> None:
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": SCHEMA}},
    )
    try:
        async with AsyncSession(async_engine, expire_on_commit=False) as session:
            await helpers._reset_generated_recording_state(session, recording_id)
            recording = await session.get(Recording, recording_id)
            recording.status = RecordingStatus.QUEUED
            session.add(recording)
            await session.commit()
    finally:
        await async_engine.dispose()


async def _assert_empty_api_publication(
    engine: Engine, recording_id: int, *, pending: bool
) -> None:
    """Read both public endpoints with a fresh session, including in-flight states."""
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg"),
        connect_args={"server_settings": {"search_path": SCHEMA}},
    )
    try:
        async with AsyncSession(async_engine, expire_on_commit=False) as session:
            recording = await session.get(Recording, recording_id)
            owner = User(
                id=recording.user_id, username="fence-owner", hashed_password="x"
            )
            detail = await routes_query.get_recording(
                recording.public_id,
                Response(),
                if_none_match=None,
                db=session,
                current_user=owner,
            )
            utterances = await routes_utterances.get_transcript_utterances(
                recording.public_id, after_revision=None, db=session, current_user=owner
            )
            assert detail.previous_publication_available is pending
            assert detail.reprocess_publication_complete is False
            assert detail.transcript.text == ""
            assert detail.transcript.segments == []
            assert utterances.utterances == []
            assert detail.transcript.notes == "Previous generated notes"
            assert detail.transcript.meeting_edge_focus == "User-entered focus"
            assert detail.transcript.meeting_edge_payload == {
                "history": [{"previous": True}]
            }
    finally:
        await async_engine.dispose()


@pytest.mark.parametrize("source", ["import", "browser"])
@pytest.mark.parametrize("empty", [False, True])
def test_completed_and_failed_retry_retain_products_including_empty_publications(
    publication_database: Database, source: str, empty: bool
) -> None:
    engine, recording_id, _ = publication_database
    speaker_id = _seed_previous(engine, recording_id)
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id, lock=True)
        transcript.settling_state = (
            {"eligible": True, "policy": {"enabled": False}, "sealed": True}
            if source == "browser"
            else None
        )
        if empty:
            # A published no-speech result has no canonical lines either. A
            # projection-only clear would leave the API reading old speech.
            session.execute(
                delete(TranscriptUtterance).where(
                    TranscriptUtterance.recording_id == recording_id
                )
            )
            transcript.text, transcript.segments = "", []
        session.commit()
        session.refresh(transcript)
        previous = transcript.model_dump()
    if empty:
        asyncio.run(_assert_empty_api_publication(engine, recording_id, pending=False))
    asyncio.run(_reset(engine, recording_id))
    if empty:
        asyncio.run(_assert_empty_api_publication(engine, recording_id, pending=True))
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id, lock=True)
        assert reprocess_publication_pending(transcript)
        assert transcript.transcript_status == "pending"
        for field in (
            "text",
            "segments",
            "notes",
            "notes_status",
            "meeting_edge_focus",
            "meeting_edge_payload",
            "meeting_edge_status",
        ):
            assert getattr(transcript, field) == previous[field], field
        transcript.fail_transcription("Candidate failed before publication")
        recording = session.get(Recording, recording_id)
        recording.status = RecordingStatus.ERROR
        session.commit()
    if empty:
        asyncio.run(_assert_empty_api_publication(engine, recording_id, pending=True))
    # A new session represents the restarted worker/API, not an in-memory flag.
    asyncio.run(_reset(engine, recording_id))
    if empty:
        asyncio.run(_assert_empty_api_publication(engine, recording_id, pending=True))
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        assert reprocess_publication_pending(transcript)
        assert transcript.error_message is None
        assert session.get(RecordingSpeaker, speaker_id) is not None
        assert session.get(Recording, recording_id).name == "Previous generated title"
        assert transcript.notes == previous["notes"]
        assert transcript.meeting_edge_payload == previous["meeting_edge_payload"]
        assert transcript.meeting_edge_focus == previous["meeting_edge_focus"]


@pytest.mark.parametrize("state", [None, {"reprocess_pending": "true"}])
def test_first_processing_keeps_draft_content_hidden(
    publication_database: Database, state: dict[str, str] | None
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id, lock=True)
        transcript.transcript_status = "processing"
        transcript.settling_state = state
        recording = session.get(Recording, recording_id)
        recording.status = RecordingStatus.QUEUED
        public_id, user_id = recording.public_id, recording.user_id
        session.commit()

    async def read() -> Any:
        async_engine = create_async_engine(
            engine.url.set(drivername="postgresql+asyncpg"),
            connect_args={"server_settings": {"search_path": SCHEMA}},
        )
        try:
            async with AsyncSession(async_engine, expire_on_commit=False) as session:
                return await routes_query.get_recording(
                    public_id,
                    Response(),
                    if_none_match=None,
                    db=session,
                    current_user=User(
                        id=user_id, username="fence-owner", hashed_password="x"
                    ),
                )
        finally:
            await async_engine.dispose()

    response = asyncio.run(read())
    assert response.previous_publication_available is False
    assert response.reprocess_publication_complete is False
    assert response.transcript.text == ""
    assert response.transcript.segments == []


@pytest.mark.parametrize("route", ["notes", "speakers"])
@pytest.mark.usefixtures("stub_celery_dispatch")
def test_notes_are_independent_while_speaker_inference_waits_for_reprocess(
    publication_database: Database, route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, recording_id, _ = publication_database
    _seed_previous(engine, recording_id)
    asyncio.run(_reset(engine, recording_id))

    async def configured(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(missing_configuration_message=lambda: None)

    monkeypatch.setattr(routes_notes, "resolve_llm_config_async", configured)

    async def start() -> int:
        async_engine = create_async_engine(
            engine.url.set(drivername="postgresql+asyncpg"),
            connect_args={"server_settings": {"search_path": SCHEMA}},
        )
        try:
            async with AsyncSession(async_engine, expire_on_commit=False) as session:
                recording = await session.get(Recording, recording_id)
                owner = User(
                    id=recording.user_id, username="fence-owner", hashed_password="x"
                )
                if route == "notes":
                    # MCP's regenerate_notes calls this same route.
                    result = await routes_notes.generate_notes(
                        recording.public_id, None, db=session, current_user=owner
                    )
                    assert result["notes_status"] == "generating"
                    return 200
                with pytest.raises(HTTPException) as refused:
                    await routes_actions.infer_speakers_for_recording(
                        recording.public_id, db=session, current_user=owner
                    )
                return refused.value.status_code
        finally:
            await async_engine.dispose()

    assert asyncio.run(start()) == (200 if route == "notes" else 409)
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        transcript = _load_transcript(session, recording_id)
        assert recording.status == RecordingStatus.QUEUED
        assert transcript.notes_status == (
            "generating" if route == "notes" else "completed"
        )
        assert transcript.notes == "Previous generated notes"


def test_the_polled_read_leaves_a_retained_transcript_to_the_utterances_read(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    _seed_previous(engine, recording_id)
    asyncio.run(_reset(engine, recording_id))

    async def read() -> tuple[Any, Any]:
        async_engine = create_async_engine(
            engine.url.set(drivername="postgresql+asyncpg"),
            connect_args={"server_settings": {"search_path": SCHEMA}},
        )
        try:
            async with AsyncSession(async_engine, expire_on_commit=False) as session:
                recording = await session.get(Recording, recording_id)
                owner = User(
                    id=recording.user_id, username="fence-owner", hashed_password="x"
                )
                detail = await routes_query.get_recording(
                    recording.public_id,
                    Response(),
                    if_none_match=None,
                    db=session,
                    current_user=owner,
                )
                utterances = await routes_utterances.get_transcript_utterances(
                    recording.public_id,
                    after_revision=None,
                    db=session,
                    current_user=owner,
                )
                return detail, utterances
        finally:
            await async_engine.dispose()

    detail, utterances = asyncio.run(read())
    # Polled every few seconds while in flight: a long meeting's transcript
    # would be megabytes on each poll.
    assert detail.previous_publication_available is True
    assert (detail.transcript.text, detail.transcript.segments) == ("", [])
    assert sorted(line.text for line in utterances.utterances) == [
        "first",
        "last",
        "middle",
    ]
