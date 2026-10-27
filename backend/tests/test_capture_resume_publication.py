"""Capture status transitions publish once, even without transcript words."""

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from sqlmodel import select

from backend.api.v1.endpoints.recordings import routes_capture as capture
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests import test_live_settling_lifecycle as lifecycle
from backend.utils.transcript_settling import new_capture_settling_state

lifecycle_sessions = lifecycle.lifecycle_sessions


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def committed_transcript_changes(
    lifecycle_sessions: async_sessionmaker[AsyncSession],
) -> AsyncIterator[list[dict[int, int]]]:
    """Observe actual committed sequence changes through a separate connection.

    The old fixture recorded Redis publication calls. Keep the exact commit,
    no-op and rollback expectations against the durable facts polling reads.
    """
    async with lifecycle_sessions() as db:
        engine = db.sync_session.get_bind()
    previous: dict[int, int] = {}
    committed: list[dict[int, int]] = []

    def observe_commit(session: Session) -> None:
        if session.get_bind() is not engine or session.get_nested_transaction():
            return
        with engine.connect() as connection:
            current = dict(
                connection.execute(
                    select(Transcript.recording_id, Transcript.change_sequence)
                ).all()
            )
        changes = {
            key: value for key, value in current.items() if previous.get(key) != value
        }
        if changes:
            committed.append(changes)
        previous.clear()
        previous.update(current)

    event.listen(Session, "after_commit", observe_commit)
    try:
        yield committed
    finally:
        event.remove(Session, "after_commit", observe_commit)


async def _empty_capture(
    sessions: async_sessionmaker[AsyncSession], marked: bool
) -> tuple[int, int, str]:
    async with sessions() as db:
        user = User(username="status-publication", hashed_password="x")
        db.add(user)
        await db.flush()
        recording = Recording(
            name="Empty capture",
            audio_path="/data/live.wav",
            user_id=user.id,
            status=RecordingStatus.UPLOADING,
            client_status=ClientStatus.RECORDING,
            capture_epoch=4,
        )
        db.add(recording)
        await db.flush()
        db.add(
            Transcript(
                recording_id=recording.id,
                segments=[],
                settling_state=new_capture_settling_state(capture_epoch=4)
                if marked
                else None,
            )
        )
        await db.commit()
        return user.id, recording.id, recording.public_id


async def _status(
    sessions: async_sessionmaker[AsyncSession], recording_id: int
) -> tuple[int, RecordingStatus, ClientStatus]:
    async with sessions() as db:
        recording = await db.get(Recording, recording_id)
        transcript = (
            await db.execute(
                select(Transcript).where(Transcript.recording_id == recording_id)
            )
        ).scalar_one()
        return transcript.change_sequence, recording.status, recording.client_status


@pytest.mark.anyio
@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("initially_paused", [False, True])
async def test_empty_capture_lifecycle_publishes_only_status_changes(
    lifecycle_sessions: async_sessionmaker[AsyncSession],
    committed_transcript_changes: list[dict[int, int]],
    marked: bool,
    initially_paused: bool,
) -> None:
    sessions = lifecycle_sessions
    user_id, recording_id, public_id = await _empty_capture(sessions, marked)
    if initially_paused:
        async with sessions() as db:
            recording = await db.get(Recording, recording_id)
            recording.status = RecordingStatus.PAUSED
            recording.client_status = ClientStatus.PAUSED
            await db.commit()
    sequence, _, _ = await _status(sessions, recording_id)
    committed_transcript_changes.clear()
    user = SimpleNamespace(id=user_id)
    transitions = [
        (capture.pause_upload, RecordingStatus.PAUSED, 1),
        (capture.pause_upload, RecordingStatus.PAUSED, 0),
        (capture.resume_upload, RecordingStatus.UPLOADING, 1),
        (capture.resume_upload, RecordingStatus.UPLOADING, 0),
    ]
    for action, expected, increment in transitions[2 if initially_paused else 0 :]:
        async with sessions() as db:
            await action(public_id, epoch=4, db=db, current_user=user)
        sequence += increment
        client_status = (
            ClientStatus.PAUSED
            if expected == RecordingStatus.PAUSED
            else ClientStatus.RECORDING
        )
        assert await _status(sessions, recording_id) == (
            sequence,
            expected,
            client_status,
        )
        assert committed_transcript_changes == (
            [{recording_id: sequence}] if increment else []
        )
        committed_transcript_changes.clear()


@pytest.mark.anyio
async def test_pause_word_closure_and_status_share_one_publication(
    lifecycle_sessions: async_sessionmaker[AsyncSession],
    committed_transcript_changes: list[dict[int, int]],
) -> None:
    sessions = lifecycle_sessions
    user_id, recording_id, public_id = await lifecycle._seed(sessions)
    sequence, _, _ = await _status(sessions, recording_id)
    committed_transcript_changes.clear()
    async with sessions() as db:
        await capture.pause_upload(
            public_id, epoch=4, db=db, current_user=SimpleNamespace(id=user_id)
        )
    assert (await _status(sessions, recording_id))[0] == sequence + 1
    assert committed_transcript_changes == [{recording_id: sequence + 1}]
    _, rows = await lifecycle._snapshot(sessions, recording_id)
    assert rows[0][1][0]["locked"] is True
    assert rows[0][1][0]["lock_reason"] == "closure"


@pytest.mark.anyio
@pytest.mark.parametrize("transition", ["pause", "resume"])
async def test_failed_lifecycle_transaction_rolls_back_status_and_hint(
    lifecycle_sessions: async_sessionmaker[AsyncSession],
    committed_transcript_changes: list[dict[int, int]],
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    sessions = lifecycle_sessions
    user_id, recording_id, public_id = await _empty_capture(sessions, False)
    user = SimpleNamespace(id=user_id)
    if transition == "resume":
        async with sessions() as db:
            await capture.pause_upload(public_id, epoch=4, db=db, current_user=user)
    before = await _status(sessions, recording_id)
    committed_transcript_changes.clear()

    async def fail_commit(db: AsyncSession) -> None:
        await db.flush()
        raise RuntimeError("lifecycle commit failed")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    action = capture.pause_upload if transition == "pause" else capture.resume_upload
    async with sessions() as db:
        with pytest.raises(RuntimeError, match="lifecycle commit failed"):
            await action(public_id, epoch=4, db=db, current_user=user)
        await db.rollback()
    assert await _status(sessions, recording_id) == before
    assert committed_transcript_changes == []
