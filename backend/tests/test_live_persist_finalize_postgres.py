"""The live lane's last write against Stop's finalize, on a real PostgreSQL.

Finalize holds the recording's audio lock, seals the tail windows and then
repoints ``audio_path``, which needs the recording row for update. The live
write for the last segment inserts lines, whose foreign keys share that row,
and then marks the same windows processed. Taken in those orders the two
deadlock, so the live write takes the audio lock first and, once finalize has
committed, sees the capture has ended and writes nothing. Row and advisory
locks need a real server. Skips without NOJOIN_TEST_POSTGRES_URL, like the
other Postgres tests.
"""

from __future__ import annotations

import threading
import time

from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, func, select

from backend.core import db as db_module
from backend.models.pipeline import RecordingAudioWindowManifest, TranscriptUtterance
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.processing.live_transcribe import _persist_live_run
from backend.tests.storage_hygiene_harness import add_recording, postgres_engine
from backend.utils.recording_audio_sync import lock_recording_audio

WINDOW_MS = 30_000
LAST_LINE = {"start": 31.0, "end": 34.0, "text": "one last thing", "speaker": "A"}


def _capture_with_tail_windows(engine: Engine) -> int:
    recording_id = add_recording(
        engine, "segments/capture.wav", status=RecordingStatus.UPLOADING
    )
    with Session(engine) as session:
        session.add(Transcript(recording_id=recording_id, segments=[]))
        for index in range(2):
            session.add(
                RecordingAudioWindowManifest(
                    recording_id=recording_id,
                    window_index=index,
                    target_window_ms=WINDOW_MS,
                    hop_ms=WINDOW_MS,
                    window_start_ms=index * WINDOW_MS,
                    window_end_ms=(index + 1) * WINDOW_MS,
                    chunk_start_sequence=index,
                    chunk_end_sequence=index,
                )
            )
        session.commit()
    return recording_id


def _wait_until_waiting_on_a_lock(engine: Engine, pid: int) -> None:
    """Return once backend ``pid`` is blocked on a lock; fail after 10 s."""
    deadline = time.monotonic() + 10
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            wait = watcher.execute(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).scalar_one_or_none()
            watcher.rollback()
            if wait == "Lock":
                return
            time.sleep(0.01)
    raise AssertionError("the live write never reached a lock")


def test_live_write_during_stop_waits_for_finalize_and_writes_nothing(
    postgres_test_url: str, monkeypatch
) -> None:
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _capture_with_tail_windows(engine)
        live_pid: list[int] = []
        live_session_opened = threading.Event()

        def live_session() -> Session:
            session = Session(engine)
            live_pid.append(
                session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            )
            live_session_opened.set()
            return session

        monkeypatch.setattr(db_module, "get_sync_session", live_session)

        outcome: dict = {}

        def live_write() -> None:
            try:
                outcome["dispatch"] = _persist_live_run(
                    recording_id=recording_id,
                    sequence=1,
                    run=[0, 1],
                    new_segments=[dict(LAST_LINE)],
                    state={},
                    live_config={},
                    live_config_hash="live-hash",
                    live_model_name="live-model",
                )
            except OperationalError as exc:
                outcome["error"] = exc

        with Session(engine) as finalize:
            finalize.execute(text("SET lock_timeout = '10s'"))
            lock_recording_audio(finalize, recording_id)
            finalize.execute(
                text(
                    "UPDATE recording_audio_window_manifests SET is_sealed = true"
                    " WHERE recording_id = :id"
                ),
                {"id": recording_id},
            )

            live = threading.Thread(target=live_write)
            live.start()
            assert live_session_opened.wait(10)
            _wait_until_waiting_on_a_lock(engine, live_pid[0])

            finalize.execute(
                text(
                    "UPDATE recordings SET audio_path = :path, status = 'QUEUED'"
                    " WHERE id = :id"
                ),
                {"path": "recordings/capture.flac", "id": recording_id},
            )
            finalize.commit()

        live.join(10)
        assert not live.is_alive()
        assert "error" not in outcome, outcome.get("error")
        assert outcome["dispatch"] is False

        with Session(engine) as session:
            recording = session.get(Recording, recording_id)
            assert recording is not None
            assert recording.status == RecordingStatus.QUEUED
            lines = session.exec(
                select(func.count())
                .select_from(TranscriptUtterance)
                .where(TranscriptUtterance.recording_id == recording_id)
            ).one()
            assert lines == 0
            windows = session.exec(
                select(RecordingAudioWindowManifest).where(
                    RecordingAudioWindowManifest.recording_id == recording_id
                )
            ).all()
            assert [(w.asr_status, w.is_sealed) for w in windows] == [
                ("pending", True),
                ("pending", True),
            ]
