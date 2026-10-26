"""Episode provenance and policy publication against real PostgreSQL locks."""

import threading
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, func, text
from sqlmodel import Session, select

from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtteranceEvent
from backend.models.transcript import Transcript
from backend.processing.live_persistence import LivePublicationResult, _persist_live_run
from backend.tests.postgres_setup import fresh_schema
from backend.tests.storage_hygiene_harness import add_recording
from backend.utils.config_manager import config_manager
from backend.utils.transcript_settling import (
    CapturePublicationToken,
    capture_publication_token,
    load_capture_publication,
    new_capture_settling_state,
    update_capture_settling_state,
)


@pytest.fixture
def capture_engine(postgres_test_url: str) -> Iterator[Engine]:
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": "-csearch_path=capture_publication_test,public"},
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(engine, "capture_publication_test")
    yield engine
    engine.dispose()


def _seed(engine: Engine, monkeypatch: pytest.MonkeyPatch, marked: bool) -> int:
    from backend.models.recording import RecordingStatus

    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: {
            "live_text_settling": "auto" if marked else "off",
        }.get(key, default),
    )
    recording_id = add_recording(
        engine, "capture.wav", status=RecordingStatus.UPLOADING
    )
    with Session(engine) as session:
        session.add(
            Transcript(
                recording_id=recording_id,
                segments=[],
                settling_state=new_capture_settling_state(capture_epoch=0),
            )
        )
        session.commit()
    return recording_id


def _publish(
    recording_id: int, token: CapturePublicationToken, *, device: str = "cpu"
) -> LivePublicationResult:
    return _persist_live_run(
        recording_id=recording_id,
        sequence=0,
        run=[0],
        new_segments=[],
        state={},
        live_config={"_settling_device": device},
        live_config_hash="test",
        live_model_name="test",
        expected_token=token,
    )


@pytest.mark.parametrize("marked", [False, True])
def test_a_run_waiting_on_resume_rejects_its_old_episode(
    capture_engine: Engine, monkeypatch: pytest.MonkeyPatch, marked: bool
) -> None:
    import backend.core.db as core_db

    recording_id = _seed(capture_engine, monkeypatch, marked)
    monkeypatch.setattr(core_db, "get_sync_session", lambda: Session(capture_engine))
    with Session(capture_engine) as snapshot:
        transcript, recording = load_capture_publication(
            snapshot, recording_id, lock=False
        )
        token = capture_publication_token(recording, transcript)
        sequence = transcript.change_sequence
    outcome: list[LivePublicationResult] = []
    started = threading.Event()

    def publish() -> None:
        started.set()
        outcome.append(_publish(recording_id, token))

    with Session(capture_engine) as resume:
        transcript, recording = load_capture_publication(resume, recording_id)
        thread = threading.Thread(target=publish)
        thread.start()
        assert started.wait(2)
        thread.join(0.1)
        assert thread.is_alive(), "publication did not wait for the episode fence"
        recording.capture_epoch += 1
        update_capture_settling_state(
            transcript,
            capture_epoch=recording.capture_epoch,
            sealed=False,
            phase="live",
        )
        resume.commit()
    thread.join(3)
    assert not thread.is_alive()
    assert not outcome[0].accepted
    with Session(capture_engine) as session:
        transcript = session.execute(select(Transcript)).scalar_one()
        assert transcript.change_sequence == sequence
        assert transcript.segments == []
        assert (
            session.execute(select(func.count(TranscriptUtteranceEvent.id))).scalar()
            == 0
        )


def test_cpu_resolution_is_frozen_without_publishing_a_public_change(
    capture_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.core.db as core_db

    recording_id = _seed(capture_engine, monkeypatch, True)
    monkeypatch.setattr(core_db, "get_sync_session", lambda: Session(capture_engine))
    with Session(capture_engine) as session:
        transcript, recording = load_capture_publication(
            session, recording_id, lock=False
        )
        token = capture_publication_token(recording, transcript)
        sequence = transcript.change_sequence
    assert _publish(recording_id, token).accepted
    assert _publish(recording_id, token, device="cuda").accepted
    with Session(capture_engine) as session:
        transcript = session.execute(select(Transcript)).scalar_one()
        assert transcript.settling_state["policy"] == {
            "k": 3,
            "window_s": 10.0,
            "enabled": False,
        }
        assert transcript.change_sequence == sequence
