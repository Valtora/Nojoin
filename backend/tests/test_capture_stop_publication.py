"""Stop seals the live episode under PostgreSQL locks, with a second tab racing it."""

import asyncio
import time
import wave
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select

import backend.core.db as core_db
from backend.api.v1.endpoints import recordings
from backend.api.v1.endpoints.recordings import routes_capture
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.live_persistence import _persist_live_run
from backend.tests.test_canonical_publication_fence import SCHEMA
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils.browser_capture_audio import BrowserAudioFormatError
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.config_manager import config_manager
from backend.utils.recording_audio_sync import (
    sync_recording_audio_chunks_from_directory,
    sync_recording_audio_window_manifests,
)
from backend.utils.transcript_settling import (
    CapturePublicationToken,
    capture_publication_token,
    new_capture_settling_state,
)

CaptureHarness = tuple[Engine, int, str, int, Path]
Route = Callable[[AsyncSession, str, User], Coroutine[Any, Any, Any]]
SECOND_TAB = "second-tab"


@pytest.fixture
def capture_harness(
    publication_database: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: Any,
) -> CaptureHarness:
    engine, recording_id, _ = publication_database
    directory = tmp_path / "segments"
    directory.mkdir()
    with wave.open(str(directory / "0.wav"), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * 6400)

    def upload_dir(recording_id: int, *, create: bool = True) -> Path:
        return directory

    monkeypatch.setattr(recordings, "recording_upload_temp_dir", upload_dir)
    monkeypatch.setattr(recordings, "_recording_has_proxy", lambda recording: False)
    # The live publisher opens its own sessions.
    monkeypatch.setattr(core_db, "get_sync_session", lambda: Session(engine))
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        recording.audio_path = str(tmp_path / "capture.wav")
        session.add(recording)
        session.commit()
        public_id, owner_id = recording.public_id, recording.user_id
    return engine, recording_id, public_id, owner_id, directory


def _stop_route(db: AsyncSession, public_id: str, user: User) -> Any:
    return routes_capture.finalize_upload(
        public_id, epoch=0, client_elapsed_seconds=None, db=db, current_user=user
    )


def _pause_route(db: AsyncSession, public_id: str, user: User) -> Any:
    return routes_capture.pause_upload(public_id, epoch=0, db=db, current_user=user)


async def _request(harness: CaptureHarness, route: Route, tab: str) -> Any:
    engine, _, public_id, owner_id, _ = harness
    async_engine = create_async_engine(
        engine.url.set(drivername="postgresql+asyncpg"),
        connect_args={
            "server_settings": {
                "search_path": f"{SCHEMA},public",
                "application_name": tab,
            }
        },
    )
    user = User(id=owner_id, username="fence-owner", hashed_password="x")
    try:
        async with AsyncSession(async_engine, expire_on_commit=False) as session:
            try:
                return await route(session, public_id, user)
            except BaseException:
                await session.rollback()
                raise
    finally:
        await async_engine.dispose()


def _stop(harness: CaptureHarness) -> Any:
    return asyncio.run(_request(harness, _stop_route, "first-tab"))


def _capture_token(
    harness: CaptureHarness, monkeypatch: pytest.MonkeyPatch, marked: bool
) -> CapturePublicationToken:
    engine, recording_id, _, _, _ = harness
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: {
            "live_text_settling": "auto" if marked else "off"
        }.get(key, default),
    )
    with Session(engine) as session:
        transcript = session.exec(select(Transcript)).one()
        transcript.settling_state = new_capture_settling_state(capture_epoch=0)
        session.add(transcript)
        session.commit()
        return capture_publication_token(
            session.get(Recording, recording_id), transcript
        )


def _publish(recording_id: int, token: CapturePublicationToken) -> bool:
    return _persist_live_run(
        recording_id=recording_id,
        sequence=0,
        run=[0],
        new_segments=[],
        state={},
        live_config={"_settling_device": "cpu"},
        live_config_hash="test",
        live_model_name="test",
        expected_token=token,
    ).accepted


def _wait_for_lock_wait(engine: Engine, tab: str) -> None:
    """Block until the named tab's request is queued on a lock Stop holds."""
    deadline = time.monotonic() + 10
    with engine.connect() as connection:
        while time.monotonic() < deadline:
            waiting = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name = :tab AND wait_event_type = 'Lock'"
                ),
                {"tab": tab},
            ).scalar_one()
            if waiting:
                return
            # pg_stat_activity is read once per transaction; start a new one.
            connection.rollback()
            time.sleep(0.05)
    pytest.fail(f"{tab} never waited for Stop's locks")


@pytest.mark.parametrize("second", ["stop", "pause"])
def test_a_second_tab_waits_for_stop_and_is_refused(
    capture_harness: CaptureHarness,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list[tuple[str, object, object]],
    second: str,
) -> None:
    engine, recording_id, _, _, _ = capture_harness
    token = _capture_token(capture_harness, monkeypatch, True)
    route = _stop_route if second == "stop" else _pause_route
    original = recordings.write_browser_master_audio

    async def race() -> tuple[Any, int]:
        loop = asyncio.get_running_loop()
        holding = asyncio.Event()

        def build_while_second_tab_waits(paths: list[Path], destination: str) -> float:
            loop.call_soon_threadsafe(holding.set)
            _wait_for_lock_wait(engine, SECOND_TAB)
            return original(paths, destination)

        monkeypatch.setattr(
            recordings, "write_browser_master_audio", build_while_second_tab_waits
        )

        async def second_tab() -> int:
            await holding.wait()
            with pytest.raises(HTTPException) as refused:
                await _request(capture_harness, route, SECOND_TAB)
            return refused.value.status_code

        return await asyncio.gather(
            _request(capture_harness, _stop_route, "first-tab"), second_tab()
        )

    response, refused = asyncio.run(race())

    assert response.status == RecordingStatus.QUEUED
    assert refused == 409
    names = [name for name, _, _ in stub_celery_dispatch]
    assert names.count("backend.worker.tasks.process_recording_task") == 1
    with Session(engine) as session:
        assert session.exec(select(Transcript)).one().settling_state["sealed"]
    assert not _publish(recording_id, token)


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, BrowserAudioFormatError])
def test_failed_stop_seals_before_abandoning_audio_and_fences_its_publisher(
    capture_harness: CaptureHarness,
    monkeypatch: pytest.MonkeyPatch,
    marked: bool,
    failure: type[Exception],
) -> None:
    engine, recording_id, _, _, directory = capture_harness
    token = _capture_token(capture_harness, monkeypatch, marked)
    failed = directory.parent / "failed"
    masters: list[Path] = []

    def build(paths: list[Path], destination: str) -> float:
        masters.append(Path(destination))
        masters[-1].write_bytes(b"partial")
        raise failure("failed encoding")

    def move(recording_id: int, *, logger: Any) -> Path:
        directory.rename(failed)
        return failed

    monkeypatch.setattr(recordings, "write_browser_master_audio", build)
    monkeypatch.setattr(recordings, "move_recording_upload_to_failed", move)
    with pytest.raises(HTTPException) as rejected:
        _stop(capture_harness)
    assert rejected.value.status_code == (
        400 if failure is BrowserAudioFormatError else 500
    )
    assert (failed / "0.wav").exists()
    assert all(not path.exists() for path in masters)
    with Session(engine) as session:
        transcript = session.exec(select(Transcript)).one()
        assert bool(transcript.settling_state) == marked
        if marked:
            assert transcript.settling_state["sealed"]
        recording = session.get(Recording, recording_id)
        expected = (
            RecordingStatus.ERROR
            if failure is BrowserAudioFormatError
            else RecordingStatus.UPLOADING
        )
        assert recording.status == expected
    # Unmarked captures keep upstream's generic-error behaviour.
    assert _publish(recording_id, token) == (not marked and failure is RuntimeError)


def test_stop_waits_for_a_publisher_before_touching_the_window_rows(
    capture_harness: CaptureHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, recording_id, _, _, directory = capture_harness
    _capture_token(capture_harness, monkeypatch, True)
    with Session(engine) as session:
        sync_recording_audio_chunks_from_directory(
            session,
            recording_id=recording_id,
            source_kind="browser",
            suffix=".wav",
            temp_dir=directory,
        )
        sync_recording_audio_window_manifests(
            session, recording_id=recording_id, source_kind="browser", seal_tail=False
        )
        session.commit()

    async def overlap() -> Any:
        with Session(engine) as publisher:
            # A live publisher holds Transcript, then marks windows processed.
            _load_transcript(publisher, recording_id, lock=True)
            stop = asyncio.create_task(
                _request(capture_harness, _stop_route, "first-tab")
            )
            await asyncio.to_thread(_wait_for_lock_wait, engine, "first-tab")
            marked = await asyncio.to_thread(
                publisher.execute,
                text(
                    "UPDATE recording_audio_window_manifests SET status = status "
                    "WHERE recording_id = :recording_id"
                ),
                {"recording_id": recording_id},
            )
            assert marked.rowcount
            publisher.commit()
        return await stop

    assert asyncio.run(overlap()).status == RecordingStatus.QUEUED
