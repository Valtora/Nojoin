from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

import backend.worker.tasks as tasks_module


RECORDING_PROXY_SCHEMA = """
CREATE TABLE recordings (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    name VARCHAR(255) NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    meeting_uid VARCHAR(36) NOT NULL,
    audio_path VARCHAR(1024) NOT NULL,
    proxy_path VARCHAR(1024),
    celery_task_id VARCHAR(255),
    duration_seconds FLOAT,
    file_size_bytes INTEGER,
    status VARCHAR(32) NOT NULL,
    client_status VARCHAR(32),
    upload_progress INTEGER NOT NULL,
    processing_progress INTEGER NOT NULL,
    processing_step VARCHAR(255),
    processing_started_at DATETIME,
    processing_completed_at DATETIME,
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    user_id INTEGER,
    calendar_event_id INTEGER
)
"""


def create_recording_row(engine, *, recording_id: int, audio_path: str, status: str = "PROCESSED") -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO recordings (
                    id,
                    created_at,
                    updated_at,
                    name,
                    public_id,
                    meeting_uid,
                    audio_path,
                    proxy_path,
                    celery_task_id,
                    duration_seconds,
                    file_size_bytes,
                    status,
                    client_status,
                    upload_progress,
                    processing_progress,
                    processing_step,
                    processing_started_at,
                    processing_completed_at,
                    is_archived,
                    is_deleted,
                    user_id
                ) VALUES (
                    :id,
                    :created_at,
                    :updated_at,
                    :name,
                    :public_id,
                    :meeting_uid,
                    :audio_path,
                    :proxy_path,
                    :celery_task_id,
                    :duration_seconds,
                    :file_size_bytes,
                    :status,
                    :client_status,
                    :upload_progress,
                    :processing_progress,
                    :processing_step,
                    :processing_started_at,
                    :processing_completed_at,
                    :is_archived,
                    :is_deleted,
                    :user_id
                )
                """
            ),
            {
                "id": recording_id,
                "created_at": datetime(2026, 4, 12, 12, 0, 0),
                "updated_at": datetime(2026, 4, 12, 12, 0, 0),
                "name": "Imported MP3",
                "public_id": f"public-recording-{recording_id}",
                "meeting_uid": "proxy-test-meeting-uid",
                "audio_path": audio_path,
                "proxy_path": None,
                "celery_task_id": None,
                "duration_seconds": 42.0,
                "file_size_bytes": 1024,
                "status": status,
                "client_status": None,
                "upload_progress": 100,
                "processing_progress": 100,
                "processing_step": "Completed",
                "processing_started_at": None,
                "processing_completed_at": datetime(2026, 4, 12, 12, 1, 0),
                "is_archived": False,
                "is_deleted": False,
                "user_id": None,
            },
        )


def test_can_delete_source_audio_returns_false_for_same_file(tmp_path: Path) -> None:
    audio_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"fake-mp3")

    recording = SimpleNamespace(audio_path=str(audio_path), proxy_path=str(audio_path))

    assert tasks_module._can_delete_source_audio(recording) is False


def test_generate_proxy_task_reuses_imported_mp3_without_deleting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "proxy-test.sqlite"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as connection:
        connection.execute(text(RECORDING_PROXY_SCHEMA))

    audio_path = tmp_path / "imported-meeting.mp3"
    audio_path.write_bytes(b"fake-mp3")
    create_recording_row(engine, recording_id=1, audio_path=str(audio_path))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("convert_to_proxy_mp3 should not run for same-path MP3 imports")

    monkeypatch.setattr("backend.utils.audio.convert_to_proxy_mp3", fail_if_called)
    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    tasks_module.generate_proxy_task._session = None

    try:
        tasks_module.generate_proxy_task.run(1)
    finally:
        if tasks_module.generate_proxy_task._session is not None:
            tasks_module.generate_proxy_task._session.close()
        tasks_module.generate_proxy_task._session = None
        engine.dispose()

    assert audio_path.exists()

    verification_engine = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        with Session(verification_engine) as session:
            recording = session.exec(text("SELECT proxy_path, audio_path FROM recordings WHERE id = 1")).one()
        assert recording[0] == str(audio_path)
        assert recording[1] == str(audio_path)
    finally:
        verification_engine.dispose()