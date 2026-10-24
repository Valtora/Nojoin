"""Name events go with their recording, and outlive what they point at, on
PostgreSQL.

The rest of the suite runs on SQLite without foreign keys enforced, so the
cascades and the span check on ``speaker_name_events`` are checked here
against the real schema. Skips unless NOJOIN_TEST_POSTGRES_URL names a server.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, exc, text
from sqlmodel import Session, SQLModel, select

from backend.core.db import driver_urls
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.user import User

SCHEMA = "speaker_name_events_test"


@pytest.fixture
def engine(postgres_test_url: str):
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
    try:
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        engine.dispose()


def _event(ids: SimpleNamespace, **fields) -> SpeakerNameEvent:
    values = {
        "recording_id": ids.recording,
        "kind": "name",
        "name": "Dana",
        "source": "user",
        "authority": 40,
        "anchor_start_ms": 0,
        "anchor_end_ms": 5000,
        "recording_speaker_id": ids.speaker,
        "global_speaker_id": ids.person,
        "created_by_user_id": ids.user,
    }
    values.update(fields)
    return SpeakerNameEvent(**values)


@pytest.fixture
def named(engine) -> Iterator[SimpleNamespace]:
    """Dana named on one speaker, then renamed: two rows in a chain."""
    with Session(engine) as session:
        user = User(username="alice", hashed_password="x")
        session.add(user)
        session.flush()
        recording = Recording(
            name="Planning", audio_path="/data/recordings/p.wav", user_id=user.id
        )
        dana = GlobalSpeaker(name="Dana", user_id=user.id)
        session.add_all([recording, dana])
        session.flush()
        speaker = RecordingSpeaker(
            recording_id=recording.id, diarization_label="SPEAKER_00"
        )
        session.add(speaker)
        session.flush()
        ids = SimpleNamespace(
            user=user.id, recording=recording.id, person=dana.id, speaker=speaker.id
        )
        first = _event(ids, name="Dan")
        session.add(first)
        session.flush()
        second = _event(ids, supersedes_event_id=first.id)
        session.add(second)
        session.commit()
        ids.first, ids.second = first.id, second.id
    yield ids


def _rows(engine) -> list[SpeakerNameEvent]:
    with Session(engine) as session:
        return list(
            session.exec(select(SpeakerNameEvent).order_by(SpeakerNameEvent.id))
        )


def test_deleting_the_recording_deletes_its_events(engine, named) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM recordings"))
    assert _rows(engine) == []


def test_an_event_outlives_its_speaker_person_author_and_predecessor(
    engine, named
) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM recording_speakers"))
        connection.execute(text("DELETE FROM global_speakers"))
        connection.execute(
            text("DELETE FROM speaker_name_events WHERE id = :id"), {"id": named.first}
        )
        connection.execute(text("UPDATE recordings SET user_id = NULL"))
        connection.execute(text("DELETE FROM users"))

    (row,) = _rows(engine)
    assert row.id == named.second
    assert (row.name, row.anchor_start_ms, row.anchor_end_ms) == ("Dana", 0, 5000)
    assert row.recording_speaker_id is None
    assert row.global_speaker_id is None
    assert row.supersedes_event_id is None
    assert row.created_by_user_id is None


@pytest.mark.parametrize("end_ms", [0, -1])
def test_an_event_needs_a_span(engine, named, end_ms: int) -> None:
    with Session(engine) as session:
        session.add(_event(named, anchor_start_ms=0, anchor_end_ms=end_ms))
        with pytest.raises(exc.IntegrityError):
            session.commit()
