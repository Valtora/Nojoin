"""A meeting that has finished once stays finished, whatever its status says.

Notes regeneration, speaker inference and a reprocess each show a finished
meeting as QUEUED or PROCESSING, so Discard cannot judge by status (issue #833).
``Recording.first_processed_at`` is stamped on the first flush that makes a
recording PROCESSED, and ``recording_is_discardable`` reads it.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

from backend.models.recording import (
    ClientStatus,
    Recording,
    RecordingStatus,
    recording_is_discardable,
)
from backend.models.recording_public import serialize_recording
from backend.tests.sqlite_schemas import RECORDINGS_SCHEMA


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text(RECORDINGS_SCHEMA))
    with Session(engine) as session:
        yield session
    engine.dispose()


def _capture(session: Session) -> Recording:
    recording = Recording(
        name="Standup",
        audio_path="/tmp/standup.wav",
        status=RecordingStatus.UPLOADING,
        client_status=ClientStatus.RECORDING,
        user_id=1,
    )
    session.add(recording)
    session.commit()
    return recording


def _move(session: Session, recording: Recording, status: RecordingStatus) -> None:
    recording.status = status
    session.add(recording)
    session.commit()


def test_a_capture_is_discardable_until_its_first_pass_finishes(
    session: Session,
) -> None:
    recording = _capture(session)
    for status in (
        RecordingStatus.UPLOADING,
        RecordingStatus.PAUSED,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    ):
        _move(session, recording, status)
        assert recording.first_processed_at is None
        assert recording_is_discardable(recording), status

    _move(session, recording, RecordingStatus.PROCESSED)

    stamped = recording.first_processed_at
    assert isinstance(stamped, datetime)
    assert not recording_is_discardable(recording)


@pytest.mark.parametrize(
    "later",
    [
        # Notes regenerating and speaker inference hold it in PROCESSING; a
        # reprocess queues it; a failed reprocess leaves it in ERROR.
        [RecordingStatus.PROCESSING],
        [RecordingStatus.QUEUED, RecordingStatus.PROCESSING],
        [RecordingStatus.QUEUED, RecordingStatus.ERROR, RecordingStatus.QUEUED],
    ],
)
def test_a_finished_meeting_stays_finished_through_later_runs(
    session: Session, later: list[RecordingStatus]
) -> None:
    recording = _capture(session)
    _move(session, recording, RecordingStatus.PROCESSED)
    stamped = recording.first_processed_at

    for status in later:
        _move(session, recording, status)
        assert recording.first_processed_at == stamped
        assert not recording_is_discardable(recording)

    _move(session, recording, RecordingStatus.PROCESSED)
    assert recording.first_processed_at == stamped


def test_a_recording_created_processed_is_stamped(session: Session) -> None:
    # The sample meeting and a restored row are inserted as PROCESSED.
    recording = Recording(
        name="Sample", audio_path="/tmp/sample.wav", status=RecordingStatus.PROCESSED
    )
    session.add(recording)
    session.commit()

    assert recording.first_processed_at is not None
    stored = session.execute(
        text("SELECT first_processed_at FROM recordings WHERE id = :id"),
        {"id": recording.id},
    ).scalar_one()
    assert stored is not None


def test_a_failed_first_pass_stays_discardable_when_retried(session: Session) -> None:
    recording = _capture(session)
    _move(session, recording, RecordingStatus.QUEUED)
    _move(session, recording, RecordingStatus.ERROR)
    _move(session, recording, RecordingStatus.QUEUED)

    assert recording.first_processed_at is None
    assert recording_is_discardable(recording)


def test_the_payload_reports_what_discard_would_do(session: Session) -> None:
    recording = _capture(session)
    _move(session, recording, RecordingStatus.PROCESSING)
    assert serialize_recording(recording).discardable is True

    _move(session, recording, RecordingStatus.PROCESSED)
    assert serialize_recording(recording).discardable is False

    _move(session, recording, RecordingStatus.PROCESSING)
    assert serialize_recording(recording).discardable is False
