"""Storage-permission regression tests for issue #153.

A recordings directory that exists but is not writable used to surface only as an
HTTP 500 on the first import, leaving an orphaned UPLOADING row behind each time.
These cover the probe that now reports the condition, and the reaper that clears
rows which never received audio.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from backend.models import registry  # noqa: F401 -- resolve the mapper graph
from backend.utils.recording_storage import (
    cleanup_orphaned_uploading_recordings,
    probe_recordings_storage,
    recording_upload_temp_dir,
)

RECORDINGS_SCHEMA = """
CREATE TABLE recordings (
    max_speakers INTEGER,
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    name VARCHAR(255) NOT NULL, public_id VARCHAR(36) NOT NULL, meeting_uid VARCHAR(36) NOT NULL,
    audio_path VARCHAR(1024) NOT NULL, proxy_path VARCHAR(1024), celery_task_id VARCHAR(255),
    duration_seconds FLOAT, file_size_bytes INTEGER, status VARCHAR(32) NOT NULL,
    client_status VARCHAR(32), upload_progress INTEGER NOT NULL, processing_progress INTEGER NOT NULL,
    processing_step VARCHAR(255), processing_started_at TIMESTAMP, processing_completed_at TIMESTAMP,
    pipeline_generation VARCHAR(32) DEFAULT 'unified', is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL, last_activity_at TIMESTAMP, user_id INTEGER, calendar_event_id INTEGER
)
"""

RECORDING_AUDIO_CHUNKS_SCHEMA = """
CREATE TABLE recording_audio_chunks (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    absolute_start_ms INTEGER NOT NULL,
    absolute_end_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    sample_rate_hz INTEGER NOT NULL,
    channel_count INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 VARCHAR(128) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    upload_status VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(255),
    received_at DATETIME NOT NULL,
    cleanup_eligible_at DATETIME
)
"""

_NOW = datetime(2026, 7, 28, 12, 0, 0)
_STALE = _NOW - timedelta(hours=48)


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "recordings"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("RECORDINGS_DIR", str(root))
    return root


def _make_engine():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(RECORDINGS_SCHEMA))
        connection.execute(text(RECORDING_AUDIO_CHUNKS_SCHEMA))
    return engine


def _insert_recording(
    connection,
    *,
    recording_id: int,
    status: str = "UPLOADING",
    created_at: datetime = _STALE,
    audio_path: str = "/nonexistent/never-written.wav",
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO recordings (
                id, created_at, updated_at, name, public_id, meeting_uid,
                audio_path, status, upload_progress, processing_progress,
                is_archived, is_deleted
            ) VALUES (
                :id, :created_at, :created_at, :name, :public_id, :meeting_uid,
                :audio_path, :status, 0, 0, 0, 0
            )
            """
        ),
        {
            "id": recording_id,
            "created_at": created_at,
            "name": f"recording-{recording_id}",
            "public_id": f"public-{recording_id}",
            "meeting_uid": f"uid-{recording_id}",
            "audio_path": audio_path,
            "status": status,
        },
    )


def _insert_chunk(connection, *, recording_id: int, storage_path: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO recording_audio_chunks (
                id, created_at, updated_at, public_id, recording_id,
                sequence_no, source_kind, absolute_start_ms, absolute_end_ms,
                duration_ms, sample_rate_hz, channel_count, byte_size,
                sha256, storage_path, upload_status, received_at
            ) VALUES (
                :id, :now, :now, :public_id, :recording_id,
                0, 'import_part', 0, 500,
                500, 16000, 1, 7,
                'abc', :storage_path, 'pending', :now
            )
            """
        ),
        {
            "id": recording_id,
            "now": _NOW,
            "public_id": f"chunk-{recording_id}",
            "recording_id": recording_id,
            "storage_path": storage_path,
        },
    )


def _is_deleted(engine, recording_id: int) -> bool:
    with Session(engine) as session:
        row = session.execute(
            text("SELECT is_deleted FROM recordings WHERE id = :id"),
            {"id": recording_id},
        ).one()
    return bool(row[0])


def test_probe_reports_writable_storage(storage_root: Path) -> None:
    writable, detail = probe_recordings_storage()

    assert writable is True
    assert detail is None


def test_probe_leaves_no_residue(storage_root: Path) -> None:
    probe_recordings_storage()

    temp_dir = storage_root / "temp"
    assert list(temp_dir.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits required")
@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
def test_probe_reports_unwritable_storage(storage_root: Path) -> None:
    """The reported failure: the directory exists but the process cannot write."""
    temp_dir = storage_root / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.chmod(0o500)

    try:
        writable, detail = probe_recordings_storage()
    finally:
        temp_dir.chmod(0o700)

    assert writable is False
    assert detail is not None
    assert "not writable" in detail


def test_reaper_soft_deletes_orphan_that_never_received_audio(
    storage_root: Path,
) -> None:
    engine = _make_engine()
    with engine.begin() as connection:
        _insert_recording(connection, recording_id=1)

    with Session(engine) as session:
        reaped = cleanup_orphaned_uploading_recordings(
            session, logger=logging.getLogger(__name__), now=_NOW
        )

    assert reaped == 1
    assert _is_deleted(engine, 1) is True


def test_reaper_keeps_recording_with_uploaded_chunks(storage_root: Path) -> None:
    chunk_path = recording_upload_temp_dir(2, create=True) / "0.part"
    chunk_path.write_bytes(b"segment")

    engine = _make_engine()
    with engine.begin() as connection:
        _insert_recording(connection, recording_id=2)
        _insert_chunk(connection, recording_id=2, storage_path=str(chunk_path))

    with Session(engine) as session:
        reaped = cleanup_orphaned_uploading_recordings(
            session, logger=logging.getLogger(__name__), now=_NOW
        )

    assert reaped == 0
    assert _is_deleted(engine, 2) is False


def test_reaper_keeps_recording_with_audio_on_disk(storage_root: Path) -> None:
    audio_path = storage_root / "partial.wav"
    audio_path.write_bytes(b"audio")

    engine = _make_engine()
    with engine.begin() as connection:
        _insert_recording(connection, recording_id=3, audio_path=str(audio_path))

    with Session(engine) as session:
        reaped = cleanup_orphaned_uploading_recordings(
            session, logger=logging.getLogger(__name__), now=_NOW
        )

    assert reaped == 0
    assert _is_deleted(engine, 3) is False


def test_reaper_keeps_recording_with_segments_in_temp_dir(storage_root: Path) -> None:
    """A live capture writes segments before any chunk row is synced."""
    temp_dir = recording_upload_temp_dir(4, create=True)
    (temp_dir / "0.part").write_bytes(b"segment")

    engine = _make_engine()
    with engine.begin() as connection:
        _insert_recording(connection, recording_id=4)

    with Session(engine) as session:
        reaped = cleanup_orphaned_uploading_recordings(
            session, logger=logging.getLogger(__name__), now=_NOW
        )

    assert reaped == 0
    assert _is_deleted(engine, 4) is False


def test_reaper_keeps_recent_upload_still_in_flight(storage_root: Path) -> None:
    engine = _make_engine()
    with engine.begin() as connection:
        _insert_recording(
            connection, recording_id=5, created_at=_NOW - timedelta(minutes=5)
        )

    with Session(engine) as session:
        reaped = cleanup_orphaned_uploading_recordings(
            session, logger=logging.getLogger(__name__), now=_NOW
        )

    assert reaped == 0
    assert _is_deleted(engine, 5) is False


def test_reaper_ignores_recordings_past_the_uploading_state(
    storage_root: Path,
) -> None:
    engine = _make_engine()
    with engine.begin() as connection:
        _insert_recording(connection, recording_id=6, status="PROCESSED")

    with Session(engine) as session:
        reaped = cleanup_orphaned_uploading_recordings(
            session, logger=logging.getLogger(__name__), now=_NOW
        )

    assert reaped == 0
    assert _is_deleted(engine, 6) is False
