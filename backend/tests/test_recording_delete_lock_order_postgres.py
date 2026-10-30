"""Deletes and reprocess lock in Discard's order, on PostgreSQL (issue #816).

Discard takes the recording's audio lock, then Transcript, then the row, and a
live capture's publisher holds Transcript before the row. Permanent and batch
delete locked the row first and reached Transcript only through the cascade;
a reprocess that changed the speaker cap or language flushed the row before
its Transcript update; and an Overwrite restore locked the rows it replaces
before the cascade reached their transcripts. Each could deadlock with a
writer holding Transcript and waiting for the row, and PostgreSQL aborted one
of them. Here a writer holds Transcript, the other side starts and waits, and
the writer then writes the row and commits. Both must succeed.

SQLite has none of these locks, so these run only when
``NOJOIN_TEST_POSTGRES_URL`` names a server. CI sets it for the backend suite.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import threading
import time
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, delete

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.celery_app import celery_app
from backend.core.backup.restore.stages import _restore_preflight_overwrite
from backend.core.backup.restore.state import _RestoreState
from backend.core.db import driver_urls
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User, UserRole
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.recording_audio_sync import lock_recording_audio

SCHEMA = "recording_delete_lock_order_test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def pg(postgres_test_url, tmp_path, monkeypatch, stub_celery_dispatch):
    importlib.import_module("backend.models.registry")
    async_url, sync_url = driver_urls(postgres_test_url)
    sync_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={SCHEMA},public"}
    )
    with sync_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(sync_engine, SCHEMA)
    async_engine = create_async_engine(
        async_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    recordings_dir = tmp_path / "data" / "recordings"
    recordings_dir.mkdir(parents=True)
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path / "data" / "documents"))
    monkeypatch.setattr(celery_app.control, "revoke", lambda task_id, **kw: None)

    with Session(sync_engine) as session:
        user = User(username="owner", hashed_password="x", role=UserRole.OWNER)
        session.add(user)
        session.commit()
        session.refresh(user)

    app = FastAPI()
    app.include_router(api_router)

    async def override_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user

    def add(name: str, *, is_deleted: bool = True) -> tuple[int, str]:
        audio = recordings_dir / f"{name}.wav"
        audio.write_bytes(b"RIFF")
        with Session(sync_engine) as session:
            recording = Recording(
                name=name,
                audio_path=str(audio),
                status=RecordingStatus.PROCESSED,
                is_deleted=is_deleted,
                user_id=user.id,
            )
            session.add(recording)
            session.flush()
            session.add(
                Transcript(
                    recording_id=recording.id,
                    text="Hello there.",
                    segments=[],
                    transcript_status="completed",
                )
            )
            session.commit()
            return recording.id, recording.public_id

    def exists(recording_id: int) -> bool:
        with sync_engine.connect() as connection:
            return bool(
                connection.execute(
                    text("SELECT count(*) FROM recordings WHERE id = :id"),
                    {"id": recording_id},
                ).scalar_one()
            )

    async def request(method: str, url: str, **kwargs):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.request(method, url, **kwargs)

    try:
        yield SimpleNamespace(
            engine=sync_engine,
            add=add,
            exists=exists,
            request=request,
            recordings_dir=recordings_dir,
            dispatched=stub_celery_dispatch,
        )
    finally:
        sync_engine.dispose()


class _Writer:
    """A transaction that holds a lock, waits to be released, then finishes.

    By default it holds Transcript and then writes the row, as a live
    capture's publisher does.
    """

    def __init__(
        self,
        engine: Engine,
        recording_id: int,
        hold: Callable[[Session, int], Any] | None = None,
        finish: Callable[[Session, int], Any] | None = None,
    ) -> None:
        self.reached, self.release = threading.Event(), threading.Event()
        self.errors: list[DBAPIError] = []
        self._hold = hold or (
            lambda session, rid: _load_transcript(session, rid, lock=True)
        )
        self._finish = finish or (
            lambda session, rid: session.execute(
                update(Recording)
                .where(Recording.id == rid)
                .values(processing_step="Publishing live lines")
            )
        )
        self._thread = threading.Thread(
            target=self._run, args=(engine, recording_id), daemon=True
        )

    def _run(self, engine: Engine, recording_id: int) -> None:
        try:
            with Session(engine) as session:
                self._hold(session, recording_id)
                self.reached.set()
                assert self.release.wait(30)
                self._finish(session, recording_id)
                session.commit()
        except DBAPIError as exc:
            self.errors.append(exc)

    async def hold(self) -> None:
        self._thread.start()
        assert await asyncio.to_thread(self.reached.wait, 30)

    async def finish(self) -> None:
        self.release.set()
        await asyncio.to_thread(self._thread.join, 30)
        assert not self._thread.is_alive()


def _wait_for_a_lock_wait(engine: Engine) -> None:
    """Return once another backend waits on a lock; fail after 10 s."""
    deadline = time.monotonic() + 10
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database()"
                    " AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
                )
            ).scalar_one()
            watcher.rollback()
            if waiting:
                return
            time.sleep(0.01)
    raise AssertionError("nothing waited on the writer's lock")


async def _race(pg, writer: _Writer, action: Awaitable[Any]) -> Any:
    """Start ``action`` while ``writer`` holds its lock, then let the writer
    finish. Returns what the action returned."""
    await writer.hold()
    started = asyncio.ensure_future(action)
    await asyncio.to_thread(_wait_for_a_lock_wait, pg.engine)
    await writer.finish()
    result = await started
    assert writer.errors == []
    return result


@pytest.mark.anyio
async def test_a_permanent_delete_waits_for_a_writer_holding_transcript(pg):
    recording_id, public_id = pg.add("binned")
    writer = _Writer(pg.engine, recording_id)

    response = await _race(
        pg, writer, pg.request("DELETE", f"/recordings/{public_id}/permanent")
    )

    assert response.status_code == 200, response.text
    assert not pg.exists(recording_id)
    assert not (pg.recordings_dir / "binned.wav").exists()


@pytest.mark.anyio
async def test_a_batch_delete_waits_for_a_writer_holding_transcript(pg):
    first_id, first = pg.add("first")
    second_id, second = pg.add("second")
    # The batch has locked the first recording when it reaches the second.
    writer = _Writer(pg.engine, second_id)

    response = await _race(
        pg,
        writer,
        pg.request(
            "POST",
            "/recordings/batch/permanent",
            json={"recording_ids": [first, second]},
        ),
    )

    assert response.status_code == 200, response.text
    assert sorted(response.json()["recording_ids"]) == sorted([first, second])
    assert not pg.exists(first_id) and not pg.exists(second_id)


@pytest.mark.anyio
async def test_a_batch_delete_skips_a_recording_deleted_while_it_waited(pg):
    """Another delete, in the same order, holds the second recording and then
    removes it. The batch, waiting at that recording's audio lock, finds the
    row gone and counts it as skipped, rather than failing the whole batch."""
    first_id, first = pg.add("first")
    second_id, second = pg.add("second")

    def hold_like_a_delete(session: Session, rid: int) -> None:
        lock_recording_audio(session, rid)
        _load_transcript(session, rid, lock=True)
        session.get(Recording, rid, with_for_update=True)

    writer = _Writer(
        pg.engine,
        second_id,
        hold=hold_like_a_delete,
        finish=lambda session, rid: session.exec(
            delete(Recording).where(Recording.id == rid)
        ),
    )

    response = await _race(
        pg,
        writer,
        pg.request(
            "POST",
            "/recordings/batch/permanent",
            json={"recording_ids": [first, second]},
        ),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["recording_ids"], body["skipped"]) == ([first], 1)
    assert not pg.exists(first_id) and not pg.exists(second_id)


@pytest.mark.anyio
async def test_a_reprocess_that_changes_the_speaker_cap_waits_for_transcript(pg):
    """The changed cap dirtied the row, and the reset's first query flushed it
    ahead of the Transcript update."""
    recording_id, public_id = pg.add("processed", is_deleted=False)
    writer = _Writer(pg.engine, recording_id)

    response = await _race(
        pg,
        writer,
        pg.request(
            "POST",
            f"/recordings/{public_id}/reprocess",
            json={"transcription_backend": "whisper", "max_speakers": 3},
        ),
    )

    assert response.status_code == 200, response.text
    with Session(pg.engine) as session:
        recording = session.get(Recording, recording_id)
        assert (recording.status, recording.max_speakers) == (
            RecordingStatus.QUEUED,
            3,
        )
    assert [name for name, _, _ in pg.dispatched] == [
        "backend.worker.tasks.process_recording_task"
    ]


@pytest.mark.anyio
async def test_an_overwrite_restore_waits_for_a_writer_holding_transcript(
    pg, tmp_path: Path
):
    """The pre-delete of a recording the archive replaces."""
    recording_id, public_id = pg.add("replaced", is_deleted=False)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipf:
        zipf.writestr("recordings.json", json.dumps([{"public_id": public_id}]))
    state = _RestoreState(
        job_id="lock-order",
        clear_existing=False,
        overwrite_existing=True,
        recordings_dir=pg.recordings_dir,
        config_path=tmp_path / "config.json",
        user_data_dir=tmp_path,
        documents_dir=tmp_path,
        id_map={},
    )

    def restore() -> None:
        with zipfile.ZipFile(archive) as zipf, Session(pg.engine) as session:
            _restore_preflight_overwrite(zipf, session, state)
            session.commit()

    writer = _Writer(pg.engine, recording_id)
    await _race(pg, writer, asyncio.to_thread(restore))

    assert not pg.exists(recording_id)
    assert [Path(audio).name for audio, _ in state.replaced_recording_files] == [
        "replaced.wav"
    ]
