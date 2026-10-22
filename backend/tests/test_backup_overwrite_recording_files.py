"""An Overwrite restore removes the audio and proxies of the recordings it replaces.

The replaced recording's row is deleted, and its files are unlinked after the
commit, inside the restore's recordings directory only, apart from any file a row
still names: a restored recording can land on the replaced copy's audio, which is
how Overwrite keeps the audio an older archive left out.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from sqlmodel import Session, select

import backend.core.backup.restore.stages as backup_stages
from backend.core.backup import ARCHIVE_QUALITY_ORIGINAL, BackupManager
from backend.tests.backup_harness import (
    TestContext,
    TestRecording,
    build_test_context,
    patch_backup_manager,
    seed_source_data,
)
from backend.tests.test_backup_manager import seed_existing_target_recording
from backend.tests.test_backup_overwrite_documents import restore_overwriting

MEETING_UID = "meeting-uid-shared"


async def build_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    audio: bytes | None,
    meeting_uid: str | None = MEETING_UID,
) -> str:
    """A backup of one recording, quarterly-planning.wav, with or without its audio."""
    source_context = build_test_context(tmp_path / "source")
    patch_backup_manager(monkeypatch, source_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "source-encryption-key")
    if audio is not None:
        recordings_dir = source_context.path_manager.recordings_directory
        (recordings_dir / "quarterly-planning.wav").write_bytes(audio)
    await seed_source_data(
        source_context.async_session_maker, recording_meeting_uid=meeting_uid
    )
    zip_path, _ = await BackupManager.create_backup(
        include_audio=audio is not None, archive_quality=ARCHIVE_QUALITY_ORIGINAL
    )
    await source_context.async_engine.dispose()
    return zip_path


async def seed_replaced_recording(
    target_context: TestContext,
    monkeypatch: pytest.MonkeyPatch,
    *,
    audio_path: str,
    proxy_path: str,
    meeting_uid: str | None = MEETING_UID,
) -> None:
    """The target's recording, which the archive replaces, naming these files."""
    patch_backup_manager(monkeypatch, target_context)
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    await seed_existing_target_recording(
        target_context.async_session_maker,
        meeting_uid=meeting_uid,
        audio_path=audio_path,
        proxy_path=proxy_path,
    )


def restored_audio(target_context: TestContext) -> Path:
    with Session(target_context.sync_engine) as session:
        recording = session.exec(select(TestRecording)).one()
    return Path(recording.audio_path).resolve()


def skip_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    # The insert loop's own delete, for a match the pre-delete missed.
    monkeypatch.setattr(backup_stages, "_restore_preflight_overwrite", lambda *a: None)


@pytest.mark.anyio
@pytest.mark.parametrize("archive_has_audio", [True, False], ids=["audio", "no-audio"])
@pytest.mark.parametrize("preflight", [True, False], ids=["preflight", "fallback"])
async def test_overwrite_removes_the_replaced_recordings_audio_and_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preflight: bool,
    archive_has_audio: bool,
) -> None:
    zip_path = await build_archive(
        tmp_path, monkeypatch, audio=b"backup audio" if archive_has_audio else None
    )
    target_context = build_test_context(tmp_path / "target")
    recordings_dir = target_context.path_manager.recordings_directory
    (recordings_dir / "renamed-quarterly.wav").write_bytes(b"old audio")
    (recordings_dir / "renamed-quarterly.mp3").write_bytes(b"old proxy")
    await seed_replaced_recording(
        target_context,
        monkeypatch,
        audio_path=str(recordings_dir / "renamed-quarterly.wav"),
        proxy_path=str(recordings_dir / "renamed-quarterly.mp3"),
    )
    if not preflight:
        skip_preflight(monkeypatch)

    job = await restore_overwriting(zip_path, "overwrite-recording-files-job")

    assert job["status"] == "completed"
    expected = ["quarterly-planning.wav"] if archive_has_audio else []
    assert sorted(os.listdir(recordings_dir)) == expected
    if archive_has_audio:
        assert restored_audio(target_context).read_bytes() == b"backup audio"

    await target_context.async_engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("archive_has_audio", [True, False], ids=["audio", "no-audio"])
@pytest.mark.parametrize("preflight", [True, False], ids=["preflight", "fallback"])
async def test_overwrite_keeps_the_replaced_audio_a_restored_recording_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    preflight: bool,
    archive_has_audio: bool,
) -> None:
    # An older archive matched by file name alone. Without the audio, the restored
    # recording lands on the replaced copy's file and keeps it, as
    # BACKUP_RESTORE.md promises; with it, the archived audio moves in on top. The
    # replaced proxy goes either way and is rebuilt from the restored audio.
    zip_path = await build_archive(
        tmp_path,
        monkeypatch,
        audio=b"backup audio" if archive_has_audio else None,
        meeting_uid=None,
    )
    target_context = build_test_context(tmp_path / "target")
    recordings_dir = target_context.path_manager.recordings_directory
    audio = recordings_dir / "quarterly-planning.wav"
    audio.write_bytes(b"old audio")
    (recordings_dir / "quarterly-planning.mp3").write_bytes(b"old proxy")
    await seed_replaced_recording(
        target_context,
        monkeypatch,
        audio_path=str(audio),
        proxy_path=str(recordings_dir / "quarterly-planning.mp3"),
        meeting_uid=None,
    )
    if not preflight:
        skip_preflight(monkeypatch)

    job = await restore_overwriting(zip_path, "overwrite-keep-audio-job")

    assert job["status"] == "completed"
    assert restored_audio(target_context) == audio.resolve()
    assert os.listdir(recordings_dir) == ["quarterly-planning.wav"]
    expected = b"backup audio" if archive_has_audio else b"old audio"
    assert audio.read_bytes() == expected

    await target_context.async_engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("alias", ["home", "hard-link"])
async def test_overwrite_keeps_a_replaced_spelling_of_the_audio_a_restored_row_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    # The replaced row spells the restored row's file another way. Compared as
    # strings they differ, and the unlink would remove the audio the restored
    # recording now names. A hard link stands in for a case-insensitive mount,
    # where two spellings are one directory entry.
    zip_path = await build_archive(tmp_path, monkeypatch, audio=None)
    target_context = build_test_context(tmp_path / "target")
    recordings_dir = target_context.path_manager.recordings_directory
    audio = recordings_dir / "quarterly-planning.wav"
    audio.write_bytes(b"old audio")
    (recordings_dir / "quarterly-planning.mp3").write_bytes(b"old proxy")
    if alias == "home":
        monkeypatch.setenv("HOME", str(tmp_path / "target"))
        replaced_spelling = "~/recordings/quarterly-planning.wav"
    else:
        os.link(audio, recordings_dir / "Quarterly-Planning.wav")
        replaced_spelling = str(recordings_dir / "Quarterly-Planning.wav")
    await seed_replaced_recording(
        target_context,
        monkeypatch,
        audio_path=replaced_spelling,
        proxy_path=str(recordings_dir / "quarterly-planning.mp3"),
    )

    job = await restore_overwriting(zip_path, f"overwrite-audio-alias-{alias}-job")

    assert job["status"] == "completed"
    assert restored_audio(target_context) == audio.resolve()
    assert audio.read_bytes() == b"old audio"
    assert Path(replaced_spelling).expanduser().exists()
    assert not (recordings_dir / "quarterly-planning.mp3").exists()

    await target_context.async_engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["absolute", "symlink"])
async def test_overwrite_leaves_a_replaced_file_outside_the_recordings_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    # The replaced audio lives outside the recordings directory, named directly or
    # through a link inside it. Symlinks are resolved before the containment
    # check, so neither is followed out. The proxy inside still goes.
    zip_path = await build_archive(tmp_path, monkeypatch, audio=None)
    target_context = build_test_context(tmp_path / "target")
    recordings_dir = target_context.path_manager.recordings_directory
    outside = tmp_path / "elsewhere" / "renamed-quarterly.wav"
    outside.parent.mkdir()
    outside.write_bytes(b"old audio")
    if route == "absolute":
        replaced_audio = outside
    else:
        replaced_audio = recordings_dir / "renamed-quarterly.wav"
        replaced_audio.symlink_to(outside)
    (recordings_dir / "renamed-quarterly.mp3").write_bytes(b"old proxy")
    await seed_replaced_recording(
        target_context,
        monkeypatch,
        audio_path=str(replaced_audio),
        proxy_path=str(recordings_dir / "renamed-quarterly.mp3"),
    )

    job = await restore_overwriting(zip_path, f"overwrite-outside-{route}-job")

    assert job["status"] == "completed"
    assert outside.read_bytes() == b"old audio"
    assert not (recordings_dir / "renamed-quarterly.mp3").exists()

    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_overwrite_logs_a_failed_unlink_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The database has committed by then, so a file that cannot be removed is a
    # warning, and the restore still moves the archived audio into place.
    zip_path = await build_archive(tmp_path, monkeypatch, audio=b"backup audio")
    target_context = build_test_context(tmp_path / "target")
    recordings_dir = target_context.path_manager.recordings_directory
    (recordings_dir / "renamed-quarterly.wav").write_bytes(b"old audio")
    await seed_replaced_recording(
        target_context,
        monkeypatch,
        audio_path=str(recordings_dir / "renamed-quarterly.wav"),
        proxy_path=str(recordings_dir / "renamed-quarterly.mp3"),
    )

    def refuse(**_kwargs) -> None:
        raise PermissionError("refused by the test")

    monkeypatch.setattr(backup_stages, "delete_recording_artifacts", refuse)

    with caplog.at_level(logging.WARNING, logger=backup_stages.__name__):
        job = await restore_overwriting(zip_path, "overwrite-unlink-fails-job")

    assert job["status"] == "completed"
    assert "Failed to delete the replaced recording files" in caplog.text
    assert restored_audio(target_context).read_bytes() == b"backup audio"
    assert (recordings_dir / "renamed-quarterly.wav").read_bytes() == b"old audio"

    await target_context.async_engine.dispose()


@pytest.mark.anyio
async def test_overwrite_that_fails_before_its_commit_keeps_the_replaced_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zip_path = await build_archive(tmp_path, monkeypatch, audio=b"backup audio")
    target_context = build_test_context(tmp_path / "target")
    recordings_dir = target_context.path_manager.recordings_directory
    (recordings_dir / "renamed-quarterly.wav").write_bytes(b"old audio")
    (recordings_dir / "renamed-quarterly.mp3").write_bytes(b"old proxy")
    await seed_replaced_recording(
        target_context,
        monkeypatch,
        audio_path=str(recordings_dir / "renamed-quarterly.wav"),
        proxy_path=str(recordings_dir / "renamed-quarterly.mp3"),
    )

    def refuse(*_args) -> None:
        raise RuntimeError("refused by the test")

    monkeypatch.setattr(backup_stages, "_normalise_restored_recording_state", refuse)

    job = await restore_overwriting(zip_path, "overwrite-rolled-back-job")

    assert job["status"] == "failed"
    with Session(target_context.sync_engine) as session:
        assert session.exec(select(TestRecording.id)).all() == [102]
    assert sorted(os.listdir(recordings_dir)) == [
        "renamed-quarterly.mp3",
        "renamed-quarterly.wav",
    ]

    await target_context.async_engine.dispose()
