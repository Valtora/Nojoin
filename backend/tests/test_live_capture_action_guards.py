"""Notes generation and reprocess refuse a live capture (issue #851).

The web app offers neither while a meeting records, but MCP's
regenerate_notes and reprocess_recording call the same routes. A notes run
marked the recording PROCESSING until #828, and its live transcript kept it
there, so the capture's next segment was refused; a reprocess of a paused
capture ends it before its segments are joined into audio. The MCP tools
pass the route's reason on, where an HTTPException would reach the
assistant only as "Error executing tool".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.transcripts import routes_notes
from backend.core.security import MCP_READ_SCOPE, MCP_WRITE_SCOPE
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.tools_manage import regenerate_notes, reprocess_recording
from backend.models.user import User
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)

pytestmark = pytest.mark.anyio

TIMESTAMP = datetime(2026, 6, 1, 12, 0, 0)
NOTES_TASK = "backend.worker.tasks.generate_notes_task"
NOTES_REFUSAL = "Notes can be generated once the recording has stopped."
REPROCESS_REFUSAL = (
    "This recording is paused, not finished. Stop it before reprocessing."
)

# (status, client_status) for each shape of a capture still in progress. A
# capture paused before captures recorded a client status has none.
LIVE_CAPTURES = pytest.mark.parametrize(
    "capture",
    [("UPLOADING", "RECORDING"), ("PAUSED", "PAUSED"), ("PAUSED", None)],
    ids=["recording", "paused", "paused-without-client-status"],
)

Seed = Callable[..., Awaitable[None]]
State = Callable[[], Awaitable[tuple[Any, ...]]]


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
        ):
            await connection.execute(text(schema))
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def owner(session_maker) -> User:
    async with session_maker() as session:
        user = User(
            username="alice",
            hashed_password="hashed",
            role="user",
            is_active=True,
            settings={},
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture
def seed(session_maker, owner: User) -> Seed:
    """Seed recording "rec-1" with a transcript that has a spoken line."""

    async def _seed(
        *, status: str, client_status: str | None, transcript_status: str
    ) -> None:
        async with session_maker() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO recordings (
                        id, created_at, updated_at, name, public_id,
                        meeting_uid, audio_path, status, client_status,
                        upload_progress, processing_progress, processing_step,
                        is_archived, is_deleted, user_id
                    ) VALUES (
                        1, :ts, :ts, 'Weekly sync', 'rec-1', 'uid-1',
                        '/tmp/a.wav', :status, :client_status, 0, 0,
                        'Recording', 0, 0, :user_id
                    )
                    """
                ),
                {
                    "ts": TIMESTAMP,
                    "status": status,
                    "client_status": client_status,
                    "user_id": owner.id,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO transcripts (
                        id, created_at, updated_at, recording_id, segments,
                        notes_status, transcript_status
                    ) VALUES (
                        1, :ts, :ts, 1, :segments, 'pending', :transcript_status
                    )
                    """
                ),
                {
                    "ts": TIMESTAMP,
                    "segments": (
                        '[{"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00",'
                        ' "text": "We agreed on the budget."}]'
                    ),
                    "transcript_status": transcript_status,
                },
            )
            await session.commit()

    return _seed


@pytest.fixture
def state(session_maker) -> State:
    """Read back what a refused request must not have changed."""

    async def _state() -> tuple[Any, ...]:
        async with session_maker() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT r.status, r.client_status, r.processing_step, "
                        "t.notes_status FROM recordings r "
                        "JOIN transcripts t ON t.recording_id = r.id "
                        "WHERE r.id = 1"
                    )
                )
            ).one()
        return tuple(row)

    return _state


@pytest.fixture
async def client(session_maker, owner: User):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def database():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: owner
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest.fixture(autouse=True)
def llm_configured(monkeypatch) -> None:
    async def resolved(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(missing_configuration_message=lambda: None)

    monkeypatch.setattr(routes_notes, "resolve_llm_config_async", resolved)


@pytest.fixture
def mcp_user(monkeypatch, session_maker, owner: User) -> User:
    """Point the tools' lazily imported session maker at the test database."""
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)
    return owner


def bind_mcp_identity(user: User) -> None:
    # Set inside the test: anyio copies the context when the test starts.
    current_mcp_user.set(user)
    current_mcp_scopes.set(frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}))


@LIVE_CAPTURES
async def test_notes_generation_refuses_a_live_capture(
    client: AsyncClient,
    seed: Seed,
    state: State,
    stub_celery_dispatch: list,
    capture: tuple[str, str | None],
) -> None:
    status, client_status = capture
    await seed(
        status=status, client_status=client_status, transcript_status="processing"
    )

    response = await client.post("/api/v1/transcripts/rec-1/notes/generate")

    assert response.status_code == 409
    assert response.json()["detail"] == NOTES_REFUSAL
    assert stub_celery_dispatch == []
    assert await state() == (status, client_status, "Recording", "pending")


async def test_notes_generation_still_runs_on_a_finished_meeting(
    client: AsyncClient, seed: Seed, stub_celery_dispatch: list
) -> None:
    await seed(status="PROCESSED", client_status="IDLE", transcript_status="completed")

    response = await client.post("/api/v1/transcripts/rec-1/notes/generate")

    assert response.status_code == 200, response.text
    assert response.json()["notes_status"] == "generating"
    assert stub_celery_dispatch == [(NOTES_TASK, [1, None], None)]


@LIVE_CAPTURES
async def test_mcp_regenerate_notes_gives_the_reason_for_a_live_capture(
    mcp_user: User,
    seed: Seed,
    state: State,
    stub_celery_dispatch: list,
    capture: tuple[str, str | None],
) -> None:
    status, client_status = capture
    bind_mcp_identity(mcp_user)
    await seed(
        status=status, client_status=client_status, transcript_status="processing"
    )

    with pytest.raises(ToolError, match=NOTES_REFUSAL):
        await regenerate_notes("rec-1")

    assert stub_celery_dispatch == []
    assert await state() == (status, client_status, "Recording", "pending")


async def test_mcp_regenerate_notes_still_runs_on_a_finished_meeting(
    mcp_user: User, seed: Seed, stub_celery_dispatch: list
) -> None:
    bind_mcp_identity(mcp_user)
    await seed(status="PROCESSED", client_status="IDLE", transcript_status="completed")

    result = await regenerate_notes("rec-1")

    assert result == {
        "recording_id": "rec-1",
        "notes_status": "generating",
        "message": "Note generation started",
    }
    assert stub_celery_dispatch == [(NOTES_TASK, [1, None], None)]


@pytest.mark.parametrize("client_status", ["PAUSED", None])
async def test_mcp_reprocess_gives_the_reason_for_a_paused_capture(
    mcp_user: User,
    seed: Seed,
    state: State,
    stub_celery_dispatch: list,
    client_status: str | None,
) -> None:
    bind_mcp_identity(mcp_user)
    await seed(
        status="PAUSED", client_status=client_status, transcript_status="processing"
    )

    with pytest.raises(ToolError, match=REPROCESS_REFUSAL):
        await reprocess_recording("rec-1", "whisper")

    assert stub_celery_dispatch == []
    assert await state() == ("PAUSED", client_status, "Recording", "pending")


async def test_mcp_reprocess_gives_the_reason_for_a_running_capture(
    mcp_user: User, seed: Seed, stub_celery_dispatch: list
) -> None:
    """Refused before #851 too, but the assistant saw no reason."""
    bind_mcp_identity(mcp_user)
    await seed(
        status="UPLOADING", client_status="RECORDING", transcript_status="processing"
    )

    with pytest.raises(ToolError, match="already uploading or processing"):
        await reprocess_recording("rec-1", "whisper")

    assert stub_celery_dispatch == []


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            ("regenerate_notes", {"recording_id": "rec-1"}, NOTES_REFUSAL),
            id="regenerate_notes",
        ),
        pytest.param(
            (
                "reprocess_recording",
                {"recording_id": "rec-1", "transcription_backend": "whisper"},
                REPROCESS_REFUSAL,
            ),
            id="reprocess_recording",
        ),
    ],
)
async def test_the_reason_reaches_the_mcp_caller(
    mcp_user: User,
    seed: Seed,
    stub_celery_dispatch: list,
    call: tuple[str, dict[str, str], str],
) -> None:
    """Called through the server, as a client's request is. A route's
    HTTPException used to reach the caller as "Error executing tool <name>"
    alone, with the reason kept on the server."""
    from mcp.server.mcpserver.exceptions import UnexpectedToolError

    from backend.mcp_server.server import mcp

    tool, arguments, reason = call
    bind_mcp_identity(mcp_user)
    await seed(status="PAUSED", client_status="PAUSED", transcript_status="processing")

    with pytest.raises(ToolError) as refused:
        await mcp.call_tool(tool, arguments)

    assert not isinstance(refused.value, UnexpectedToolError)
    assert str(refused.value) == f"Error executing tool {tool}: {reason}"
    assert stub_celery_dispatch == []
