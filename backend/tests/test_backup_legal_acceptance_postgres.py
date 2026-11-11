"""Backups carry each user's acceptance of the terms of use (#1004).

An archive holds the time as the database does, in naive UTC as every other
time it holds, and a restore puts it back. An archive from before the column restores its users with no
time, so they are asked at their next visit, as an upgrade asks them.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, create_engine, delete, text
from sqlmodel import Session, col, select

from backend.core.backup import BackupManager, runtime
from backend.core.db import driver_urls
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema

SCHEMA = "backup_legal_acceptance_test"
ACCEPTED_AT = datetime(2026, 11, 2, 9, 30, 15, 250000)


@pytest.fixture
def engine(
    postgres_test_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": f"-csearch_path={SCHEMA},public"},
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(engine, SCHEMA)
    for name in ("recordings", "documents"):
        (tmp_path / name).mkdir()
    paths = SimpleNamespace(
        user_data_directory=tmp_path,
        recordings_directory=tmp_path / "recordings",
        documents_directory=tmp_path / "documents",
        config_path=tmp_path / "config.json",
    )
    monkeypatch.setattr(runtime, "sync_engine", engine)
    monkeypatch.setattr(runtime, "PathManager", lambda: paths)
    monkeypatch.setattr(runtime, "get_app_version", lambda: "3.2.0")
    try:
        yield engine
    finally:
        engine.dispose()


def _times(engine: Engine) -> dict[str, datetime | None]:
    with Session(engine) as session:
        return {
            user.username: user.legal_accepted_at
            for user in session.exec(select(User)).all()
        }


def _forget(engine: Engine, *usernames: str) -> None:
    with Session(engine) as session:
        session.execute(delete(User).where(col(User.username).in_(usernames)))
        session.commit()


def _archive_without_the_column(archive_path: str) -> str:
    path = runtime.PathManager().user_data_directory / "before-3.2.0.zip"
    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(path, "w") as target:
        for name in source.namelist():
            payload = source.read(name)
            if name == "users.json":
                rows = json.loads(payload)
                for row in rows:
                    row.pop("legal_accepted_at")
                payload = json.dumps(rows).encode()
            target.writestr(name, payload)
    return str(path)


@pytest.mark.usefixtures("stub_celery_dispatch")
def test_a_backup_keeps_each_acceptance_and_an_older_one_has_none(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        session.add(
            User(username="owner", hashed_password="x", is_superuser=True, role="owner")
        )
        session.add(
            User(username="alice", hashed_password="x", legal_accepted_at=ACCEPTED_AT)
        )
        session.add(User(username="bob", hashed_password="x"))
        session.commit()

    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            archived = {
                row["username"]: row["legal_accepted_at"]
                for row in json.loads(archive.read("users.json"))
            }
        assert archived["owner"] is None
        assert archived["bob"] is None
        # Written as created_at is, with no offset.
        assert archived["alice"] == "2026-11-02T09:30:15.250000"

        # Restored onto an install that no longer has them, so the archive's
        # rows are inserted rather than matched to existing accounts.
        _forget(engine, "alice", "bob")
        result = BackupManager.restore_backup_blocking("legal-acceptance", archive_path)
        assert result["status"] == "completed", result
        assert _times(engine) == {"owner": None, "alice": ACCEPTED_AT, "bob": None}

        _forget(engine, "alice")
        result = BackupManager.restore_backup_blocking(
            "legal-acceptance-older", _archive_without_the_column(archive_path)
        )
        assert result["status"] == "completed", result
        assert _times(engine)["alice"] is None
    finally:
        Path(archive_path).unlink(missing_ok=True)


@pytest.mark.usefixtures("stub_celery_dispatch")
def test_a_restore_keeps_an_existing_account_and_its_acceptance(
    engine: Engine,
) -> None:
    """An account already here is not overwritten, its acceptance included."""
    with Session(engine) as session:
        session.add(User(username="alice", hashed_password="x"))
        session.commit()
    archive_path, _ = BackupManager.create_backup_blocking(include_audio=False)
    try:
        with Session(engine) as session:
            alice = session.exec(select(User)).one()
            alice.legal_accepted_at = ACCEPTED_AT
            session.add(alice)
            session.commit()

        result = BackupManager.restore_backup_blocking(
            "legal-acceptance-existing", archive_path, clear_existing=True
        )

        assert result["status"] == "completed", result
        assert _times(engine) == {"alice": ACCEPTED_AT}
    finally:
        Path(archive_path).unlink(missing_ok=True)
