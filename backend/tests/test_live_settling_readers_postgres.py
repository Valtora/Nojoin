"""Live API readers use canonical speech while the compatibility blob is deferred."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.transcripts import routes_notes
from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema

pytestmark = pytest.mark.anyio
POLICY = {"eligible": True, "policy": {"enabled": True}, "phase": "live"}


@pytest.fixture
async def reader_sessions(
    postgres_test_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async_url, sync_url = driver_urls(postgres_test_url)
    schema = "live_settling_readers_test"
    sync_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    with sync_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(sync_engine, schema)
    sync_engine.dispose()
    engine = create_async_engine(
        async_url, connect_args={"server_settings": {"search_path": f"{schema},public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                User(id=1, username="reader", hashed_password="x"),
                User(id=2, username="other", hashed_password="x"),
            ]
        )
        await session.commit()
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
async def reader_client(
    reader_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def database() -> AsyncIterator[AsyncSession]:
        async with reader_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, settings={}, force_password_change=False
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _recording(
    maker: async_sessionmaker[AsyncSession],
    name: str,
    *,
    state: dict[str, Any] | None = None,
    snapshot: str = "",
    owner: int = 1,
) -> int:
    async with maker() as session:
        recording = Recording(
            name=name,
            public_id=name,
            status=RecordingStatus.UPLOADING,
            user_id=owner,
            audio_path=f"{name}.wav",
            pipeline_generation="unified",
        )
        session.add(recording)
        await session.flush()
        session.add(
            Transcript(
                recording_id=recording.id,
                text=snapshot,
                segments=[],
                settling_state=state,
                transcript_status="processing",
            )
        )
        await session.commit()
        assert recording.id is not None
        return recording.id


async def _line(
    maker: async_sessionmaker[AsyncSession],
    recording_id: int,
    text_value: str,
    order: str,
    state: TranscriptUtteranceState = TranscriptUtteranceState.PROVISIONAL,
) -> None:
    async with maker() as session:
        session.add(
            TranscriptUtterance(
                recording_id=recording_id,
                text=text_value,
                sort_key=order,
                start_ms=0,
                end_ms=1000,
                state=state,
                source_kind="live",
            )
        )
        await session.commit()


async def test_library_search_reads_live_text_in_canonical_order_and_keeps_legacy(
    reader_client: AsyncClient, reader_sessions: async_sessionmaker[AsyncSession]
) -> None:
    live = await _recording(reader_sessions, "live", state=POLICY, snapshot="obsolete")
    # Insert in the opposite order: phrase matching follows sort_key, not row id.
    await _line(reader_sessions, live, "budget decision", "b")
    await _line(reader_sessions, live, "Current", "a")
    await _line(
        reader_sessions, live, "superseded", "c", TranscriptUtteranceState.SUPERSEDED
    )
    await _line(reader_sessions, live, "deleted", "d", TranscriptUtteranceState.DELETED)
    await _recording(reader_sessions, "legacy", snapshot="Current budget decision")
    disabled = {**POLICY, "policy": {"enabled": False}}
    off = await _recording(
        reader_sessions, "off", state=disabled, snapshot="Current budget decision"
    )
    await _line(reader_sessions, off, "not the legacy contract", "a")
    other = await _recording(reader_sessions, "private", state=POLICY, owner=2)
    await _line(reader_sessions, other, "Current budget decision", "a")

    response = await reader_client.get(
        "/api/v1/recordings/", params={"q": "CURRENT BUDGET"}
    )

    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()} == {"live", "legacy", "off"}
    for absent in ("obsolete", "superseded", "deleted", "not the legacy contract"):
        response = await reader_client.get("/api/v1/recordings/", params={"q": absent})
        assert response.status_code == 200, response.text
        assert response.json() == []


async def test_notes_dispatch_with_live_canonical_speech_and_empty_snapshot(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list[tuple[str, object, object]],
) -> None:
    recording_id = await _recording(reader_sessions, "notes-live", state=POLICY)
    await _line(reader_sessions, recording_id, "We agreed on the budget.", "a")

    async def llm_config(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(missing_configuration_message=lambda: None)

    monkeypatch.setattr(routes_notes, "resolve_llm_config_async", llm_config)
    response = await reader_client.post("/api/v1/transcripts/notes-live/notes/generate")

    assert response.status_code == 200, response.text
    assert response.json()["notes_status"] == "generating"
    assert stub_celery_dispatch == [
        ("backend.worker.tasks.generate_notes_task", [recording_id, None], None)
    ]
    async with reader_sessions() as session:
        transcript = (
            await session.execute(
                select(Transcript).where(Transcript.recording_id == recording_id)
            )
        ).scalar_one()
        assert transcript.segments == []
        assert transcript.text == ""


@pytest.mark.parametrize("stale_snapshot", [False, True])
async def test_notes_still_refuse_a_live_transcript_without_active_speech(
    reader_client: AsyncClient,
    reader_sessions: async_sessionmaker[AsyncSession],
    stub_celery_dispatch: list[tuple[str, object, object]],
    stale_snapshot: bool,
) -> None:
    recording_id = await _recording(reader_sessions, "silent", state=POLICY)
    if stale_snapshot:
        async with reader_sessions() as session:
            transcript = (
                await session.execute(
                    select(Transcript).where(Transcript.recording_id == recording_id)
                )
            ).scalar_one()
            transcript.segments = [{"text": "Retracted speech"}]
            transcript.text = "Retracted speech"
            await session.commit()
    await _line(
        reader_sessions,
        recording_id,
        "old hypothesis",
        "a",
        TranscriptUtteranceState.SUPERSEDED,
    )

    response = await reader_client.post("/api/v1/transcripts/silent/notes/generate")

    assert response.status_code == 404
    assert response.json()["detail"] == "Transcript not found or empty"
    assert stub_celery_dispatch == []
    # A full live read must not revive that same stale compatibility snapshot.
    response = await reader_client.get("/api/v1/transcripts/silent/utterances")
    assert response.status_code == 200, response.text
    assert response.json()["utterances"] == []
