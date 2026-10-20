"""Document files are removed with their rows, and never orphaned by a race (PostgreSQL).

Documents are stored flat in the documents directory, apart from the recording's
audio, and their rows go by cascade. Most tests attach a document through the
real upload route, delete through one of the paths that remove a recording or a
document, and check the disk. Postgres because two of those paths delete with
Core statements and rely on the foreign keys' ON DELETE CASCADE, which the SQLite
suites do not enforce, and because the race tests hold one side's transaction
open at a known point and wait until the other is blocked on its lock.

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
from backend.models.document import Document, DocumentStatus
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


async def _request(
    harness, method: str, url: str, *, raise_app_exceptions: bool = True, **kwargs
):
    transport = ASGITransport(
        app=harness.app, raise_app_exceptions=raise_app_exceptions
    )
    async with AsyncClient(transport=transport, base_url="http://t") as client:
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


def _recording_id(harness, public_id: str) -> int:
    with Session(harness.engine) as session:
        return session.exec(
            select(Recording.id).where(Recording.public_id == public_id)
        ).one()


def _document_id(harness, path: Path) -> int:
    with Session(harness.engine) as session:
        return session.exec(
            select(Document.id).where(Document.file_path == str(path))
        ).one()


def _add_document_row(harness, public_id: str, path: Path) -> None:
    with Session(harness.engine) as session:
        session.add(
            Document(
                recording_id=_recording_id(harness, public_id),
                title=path.name,
                file_path=str(path),
            )
        )
        session.commit()


def _backend_pid(session: Session) -> int:
    return session.connection().execute(text("SELECT pg_backend_pid()")).scalar_one()


async def _wait_until_blocked_by(harness, pid: int, request: asyncio.Task) -> None:
    """Return once some backend is waiting on a lock that ``pid`` holds."""
    query = text(
        "SELECT count(*) FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid))"
    )
    for _ in range(400):
        with harness.engine.connect() as connection:
            if connection.execute(query, {"pid": pid}).scalar_one():
                return
        if request.done():
            raise AssertionError(
                f"the request finished without waiting: {request.result()!r}"
            )
        await asyncio.sleep(0.025)
    raise AssertionError("the request never waited on the held transaction")


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


def test_a_parse_whose_page_insert_fails_ends_in_error(harness, monkeypatch, caplog):
    """A failed insert aborts the parse's transaction; the document still ends ERROR.

    A second parse of the same document stores page 1 after this one has read
    which pages exist, so this parse's insert of page 1 breaks the unique
    constraint. The row is still there, so the failure must be recorded on it.
    """
    target = _add_recording(harness, "target")
    path = asyncio.run(_attach(harness, target))
    with Session(harness.engine) as session:
        document_id = session.exec(select(Document.id)).one()

    real_existing = document_tasks._existing_page_numbers

    def existing_then_race(session, doc_id):
        found = real_existing(session, doc_id)
        with Session(harness.engine) as other:
            other.add(DocumentPage(document_id=doc_id, page_number=1, content="other"))
            other.commit()
        return found

    monkeypatch.setattr(document_tasks, "_existing_page_numbers", existing_then_race)
    task = document_tasks.process_document_task
    task._session = Session(harness.engine)
    try:
        with caplog.at_level(logging.ERROR):
            task.run(document_id)
    finally:
        task._session.close()
        task._session = None

    with Session(harness.engine) as session:
        document = session.get(Document, document_id)
        assert document.status == DocumentStatus.ERROR
        assert "uq_document_page_number" in document.error_message
    assert path.is_file()
    assert f"Failed to process document {document_id}" in caplog.text


@pytest.mark.anyio
async def test_deleting_one_document_removes_its_file_and_no_other(harness, caplog):
    target = _add_recording(harness, "target")
    gone = await _attach(harness, target, "agenda.md")
    kept = await _attach(harness, target, "minutes.md")
    # A legacy row, stored before documents lived in the documents directory.
    legacy = harness.tmp_path / "legacy" / "old.md"
    legacy.parent.mkdir()
    legacy.write_text("legacy")
    _add_document_row(harness, target, legacy)

    first = await _request(
        harness, "DELETE", f"/documents/{_document_id(harness, gone)}"
    )
    with caplog.at_level(logging.WARNING):
        second = await _request(
            harness, "DELETE", f"/documents/{_document_id(harness, legacy)}"
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert not gone.exists()
    assert kept.is_file()
    assert legacy.read_text() == "legacy"
    assert f"Left document file {legacy} in place" in caplog.text
    assert _document_rows(harness) == 1


@pytest.mark.anyio
async def test_a_file_that_cannot_be_removed_does_not_stop_the_rest(harness, caplog):
    target = _add_recording(harness, "target")
    # A directory where a file should be: unlinking it fails with an OSError.
    stuck = harness.documents_dir / "stuck.md"
    stuck.mkdir()
    _add_document_row(harness, target, stuck)
    others = [
        await _attach(harness, target, "agenda.md"),
        await _attach(harness, target, "minutes.md"),
    ]

    with caplog.at_level(logging.WARNING):
        response = await _request(harness, "DELETE", f"/recordings/{target}")

    assert response.status_code == 200, response.text
    assert not any(path.exists() for path in others)
    assert stuck.is_dir()
    assert f"Failed to delete document file {stuck}" in caplog.text
    assert _document_rows(harness) == 0


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["delete_user", "discard"])
async def test_a_delete_waits_for_an_upload_in_flight_and_removes_its_file(
    harness, path
):
    """A document row that commits while the recording is being deleted.

    The upload's insert is made and its transaction held open; the delete is
    started and allowed to run until it waits on that transaction's lock. Only
    then does the upload commit. If the delete read its document paths before
    taking its lock, it would miss the row, the cascade would still delete it,
    and the file would stay with nothing referencing it.
    """
    if path == "delete_user":
        public_id = _add_recording(harness, "theirs", owner="member")
        method, url = "DELETE", f"/users/{harness.users['member']}"
    else:
        public_id = _add_recording(harness, "live", status=RecordingStatus.UPLOADING)
        method, url = "POST", f"/recordings/{public_id}/discard"
    late = harness.documents_dir / "late.md"
    late.write_text("# Late upload")

    upload = Session(harness.engine)
    upload.add(
        Document(
            recording_id=_recording_id(harness, public_id),
            title=late.name,
            file_path=str(late),
        )
    )
    upload.flush()
    request = asyncio.create_task(_request(harness, method, url))
    try:
        await _wait_until_blocked_by(harness, _backend_pid(upload), request)
    finally:
        upload.commit()
        upload.close()
    response = await request

    assert response.status_code == 200, response.text
    assert not late.exists()
    assert _document_rows(harness) == 0


@pytest.mark.anyio
async def test_an_upload_whose_recording_is_deleted_meanwhile_removes_its_file(
    harness,
):
    """The upload writes its file, then its insert fails the recording's foreign key.

    The recording's delete is made and held open, so the upload reads the
    recording, stores the file, and waits at its insert. Once the delete commits
    the insert fails, the file it wrote must not stay behind, and the client gets
    the same 404 as for any recording that is gone.
    """
    public_id = _add_recording(harness, "target")
    deleter = Session(harness.engine)
    deleter.connection().execute(
        text("DELETE FROM recordings WHERE id = :id"),
        {"id": _recording_id(harness, public_id)},
    )
    request = asyncio.create_task(
        _request(
            harness,
            "POST",
            f"/recordings/{public_id}/documents",
            files={"file": ("agenda.md", b"# Agenda\n", "text/markdown")},
            data={"deep_parse": "false"},
            raise_app_exceptions=False,
        )
    )
    try:
        await _wait_until_blocked_by(harness, _backend_pid(deleter), request)
    finally:
        deleter.commit()
        deleter.close()
    response = await request

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Recording not found"}
    assert list(harness.documents_dir.iterdir()) == []
    assert _document_rows(harness) == 0
