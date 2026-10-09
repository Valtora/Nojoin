"""Chunked-import finalize's claim against a real PostgreSQL.

SQLite serialises writers, so it cannot show what concurrent finalize calls
do to row locks and the connection pool. The claim and its races are the
subject, so the extraction is a stub and ffmpeg is not needed (real
extraction is covered in ``test_import_audio`` and ``test_reprocess``). Skips
unless NOJOIN_TEST_POSTGRES_URL names a server (see ``postgres_test_url``).
"""

from __future__ import annotations

import asyncio
import importlib
import os
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
from backend.tests.test_reprocess import build_test_user
from backend.utils.import_audio import AudioExtractionError, KeptAudio
from backend.utils.time import utc_now

SCHEMA = "import_finalize_test"
FINALIZE_CALLS = 5


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


def _stub_keep(path: str) -> KeptAudio:
    """Stand-in for ``keep_imported_audio``: the same file contract (named
    after the upload, which is deleted), no ffmpeg."""
    source = Path(path)
    if not source.exists():
        raise AudioExtractionError(f"{path} is gone")
    kept = source.with_name(f"{source.stem}.{uuid.uuid4().hex}.m4a")
    kept.write_bytes(source.read_bytes())
    os.remove(source)
    return KeptAudio(str(kept))


def _patch_keep(monkeypatch, keep) -> None:
    from backend.api.v1.endpoints.recordings import routes_import_upload

    monkeypatch.setattr(routes_import_upload, "keep_imported_audio", keep)


async def _start(pg_client, monkeypatch, tmp_path: Path):
    """An import with its one part uploaded; returns its id, the recorded
    dispatches and the recordings directory."""
    source = tmp_path / "screen.mkv"
    source.write_bytes(b"\x1a\x45\xdf\xa3 not really a matroska file")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))
    dispatches = _record_dispatches(monkeypatch)
    recording_id = await _start_import(pg_client, source)
    return recording_id, dispatches, recordings_dir


@pytest.mark.anyio
async def test_concurrent_finalizes_extract_once_and_hold_nothing_meanwhile(
    pg, pg_client, monkeypatch, tmp_path: Path
) -> None:
    """One call claims the import; the others answer 409 at once, and no
    connection or row lock is held while the audio is extracted."""
    engine, maker = pg
    recording_id, dispatches, _ = await _start(pg_client, monkeypatch, tmp_path)
    extractions: list[str] = []

    def slow_keep(path: str) -> KeptAudio:
        extractions.append(path)
        time.sleep(2.0)
        return _stub_keep(path)

    _patch_keep(monkeypatch, slow_keep)

    calls = [
        asyncio.create_task(_finalize(pg_client, recording_id))
        for _ in range(FINALIZE_CALLS)
    ]
    await asyncio.sleep(1.0)
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
    """Hold the first extraction until ``release`` is set: before it reads
    the upload, or once the kept audio is written. Later ones run unheld."""
    entered = threading.Event()
    release = threading.Event()

    def keep(path: str) -> KeptAudio:
        if entered.is_set():
            return _stub_keep(path)
        if not after_keep:
            entered.set()
            release.wait(30)
            return _stub_keep(path)
        kept = _stub_keep(path)
        entered.set()
        release.wait(30)
        return kept

    _patch_keep(monkeypatch, keep)
    return entered, release


async def _until(event: threading.Event) -> None:
    for _ in range(600):
        if event.is_set():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the extraction never started")


async def _finalize_held(pg_client, monkeypatch, tmp_path: Path, *, after_keep: bool):
    """Start an import's finalize and return once its extraction is held."""
    recording_id, dispatches, recordings_dir = await _start(
        pg_client, monkeypatch, tmp_path
    )
    entered, release = _hold_extraction(monkeypatch, after_keep=after_keep)
    call = asyncio.create_task(_finalize(pg_client, recording_id))
    await _until(entered)
    return recording_id, call, release, dispatches, recordings_dir


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


async def _stored_audio(engine) -> Path:
    async with engine.connect() as conn:
        return Path(
            (await conn.execute(text("SELECT audio_path FROM recordings"))).scalar_one()
        )


@pytest.mark.anyio
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
@pytest.mark.parametrize("after_keep", [False, True], ids=["before-keep", "after-keep"])
async def test_deleting_an_import_being_finalized_leaves_nothing_behind(
    pg, pg_client, monkeypatch, tmp_path: Path, after_keep: bool
) -> None:
    """Finalize answers that the import is gone, and nothing it or the delete
    left remains; nothing is queued."""
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
@pytest.mark.parametrize("after_keep", [False, True], ids=["before-keep", "after-keep"])
async def test_a_finalize_whose_claim_was_taken_over_leaves_the_new_owner_alone(
    pg, pg_client, monkeypatch, tmp_path: Path, after_keep: bool
) -> None:
    """Its claim looks stale by the database clock (a suspended host, a clock
    step), so another finalize takes it over and queues the import. When the
    first one resumes, it removes only its own files and answers as a
    repeated call would."""
    engine, _ = pg
    recording_id, first, release, dispatches, recordings_dir = await _finalize_held(
        pg_client, monkeypatch, tmp_path, after_keep=after_keep
    )
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE recordings SET updated_at = :t"),
            {"t": utc_now() - timedelta(hours=3)},
        )

    second = await _finalize(pg_client, recording_id)
    release.set()
    first_answer = await first

    assert second.status_code == 200, second.text
    assert second.json()["status"] == "QUEUED"
    stored = await _stored_audio(engine)
    assert stored.exists()
    assert [path for path in recordings_dir.iterdir() if path.is_file()] == [stored]
    assert first_answer.status_code == 200, first_answer.text
    assert first_answer.json()["status"] == "QUEUED"
    assert len(dispatches) == 1


async def _backdate_claim(engine) -> None:
    """Make the claim look stale by the database clock, as after a suspended
    host or a clock step."""
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE recordings SET updated_at = :t"),
            {"t": utc_now() - timedelta(hours=3)},
        )


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["pause", "segment"])
async def test_a_stale_claim_still_refuses_pause_and_segment_upload(
    pg, pg_client, monkeypatch, tmp_path: Path, route: str
) -> None:
    """A stale claim's finalize may still be running: pausing or adding a
    part would throw away its good extraction."""
    engine, _ = pg
    recording_id, call, release, dispatches, _ = await _finalize_held(
        pg_client, monkeypatch, tmp_path, after_keep=True
    )
    await _backdate_claim(engine)

    if route == "pause":
        refused = await pg_client.post(f"/api/v1/recordings/{recording_id}/pause")
    else:
        refused = await pg_client.post(
            "/api/v1/recordings/import/chunked/segment",
            params={"recording_id": recording_id, "sequence": 0},
            files={"file": ("0.part", b"resent", "application/octet-stream")},
        )
    release.set()
    answer = await call

    assert refused.status_code == 409, refused.text
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "QUEUED"
    assert (await _stored_audio(engine)).exists()
    assert len(dispatches) == 1


@pytest.mark.anyio
async def test_a_finalize_that_waited_on_a_discard_answers_not_found(
    pg, pg_client, monkeypatch, tmp_path: Path
) -> None:
    """A finalize arriving while a discard holds the row waits for it, then
    finds the import gone, as a repeated call would."""
    from backend.api.v1.endpoints import recordings as recordings_module

    recording_id, dispatches, recordings_dir = await _start(
        pg_client, monkeypatch, tmp_path
    )
    _patch_keep(monkeypatch, _stub_keep)
    lock = recordings_module._lock_unless_finalizing_import
    locked = threading.Event()

    async def lock_and_linger(db, recording, **kwargs):
        await lock(db, recording, **kwargs)
        locked.set()
        await asyncio.sleep(1.0)

    monkeypatch.setattr(
        recordings_module, "_lock_unless_finalizing_import", lock_and_linger
    )
    discard = asyncio.create_task(
        pg_client.post(f"/api/v1/recordings/{recording_id}/discard")
    )
    await _until(locked)
    finalize = await _finalize(pg_client, recording_id)

    assert (await discard).status_code == 200
    assert finalize.status_code == 404, finalize.text
    assert dispatches == []
    assert _files(recordings_dir) == []


@pytest.mark.anyio
async def test_a_failed_duration_probe_does_not_fail_the_import(
    pg, pg_client, monkeypatch, tmp_path: Path
) -> None:
    """The duration is optional: ffprobe failing to start (a fork error)
    leaves a good extraction stored, as on /import and /upload."""
    from backend.api.v1.endpoints.recordings import routes_import_upload

    engine, _ = pg
    recording_id, dispatches, _ = await _start(pg_client, monkeypatch, tmp_path)
    _patch_keep(monkeypatch, _stub_keep)

    def duration(path, timeout=None):
        raise OSError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(routes_import_upload, "get_audio_duration", duration)
    answer = await _finalize(pg_client, recording_id)

    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "QUEUED"
    assert (await _stored_audio(engine)).exists()
    assert len(dispatches) == 1


@pytest.mark.anyio
async def test_a_failure_after_the_audio_is_kept_leaves_no_file(
    pg, pg_client, monkeypatch, tmp_path: Path
) -> None:
    """The kept audio is known only inside the extraction, so it is removed
    there; nothing is left for the failed import's deletion to miss."""
    from backend.api.v1.endpoints.recordings import routes_import_upload

    recording_id, dispatches, recordings_dir = await _start(
        pg_client, monkeypatch, tmp_path
    )
    _patch_keep(monkeypatch, _stub_keep)

    def duration(path, timeout=None):
        raise TypeError("unexpected")

    monkeypatch.setattr(routes_import_upload, "get_audio_duration", duration)
    answer = await _finalize(pg_client, recording_id)
    root_files = [path for path in recordings_dir.iterdir() if path.is_file()]
    deleted = await pg_client.delete(f"/api/v1/recordings/{recording_id}")

    assert answer.status_code == 500
    assert root_files == []
    assert deleted.status_code == 200
    assert dispatches == []


@pytest.mark.anyio
async def test_a_finalize_that_outlives_the_stale_age_untaken_still_stores(
    pg, pg_client, monkeypatch, tmp_path: Path
) -> None:
    """Staleness only lets another finalize take the claim over. With none
    doing so, the owner keeps its claim and its good extraction."""
    from backend.utils import recording_storage

    engine, _ = pg
    monkeypatch.setattr(
        recording_storage, "FINALIZE_CLAIM_STALE_AFTER", timedelta(seconds=1)
    )
    recording_id, call, release, dispatches, _ = await _finalize_held(
        pg_client, monkeypatch, tmp_path, after_keep=True
    )
    await asyncio.sleep(1.2)
    release.set()
    answer = await call

    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "QUEUED"
    assert len(dispatches) == 1
    assert (await _stored_audio(engine)).exists()


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["delete", "discard"])
async def test_removing_an_import_a_dead_finalize_held_removes_what_it_wrote(
    pg, pg_client, monkeypatch, tmp_path: Path, route: str
) -> None:
    """A finalize that died leaves its reassembled upload and partial audio,
    named after the import's audio path but not at it."""
    engine, _ = pg
    recording_id, _, recordings_dir = await _start(pg_client, monkeypatch, tmp_path)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE recordings SET processing_step = 'Finalizing import',"
                " celery_task_id = 'import-finalize:0123abcd', updated_at = :t"
            ),
            {"t": utc_now() - timedelta(hours=3)},
        )
    upload = await _stored_audio(engine)
    leftovers = [
        upload.with_name(f"{upload.stem}.0123abcd.mkv"),
        upload.with_name(f"{upload.stem}.0123abcd.4567ef.flac"),
    ]
    for leftover in leftovers:
        leftover.write_bytes(b"left behind")

    if route == "delete":
        removed = await pg_client.delete(f"/api/v1/recordings/{recording_id}")
    else:
        removed = await pg_client.post(f"/api/v1/recordings/{recording_id}/discard")

    assert removed.status_code == 200, removed.text
    assert _files(recordings_dir) == []
