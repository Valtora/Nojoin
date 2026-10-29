from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine

from backend.utils import stored_paths
from backend.utils.recording_storage import (
    cleanup_recording_audio_chunks,
    cleanup_stale_recording_artifacts,
    delete_recording_artifacts,
    mark_recording_audio_chunks_ready_for_cleanup,
    recording_upload_temp_dir,
    recordings_failed_dir,
)

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
    pcm_data_offset INTEGER,
    pcm_byte_size INTEGER,
    sha256 VARCHAR(128) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    upload_status VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(255),
    received_at DATETIME NOT NULL,
    cleanup_eligible_at DATETIME
)
"""


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "recordings"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("RECORDINGS_DIR", str(root))
    return root


@pytest.fixture
def path_manager_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stored_files_may_live_in_temp: tuple[Callable[[], tuple[Path, ...]], ...],
) -> Path:
    root = tmp_path / "restored-recordings"
    root.mkdir()
    monkeypatch.setattr(
        stored_paths, "PathManager", lambda: SimpleNamespace(recordings_directory=root)
    )
    # Undo the suite's /tmp allowance so paths outside both roots are refused.
    monkeypatch.setattr(
        stored_paths, "recording_roots", stored_files_may_live_in_temp[0]
    )
    return root


def test_delete_recording_artifacts_removes_path_manager_audio(
    storage_root: Path,
    path_manager_root: Path,
) -> None:
    audio_path = path_manager_root / "restored.wav"
    proxy_path = path_manager_root / "restored.mp3"
    audio_path.write_bytes(b"wav")
    proxy_path.write_bytes(b"mp3")

    delete_recording_artifacts(
        recording_id=700,
        audio_path=str(audio_path),
        proxy_path=str(proxy_path),
        logger=logging.getLogger(__name__),
    )

    assert not audio_path.exists()
    assert not proxy_path.exists()


@pytest.mark.parametrize(
    "route", ["absolute", "upload-parent", "restore-parent", "symlink"]
)
def test_delete_recording_artifacts_keeps_paths_outside_both_roots(
    storage_root: Path,
    path_manager_root: Path,
    tmp_path: Path,
    route: str,
) -> None:
    outside = tmp_path / "unrelated.wav"
    outside.write_bytes(b"keep")
    linked = path_manager_root / "linked.wav"
    linked.symlink_to(outside)
    paths = {
        "absolute": outside,
        "upload-parent": storage_root / ".." / outside.name,
        "restore-parent": path_manager_root / ".." / outside.name,
        "symlink": linked,
    }

    delete_recording_artifacts(
        recording_id=701,
        audio_path=str(paths[route]),
        proxy_path=None,
        logger=logging.getLogger(__name__),
    )

    assert outside.read_bytes() == b"keep"


def test_delete_recording_artifacts_removes_distinct_audio_proxy_and_temp_dir(
    storage_root: Path,
) -> None:
    audio_path = storage_root / "meeting.wav"
    proxy_path = storage_root / "meeting.mp3"
    audio_path.write_bytes(b"wav")
    proxy_path.write_bytes(b"mp3")

    temp_dir = recording_upload_temp_dir(101, create=True)
    (temp_dir / "0.wav").write_bytes(b"segment")

    delete_recording_artifacts(
        recording_id=101,
        audio_path=str(audio_path),
        proxy_path=str(proxy_path),
        logger=logging.getLogger(__name__),
    )

    assert not audio_path.exists()
    assert not proxy_path.exists()
    assert not temp_dir.exists()


def test_delete_recording_artifacts_handles_shared_audio_and_proxy_path(
    storage_root: Path,
) -> None:
    shared_mp3_path = storage_root / "imported.mp3"
    shared_mp3_path.write_bytes(b"shared-mp3")

    temp_dir = recording_upload_temp_dir(202, create=True)
    (temp_dir / "0.wav").write_bytes(b"segment")

    delete_recording_artifacts(
        recording_id=202,
        audio_path=str(shared_mp3_path),
        proxy_path=str(shared_mp3_path),
        logger=logging.getLogger(__name__),
    )

    assert not shared_mp3_path.exists()
    assert not temp_dir.exists()


def test_cleanup_stale_recording_artifacts_removes_old_temp_and_failed_entries(
    storage_root: Path,
) -> None:
    temp_dir = recording_upload_temp_dir(301, create=True)
    (temp_dir / "0.wav").write_bytes(b"segment")

    failed_dir = recordings_failed_dir() / "301_failed_1"
    failed_dir.mkdir(parents=True, exist_ok=True)
    (failed_dir / "0.wav").write_bytes(b"failed")

    stale_time = time.time() - (48 * 60 * 60)
    for path in (temp_dir, failed_dir):
        os.utime(path, (stale_time, stale_time))

    cleaned_count = cleanup_stale_recording_artifacts(
        max_age_hours=24,
        logger=logging.getLogger(__name__),
    )

    assert cleaned_count == 2
    assert not temp_dir.exists()
    assert not failed_dir.exists()


def test_cleanup_recording_audio_chunks_deletes_eligible_files_and_marks_rows(
    storage_root: Path,
) -> None:
    chunk_path = recording_upload_temp_dir(401, create=True) / "0.wav"
    chunk_path.write_bytes(b"segment")

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(RECORDING_AUDIO_CHUNKS_SCHEMA))
        connection.execute(
            text(
                """
                INSERT INTO recording_audio_chunks (
                    id, created_at, updated_at, public_id, recording_id,
                    sequence_no, source_kind, absolute_start_ms, absolute_end_ms,
                    duration_ms, sample_rate_hz, channel_count, byte_size,
                    sha256, storage_path, upload_status, idempotency_key,
                    received_at, cleanup_eligible_at
                ) VALUES (
                    1, :created_at, :updated_at, 'chunk-public-id', 401,
                    0, 'browser', 0, 500,
                    500, 16000, 1, 7,
                    'abc', :storage_path, 'finalized', 'browser:0:abc',
                    :received_at, :cleanup_eligible_at
                )
                """
            ),
            {
                "created_at": "2026-05-19 00:00:00",
                "updated_at": "2026-05-19 00:00:00",
                "storage_path": str(chunk_path),
                "received_at": "2026-05-19 00:00:00",
                "cleanup_eligible_at": "2026-05-18 00:00:00",
            },
        )

    with Session(engine) as session:
        cleaned_count = cleanup_recording_audio_chunks(
            session,
            logger=logging.getLogger(__name__),
            now=datetime(2026, 5, 19),
        )
        cleaned_row = session.execute(
            text(
                "SELECT upload_status, cleanup_eligible_at FROM recording_audio_chunks WHERE id = 1"
            )
        ).one()

    assert cleaned_count == 1
    assert not chunk_path.exists()
    assert cleaned_row[0] == "cleaned"
    assert cleaned_row[1] is None


def test_mark_recording_audio_chunks_ready_for_cleanup_sets_deadline_without_deleting(
    storage_root: Path,
) -> None:
    chunk_path = recording_upload_temp_dir(402, create=True) / "0.wav"
    chunk_path.write_bytes(b"segment")

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(RECORDING_AUDIO_CHUNKS_SCHEMA))
        connection.execute(
            text(
                """
                INSERT INTO recording_audio_chunks (
                    id, created_at, updated_at, public_id, recording_id,
                    sequence_no, source_kind, absolute_start_ms, absolute_end_ms,
                    duration_ms, sample_rate_hz, channel_count, byte_size,
                    sha256, storage_path, upload_status, idempotency_key,
                    received_at, cleanup_eligible_at
                ) VALUES (
                    1, :created_at, :updated_at, 'chunk-public-id', 402,
                    0, 'browser', 0, 500,
                    500, 16000, 1, 7,
                    'abc', :storage_path, 'received', 'browser:0:abc',
                    :received_at, NULL
                )
                """
            ),
            {
                "created_at": "2026-05-19 00:00:00",
                "updated_at": "2026-05-19 00:00:00",
                "storage_path": str(chunk_path),
                "received_at": "2026-05-19 00:00:00",
            },
        )

    with Session(engine) as session:
        updated_count = mark_recording_audio_chunks_ready_for_cleanup(
            session,
            recording_id=402,
            upload_status="finalized",
        )
        session.commit()
        row = session.execute(
            text(
                "SELECT upload_status, cleanup_eligible_at FROM recording_audio_chunks WHERE id = 1"
            )
        ).one()

    assert updated_count == 1
    assert chunk_path.exists()
    assert row[0] == "finalized"
    assert row[1] is not None


def test_delete_recording_artifacts_without_recording_id_keeps_upload_temp_dir(
    storage_root: Path,
) -> None:
    """The failed-finalize paths rely on this: only the master goes, not temp/<id>/.

    When a capture's master cannot be built, the routes delete the partial
    master with ``recording_id=None`` so the segments - already moved to
    failed/, or left in temp/<id>/ when the move itself failed - survive for
    the 24-hour chunk sweep the failed chunk rows point at.
    """
    master_path = storage_root / "recording.wav"
    master_path.write_bytes(b"partial master")

    temp_dir = recording_upload_temp_dir(303, create=True)
    (temp_dir / "0.wav").write_bytes(b"segment")

    delete_recording_artifacts(
        recording_id=None,
        audio_path=str(master_path),
        proxy_path=None,
        logger=logging.getLogger(__name__),
    )

    assert not master_path.exists()
    assert (temp_dir / "0.wav").exists()
