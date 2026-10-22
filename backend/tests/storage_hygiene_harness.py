"""A data directory, a schema and a restore lock for the storage hygiene tests.

Lives in a module of its own so both storage hygiene test files can share it:
pytest imports a test file under its bare name, so importing one test file from
another would load it twice.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, Engine, Integer, create_engine, text
from sqlmodel import Session, SQLModel

import backend.models.registry  # noqa: F401
from backend.core.backup.format import RESTORE_LOCK_KEY
from backend.core.db import driver_urls
from backend.models.document import Document
from backend.models.pipeline import RecordingAudioChunk
from backend.models.recording import Recording, RecordingStatus
from backend.models.user import User
from backend.utils.path_manager import PathManager
from backend.utils.storage_hygiene import (
    HygieneOptions,
    HygieneReport,
    run_storage_hygiene,
)

POSTGRES_SCHEMA = "storage_hygiene_test"
HOUR = 60 * 60
# Well past the 24 hour grace.
OLD = 48


@dataclass(frozen=True)
class Layout:
    """``<tmp>/app/data`` laid out as a container has it, with /app the cwd."""

    app: Path
    data: Path
    documents: Path
    recordings: Path


def make_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Layout:
    app = tmp_path / "app"
    data = app / "data"
    layout = Layout(
        app=app,
        data=data,
        documents=data / "documents",
        recordings=data / "recordings",
    )
    for directory in (
        layout.documents,
        layout.recordings / "temp",
        layout.recordings / "failed",
    ):
        directory.mkdir(parents=True)
    # What sits beside them at the top of data/, which no rule may reach.
    write_file(data / "config.json", size=2)
    write_file(data / "celerybeat-schedule", size=4)
    monkeypatch.chdir(app)
    monkeypatch.setenv("HOME", str(app))
    monkeypatch.setenv("NOJOIN_CONTAINERIZED", "1")
    monkeypatch.setenv("NOJOIN_DATA_DIR", str(data))
    monkeypatch.delenv("DOCUMENTS_DIR", raising=False)
    monkeypatch.delenv("RECORDINGS_DIR", raising=False)
    # A fresh singleton reads the variables above; the old one comes back after.
    monkeypatch.setattr(PathManager, "_instance", None)
    return layout


def write_file(path: Path, *, size: int = 10, age_hours: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    set_age(path, age_hours)
    return path


def set_age(path: Path, age_hours: float, *, now: float | None = None) -> None:
    moment = (time.time() if now is None else now) - age_hours * HOUR
    os.utime(path, (moment, moment), follow_symlinks=False)


SQLITE_TABLES = (User, Recording, Document, RecordingAudioChunk)


def sqlite_engine(path: Path) -> Engine:
    """The tables the routine and the daily task read, in a SQLite file.

    SQLite autoincrements only an INTEGER primary key, so the BIGINT keys are
    created as INTEGER, which stores the same 64 bits, and then put back.
    """
    engine = create_engine(f"sqlite:///{path}")
    tables = [model.__table__ for model in SQLITE_TABLES]
    swapped = [
        (column, column.type)
        for table in tables
        for column in table.primary_key.columns
        if isinstance(column.type, BigInteger)
    ]
    for column, _ in swapped:
        column.type = Integer()
    try:
        SQLModel.metadata.create_all(engine, tables=tables)
    finally:
        for column, original in swapped:
            column.type = original
    return engine


@contextmanager
def postgres_engine(url: str) -> Iterator[Engine]:
    """The whole schema in a scratch Postgres schema, where foreign keys hold."""
    engine = create_engine(
        driver_urls(url)[1],
        connect_args={"options": f"-csearch_path={POSTGRES_SCHEMA},public"},
    )
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {POSTGRES_SCHEMA} CASCADE"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f"CREATE SCHEMA {POSTGRES_SCHEMA}"))
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {POSTGRES_SCHEMA} CASCADE"))
        engine.dispose()


def add_recording(
    engine: Engine,
    audio_path: str | Path,
    proxy_path: str | Path | None = None,
    *,
    status: RecordingStatus = RecordingStatus.PROCESSED,
    is_deleted: bool = False,
) -> int:
    with Session(engine) as session:
        recording = Recording(
            name="Meeting",
            audio_path=str(audio_path),
            proxy_path=None if proxy_path is None else str(proxy_path),
            status=status,
            is_deleted=is_deleted,
        )
        session.add(recording)
        session.commit()
        assert recording.id is not None
        return recording.id


def add_document(
    engine: Engine, recording_id: int | None, file_path: str | Path
) -> int:
    with Session(engine) as session:
        document = Document(
            recording_id=recording_id, title="Agenda", file_path=str(file_path)
        )
        session.add(document)
        session.commit()
        assert document.id is not None
        return document.id


def uuid_name(extension: str) -> str:
    """A name as Nojoin mints one: a lowercase uuid4 and the extension."""
    return f"{uuid.uuid4()}{extension}"


def add_referenced_audio(
    engine: Engine, layout: Layout, count: int, *, size: int = 10
) -> list[Path]:
    """``count`` recordings, each naming an old audio file of ``size`` bytes."""
    paths = []
    for _ in range(count):
        path = write_file(layout.recordings / uuid_name(".wav"), size=size)
        add_recording(engine, path)
        paths.append(path)
    return paths


def add_referenced_documents(
    engine: Engine, layout: Layout, count: int, *, size: int = 10
) -> list[Path]:
    """``count`` old documents, attached to a recording whose audio exists."""
    recording_id = add_recording(
        engine, write_file(layout.recordings / uuid_name(".wav"), size=size)
    )
    paths = []
    for _ in range(count):
        path = write_file(layout.documents / uuid_name(".pdf"), size=size)
        add_document(engine, recording_id, path)
        paths.append(path)
    return paths


class FakeLockStore:
    """The restore lock as Redis holds it, free or held, per read.

    ``held`` is the answer to each successive read; the last one repeats.
    """

    def __init__(self, *held: bool) -> None:
        self.held = list(held) or [False]
        self.reads = 0

    def open(self) -> FakeLockStore:
        return self

    def exists(self, key: str) -> int:
        answer = self.held[min(self.reads, len(self.held) - 1)]
        self.reads += 1
        return int(answer and key == RESTORE_LOCK_KEY)

    def close(self) -> None:
        pass


def run(
    engine: Engine,
    *,
    lock: FakeLockStore | None = None,
    delete: bool = False,
    bulk: Mapping[Path, int] | bool = False,
    now: float | None = None,
) -> HygieneReport:
    """One run as the daily task makes it, against ``engine`` and a free lock.

    ``bulk`` passes allowances as the CLI's --allow-bulk and --expect do. True
    takes the operator's way to them: a dry run first, then each directory
    allowed exactly the count that dry run printed for it.
    """
    if bulk is True:
        dry = run_storage_hygiene(
            lambda: Session(engine),
            HygieneOptions(now=now, redis_client=FakeLockStore().open),
        )
        bulk = {d.path: d.candidates for d in dry.directories if d.candidates}
    store = lock or FakeLockStore()
    return run_storage_hygiene(
        lambda: Session(engine),
        HygieneOptions(
            delete=delete,
            bulk_allowances=bulk or {},
            now=now,
            redis_client=store.open,
        ),
    )


def rules(report: HygieneReport) -> list[str]:
    return [trip.rule for trip in report.breaker]
