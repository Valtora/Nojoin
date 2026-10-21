"""Drift guard refusals go with their person and their link, on PostgreSQL.

The rest of the suite runs on SQLite without foreign keys enforced, so the
cascades on ``recording_speaker_drift_refusals`` are checked here against the
real schema, and so is a refusal two sessions write at once. Skips unless
NOJOIN_TEST_POSTGRES_URL names a server.
"""

from __future__ import annotations

import importlib
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

from backend.core.db import driver_urls
from backend.models.recording import Recording
from backend.models.speaker import (
    GlobalSpeaker,
    RecordingSpeaker,
    RecordingSpeakerDriftRefusal,
)
from backend.models.user import User
from backend.processing.embedding import VoiceprintStatus, offer_linked_voiceprint
from backend.processing.embedding_version import EMBEDDING_METHOD_VERSION

SCHEMA = "drift_refusals_test"


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


@pytest.fixture
def library(engine) -> Iterator[SimpleNamespace]:
    """Dana's speaker in one recording, refused for Dana and for Eli."""
    with Session(engine) as session:
        user = User(username="alice", hashed_password="x")
        session.add(user)
        session.flush()
        recording = Recording(
            name="Planning", audio_path="/data/recordings/p.wav", user_id=user.id
        )
        dana = GlobalSpeaker(name="Dana", user_id=user.id)
        eli = GlobalSpeaker(name="Eli", user_id=user.id)
        session.add_all([recording, dana, eli])
        session.flush()
        link = RecordingSpeaker(
            recording_id=recording.id,
            diarization_label="SPEAKER_00",
            global_speaker_id=dana.id,
        )
        session.add(link)
        session.flush()
        for person in (dana, eli):
            link.drift_refusals.append(
                RecordingSpeakerDriftRefusal(global_speaker_id=person.id)
            )
        session.commit()
        ids = SimpleNamespace(link=link.id, dana=dana.id, eli=eli.id, user=user.id)
    yield ids


def _refusals(engine) -> list[tuple[int, int]]:
    with Session(engine) as session:
        rows = session.exec(select(RecordingSpeakerDriftRefusal)).all()
        return sorted((r.recording_speaker_id, r.global_speaker_id) for r in rows)


def test_deleting_a_person_deletes_only_their_refusals(engine, library) -> None:
    """As the People delete does it: the person's links are loaded first."""
    with Session(engine) as session:
        links = session.exec(
            select(RecordingSpeaker).where(
                RecordingSpeaker.global_speaker_id == library.dana
            )
        ).all()
        assert [len(link.drift_refusals) for link in links] == [2]
        session.delete(session.get(GlobalSpeaker, library.dana))
        session.commit()

    assert _refusals(engine) == [(library.link, library.eli)]


def _wait_for_a_session_to_block(engine, timeout: float = 10.0) -> None:
    """Return once a session of this database is waiting on another's lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # A new transaction each time: pg_stat_activity is a per-transaction
        # snapshot, so one long transaction would never see the wait begin.
        with engine.connect() as connection:
            waiting = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
        if waiting:
            return
        time.sleep(0.05)
    raise AssertionError("the second session never waited for the first")


def test_two_sessions_refusing_a_link_for_one_person_both_commit(
    engine, library
) -> None:
    """A rename and the worker's auto-applied suggestion can judge one link at once.

    Both sessions link the speaker to Fay and offer its voiceprint to her
    before either has written, so each finds no refusal for Fay and records
    one. The second waits on the first's uncommitted row, then finds the
    refusal it was about to write already there: its commit goes through, as
    the column update this table replaced always did.
    """
    with Session(engine) as session:
        fay = GlobalSpeaker(
            name="Fay",
            user_id=library.user,
            embedding=[1.0, 0.0],
            embedding_version=EMBEDDING_METHOD_VERSION,
        )
        session.add(fay)
        link = session.get(RecordingSpeaker, library.link)
        link.embedding = [0.0, 1.0]
        link.embedding_version = EMBEDDING_METHOD_VERSION
        link.embedding_speech_seconds = 25.0
        session.add(link)
        session.commit()
        fay_id = fay.id

    with Session(engine) as first, Session(engine) as second:
        for session in (first, second):
            link = session.get(RecordingSpeaker, library.link)
            person = session.get(GlobalSpeaker, fay_id)
            link.global_speaker_id = person.id
            update = offer_linked_voiceprint(person, link, alpha=0.3)
            assert update.status == VoiceprintStatus.SKIPPED_DRIFT
            session.add(link)
        first.flush()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(second.commit)
            try:
                _wait_for_a_session_to_block(engine)
            finally:
                first.commit()
            pending.result(timeout=30)

    assert _refusals(engine) == sorted(
        [
            (library.link, library.dana),
            (library.link, library.eli),
            (library.link, fay_id),
        ]
    )


def test_deleting_a_link_deletes_all_of_its_refusals(engine, library) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM recording_speakers WHERE id = :id"),
            {"id": library.link},
        )

    assert _refusals(engine) == []
