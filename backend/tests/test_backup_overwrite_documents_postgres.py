"""An Overwrite restore racing an upload on PostgreSQL, where the row locks are real.

The backup suite's surrogate tables run on SQLite, which has no row locks, so
nothing there shows the restore locking a recording before it reads the paths
of the documents the cascade will take. Here the same tables live in a Postgres
schema and an upload holds an uncommitted document insert open when the restore
starts.

Skips unless NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for the backend
suite.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import anyio
import pytest
from sqlalchemy import create_engine, func, insert, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import Session, select

import backend.core.backup.restore.stages as backup_stages
from backend.core.db import driver_urls
from backend.tests.backup_harness import (
    TEST_MODELS,
    StubPathManager,
    TestContext,
    TestDocument,
    TestInvitation,
)
from backend.tests.test_backup_overwrite_documents import (
    build_overwrite_archive,
    restore_overwriting,
    seed_replaced_documents,
)

SCHEMA = "backup_overwrite_documents_test"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def target_context(postgres_test_url: str, tmp_path: Path) -> Iterator[TestContext]:
    async_url, sync_url = driver_urls(postgres_test_url)
    sync_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={SCHEMA}"}
    )
    with sync_engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    TestInvitation.__table__.create(sync_engine)
    for _, model_cls in TEST_MODELS:
        model_cls.__table__.create(sync_engine)
    async_engine = create_async_engine(
        async_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": SCHEMA}},
    )
    root = tmp_path / "target"
    root.mkdir()
    try:
        yield TestContext(
            path_manager=StubPathManager(root),
            sync_engine=sync_engine,
            async_session_maker=sessionmaker(
                async_engine, class_=AsyncSession, expire_on_commit=False
            ),
            async_engine=async_engine,
        )
    finally:
        with sync_engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        sync_engine.dispose()


def _restore_statements_waiting_on_a_lock(target_context: TestContext) -> list[str]:
    with target_context.sync_engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT query FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database() "
                    "AND query LIKE '%backup_test_recordings%'"
                )
            ).scalars()
        )


@pytest.mark.anyio
@pytest.mark.parametrize("preflight", [True, False], ids=["preflight", "fallback"])
async def test_overwrite_removes_the_file_of_an_upload_in_flight_when_it_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_context: TestContext,
    preflight: bool,
) -> None:
    # The upload has written its file and inserted its row, uncommitted, on the
    # recording the archive replaces. Unlocked, the restore read the paths without
    # seeing that row, then its delete waited for the upload to commit and took
    # the row by cascade, and the file stayed with nothing naming it. Locked
    # first, the read waits for the commit and sees the row.
    zip_path = await build_overwrite_archive(
        tmp_path, monkeypatch, archive_has_file=True
    )
    documents_dir = target_context.path_manager.documents_directory
    await seed_replaced_documents(target_context, monkeypatch, {})
    if not preflight:
        # The insert loop's own delete, for a match the pre-delete missed.
        monkeypatch.setattr(
            backup_stages, "_restore_preflight_overwrite", lambda *args: None
        )
    upload = documents_dir / f"{uuid.uuid4()}.pdf"
    upload.write_bytes(b"uploaded")
    upload_connection = target_context.sync_engine.connect()
    upload_connection.execute(
        insert(TestDocument.__table__).values(
            recording_id=102,
            title="Uploaded",
            file_path=str(upload),
            file_type="application/pdf",
            status="READY",
            created_at=func.now(),
            updated_at=func.now(),
        )
    )

    job: dict = {}
    waiting: list[str] = []

    async def restore() -> None:
        job.update(await restore_overwriting(zip_path, "overwrite-race-job"))

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(restore)
            try:
                # Until the restore is held behind the upload's transaction.
                with anyio.fail_after(10):
                    while not waiting:
                        await anyio.sleep(0.02)
                        waiting = _restore_statements_waiting_on_a_lock(target_context)
            finally:
                upload_connection.commit()
    finally:
        upload_connection.close()

    assert job["status"] == "completed"
    with Session(target_context.sync_engine) as session:
        named = [doc.file_path for doc in session.exec(select(TestDocument)).all()]
    assert [Path(path).name for path in named] == ["agenda.pdf"]
    assert sorted(os.listdir(documents_dir)) == ["agenda.pdf"]
