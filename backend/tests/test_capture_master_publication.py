"""Stop prepares audio without blocking edits and rejects obsolete masters."""

import asyncio
import wave
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select

from backend.api.v1.endpoints import recordings
from backend.api.v1.endpoints.recordings import capture_master, routes_capture
from backend.models.pipeline import RecordingAudioChunk
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.live_persistence import _persist_live_run
from backend.tests.test_canonical_publication_fence import (
    SCHEMA,
)
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.utils.browser_capture_audio import BrowserAudioFormatError
from backend.utils.canonical_pipeline import update_utterance_text
from backend.utils.config_manager import config_manager
from backend.utils.recording_audio_sync import lock_recording_audio
from backend.utils.transcript_settling import (
    CapturePublicationToken,
    capture_publication_token,
    new_capture_settling_state,
)

CaptureHarness = tuple[Engine, int, str, int, Path]


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
        if create:
            directory.mkdir(exist_ok=True)
        return directory

    monkeypatch.setattr(recordings, "recording_upload_temp_dir", upload_dir)
    monkeypatch.setattr(recordings, "_recording_has_proxy", lambda recording: False)
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        recording.audio_path = str(tmp_path / "capture.wav")
        session.add(recording)
        session.commit()
        public_id, owner_id = recording.public_id, recording.user_id
    return engine, recording_id, public_id, owner_id, directory


def _stop(harness: CaptureHarness) -> Any:
    engine, _, public_id, owner_id, _ = harness

    async def run() -> Any:
        async_engine = create_async_engine(
            engine.url.set(drivername="postgresql+asyncpg"),
            connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
        )
        try:
            async with AsyncSession(async_engine, expire_on_commit=False) as session:
                try:
                    return await routes_capture.finalize_upload(
                        public_id,
                        epoch=0,
                        client_elapsed_seconds=None,
                        db=session,
                        current_user=User(
                            id=owner_id, username="fence-owner", hashed_password="x"
                        ),
                    )
                except BaseException:
                    await session.rollback()
                    raise
        finally:
            await async_engine.dispose()

    return asyncio.run(run())


@pytest.mark.parametrize("phase", ["hash", "master"])
def test_an_edit_and_audio_lock_proceed_during_stop_io(
    capture_harness: CaptureHarness, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    engine, recording_id, _, _, _ = capture_harness
    edited: list[str] = []

    def edit() -> None:
        with Session(engine) as editor:
            editor.execute(text("SET lock_timeout = '250ms'"))
            update_utterance_text(
                editor,
                recording_id=recording_id,
                utterance_public_id="fence-a",
                text="edited during Stop",
            )
            editor.commit()
        # An audio-only preparation lock would also let the edit pass, but a
        # lifecycle request waiting for it would hold Transcript and stall edits.
        with Session(engine) as uploader:
            uploader.execute(text("SET lock_timeout = '250ms'"))
            lock_recording_audio(uploader, recording_id)
            uploader.commit()
        edited.append(phase)

    target = capture_master if phase == "hash" else recordings
    name = (
        "build_recording_audio_chunk_fields"
        if phase == "hash"
        else "write_browser_master_audio"
    )
    original = getattr(target, name)

    def prepare(*args: Any, **kwargs: Any) -> Any:
        edit()
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, prepare)
    response = _stop(capture_harness)
    assert edited == [phase]
    assert response.status == RecordingStatus.QUEUED
    with Session(engine) as session:
        transcript = session.exec(select(Transcript)).one()
        assert "edited during Stop" in transcript.text


@pytest.mark.parametrize(
    "change", ["epoch", "state", "generation", "audio", "file", "master"]
)
def test_stop_rejects_preparation_changed_before_the_fence(
    capture_harness: CaptureHarness, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    engine, recording_id, _, _, directory = capture_harness
    with Session(engine) as session:
        transcript = session.exec(select(Transcript)).one()
        transcript.settling_state = {
            "generation": "before",
            "phase": "live",
            "sealed": False,
        }
        session.add(transcript)
        session.commit()
    original = capture_master.PreparedCaptureMaster.build
    candidates: list[Path] = []

    async def build(prepared: capture_master.PreparedCaptureMaster) -> None:
        await original(prepared)
        candidates.append(prepared.candidate_path)
        with Session(engine) as other:
            recording = other.get(Recording, recording_id)
            if change == "epoch":
                recording.capture_epoch += 1
            elif change == "state":
                recording.status = RecordingStatus.PAUSED
            elif change == "generation":
                transcript = other.exec(select(Transcript)).one()
                transcript.settling_state = {
                    **transcript.settling_state,
                    "generation": "after",
                }
                other.add(transcript)
            elif change == "audio":
                chunk = other.exec(select(RecordingAudioChunk)).one()
                chunk.sha256 = "changed"
                other.add(chunk)
            elif change == "file":
                with (directory / "0.wav").open("ab") as audio:
                    audio.write(b"changed")
            else:
                prepared.candidate_path.unlink()
            other.add(recording)
            other.commit()

    monkeypatch.setattr(capture_master.PreparedCaptureMaster, "build", build)
    with pytest.raises(HTTPException) as rejected:
        _stop(capture_harness)
    assert rejected.value.status_code == 409
    assert all(not path.exists() for path in candidates)
    with Session(engine) as session:
        recording = session.get(Recording, recording_id)
        assert recording.status != RecordingStatus.QUEUED
        assert recording.file_size_bytes is None
        assert not Path(recording.audio_path).exists()
        assert not session.exec(select(Transcript)).one().settling_state["sealed"]


@pytest.mark.parametrize("change", ["changed", "removed"])
def test_a_segment_changed_while_hashing_is_not_registered_or_published(
    capture_harness: CaptureHarness, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    engine, _, _, _, directory = capture_harness
    original = capture_master.build_recording_audio_chunk_fields

    def changed_segment(**kwargs: Any) -> dict[str, Any]:
        if change == "removed":
            (directory / "0.wav").unlink()
        fields = original(**kwargs)
        with (directory / "0.wav").open("ab") as audio:
            audio.write(b"new tail")
        return fields

    monkeypatch.setattr(
        capture_master, "build_recording_audio_chunk_fields", changed_segment
    )
    with pytest.raises(HTTPException) as rejected:
        _stop(capture_harness)
    assert rejected.value.status_code == 409
    with Session(engine) as session:
        assert session.exec(select(RecordingAudioChunk)).all() == []


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


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, BrowserAudioFormatError])
def test_failed_stop_seals_before_abandoning_audio_and_fences_its_publisher(
    capture_harness: CaptureHarness,
    monkeypatch: pytest.MonkeyPatch,
    marked: bool,
    failure: type[Exception],
) -> None:
    import backend.core.db as core_db

    engine, recording_id, _, _, directory = capture_harness
    token = _capture_token(capture_harness, monkeypatch, marked)
    failed = directory.parent / "failed"
    candidates: list[Path] = []

    def build(paths: list[Path], destination: Path) -> float:
        candidates.append(destination)
        destination.write_bytes(b"partial")
        raise failure("failed encoding")

    def move(recording_id: int, *, logger: Any) -> Path:
        directory.rename(failed)
        return failed

    monkeypatch.setattr(recordings, "write_browser_master_audio", build)
    monkeypatch.setattr(recordings, "move_recording_upload_to_failed", move)
    monkeypatch.setattr(core_db, "get_sync_session", lambda: Session(engine))
    with pytest.raises(HTTPException) as rejected:
        _stop(capture_harness)
    assert rejected.value.status_code == (
        400 if failure is BrowserAudioFormatError else 500
    )
    assert (failed / "0.wav").exists()
    assert all(not path.exists() for path in candidates)
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
    outcome = _persist_live_run(
        recording_id=recording_id,
        sequence=0,
        run=[0],
        new_segments=[],
        state={},
        live_config={"_settling_device": "cpu"},
        live_config_hash="test",
        live_model_name="test",
        expected_token=token,
    )
    # Legacy unmarked captures retain their original generic-error behaviour.
    assert outcome.accepted == (not marked and failure is RuntimeError)


@pytest.mark.parametrize("failure", [RuntimeError, BrowserAudioFormatError])
def test_a_stale_failed_stop_cannot_move_the_new_epoch_audio(
    capture_harness: CaptureHarness,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
) -> None:
    engine, recording_id, _, _, directory = capture_harness
    _capture_token(capture_harness, monkeypatch, True)

    def build(paths: list[Path], destination: Path) -> float:
        with Session(engine) as session:
            recording = session.get(Recording, recording_id)
            recording.capture_epoch += 1
            session.add(recording)
            session.commit()
        raise failure("old capture encoding failed")

    def forbidden_move(*args: Any, **kwargs: Any) -> None:
        pytest.fail("A stale Stop moved another capture's source audio")

    monkeypatch.setattr(recordings, "write_browser_master_audio", build)
    monkeypatch.setattr(recordings, "move_recording_upload_to_failed", forbidden_move)
    with pytest.raises(HTTPException) as rejected:
        _stop(capture_harness)
    assert rejected.value.status_code == 409
    assert (directory / "0.wav").exists()
    with Session(engine) as session:
        assert not session.exec(select(Transcript)).one().settling_state["sealed"]


def test_holder_resume_after_generic_error_seal_keeps_its_audio(
    capture_harness: CaptureHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, recording_id, _, _, directory = capture_harness
    _capture_token(capture_harness, monkeypatch, True)

    def build(paths: list[Path], destination: Path) -> float:
        raise RuntimeError("encoding failed")

    real_lock = recordings._lock_capture
    resumed = False

    async def resume_before_recovery(db: AsyncSession, recording: Recording) -> None:
        nonlocal resumed
        with Session(engine) as session:
            transcript = session.exec(select(Transcript)).one()
            if transcript.settling_state["sealed"] and not resumed:
                transcript.settling_state = {
                    **transcript.settling_state,
                    "generation": "holder-resumed",
                    "sealed": False,
                    "phase": "live",
                }
                session.add(transcript)
                session.commit()
                resumed = True
        await real_lock(db, recording)

    def forbidden_move(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Recovery moved a holder-resumed capture's source audio")

    monkeypatch.setattr(recordings, "write_browser_master_audio", build)
    monkeypatch.setattr(recordings, "_lock_capture", resume_before_recovery)
    monkeypatch.setattr(recordings, "move_recording_upload_to_failed", forbidden_move)
    with pytest.raises(HTTPException) as rejected:
        _stop(capture_harness)
    assert rejected.value.status_code == 409
    assert resumed and (directory / "0.wav").exists()
    with Session(engine) as session:
        assert session.get(Recording, recording_id).capture_epoch == 0
        assert not session.exec(select(Transcript)).one().settling_state["sealed"]
