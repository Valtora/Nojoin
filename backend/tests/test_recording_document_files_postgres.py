"""Deleting a recording removes its attached documents' files (PostgreSQL).

Documents are stored flat in the documents directory, apart from the recording's
audio, and their rows go by cascade. Each test attaches a document through the
real upload route, deletes through one of the paths that remove a recording, and
checks the disk. Postgres because two of those paths delete with Core statements
and rely on the foreign keys' ON DELETE CASCADE, which the SQLite suites do not
enforce.

Skips unless NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for the backend
suite.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

from backend.api.deps import get_current_recording_client_user, get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints import documents as documents_ep
from backend.core.db import driver_urls
from backend.models.document import Document
from backend.models.document_page import DocumentPage
from backend.models.recording import Recording, RecordingStatus
from backend.models.user import User, UserRole
from backend.utils import rate_limit
from backend.worker.tasks import documents as document_tasks

SCHEMA = "recording_document_files_test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def harness(postgres_test_url, tmp_path, monkeypatch, stub_celery_dispatch):
    importlib.import_module("backend.models.registry")
    async_url, sync_url = driver_urls(postgres_test_url)
    sync_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={SCHEMA},public"}
    )
    with sync_engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    SQLModel.metadata.create_all(sync_engine)

    # NullPool: the parse test drives the API from more than one event loop.
    async_engine = create_async_engine(
        async_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    documents_dir = tmp_path / "data" / "documents"
    recordings_dir = tmp_path / "data" / "recordings"
    documents_dir.mkdir(parents=True)
    recordings_dir.mkdir(parents=True)
    monkeypatch.setenv("DOCUMENTS_DIR", str(documents_dir))
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))
    monkeypatch.setattr(documents_ep, "DOCUMENTS_DIR", str(documents_dir))

    # The upload concurrency guard falls back to its in-memory counter.
    async def _no_redis():
        return None

    monkeypatch.setattr(rate_limit, "_get_redis", _no_redis)

    with Session(sync_engine) as session:
        admin = User(username="admin", hashed_password="x", role=UserRole.OWNER)
        member = User(username="member", hashed_password="x", role=UserRole.USER)
        session.add_all([admin, member])
        session.commit()
        users = {"admin": admin.id, "member": member.id}

    acting = SimpleNamespace(user=None)

    async def override_db():
        async with maker() as session:
            yield session

    app = FastAPI()
    app.include_router(api_router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: acting.user
    app.dependency_overrides[get_current_recording_client_user] = lambda: acting.user

    def act_as(name: str) -> None:
        with Session(sync_engine) as session:
            acting.user = session.get(User, users[name])

    act_as("admin")
    try:
        yield SimpleNamespace(
            app=app,
            engine=sync_engine,
            users=users,
            act_as=act_as,
            documents_dir=documents_dir,
            recordings_dir=recordings_dir,
            tmp_path=tmp_path,
        )
    finally:
        with sync_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        sync_engine.dispose()


async def _request(harness, method: str, url: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=harness.app), base_url="http://t"
    ) as client:
        return await client.request(method, url, **kwargs)


def _add_recording(
    harness,
    name: str,
    *,
    owner: str = "admin",
    status: RecordingStatus = RecordingStatus.PROCESSED,
    is_deleted: bool = False,
) -> str:
    audio = harness.recordings_dir / f"{name}.wav"
    audio.write_bytes(b"RIFF")
    with Session(harness.engine) as session:
        recording = Recording(
            name=name,
            audio_path=str(audio),
            status=status,
            is_deleted=is_deleted,
            user_id=harness.users[owner],
        )
        session.add(recording)
        session.commit()
        return recording.public_id


async def _attach(harness, public_id: str, filename: str = "agenda.md") -> Path:
    """Upload a document through the real route; return where its file landed."""
    response = await _request(
        harness,
        "POST",
        f"/recordings/{public_id}/documents",
        files={"file": (filename, b"# Agenda\n\nBudget review.\n", "text/markdown")},
        data={"deep_parse": "false"},
    )
    assert response.status_code == 200, response.text
    with Session(harness.engine) as session:
        stored = session.get(Document, response.json()["id"])
        path = Path(stored.file_path)
    assert path.is_file() and path.parent == harness.documents_dir
    return path


def _document_rows(harness) -> int:
    with Session(harness.engine) as session:
        return session.exec(select(func.count()).select_from(Document)).one()


@pytest.mark.anyio
async def test_delete_removes_the_recordings_document_files(harness):
    target = _add_recording(harness, "target")
    bystander = _add_recording(harness, "bystander")
    first = await _attach(harness, target, "agenda.md")
    second = await _attach(harness, target, "minutes.md")
    kept = await _attach(harness, bystander)

    response = await _request(harness, "DELETE", f"/recordings/{target}")

    assert response.status_code == 200, response.text
    assert not first.exists() and not second.exists()
    assert kept.is_file()
    assert _document_rows(harness) == 1


@pytest.mark.anyio
async def test_permanent_delete_removes_the_document_files(harness):
    target = _add_recording(harness, "binned", is_deleted=True)
    document = await _attach(harness, target)

    response = await _request(harness, "DELETE", f"/recordings/{target}/permanent")

    assert response.status_code == 200, response.text
    assert not document.exists()
    assert _document_rows(harness) == 0


@pytest.mark.anyio
async def test_batch_permanent_delete_removes_every_recordings_documents(harness):
    first = _add_recording(harness, "first", is_deleted=True)
    second = _add_recording(harness, "second", is_deleted=True)
    survivor = _add_recording(harness, "survivor", is_deleted=True)
    gone = [await _attach(harness, first), await _attach(harness, second)]
    kept = await _attach(harness, survivor)

    response = await _request(
        harness,
        "POST",
        "/recordings/batch/permanent",
        json={"recording_ids": [first, second]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["count"] == 2
    assert not any(path.exists() for path in gone)
    assert kept.is_file()
    assert _document_rows(harness) == 1


@pytest.mark.anyio
async def test_capture_discard_removes_the_document_files(harness):
    # Discard deletes with Core statements, so the rows go by the FK cascade
    # rather than the ORM relationship.
    target = _add_recording(harness, "live", status=RecordingStatus.UPLOADING)
    document = await _attach(harness, target)

    response = await _request(harness, "POST", f"/recordings/{target}/discard")

    assert response.status_code == 200, response.text
    assert not document.exists()
    assert _document_rows(harness) == 0


@pytest.mark.anyio
async def test_user_delete_removes_their_recordings_document_files(harness):
    harness.act_as("member")
    theirs = [
        await _attach(harness, _add_recording(harness, "one", owner="member")),
        await _attach(harness, _add_recording(harness, "two", owner="member")),
    ]
    harness.act_as("admin")
    mine = await _attach(harness, _add_recording(harness, "mine"))

    response = await _request(harness, "DELETE", f"/users/{harness.users['member']}")

    assert response.status_code == 200, response.text
    assert not any(path.exists() for path in theirs)
    assert mine.is_file()
    assert _document_rows(harness) == 1


@pytest.mark.anyio
async def test_delete_never_unlinks_outside_the_documents_directory(harness, caplog):
    """A stored path that resolves elsewhere is left alone, even via a symlink."""
    target = _add_recording(harness, "target")
    inside = await _attach(harness, target)
    outside = harness.tmp_path / "elsewhere" / "keep.md"
    outside.parent.mkdir()
    outside.write_text("not ours")
    link = harness.documents_dir / "link.md"
    link.symlink_to(outside)
    missing = harness.documents_dir / "already-gone.md"
    with Session(harness.engine) as session:
        recording = session.exec(
            select(Recording).where(Recording.public_id == target)
        ).one()
        for path in (outside, link, missing):
            session.add(
                Document(
                    recording_id=recording.id, title=path.name, file_path=str(path)
                )
            )
        session.commit()

    with caplog.at_level(logging.WARNING):
        response = await _request(harness, "DELETE", f"/recordings/{target}")

    assert response.status_code == 200, response.text
    assert not inside.exists()
    assert outside.read_text() == "not ours"
    assert link.is_symlink()
    assert _document_rows(harness) == 0
    assert str(outside) in caplog.text and str(link) in caplog.text


def test_a_parse_in_flight_stops_cleanly_when_its_recording_is_deleted(
    harness, monkeypatch, caplog
):
    """The parse reads the file and writes only rows; once the row is gone it stops."""
    target = _add_recording(harness, "target")
    path = asyncio.run(_attach(harness, target))
    with Session(harness.engine) as session:
        document_id = session.exec(select(Document.id)).one()

    real_open = document_tasks.open_document

    def open_then_delete(file_path, **kwargs):
        source = real_open(file_path, **kwargs)
        response = asyncio.run(_request(harness, "DELETE", f"/recordings/{target}"))
        assert response.status_code == 200, response.text
        return source

    monkeypatch.setattr(document_tasks, "open_document", open_then_delete)
    task = document_tasks.process_document_task
    task._session = Session(harness.engine)
    try:
        with caplog.at_level(logging.INFO):
            task.run(document_id)
    finally:
        task._session.close()
        task._session = None

    assert not path.exists()
    assert _document_rows(harness) == 0
    with Session(harness.engine) as session:
        assert session.exec(select(func.count()).select_from(DocumentPage)).one() == 0
    assert f"Document {document_id} was deleted while parsing" in caplog.text
