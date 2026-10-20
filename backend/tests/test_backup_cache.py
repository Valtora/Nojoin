"""The cached backup: reused while nothing has changed, rebuilt once something has.

A backup of a large library takes hours, so the last finished archive is kept and handed
out again until the installation changes. These tests pin down what counts as a change.
New recordings, edits, speakers and voiceprints do. The calendar sync's own bookkeeping,
which it rewrites every fifteen minutes, does not, or the cache would never survive it.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from backend.core.backup import cache as backup_cache
from backend.core.backup import export as backup_export
from backend.core.backup import export_job
from backend.core.backup.export_job import ExportJob
from backend.core.backup.format import EXPORT_STALL_SECONDS
from backend.tests.backup_harness import (
    TestCalendarConnection,
    TestCalendarEvent,
    TestCalendarSource,
    TestContext,
    TestGlobalSpeaker,
    TestRecording,
    build_test_context,
    patch_backup_manager,
    seed_source_data,
)
from backend.utils.time import utc_now


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestContext:
    context = build_test_context(tmp_path / "install")
    patch_backup_manager(monkeypatch, context)
    monkeypatch.delenv("BACKUP_EXPORT_DIR", raising=False)
    return context


async def _seed(context: TestContext) -> Path:
    audio_path = context.path_manager.recordings_directory / "quarterly-planning.wav"
    audio_path.write_bytes(b"RIFF" + b"\0" * 64)
    await seed_source_data(
        context.async_session_maker, recording_audio_path=str(audio_path)
    )
    return audio_path


def _fingerprint(include_audio: bool = False, quality: str = "compressed") -> str:
    return backup_export.current_fingerprint(include_audio, quality)


def _update(context: TestContext, model, row_id: int, **values) -> None:
    with Session(context.sync_engine) as session:
        row = session.get(model, row_id)
        for key, value in values.items():
            setattr(row, key, value)
        session.add(row)
        session.commit()


def _export_dir(context: TestContext) -> Path:
    return context.path_manager.user_data_directory / "backups"


@pytest.mark.anyio
async def test_fingerprint_is_stable_while_nothing_changes(
    context: TestContext,
) -> None:
    await _seed(context)

    assert _fingerprint() == _fingerprint()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("model", "row_id", "values"),
    [
        (TestRecording, 40, {"name": "Renamed meeting"}),
        (TestGlobalSpeaker, 30, {"embedding": [0.5, 0.6, 0.7]}),
        (TestGlobalSpeaker, 30, {"name": "Someone Else"}),
        (TestCalendarSource, 60, {"user_colour": "rose"}),
        (TestCalendarSource, 60, {"is_selected": False}),
    ],
)
async def test_a_change_the_archive_would_carry_makes_the_cache_stale(
    context: TestContext, model, row_id: int, values: dict
) -> None:
    await _seed(context)
    before = _fingerprint()

    _update(context, model, row_id, **values)

    assert _fingerprint() != before


@pytest.mark.anyio
async def test_a_new_recording_makes_the_cache_stale(context: TestContext) -> None:
    await _seed(context)
    before = _fingerprint()

    with Session(context.sync_engine) as session:
        session.add(
            TestRecording(
                id=41,
                name="Follow-up",
                audio_path="data/recordings/follow-up.wav",
                user_id=1,
            )
        )
        session.commit()

    assert _fingerprint() != before


@pytest.mark.anyio
async def test_the_calendar_sync_does_not_make_the_cache_stale(
    context: TestContext,
) -> None:
    """What the timed sync rewrites is re-fetched from the provider after a restore."""
    await _seed(context)
    before = _fingerprint()

    later = utc_now()
    _update(
        context,
        TestCalendarConnection,
        50,
        access_token_encrypted="rotated-access-token",
        refresh_token_encrypted="rotated-refresh-token",
        token_expires_at=later,
        sync_status="syncing",
        last_sync_started_at=later,
        last_sync_completed_at=later,
        last_synced_at=later,
    )
    _update(
        context,
        TestCalendarSource,
        60,
        sync_cursor="cursor-456",
        last_synced_at=later,
        sync_window_start=later,
        sync_window_end=later,
    )
    with Session(context.sync_engine) as session:
        session.delete(session.get(TestCalendarEvent, 70))
        session.add(
            TestCalendarEvent(
                id=71, calendar_id=60, provider_event_id="evt-2", title="Stand-up"
            )
        )
        session.commit()

    assert _fingerprint() == before


@pytest.mark.anyio
async def test_connecting_another_calendar_makes_the_cache_stale(
    context: TestContext,
) -> None:
    await _seed(context)
    before = _fingerprint()

    with Session(context.sync_engine) as session:
        session.add(
            TestCalendarConnection(
                id=51, user_id=1, provider="microsoft", provider_account_id="acct-2"
            )
        )
        session.commit()

    assert _fingerprint() != before


@pytest.mark.anyio
async def test_audio_on_disk_counts_only_for_a_backup_that_carries_it(
    context: TestContext,
) -> None:
    audio_path = await _seed(context)
    without_audio = _fingerprint(include_audio=False)
    with_audio = _fingerprint(include_audio=True)

    audio_path.write_bytes(b"RIFF" + b"\1" * 128)

    assert _fingerprint(include_audio=False) == without_audio
    assert _fingerprint(include_audio=True) != with_audio


@pytest.mark.anyio
async def test_the_options_are_part_of_the_fingerprint(context: TestContext) -> None:
    await _seed(context)

    assert _fingerprint(True, "compressed") != _fingerprint(True, "original")
    assert _fingerprint(True, "compressed") != _fingerprint(False, "compressed")


@pytest.mark.anyio
async def test_config_counts_as_archived_so_a_redacted_key_does_not(
    context: TestContext,
) -> None:
    await _seed(context)
    config_path = context.path_manager.config_path
    before = _fingerprint()

    config = json.loads(config_path.read_text())
    config["gemini_api_key"] = "a-different-secret"
    config_path.write_text(json.dumps(config))
    assert _fingerprint() == before

    config["theme"] = "light"
    config_path.write_text(json.dumps(config))
    assert _fingerprint() != before


@pytest.mark.anyio
async def test_export_reuses_the_cached_archive_until_something_changes(
    context: TestContext,
) -> None:
    await _seed(context)

    first, reused = backup_export.export_backup(
        export_id="export-1", include_audio=False
    )
    assert reused is False
    first_archive = backup_cache.archive_path(_export_dir(context), first)
    assert first_archive.is_file()
    with zipfile.ZipFile(first_archive) as archive:
        assert "recordings.json" in archive.namelist()

    again, reused = backup_export.export_backup(
        export_id="export-2", include_audio=False
    )
    assert reused is True
    assert again.export_id == "export-1"

    _update(context, TestRecording, 40, name="Renamed meeting")

    rebuilt, reused = backup_export.export_backup(
        export_id="export-3", include_audio=False
    )
    assert reused is False
    assert rebuilt.export_id == "export-3"
    assert not first_archive.exists()
    assert backup_cache.archive_path(_export_dir(context), rebuilt).is_file()


@pytest.mark.anyio
async def test_a_different_kind_of_backup_replaces_the_cache(
    context: TestContext,
) -> None:
    await _seed(context)
    backup_export.export_backup(export_id="no-audio", include_audio=False)

    entry, reused = backup_export.export_backup(
        export_id="with-audio", include_audio=True, archive_quality="original"
    )

    assert reused is False
    assert backup_cache.load(_export_dir(context)).export_id == "with-audio"
    assert entry.include_audio is True
    assert entry.archive_quality == "original"
    assert [p.name for p in _export_dir(context).glob("nojoin-backup-*.zip")] == [
        "nojoin-backup-with-audio.zip"
    ]


@pytest.mark.anyio
async def test_the_cached_archive_records_when_its_data_was_read(
    context: TestContext,
) -> None:
    await _seed(context)

    entry, _ = backup_export.export_backup(export_id="export-1", include_audio=False)

    assert entry.download_filename().startswith("nojoin_backup_")
    assert entry.download_filename() != "nojoin_backup.zip"
    assert entry.summary() == {
        "export_id": "export-1",
        "created_at": entry.created_at,
        "include_audio": False,
        "archive_quality": "compressed",
        "size_bytes": entry.size_bytes,
        "warnings": entry.warnings,
    }


@pytest.mark.anyio
async def test_the_sweep_keeps_the_cached_archive_and_reclaims_the_rest(
    context: TestContext,
) -> None:
    await _seed(context)
    entry, _ = backup_export.export_backup(export_id="export-1", include_audio=False)
    export_dir = _export_dir(context)
    abandoned = export_dir / "tmpabandoned.zip"
    abandoned.write_bytes(b"partial")

    old = 1_000_000_000
    for path in export_dir.iterdir():
        os.utime(path, (old, old))

    from backend.utils.path_manager import PathManager

    # The sweep the cleanup task runs. It reads nothing from the path manager, so the
    # stub stands in for a real one, which would point at a real data directory.
    PathManager.cleanup_temp_files(
        context.path_manager,  # type: ignore[arg-type]
        export_dir,
        max_age_hours=24,
        keep=backup_cache.protected_names(export_dir),
    )

    assert not abandoned.exists()
    assert backup_cache.load(export_dir) == entry


def test_a_manifest_naming_a_file_outside_the_directory_is_ignored(
    tmp_path: Path,
) -> None:
    (tmp_path / "secret.zip").write_bytes(b"x")
    (tmp_path / backup_cache.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "export_id": "x",
                "filename": "../secret.zip",
                "created_at": "2026-10-17T14:02:00+00:00",
                "include_audio": False,
                "archive_quality": "compressed",
                "fingerprint": "f",
                "size_bytes": 1,
            }
        )
    )

    assert backup_cache.load(tmp_path) is None


def test_a_manifest_whose_archive_is_gone_is_no_cache(tmp_path: Path) -> None:
    built = tmp_path / "built.zip"
    built.write_bytes(b"zip")
    entry = backup_cache.publish(
        tmp_path,
        str(built),
        backup_cache.CachedExport(
            export_id="e1",
            created_at="2026-10-17T14:02:00+00:00",
            include_audio=False,
            archive_quality="compressed",
            fingerprint="f",
            warnings={},
            app_version="1.0.0",
            filename="",
            size_bytes=0,
        ),
    )
    backup_cache.archive_path(tmp_path, entry).unlink()

    assert backup_cache.load(tmp_path) is None


def test_discard_removes_the_archive_and_manifest(tmp_path: Path) -> None:
    built = tmp_path / "built.zip"
    built.write_bytes(b"zip")
    entry = backup_cache.publish(
        tmp_path,
        str(built),
        backup_cache.CachedExport(
            export_id="e1",
            created_at="2026-10-17T14:02:00+00:00",
            include_audio=False,
            archive_quality="compressed",
            fingerprint="f",
            warnings={},
            app_version="1.0.0",
            filename="",
            size_bytes=0,
        ),
    )

    assert backup_cache.discard(tmp_path) is True
    assert not backup_cache.archive_path(tmp_path, entry).exists()
    assert not (tmp_path / backup_cache.MANIFEST_NAME).exists()
    assert backup_cache.discard(tmp_path) is False


def test_download_filename_uses_the_creation_time() -> None:
    entry = backup_cache.CachedExport(
        export_id="e1",
        filename="nojoin-backup-e1.zip",
        created_at="2026-10-17T14:02:09+00:00",
        include_audio=True,
        archive_quality="compressed",
        fingerprint="f",
        size_bytes=1,
    )

    assert entry.download_filename() == "nojoin_backup_20261017_140209.zip"


# --- The status a browser polls -------------------------------------------------------

NOW = 1_800_000_000.0


def _result(state: str, info=None, result=None) -> SimpleNamespace:
    return SimpleNamespace(state=state, info=info, result=result)


def _cached(export_id: str = "build-1") -> backup_cache.CachedExport:
    return backup_cache.CachedExport(
        export_id=export_id,
        filename=f"nojoin-backup-{export_id}.zip",
        created_at="2026-10-17T14:02:09+00:00",
        include_audio=True,
        archive_quality="compressed",
        fingerprint="f",
        size_bytes=10,
        warnings={"recordings_without_audio": 1},
    )


def _job(task_id: str = "build-1", requested_at: float = NOW) -> ExportJob:
    return ExportJob(
        task_id=task_id,
        include_audio=True,
        archive_quality="compressed",
        requested_at=requested_at,
    )


def test_status_of_the_build_that_made_the_cached_archive_is_success() -> None:
    # Even after Celery has forgotten the task, the manifest still names it.
    status = export_job.describe_export(
        "build-1", result=_result("PENDING"), job=None, cached=_cached(), now=NOW
    )

    assert status["state"] == "SUCCESS"
    assert status["export"]["export_id"] == "build-1"
    assert status["export"]["warnings"] == {"recordings_without_audio": 1}
    assert status["reused"] is False


def test_status_of_a_request_answered_from_the_cache_points_at_that_archive() -> None:
    status = export_job.describe_export(
        "request-2",
        result=_result("SUCCESS", result={"export_id": "build-1", "reused": True}),
        job=None,
        cached=_cached(),
        now=NOW,
    )

    assert status["state"] == "SUCCESS"
    assert status["export"]["export_id"] == "build-1"
    assert status["reused"] is True


def test_status_of_a_build_whose_archive_was_replaced_has_expired() -> None:
    status = export_job.describe_export(
        "build-1",
        result=_result("SUCCESS", result={"export_id": "build-1"}),
        job=None,
        cached=_cached("build-2"),
        now=NOW,
    )

    assert status["state"] == "EXPIRED"


def test_status_of_a_task_the_server_never_queued_has_expired() -> None:
    status = export_job.describe_export(
        "unknown", result=_result("PENDING"), job=_job("build-9"), cached=None, now=NOW
    )

    assert status["state"] == "EXPIRED"


def test_status_of_an_export_left_running_by_an_older_version_has_expired() -> None:
    """Exports from before heartbeats have no record, so nothing will ever finish them."""
    status = export_job.describe_export(
        "old-build",
        result=_result("PROCESSING", info={"status": "Compressing audio"}),
        job=None,
        cached=None,
        now=NOW,
    )

    assert status["state"] == "EXPIRED"


def test_status_of_a_queued_build_is_pending() -> None:
    status = export_job.describe_export(
        "build-1", result=_result("PENDING"), job=_job(), cached=None, now=NOW + 60
    )

    assert status["state"] == "PENDING"


def test_status_of_a_running_build_carries_its_progress() -> None:
    info = {
        "status": "Compressing audio (3 of 9)",
        "stage": "Compressing audio",
        "current": 3,
        "total": 9,
        "updated_at": NOW - 5,
    }
    status = export_job.describe_export(
        "build-1",
        result=_result("PROCESSING", info=info),
        job=_job(),
        cached=None,
        now=NOW,
    )

    assert status == {
        "state": "PROCESSING",
        "status": "Compressing audio (3 of 9)",
        "stage": "Compressing audio",
        "current": 3,
        "total": 9,
    }


def test_a_build_that_stops_reporting_progress_has_failed() -> None:
    info = {"status": "Compressing audio", "updated_at": NOW - EXPORT_STALL_SECONDS - 1}

    status = export_job.describe_export(
        "build-1",
        result=_result("PROCESSING", info=info),
        job=_job(),
        cached=None,
        now=NOW,
    )

    assert status["state"] == "FAILURE"


def test_a_long_build_that_keeps_reporting_is_still_running() -> None:
    """The old browser-side six-hour cut-off abandoned exports that were still working."""
    info = {"status": "Compressing audio", "updated_at": NOW - 60}
    job = _job(requested_at=NOW - 9 * 60 * 60)

    status = export_job.describe_export(
        "build-1",
        result=_result("PROCESSING", info=info),
        job=job,
        cached=None,
        now=NOW,
    )

    assert status["state"] == "PROCESSING"


def test_a_build_never_picked_up_is_lost_after_the_stall_window() -> None:
    job = _job(requested_at=NOW - EXPORT_STALL_SECONDS - 1)

    assert export_job.job_is_active(job, _result("PENDING"), NOW) is False
    assert export_job.job_is_active(_job(), _result("PENDING"), NOW) is True
    assert export_job.job_is_active(_job(), _result("SUCCESS"), NOW) is False


@pytest.mark.anyio
async def test_clear_all_restore_removes_the_old_document_files(
    context: TestContext,
) -> None:
    """The clear deletes every document row, so their files must not outlive them."""
    from backend.core.backup import BackupManager
    from backend.tests.backup_harness import TestDocument

    await _seed(context)
    zip_path, _ = await BackupManager.create_backup(include_audio=False)

    orphan = context.path_manager.documents_directory / "left-behind.pdf"
    orphan.write_bytes(b"%PDF")
    with Session(context.sync_engine) as session:
        session.add(
            TestDocument(id=90, recording_id=40, title="Agenda", file_path=str(orphan))
        )
        session.commit()

    await BackupManager.restore_backup(
        job_id="clear-all", zip_path=zip_path, clear_existing=True
    )

    assert BackupManager.restore_jobs["clear-all"]["status"] == "completed"
    assert not orphan.exists()
    with Session(context.sync_engine) as session:
        assert session.exec(select(TestDocument)).all() == []
