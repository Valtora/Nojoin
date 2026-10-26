"""Naming decisions recheck preloaded speaker identities after publication waits."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, current_thread
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlmodel import Session, select

from backend.core.db import driver_urls
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.tests import test_canonical_transcript_phase1 as schemas
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline import update_recording_speaker_identity
from backend.utils.speaker_names.review import dismiss_pending_name, link_voice_match


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    schema = None
    if request.param == "postgres":
        schema = f"name_review_fence_{uuid4().hex[:12]}"
        url = request.getfixturevalue("postgres_test_url")
        engine = create_engine(
            driver_urls(url)[1],
            connect_args={"options": f"-csearch_path={schema},public"},
        )
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        fresh_schema(engine, schema)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'name-review.db'}")
        with engine.begin() as connection:
            for name, statement in vars(schemas).items():
                if name.endswith("_SCHEMA"):
                    connection.execute(text(statement))
    try:
        yield engine
    finally:
        engine.dispose()


def _seed(engine: Engine) -> tuple[int, int, int]:
    with Session(engine) as session:
        recording = Recording(
            name="Capture",
            audio_path="/tmp/name-review.wav",
            status=RecordingStatus.UPLOADING,
        )
        person = GlobalSpeaker(name="Machine match")
        session.add_all([recording, person])
        session.flush()
        speaker = RecordingSpeaker(
            recording_id=recording.id,
            diarization_label="SPEAKER_00",
            local_name="Pending name",
            name_source="heuristic",
            identity_locked=False,
        )
        session.add_all([Transcript(recording_id=recording.id, text=""), speaker])
        session.commit()
        return recording.id, speaker.id, person.id


@pytest.mark.parametrize("action", ["scan", "dismiss"])
def test_a_preloaded_candidate_cannot_overwrite_a_confirmed_name(
    engine: Engine, action: str
) -> None:
    recording_id, speaker_id, person_id = _seed(engine)
    with Session(engine) as stale:
        candidate = stale.get(RecordingSpeaker, speaker_id)
        person = stale.get(GlobalSpeaker, person_id)
        assert candidate is not None and person is not None
        assert not candidate.identity_locked
        with Session(engine) as human:
            update_recording_speaker_identity(
                human,
                recording_id=recording_id,
                diarization_label="SPEAKER_00",
                new_speaker_name="Human choice",
            )
            human.commit()
        if action == "scan":
            assert not link_voice_match(stale, candidate, person, 0.99)
        else:
            assert dismiss_pending_name(stale, candidate, None) == "Human choice"
        stale.commit()
    with Session(engine) as check:
        candidate = check.get(RecordingSpeaker, speaker_id)
        assert candidate is not None
        assert candidate.local_name == "Human choice"
        assert candidate.identity_locked
        assert candidate.global_speaker_id is None


def test_scan_keeps_an_identity_staged_in_its_own_transaction(engine: Engine) -> None:
    _, speaker_id, person_id = _seed(engine)
    with Session(engine) as session:
        candidate = session.get(RecordingSpeaker, speaker_id)
        person = session.get(GlobalSpeaker, person_id)
        assert candidate is not None and person is not None
        candidate.local_name = "Staged choice"
        candidate.identity_locked = True
        candidate.global_speaker = person
        assert not link_voice_match(session, candidate, person, 0.99)
        session.commit()
    with Session(engine) as check:
        candidate = check.get(RecordingSpeaker, speaker_id)
        assert candidate is not None
        assert candidate.local_name == "Staged choice"
        assert candidate.identity_locked
        assert candidate.global_speaker_id == person_id


def test_scan_still_links_a_replaceable_pending_name(engine: Engine) -> None:
    _, speaker_id, person_id = _seed(engine)
    with Session(engine) as session:
        candidate = session.get(RecordingSpeaker, speaker_id)
        person = session.get(GlobalSpeaker, person_id)
        assert candidate is not None and person is not None
        assert link_voice_match(session, candidate, person, 0.99)
        session.commit()
    with Session(engine) as check:
        candidate = check.get(RecordingSpeaker, speaker_id)
        assert candidate is not None
        assert candidate.global_speaker_id == person_id
        assert candidate.local_name is None
        assert candidate.name_source == "voiceprint"


def test_scan_refuses_a_link_committed_after_candidates_were_loaded(
    engine: Engine,
) -> None:
    _, speaker_id, person_id = _seed(engine)
    with Session(engine) as stale:
        candidate = stale.get(RecordingSpeaker, speaker_id)
        person = stale.get(GlobalSpeaker, person_id)
        assert candidate is not None and person is not None
        with Session(engine) as other:
            other_person = GlobalSpeaker(name="Earlier match")
            other.add(other_person)
            other.flush()
            current = other.get(RecordingSpeaker, speaker_id)
            assert current is not None
            current.local_name = None
            current.global_speaker_id = other_person.id
            current.name_source = "voiceprint"
            other.add(current)
            other.commit()
            expected = other_person.id
        assert not link_voice_match(stale, candidate, person, 0.99)
        stale.commit()
    with Session(engine) as check:
        candidate = check.exec(select(RecordingSpeaker)).one()
        assert candidate.global_speaker_id == expected


@pytest.mark.parametrize("action", ["scan", "dismiss"])
def test_waiting_naming_work_rechecks_the_human_identity_after_the_fence(
    engine: Engine, action: str
) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("Row-lock waiting requires PostgreSQL")
    recording_id, speaker_id, person_id = _seed(engine)
    loaded, start, entered, finished = Event(), Event(), Event(), Event()
    outcome: dict[str, object] = {}

    def worker() -> None:
        try:
            with Session(engine) as session:
                session.execute(text("SET statement_timeout = '5s'"))
                candidate = session.get(RecordingSpeaker, speaker_id)
                person = session.get(GlobalSpeaker, person_id)
                assert candidate is not None and person is not None
                loaded.set()
                assert start.wait(5)
                outcome["result"] = (
                    link_voice_match(session, candidate, person, 0.99)
                    if action == "scan"
                    else dismiss_pending_name(session, candidate, None)
                )
                session.commit()
        finally:
            finished.set()

    def about_to_lock(
        conn: object, cursor: object, statement: str, *args: object
    ) -> None:
        if (
            current_thread().name.startswith("stale-name-writer")
            and "FROM transcripts" in statement
            and "FOR UPDATE" in statement
        ):
            entered.set()

    event.listen(engine, "before_cursor_execute", about_to_lock)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale-name-writer")
    future = executor.submit(worker)
    try:
        assert loaded.wait(5)
        with Session(engine) as human:
            update_recording_speaker_identity(
                human,
                recording_id=recording_id,
                diarization_label="SPEAKER_00",
                new_speaker_name="Human choice",
            )
            start.set()
            assert entered.wait(5)
            assert not finished.wait(0.1), "Naming writer did not wait for Transcript"
            human.commit()
        assert finished.wait(5)
        future.result(timeout=1)
        assert outcome["result"] == (False if action == "scan" else "Human choice")
        with Session(engine) as check:
            candidate = check.get(RecordingSpeaker, speaker_id)
            assert candidate is not None
            assert candidate.local_name == "Human choice"
            assert candidate.identity_locked
    finally:
        start.set()
        executor.shutdown(wait=True)
        event.remove(engine, "before_cursor_execute", about_to_lock)
