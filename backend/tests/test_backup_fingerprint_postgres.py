"""The backup fingerprint on PostgreSQL, where the database computes the row digests.

The rest of the backup suite runs on SQLite surrogate tables, which take the other branch
of the fingerprint. This one runs the real schema, with its JSONB and vector columns, so
the SQL is checked against the types it will meet in production and under the REPEATABLE
READ snapshot the export reads in.

Skips unless NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for the backend suite.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

from backend.core.backup import export as backup_export
from backend.core.backup import runtime
from backend.core.db import driver_urls
from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.time import utc_now

SCHEMA = "backup_fingerprint_test"


@pytest.fixture
def engine(postgres_test_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    importlib.import_module("backend.models.registry")
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": f"-csearch_path={SCHEMA},public"},
    )
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    SQLModel.metadata.create_all(engine)

    (tmp_path / "recordings").mkdir()
    (tmp_path / "documents").mkdir()
    (tmp_path / "config.json").write_text('{"theme": "dark"}')
    path_manager = SimpleNamespace(
        user_data_directory=tmp_path,
        recordings_directory=tmp_path / "recordings",
        documents_directory=tmp_path / "documents",
        config_path=tmp_path / "config.json",
    )
    monkeypatch.setattr(runtime, "sync_engine", engine)
    monkeypatch.setattr(runtime, "PathManager", lambda: path_manager)
    monkeypatch.setattr(runtime, "get_app_version", lambda: "9.9.9")

    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        engine.dispose()


@pytest.fixture
def library(engine) -> Iterator[SimpleNamespace]:
    with Session(engine) as session:
        user = User(username="alice", hashed_password="x", settings={"theme": "dark"})
        session.add(user)
        session.flush()
        recording = Recording(
            name="Planning", audio_path="/data/recordings/planning.wav", user_id=user.id
        )
        speaker = GlobalSpeaker(
            name="Alice Example", embedding=[0.1] * 4, user_id=user.id
        )
        connection = CalendarConnection(
            user_id=user.id, provider="google", provider_account_id="acct-1"
        )
        session.add_all([recording, speaker, connection])
        session.flush()
        source = CalendarSource(
            connection_id=connection.id, provider_calendar_id="primary", name="Work"
        )
        session.add(source)
        session.flush()
        session.add(
            Transcript(
                recording_id=recording.id,
                text="Hello there.",
                segments=[{"start": 0.0, "end": 1.0, "text": "Hello there."}],
            )
        )
        session.add(
            CalendarEvent(
                calendar_id=source.id, provider_event_id="evt-1", title="Sync"
            )
        )
        session.commit()
        yield SimpleNamespace(
            recording_id=recording.id,
            speaker_id=speaker.id,
            connection_id=connection.id,
            source_id=source.id,
        )


def _fingerprint() -> str:
    # Through snapshot_session, so the REPEATABLE READ branch runs on a real server.
    return backup_export.current_fingerprint(False, "compressed")


def _change(engine, model, row_id, **values) -> None:
    with Session(engine) as session:
        row = session.get(model, row_id)
        for key, value in values.items():
            setattr(row, key, value)
        session.add(row)
        session.commit()


def test_fingerprint_is_stable_on_postgres(engine, library) -> None:
    assert _fingerprint() == _fingerprint()


def test_edits_change_the_fingerprint_on_postgres(engine, library) -> None:
    before = _fingerprint()
    _change(engine, Recording, library.recording_id, name="Renamed")
    renamed = _fingerprint()
    assert renamed != before

    _change(engine, GlobalSpeaker, library.speaker_id, embedding=[0.2] * 4)
    assert _fingerprint() != renamed


def test_a_transcript_edit_changes_the_fingerprint_on_postgres(engine, library) -> None:
    before = _fingerprint()
    with Session(engine) as session:
        transcript = session.exec(select(Transcript)).one()
        transcript.segments = [{"start": 0.0, "end": 1.0, "text": "Hello again."}]
        session.add(transcript)
        session.commit()

    assert _fingerprint() != before


def test_calendar_sync_bookkeeping_is_ignored_on_postgres(engine, library) -> None:
    before = _fingerprint()
    now = utc_now()
    _change(
        engine,
        CalendarConnection,
        library.connection_id,
        access_token_encrypted="rotated",
        last_synced_at=now,
        last_sync_completed_at=now,
    )
    _change(
        engine,
        CalendarSource,
        library.source_id,
        sync_cursor="next",
        last_synced_at=now,
    )
    with Session(engine) as session:
        session.add(
            CalendarEvent(
                calendar_id=library.source_id, provider_event_id="evt-2", title="New"
            )
        )
        session.commit()

    assert _fingerprint() == before
