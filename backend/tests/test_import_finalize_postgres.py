"""Chunked-import finalize against a real PostgreSQL.

SQLite serialises writers, so it cannot show what concurrent finalize calls
do to row locks and the connection pool. Skips unless NOJOIN_TEST_POSTGRES_URL
names a server (see ``postgres_test_url``) and ffmpeg is installed.
"""

from __future__ import annotations

import asyncio
import importlib
import shutil
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from backend.api.deps import (
    get_current_recording_client_user,
    get_current_user,
    get_db,
)
from backend.api.v1.api import api_router
from backend.tests.test_reprocess import _screen_recording, build_test_user

SCHEMA = "import_finalize_test"
FINALIZE_CALLS = 5
EXTRACTION_SECONDS = 2.0


@pytest.fixture
async def pg(postgres_test_url: str):
    importlib.import_module("backend.models.registry")
    url = postgres_test_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    setup = create_async_engine(url)
    async with setup.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.execute(
            text(
                "INSERT INTO users (id, created_at, updated_at, username,"
                " hashed_password, is_active, is_superuser, force_password_change,"
                " role, token_version, settings, has_seen_demo_recording)"
                " VALUES (1, now(), now(), 'alice', 'x', true, false, false,"
                " 'user', 0, '{}', false)"
            )
        )
    try:
        yield engine, sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with setup.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        await setup.dispose()


@pytest.fixture
async def pg_client(pg):
    _, maker = pg
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = build_test_user
    app.dependency_overrides[get_current_recording_client_user] = build_test_user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", timeout=120
    ) as client:
        yield client


def _record_dispatches(monkeypatch) -> list:
    """Capture process_recording dispatches, each task with its own id (task
    ownership rows are unique per id on PostgreSQL)."""
    from backend.api.v1.endpoints import recordings as recordings_module

    dispatched: list = []

    class _Task:
        def __init__(self) -> None:
            self.id = str(uuid.uuid4())

    def send_task(name, args=None, kwargs=None, **other_kwargs):
        if name == "backend.worker.tasks.process_recording_task":
            dispatched.append(tuple(args or []))
        return _Task()

    monkeypatch.setattr(recordings_module.celery_app, "send_task", send_task)
    return dispatched


async def _start_import(client: AsyncClient, source: Path) -> str:
    init = await client.post(
        "/api/v1/recordings/import/chunked/init", params={"filename": source.name}
    )
    assert init.status_code == 200, init.text
    recording_id = init.json()["id"]
    segment = await client.post(
        "/api/v1/recordings/import/chunked/segment",
        params={"recording_id": recording_id, "sequence": 0},
        files={"file": ("0.part", source.read_bytes(), "application/octet-stream")},
    )
    assert segment.status_code == 200, segment.text
    return recording_id


def _finalize(client: AsyncClient, recording_id: str):
    return client.post(
        "/api/v1/recordings/import/chunked/finalize",
        params={"recording_id": recording_id},
    )


async def _connections_in_transaction(maker) -> int:
    async with maker() as session:
        return (
            await session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database()"
                    " AND pid <> pg_backend_pid()"
                    " AND state LIKE 'idle in transaction%'"
                )
            )
        ).scalar_one()


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
async def test_concurrent_finalizes_extract_once_and_hold_nothing_meanwhile(
    pg, pg_client, monkeypatch, tmp_path: Path
) -> None:
    """One call claims the import; the others answer 409 at once, and no
    connection or row lock is held while the audio is extracted."""
    from backend.api.v1.endpoints.recordings import routes_import_upload
    from backend.utils import import_audio

    engine, maker = pg
    source = tmp_path / "screen.mkv"
    _screen_recording(source, ["-c:a", "aac"])
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))
    dispatches = _record_dispatches(monkeypatch)
    recording_id = await _start_import(pg_client, source)
    extractions: list[str] = []
    real_keep = import_audio.keep_imported_audio

    def slow_keep(path: str):
        extractions.append(path)
        time.sleep(EXTRACTION_SECONDS)
        return real_keep(path)

    monkeypatch.setattr(routes_import_upload, "keep_imported_audio", slow_keep)

    calls = [
        asyncio.create_task(_finalize(pg_client, recording_id))
        for _ in range(FINALIZE_CALLS)
    ]
    await asyncio.sleep(EXTRACTION_SECONDS / 2)
    in_transaction = await _connections_in_transaction(maker)
    checked_out = engine.pool.checkedout()
    answers = await asyncio.gather(*calls)

    assert in_transaction == 0
    assert checked_out == 0
    statuses = sorted(answer.status_code for answer in answers)
    assert statuses == [200] + [409] * (FINALIZE_CALLS - 1)
    for answer in answers:
        if answer.status_code == 409:
            assert answer.json()["detail"]["code"] == "import_finalizing"
    assert len(extractions) == 1
    assert len(dispatches) == 1

    again = await _finalize(pg_client, recording_id)
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "QUEUED"
    assert len(extractions) == 1


def _hold_extraction(monkeypatch, *, after_keep: bool):
    """Hold the first extraction until ``release`` is set: before ffmpeg runs,
    or once the kept audio is written. Later extractions run unheld."""
    from backend.api.v1.endpoints.recordings import routes_import_upload
    from backend.utils import import_audio

    entered = threading.Event()
    release = threading.Event()
    real_keep = import_audio.keep_imported_audio

    def keep(path: str):
        if entered.is_set():
            return real_keep(path)
        if not after_keep:
            entered.set()
            release.wait(30)
            return real_keep(path)
        kept = real_keep(path)
        entered.set()
        release.wait(30)
        return kept

    monkeypatch.setattr(routes_import_upload, "keep_imported_audio", keep)
    return entered, release


async def _until(event: threading.Event) -> None:
    for _ in range(600):
        if event.is_set():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the extraction never started")


async def _finalize_held(pg_client, monkeypatch, tmp_path: Path, *, after_keep: bool):
    """Start an import's finalize and return once its extraction is held."""
    source = tmp_path / "screen.mkv"
    _screen_recording(source, ["-c:a", "aac"])
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))
    dispatches = _record_dispatches(monkeypatch)
    recording_id = await _start_import(pg_client, source)
    entered, release = _hold_extraction(monkeypatch, after_keep=after_keep)
    call = asyncio.create_task(_finalize(pg_client, recording_id))
    await _until(entered)
    return recording_id, call, release, dispatches, recordings_dir


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
async def test_an_import_being_finalized_cannot_be_reprocessed(
    pg, pg_client, monkeypatch, tmp_path: Path
) -> None:
    """The claimed import still reads as uploading, so reprocess refuses it
    and processing is dispatched once, by finalize."""
    recording_id, call, release, dispatches, _ = await _finalize_held(
        pg_client, monkeypatch, tmp_path, after_keep=False
    )

    shown = await pg_client.get(f"/api/v1/recordings/{recording_id}")
    reprocess = await pg_client.post(
        f"/api/v1/recordings/{recording_id}/reprocess",
        json={"transcription_backend": "whisper"},
    )
    release.set()
    answer = await call

    assert reprocess.status_code == 400, reprocess.text
    assert shown.json()["status"] == "UPLOADING"
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "QUEUED"
    assert len(dispatches) == 1


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
async def test_an_import_being_finalized_cannot_be_discarded_or_added_to(
    pg, pg_client, monkeypatch, tmp_path: Path
) -> None:
    """Discard, pause and another segment would pull the files or the row
    from under the extraction."""
    recording_id, call, release, dispatches, _ = await _finalize_held(
        pg_client, monkeypatch, tmp_path, after_keep=False
    )

    segment = await pg_client.post(
        "/api/v1/recordings/import/chunked/segment",
        params={"recording_id": recording_id, "sequence": 1},
        files={"file": ("1.part", b"late", "application/octet-stream")},
    )
    discard = await pg_client.post(f"/api/v1/recordings/{recording_id}/discard")
    pause = await pg_client.post(f"/api/v1/recordings/{recording_id}/pause")
    release.set()
    answer = await call

    assert [segment.status_code, discard.status_code, pause.status_code] == [
        409,
        409,
        409,
    ]
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "QUEUED"
    assert len(dispatches) == 1


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
@pytest.mark.parametrize("after_keep", [False, True], ids=["mid-probe", "after-keep"])
async def test_deleting_an_import_being_finalized_leaves_nothing_behind(
    pg, pg_client, monkeypatch, tmp_path: Path, after_keep: bool
) -> None:
    """Finalize answers that the import is gone, removes what it extracted and
    queues nothing."""
    from backend.api.v1.endpoints.recordings import routes_import_upload

    recording_id, call, release, dispatches, recordings_dir = await _finalize_held(
        pg_client, monkeypatch, tmp_path, after_keep=after_keep
    )

    deleted = await pg_client.delete(f"/api/v1/recordings/{recording_id}")
    release.set()
    answer = await call

    assert deleted.status_code == 200, deleted.text
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"] == routes_import_upload.FINALIZE_CLAIM_LOST_DETAIL
    assert dispatches == []
    assert _files(recordings_dir) == []


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
async def test_a_finalize_that_outlived_its_claim_does_not_store(
    pg, pg_client, monkeypatch, tmp_path: Path
) -> None:
    """A finalize that takes over a stale claim owns the import; the one it
    took over from keeps nothing when it finally returns."""
    from backend.utils import recording_storage

    engine, _ = pg
    monkeypatch.setattr(
        recording_storage, "FINALIZE_CLAIM_STALE_AFTER", timedelta(seconds=1)
    )
    recording_id, first, release, dispatches, recordings_dir = await _finalize_held(
        pg_client, monkeypatch, tmp_path, after_keep=True
    )
    await asyncio.sleep(1.2)

    second = await _finalize(pg_client, recording_id)
    release.set()
    first_answer = await first

    assert second.status_code == 200, second.text
    assert second.json()["status"] == "QUEUED"
    assert first_answer.status_code == 409, first_answer.text
    assert len(dispatches) == 1
    async with engine.connect() as conn:
        audio_path = (
            await conn.execute(text("SELECT audio_path FROM recordings"))
        ).scalar_one()
    stored = [path for path in recordings_dir.iterdir() if path.is_file()]
    assert stored == [Path(audio_path)]
