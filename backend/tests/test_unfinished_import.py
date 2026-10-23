# ruff: noqa: F811 -- the API fixtures are imported from test_recording_pause_resume and requested by name
"""An import whose page went away mid-upload (issue #569).

A chunked import sits in UPLOADING while its parts arrive, and one whose page
closed or lost its connection stays there. It used to hold the capture lock, so
Start Meeting was refused with no dialog to resolve it, and it kept the live ASR
model resident in the worker. These cover the lock and the worker check counting
only live captures, and the server telling the client when an import stopped.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from backend.models.recording import (
    STALLED_IMPORT_AFTER,
    ClientStatus,
    Recording,
    RecordingStatus,
    import_looks_stalled,
    is_live_capture,
)
from backend.tests.test_recording_pause_resume import (  # noqa: F401 -- fixtures
    QUIET_BRIEFLY,
    QUIET_PAST_THRESHOLD,
    TRUSTED_ORIGIN,
    _set_last_heard,
    api_app,
    client,
    insert_capture,
    insert_recording,
    set_session_cookie,
    test_session_maker,
)
from backend.utils.time import utc_now

NOW = datetime(2026, 10, 23, 12, 0, 0)
IMPORT_GONE = STALLED_IMPORT_AFTER + timedelta(minutes=5)


def _recording(
    *,
    status: RecordingStatus = RecordingStatus.UPLOADING,
    client_status: ClientStatus | None = None,
    quiet_for: timedelta = IMPORT_GONE,
) -> Recording:
    return Recording(
        name="Quarterly review",
        audio_path="/tmp/quarterly-review.mp4",
        status=status,
        client_status=client_status,
        last_activity_at=NOW - quiet_for,
    )


async def insert_import(
    session_maker: sessionmaker,
    *,
    recording_id: int,
    public_id: str,
    quiet_for: timedelta,
) -> None:
    """A chunked import whose last part arrived ``quiet_for`` ago."""
    await insert_recording(
        session_maker,
        recording_id=recording_id,
        public_id=public_id,
        status="UPLOADING",
    )
    await _set_last_heard(session_maker, public_id, quiet_for=quiet_for, capture=False)


async def _last_activity(session_maker: sessionmaker, public_id: str) -> datetime:
    async with session_maker() as session:
        raw = (
            await session.execute(
                text("SELECT last_activity_at FROM recordings WHERE public_id = :id"),
                {"id": public_id},
            )
        ).scalar_one()
    return raw if isinstance(raw, datetime) else datetime.fromisoformat(raw)


def test_only_a_capture_is_a_live_capture() -> None:
    assert is_live_capture(_recording(client_status=ClientStatus.RECORDING))
    assert is_live_capture(
        _recording(status=RecordingStatus.PAUSED, client_status=ClientStatus.PAUSED)
    )
    # Paused before captures recorded a client status.
    assert is_live_capture(_recording(status=RecordingStatus.PAUSED))
    assert not is_live_capture(_recording())
    assert not is_live_capture(_recording(client_status=ClientStatus.IDLE))


def test_import_looks_stalled_only_for_a_quiet_import() -> None:
    def stalled(recording: Recording) -> bool:
        return import_looks_stalled(recording, now=NOW)

    assert stalled(_recording())
    assert not stalled(_recording(quiet_for=STALLED_IMPORT_AFTER))
    assert not stalled(_recording(quiet_for=timedelta(minutes=2)))
    # A capture has its own rule and its own dialog.
    assert not stalled(_recording(client_status=ClientStatus.RECORDING))
    assert not stalled(
        _recording(status=RecordingStatus.PAUSED, client_status=ClientStatus.PAUSED)
    )
    # Finished uploading: the import is being processed.
    assert not stalled(_recording(status=RecordingStatus.QUEUED))


@pytest.mark.anyio
async def test_init_starts_a_capture_beside_an_unfinished_import(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    set_session_cookie(client)
    await insert_import(
        test_session_maker,
        recording_id=5001,
        public_id="import-page-closed",
        quiet_for=IMPORT_GONE,
    )
    await insert_import(
        test_session_maker,
        recording_id=5002,
        public_id="import-still-uploading",
        quiet_for=timedelta(seconds=20),
    )

    response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Standup"},
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Standup"


@pytest.mark.anyio
async def test_init_still_refuses_beside_an_unfinished_capture(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    set_session_cookie(client)
    await insert_import(
        test_session_maker,
        recording_id=5101,
        public_id="import-page-closed",
        quiet_for=IMPORT_GONE,
    )
    await insert_capture(
        test_session_maker,
        recording_id=5102,
        public_id="capture-tab-gone",
        quiet_for=QUIET_PAST_THRESHOLD,
    )

    response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Standup"},
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["recording_id"] == "capture-tab-gone"


@pytest.mark.anyio
async def test_listing_flags_only_a_quiet_import(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    set_session_cookie(client)
    await insert_import(
        test_session_maker,
        recording_id=5201,
        public_id="import-page-closed",
        quiet_for=IMPORT_GONE,
    )
    await insert_import(
        test_session_maker,
        recording_id=5202,
        public_id="import-on-a-slow-link",
        quiet_for=STALLED_IMPORT_AFTER - timedelta(minutes=1),
    )
    await insert_capture(
        test_session_maker,
        recording_id=5203,
        public_id="capture-tab-gone",
        quiet_for=QUIET_PAST_THRESHOLD,
    )
    await insert_capture(
        test_session_maker,
        recording_id=5204,
        public_id="capture-recording",
        quiet_for=QUIET_BRIEFLY,
    )

    response = await client.get(
        "/api/v1/recordings",
        params=[("status", "PAUSED"), ("status", "UPLOADING"), ("user", "me")],
    )

    assert response.status_code == 200
    assert {row["id"]: row["import_stalled"] for row in response.json()} == {
        "import-page-closed": True,
        "import-on-a-slow-link": False,
        "capture-tab-gone": False,
        "capture-recording": False,
    }


@pytest.mark.anyio
async def test_each_part_moves_an_imports_last_activity(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    set_session_cookie(client)
    started = await client.post(
        "/api/v1/recordings/import/chunked/init",
        params={"filename": "quarterly-review.mp3"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert started.status_code == 200
    public_id = started.json()["id"]
    assert started.json()["import_stalled"] is False
    assert utc_now() - await _last_activity(test_session_maker, public_id) < timedelta(
        minutes=1
    )

    # The page went quiet long enough to be judged gone, then sent a part.
    await _set_last_heard(
        test_session_maker, public_id, quiet_for=IMPORT_GONE, capture=False
    )
    before = await _last_activity(test_session_maker, public_id)
    part = await client.post(
        "/api/v1/recordings/import/chunked/segment",
        params={"recording_id": public_id, "sequence": 1},
        files={"file": ("blob", b"\x00" * 1024, "application/octet-stream")},
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert part.status_code == 200
    assert (
        await _last_activity(test_session_maker, public_id) - before > IMPORT_GONE / 2
    )
    listed = await client.get(f"/api/v1/recordings/{public_id}")
    assert listed.json()["import_stalled"] is False


def test_worker_keeps_the_model_only_for_a_capture_that_is_recording(
    monkeypatch, tmp_path
) -> None:
    import backend.celery_app as celery_app_module
    import backend.core.db as db_module

    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    SQLModel.metadata.create_all(engine, tables=[Recording.__table__])

    @contextmanager
    def sync_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(db_module, "get_sync_session", sync_session)

    def active_with(*recordings: Recording) -> bool:
        with Session(engine) as session:
            session.execute(text("DELETE FROM recordings"))
            for index, recording in enumerate(recordings):
                # SQLite does not number a BIGINT key by itself.
                recording.id = index + 1
                recording.audio_path = f"/tmp/worker-{index}.wav"
                session.add(recording)
            session.commit()
        monkeypatch.setattr(celery_app_module, "_live_capture_check_cache", None)
        return celery_app_module._has_active_live_capture()

    assert active_with(_recording(client_status=ClientStatus.RECORDING)) is True
    assert active_with(_recording()) is False
    assert (
        active_with(
            _recording(status=RecordingStatus.PAUSED, client_status=ClientStatus.PAUSED)
        )
        is False
    )
    assert (
        active_with(_recording(), _recording(client_status=ClientStatus.RECORDING))
        is True
    )
