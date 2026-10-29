"""The recording consent notice preference and outcome on a real PostgreSQL.

Saving "Don't show this again" writes the caller's own settings row. Each
request here loads the caller through its own session, as production does, so
a later GET or Start sees only what that save committed, not an object the
test still holds. Skips unless NOJOIN_TEST_POSTGRES_URL names a server; CI
sets it for the backend suite.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.api.v1.endpoints.recordings as recordings_module
from backend.api import deps
from backend.api.v1.api import api_router
from backend.api.v1.endpoints import settings as settings_ep
from backend.tests.postgres_setup import migrate_empty_database
from backend.tests.test_timeline_notes_api import OWNER_ID, _session_user
from backend.tests.test_timeline_notes_migration_postgres import _psycopg_url
from backend.utils import recording_storage
from backend.utils.config_manager import ConfigManager

IMPORTED = "rec-imported"


def _seed(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
            " is_active, is_superuser, role, force_password_change, settings)"
            " VALUES (%s, now(), now(), 'mira', 'x', true, false, 'user', false,"
            " '{}'::jsonb)",
            (OWNER_ID,),
        )
        # An imported file, or a recording made before the notice existed: the
        # column is left NULL.
        conn.execute(
            "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
            " meeting_uid, audio_path, status, upload_progress,"
            " processing_progress, is_archived, is_deleted, user_id) VALUES"
            " (10, now(), now(), 'Imported', %s, %s, '/data/imported.wav',"
            " 'PROCESSED', 100, 100, false, false, %s)",
            (IMPORTED, IMPORTED, OWNER_ID),
        )


@pytest.fixture
def database(postgres_test_url: str) -> Iterator[str]:
    """A database of its own, migrated to head and seeded."""
    admin_url = _psycopg_url(postgres_test_url)
    name = f"nojoin_consent_notice_{uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        migrate_empty_database(url, "head")
        _seed(url)
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def client(
    database: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setattr(ConfigManager, "_ensure_dirs_exist", lambda self, cfg: None)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(
        settings_ep, "config_manager", ConfigManager(config_path=str(config_path))
    )
    monkeypatch.setattr(recordings_module, "recordings_root_dir", lambda: tmp_path)
    monkeypatch.setattr(
        recording_storage, "recordings_root_dir", lambda create=True: tmp_path
    )

    engine = create_async_engine(
        make_url(database).set(drivername="postgresql+asyncpg")
    )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    application = FastAPI()
    application.include_router(api_router, prefix="/api/v1")
    application.dependency_overrides[deps.get_db] = _db
    application.dependency_overrides[deps.get_current_user] = _session_user(OWNER_ID)
    application.dependency_overrides[deps.get_current_recording_client_user] = (
        _session_user(OWNER_ID)
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as async_client:
            yield async_client
    finally:
        await engine.dispose()


async def _start_then_discard(client: AsyncClient) -> str | None:
    """Starts a recording as the Start button does, and returns its outcome.

    The recording is then discarded, so the next start is not refused as a
    second live capture.
    """
    started = await client.post("/api/v1/recordings/init")
    assert started.status_code == 200
    recording_id = started.json()["id"]
    outcome = started.json()["consent_notice_outcome"]
    read = await client.get(f"/api/v1/recordings/{recording_id}")
    assert read.status_code == 200
    assert read.json()["consent_notice_outcome"] == outcome
    discarded = await client.post(f"/api/v1/recordings/{recording_id}/discard")
    assert discarded.status_code == 200
    return outcome


async def _save_hidden(client: AsyncClient, hidden: bool) -> None:
    saved = await client.post(
        "/api/v1/settings", json={"hide_recording_consent_notice": hidden}
    )
    assert saved.status_code == 200
    assert saved.json()["hide_recording_consent_notice"] is hidden


@pytest.mark.anyio
async def test_hiding_the_notice_is_reloaded_and_skips_it_until_turned_back_on(
    client: AsyncClient,
) -> None:
    before = await client.get("/api/v1/settings")
    assert before.json().get("hide_recording_consent_notice") is not True
    assert await _start_then_discard(client) == "no_answer"

    await _save_hidden(client, True)
    reloaded = await client.get("/api/v1/settings")
    assert reloaded.status_code == 200
    assert reloaded.json()["hide_recording_consent_notice"] is True
    assert await _start_then_discard(client) == "not_shown"

    # Turned back on under Settings > Recording.
    await _save_hidden(client, False)
    reloaded = await client.get("/api/v1/settings")
    assert reloaded.json()["hide_recording_consent_notice"] is False
    assert await _start_then_discard(client) == "no_answer"


@pytest.mark.anyio
async def test_a_recording_that_never_showed_the_notice_reads_null(
    client: AsyncClient,
) -> None:
    read = await client.get(f"/api/v1/recordings/{IMPORTED}")

    assert read.status_code == 200
    body = read.json()
    assert "consent_notice_outcome" in body
    assert body["consent_notice_outcome"] is None
