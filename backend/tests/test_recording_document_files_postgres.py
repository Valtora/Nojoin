"""Document files are removed with their rows, and never orphaned by a race (PostgreSQL).

Documents are stored flat in the documents directory, apart from the recording's
audio, and their rows go by cascade. Most tests attach a document through the
real upload route, delete through one of the paths that remove a recording or a
document, and check the disk. The recording's own audio, proxy and upload
segments go by the same rule, only once the delete has committed. Postgres
because two of those paths delete with Core statements and rely on the foreign
keys' ON DELETE CASCADE, which the SQLite suites do not enforce, and because the
race tests hold one side's transaction open at a known point and wait until the
other is blocked on its lock.

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
from sqlalchemy import create_engine, event, func, text
from sqlalchemy.exc import OperationalError
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
from backend.models.user import User, UserRead, UserRole
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


def _recording_rows(harness) -> int:
    with Session(harness.engine) as session:
        return session.exec(select(func.count()).select_from(Recording)).one()


def _refuse_deletes_at_commit(harness, table: str) -> None:
    """Make every delete from ``table`` fail when its transaction commits.

    The trigger is deferred, so the delete itself runs and only the commit
    fails, which rolls the rows back.
    """
    with harness.engine.begin() as connection:
        connection.execute(
            text(
                f"CREATE FUNCTION refuse_{table}_delete() RETURNS trigger"
                " LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'refused'; END $$"
            )
        )
        connection.execute(
            text(
                f"CREATE CONSTRAINT TRIGGER refuse_{table}_delete AFTER DELETE"
                f" ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW"
                f" EXECUTE FUNCTION refuse_{table}_delete()"
            )
        )


# Each route that deletes one recording, with the status and bin state it accepts.
RECORDING_DELETES = {
    "delete": (RecordingStatus.PROCESSED, False),
    "permanent": (RecordingStatus.PROCESSED, True),
    "batch_permanent": (RecordingStatus.PROCESSED, True),
    "discard": (RecordingStatus.UPLOADING, False),
}


async def _recording_with_files(harness, route: str) -> SimpleNamespace:
    """A recording ``route`` can delete, with audio, proxy, a segment and a document."""
    status, is_deleted = RECORDING_DELETES[route]
    public_id = _add_recording(harness, "target", status=status, is_deleted=is_deleted)
    recording_id = _recording_id(harness, public_id)
    proxy = harness.recordings_dir / "target.mp3"
    proxy.write_bytes(b"ID3")
    with harness.engine.begin() as connection:
        connection.execute(
            text("UPDATE recordings SET proxy_path = :path WHERE id = :id"),
            {"path": str(proxy), "id": recording_id},
        )
    segment = harness.recordings_dir / "temp" / str(recording_id) / "0.wav"
    segment.parent.mkdir(parents=True)
    segment.write_bytes(b"RIFF")
    return SimpleNamespace(
        public_id=public_id,
        id=recording_id,
        audio=harness.recordings_dir / "target.wav",
        proxy=proxy,
        segment=segment,
        document=await _attach(harness, public_id),
    )


async def _delete_recording_through(harness, route: str, public_id: str):
    if route == "delete":
        method, url, body = "DELETE", f"/recordings/{public_id}", None
    elif route == "permanent":
        method, url, body = "DELETE", f"/recordings/{public_id}/permanent", None
    elif route == "batch_permanent":
        method, url = "POST", "/recordings/batch/permanent"
        body = {"recording_ids": [public_id]}
    else:
        method, url, body = "POST", f"/recordings/{public_id}/discard", None
    return await _request(harness, method, url, json=body, raise_app_exceptions=False)


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
    constraint. The row is still there, so the failure must be recorded on it,
    in words a user can read: the database's own text, SQL and all, goes only
    to the log.
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
        assert "INSERT INTO" not in document.error_message
        assert document.error_message == document_tasks.DATABASE_FAILURE_DETAIL
    assert path.is_file()
    assert f"Failed to process document {document_id}" in caplog.text
    assert "uq_document_page_number" in caplog.text


@pytest.mark.parametrize("deleted", ["before_the_write", "during_the_write"])
def test_a_parse_failure_stops_quietly_when_the_row_goes_before_it_is_recorded(
    harness, monkeypatch, caplog, deleted
):
    """The document is deleted after the handler found it, before it is marked ERROR.

    Deleted before the handler writes, the row cannot be reloaded for the write
    (ObjectDeletedError). Deleted after that reload, just before the UPDATE, the
    UPDATE matches no row (StaleDataError). Either way it is the same deleted
    document, so the handler stops as for one already gone.
    """
    target = _add_recording(harness, "target")
    asyncio.run(_attach(harness, target))
    with Session(harness.engine) as session:
        document_id = session.exec(select(Document.id)).one()

    def unreadable(file_path, **kwargs):
        raise RuntimeError("The file could not be read.")

    def delete_the_row():
        with harness.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM documents WHERE id = :id"), {"id": document_id}
            )

    real_fail = document_tasks._fail
    armed = SimpleNamespace(update=False)

    def delete_or_arm_then_fail(session, document, message):
        if deleted == "before_the_write":
            delete_the_row()
        else:
            armed.update = True
        real_fail(session, document, message)

    def delete_before_the_update(_conn, _cursor, statement, *_args):
        if armed.update and statement.startswith("UPDATE documents"):
            armed.update = False
            delete_the_row()

    monkeypatch.setattr(document_tasks, "open_document", unreadable)
    monkeypatch.setattr(document_tasks, "_fail", delete_or_arm_then_fail)
    event.listen(harness.engine, "before_cursor_execute", delete_before_the_update)
    task = document_tasks.process_document_task
    task._session = Session(harness.engine)
    try:
        with caplog.at_level(logging.INFO):
            task.run(document_id)
    finally:
        event.remove(harness.engine, "before_cursor_execute", delete_before_the_update)
        task._session.close()
        task._session = None

    assert _document_rows(harness) == 0
    assert f"Document {document_id} was deleted while parsing" in caplog.text


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


@pytest.mark.anyio
async def test_an_upload_cancelled_at_its_insert_removes_its_file(harness):
    """A request cancelled while its insert waits must not leave the file it wrote.

    The cancellation is a BaseException, so a guard that catches only
    Exception lets it through with the file still on disk.
    """
    public_id = _add_recording(harness, "target")
    holder = Session(harness.engine)
    holder.connection().execute(
        text("SELECT id FROM recordings WHERE id = :id FOR UPDATE"),
        {"id": _recording_id(harness, public_id)},
    )
    request = asyncio.create_task(
        _request(
            harness,
            "POST",
            f"/recordings/{public_id}/documents",
            files={"file": ("agenda.md", b"# Agenda\n", "text/markdown")},
            data={"deep_parse": "false"},
        )
    )
    try:
        await _wait_until_blocked_by(harness, _backend_pid(holder), request)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    finally:
        holder.rollback()
        holder.close()

    assert list(harness.documents_dir.iterdir()) == []
    assert _document_rows(harness) == 0


@pytest.mark.anyio
async def test_a_user_delete_waits_for_a_recording_created_meanwhile(harness):
    """A recording and its document, inserted while the user is being deleted.

    The insert is held open; the user's recordings are read only once the user
    is locked, and the recording insert holds a lock that conflicts with it, so
    the delete waits. Locking only the recordings it can see would miss this
    one, the cascade would still delete it, and its files would stay.
    """
    late = harness.documents_dir / "late.md"
    late.write_text("# Late upload")
    audio = harness.recordings_dir / "late.wav"
    audio.write_bytes(b"RIFF")
    upload = Session(harness.engine)
    recording = Recording(
        name="late", audio_path=str(audio), user_id=harness.users["member"]
    )
    upload.add(recording)
    upload.flush()
    upload.add(
        Document(recording_id=recording.id, title=late.name, file_path=str(late))
    )
    upload.flush()
    request = asyncio.create_task(
        _request(harness, "DELETE", f"/users/{harness.users['member']}")
    )
    try:
        await _wait_until_blocked_by(harness, _backend_pid(upload), request)
    finally:
        upload.commit()
        upload.close()
    response = await request

    assert response.status_code == 200, response.text
    assert not late.exists()
    assert not audio.exists()
    assert _document_rows(harness) == 0


@pytest.mark.anyio
async def test_deleting_a_user_deleted_meanwhile_is_a_404(harness):
    """Two deletions of one user: the second waits on the first, then finds no one."""
    member = harness.users["member"]
    first = Session(harness.engine)
    first.connection().execute(text("DELETE FROM users WHERE id = :id"), {"id": member})
    request = asyncio.create_task(
        _request(harness, "DELETE", f"/users/{member}", raise_app_exceptions=False)
    )
    try:
        await _wait_until_blocked_by(harness, _backend_pid(first), request)
    finally:
        first.commit()
        first.close()
    response = await request

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "User not found"}


@pytest.mark.anyio
async def test_a_user_delete_whose_commit_fails_keeps_every_file(harness):
    """The files go only once the delete has committed.

    A deferred trigger refuses the user's delete at commit time, so the delete
    itself runs and only the commit fails. The rows are rolled back, and the
    recording's audio and its document's file must still be there for them.
    """
    harness.act_as("member")
    public_id = _add_recording(harness, "theirs", owner="member")
    document = await _attach(harness, public_id)
    harness.act_as("admin")
    audio = harness.recordings_dir / "theirs.wav"
    _refuse_deletes_at_commit(harness, "users")

    response = await _request(harness, "DELETE", f"/users/{harness.users['member']}")

    assert response.status_code == 500, response.text
    assert audio.is_file()
    assert document.is_file()
    assert _document_rows(harness) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("route", list(RECORDING_DELETES))
async def test_a_recording_delete_removes_its_audio_proxy_and_segments(harness, route):
    target = await _recording_with_files(harness, route)

    response = await _delete_recording_through(harness, route, target.public_id)

    assert response.status_code == 200, response.text
    assert _recording_rows(harness) == 0
    assert not target.audio.exists()
    assert not target.proxy.exists()
    assert not target.segment.parent.exists()
    assert not target.document.exists()


@pytest.mark.anyio
@pytest.mark.parametrize("route", list(RECORDING_DELETES))
async def test_a_recording_delete_whose_commit_fails_keeps_every_file(harness, route):
    """The files go only once the delete has committed (#536).

    Unlinked before the commit, they went whatever the commit did, and a
    failed one left the row naming audio and segments that were gone.
    """
    target = await _recording_with_files(harness, route)
    _refuse_deletes_at_commit(harness, "recordings")

    response = await _delete_recording_through(harness, route, target.public_id)

    assert response.status_code == 500, response.text
    assert _recording_rows(harness) == 1
    assert target.audio.is_file()
    assert target.proxy.is_file()
    assert target.segment.is_file()
    assert target.document.is_file()
    assert _document_rows(harness) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("route", list(RECORDING_DELETES))
async def test_a_recording_delete_succeeds_when_its_audio_cannot_be_removed(
    harness, monkeypatch, caplog, route
):
    """A cleanup failure after the commit is logged and never fails the delete.

    Checking the audio raises PermissionError, as under a directory the server
    may not search. The delete has committed by then, so it answers as a clean
    delete does, and the document's file is still removed.
    """
    target = await _recording_with_files(harness, route)
    refused = target.audio.resolve()
    exists = Path.exists

    def refuse(self: Path, *args, **kwargs) -> bool:
        if self == refused:
            raise PermissionError(13, "Permission denied", str(self))
        return exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", refuse)

    with caplog.at_level(logging.WARNING):
        response = await _delete_recording_through(harness, route, target.public_id)

    assert response.status_code == 200, response.text
    assert _recording_rows(harness) == 0
    assert target.audio.is_file()
    assert not target.document.is_file()
    assert f"Failed to remove the files of deleted recording {target.id}" in caplog.text
    assert "Permission denied" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("audio_removal", ["clean", "refused"])
async def test_a_user_delete_answers_the_same_when_its_audio_cannot_be_removed(
    harness, monkeypatch, caplog, audio_removal
):
    """Cleanup after the commit never turns a committed delete into an error.

    Checking the audio raises PermissionError, as it does under a directory the
    server may not search. The delete has committed by then, so the response is
    the clean delete's, the failure is logged, and the next recording's audio and
    every document file are still removed.
    """
    harness.act_as("member")
    refused_id = _add_recording(harness, "refused", owner="member")
    other_id = _add_recording(harness, "other", owner="member")
    documents = [
        await _attach(harness, refused_id, "agenda.md"),
        await _attach(harness, other_id, "minutes.md"),
    ]
    harness.act_as("admin")
    refused_recording = _recording_id(harness, refused_id)
    refused_audio = harness.recordings_dir / "refused.wav"
    other_audio = harness.recordings_dir / "other.wav"
    with Session(harness.engine) as session:
        member = session.get(User, harness.users["member"])
        expected = UserRead.model_validate(member).model_dump(mode="json")

    if audio_removal == "refused":
        refused = refused_audio.resolve()
        exists = Path.exists

        def refuse(self: Path, *args, **kwargs) -> bool:
            if self == refused:
                raise PermissionError(13, "Permission denied", str(self))
            return exists(self, *args, **kwargs)

        monkeypatch.setattr(Path, "exists", refuse)

    with caplog.at_level(logging.WARNING):
        response = await _request(
            harness,
            "DELETE",
            f"/users/{harness.users['member']}",
            raise_app_exceptions=False,
        )

    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert not any(path.exists() for path in documents)
    assert not other_audio.exists()
    assert _document_rows(harness) == 0
    if audio_removal == "refused":
        assert refused_audio.is_file()
        assert (
            f"Failed to remove the files of deleted recording {refused_recording}"
            in caplog.text
        )
        assert "Permission denied" in caplog.text


@pytest.mark.anyio
async def test_a_recording_delete_succeeds_when_its_document_cleanup_fails(
    harness, monkeypatch, caplog
):
    """The same rule for the paths whose only cleanup after the commit is documents."""
    target = _add_recording(harness, "target")
    document = await _attach(harness, target)

    def refuse() -> Path:
        raise PermissionError(13, "Permission denied", str(harness.documents_dir))

    monkeypatch.setattr("backend.utils.document_storage.documents_root_dir", refuse)

    with caplog.at_level(logging.WARNING):
        response = await _request(
            harness, "DELETE", f"/recordings/{target}", raise_app_exceptions=False
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert _document_rows(harness) == 0
    assert document.is_file()
    assert f"Failed to delete document files ['{document}']" in caplog.text
    assert "Permission denied" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["delete_user", "batch_permanent"])
async def test_a_delete_locks_its_recordings_in_id_order(harness, path):
    """Deletes over the same recordings take their locks in one order.

    The lower-id recording is rewritten, so a scan meets it after the other
    one, and given the later public id, so the batch lists it second too. The
    higher-id recording is held locked. A delete that locks in id order holds
    the lower one while it waits; one that locks in scan order waits first,
    with the lower one still free.
    """
    owner = "member" if path == "delete_user" else "admin"
    is_deleted = path == "batch_permanent"
    lower = _recording_id(
        harness, _add_recording(harness, "lower", owner=owner, is_deleted=is_deleted)
    )
    higher = _recording_id(
        harness, _add_recording(harness, "higher", owner=owner, is_deleted=is_deleted)
    )
    with harness.engine.begin() as connection:
        for recording_id, public_id in ((higher, "a" * 32), (lower, "z" * 32)):
            connection.execute(
                text("UPDATE recordings SET public_id = :public_id WHERE id = :id"),
                {"public_id": public_id, "id": recording_id},
            )
    if path == "delete_user":
        method, url, body = "DELETE", f"/users/{harness.users['member']}", None
    else:
        method, url = "POST", "/recordings/batch/permanent"
        body = {"recording_ids": ["z" * 32, "a" * 32]}

    holder = Session(harness.engine)
    holder.connection().execute(
        text("SELECT id FROM recordings WHERE id = :id FOR UPDATE"), {"id": higher}
    )
    request = asyncio.create_task(_request(harness, method, url, json=body))
    try:
        await _wait_until_blocked_by(harness, _backend_pid(holder), request)
        with harness.engine.connect() as probe:
            with pytest.raises(OperationalError) as locked:
                probe.execute(
                    text("SELECT id FROM recordings WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": lower},
                )
        assert locked.value.orig.sqlstate == "55P03"
    finally:
        holder.rollback()
        holder.close()
    response = await request

    assert response.status_code == 200, response.text
