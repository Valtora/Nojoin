from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from backend.utils.recording_storage import (
    cleanup_stale_recording_artifacts,
    delete_recording_artifacts,
    recording_upload_temp_dir,
    recordings_failed_dir,
)


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "recordings"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("RECORDINGS_DIR", str(root))
    return root


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