import logging
from typing import Any, Dict, Tuple

from backend.core.backup import runtime
from backend.core.backup.export import (  # noqa: F401  (public surface preserved)
    _compress_to_opus,
    _create_backup_sync,
    _normalise_archive_quality,
    _table_dump_statement,
    create_backup,
    create_backup_blocking,
)
from backend.core.backup.format import (  # noqa: F401  (re-exported for callers and tests)
    ARCHIVABLE_AUDIO_EXTENSIONS,
    ARCHIVE_QUALITIES,
    ARCHIVE_QUALITY_COMPRESSED,
    ARCHIVE_QUALITY_ORIGINAL,
    BACKUP_FORMAT_VERSION,
    CALENDAR_PROVIDER_ENV_KEYS,
    DEFERRED_FOREIGN_KEYS,
    LEGACY_BACKUP_FORMAT_VERSION,
    MICROSOFT_COMMON_TENANT,
    RESTORE_DISK_HEADROOM_BYTES,
    RESTORE_FOREIGN_KEYS,
    RESTORE_LOCK_KEY,
    RESTORE_LOCK_TTL_SECONDS,
    RESTORE_STAGING_DIRNAME,
    RESTORED_RECORDING_INTERRUPTED_MESSAGE,
    RESTORED_RECORDING_INTERRUPTED_STATUS,
    RESTORED_RECORDING_TERMINAL_STATUS,
    SKIP_REASON_INSERT_FAILED,
    SKIP_REASON_NO_IDENTITY,
    SKIP_REASON_UNRESOLVED_OWNER,
    TRANSIENT_RECORDING_STATUSES,
    UNARCHIVED_TABLES,
    _ForeignKeySpec,
)
from backend.core.backup.paths import (  # noqa: F401
    _build_backup_document_path,
    _build_backup_recording_audio_path,
    _build_runtime_document_path,
    _build_runtime_recording_audio_path,
    _get_document_subpath,
    _get_recording_identity,
    _get_recording_match_key,
    _get_recording_match_keys,
    _get_recording_subpath,
    _get_subpath_after,
    _normalise_meeting_uid,
    _normalise_public_id,
)
from backend.core.backup.plans import (  # noqa: F401
    _AudioPlan,
    _AudioPlanEntry,
    _build_audio_plan,
    _build_document_plan,
    _DocumentPlan,
    _resolve_source_audio_path,
)
from backend.core.backup.records import (  # noqa: F401
    _adapt_record,
    _prepare_calendar_connection_for_restore,
    _prepare_calendar_provider_config_for_restore,
    _redact_sensitive_data,
    _restore_redacted_sensitive_data,
    _serialise_backup_table_rows,
    _serialise_calendar_provider_configs,
    _topological_sort,
)
from backend.core.backup.restore import jobs
from backend.core.backup.restore.runner import _restore_backup_sync, _RestoreRequest

logger = logging.getLogger(__name__)


class BackupManager:
    # Job tracking: job_id -> {status, progress, error, warnings}. The same object the
    # restore stages use, so seeding or clearing it here is visible to them.
    restore_jobs: Dict[str, Dict[str, Any]] = jobs.restore_jobs

    # Export lives in backup.export; these keep the public entry points on the facade so
    # callers and the worker tasks are unaffected by where the implementation sits.
    @staticmethod
    async def create_backup(
        include_audio: bool = True,
        archive_quality: str = ARCHIVE_QUALITY_COMPRESSED,
        progress_callback: Any = None,
    ) -> Tuple[str, Dict[str, Any]]:
        return await create_backup(
            include_audio=include_audio,
            archive_quality=archive_quality,
            progress_callback=progress_callback,
        )

    @staticmethod
    def create_backup_blocking(
        include_audio: bool = True,
        archive_quality: str = ARCHIVE_QUALITY_COMPRESSED,
        progress_callback: Any = None,
    ) -> Tuple[str, Dict[str, Any]]:
        return create_backup_blocking(
            include_audio=include_audio,
            archive_quality=archive_quality,
            progress_callback=progress_callback,
        )

    @staticmethod
    def _get_app_version() -> str:
        return runtime.get_app_version()

    @staticmethod
    def _documents_directory(path_manager: Any) -> Any:
        """Resolve the documents directory, tolerating path managers without it."""
        documents_dir = getattr(path_manager, "documents_directory", None)
        if documents_dir is not None:
            return documents_dir
        return path_manager.user_data_directory / "documents"

    @staticmethod
    def restore_backup_blocking(
        job_id: str,
        zip_path: str,
        clear_existing: bool = False,
        overwrite_existing: bool = False,
        progress_callback: Any = None,
    ) -> Dict[str, Any]:
        """Run a restore synchronously and return the job record.

        This is the entry point the Celery task uses. Job state lives in the result
        backend rather than in an API process's memory, so it survives a restart and a
        restore no longer runs multi-gigabyte extraction inside a request worker.
        """
        path_manager = runtime.PathManager()

        jobs.restore_jobs.setdefault(
            job_id,
            {
                "status": "pending",
                "progress": "Initializing...",
                "error": None,
                "warnings": None,
            },
        )

        _restore_backup_sync(
            _RestoreRequest(
                job_id=job_id,
                zip_path=zip_path,
                clear_existing=clear_existing,
                overwrite_existing=overwrite_existing,
                recordings_dir=path_manager.recordings_directory,
                config_path=path_manager.config_path,
                user_data_dir=path_manager.user_data_directory,
                documents_dir=runtime.documents_directory(path_manager),
                progress_callback=progress_callback,
            )
        )

        return dict(jobs.restore_jobs[job_id])

    @staticmethod
    async def restore_backup(
        job_id: str,
        zip_path: str,
        clear_existing: bool = False,
        overwrite_existing: bool = False,
    ):
        """
        Async wrapper for the synchronous restore process.
        """
        import asyncio

        # Defensively initialise job status; the entry point normally sets it first.
        if job_id not in jobs.restore_jobs:
            jobs.restore_jobs[job_id] = {
                "status": "pending",
                "progress": "Initializing...",
                "error": None,
                "warnings": None,
            }

        try:
            await asyncio.to_thread(
                BackupManager.restore_backup_blocking,
                job_id,
                zip_path,
                clear_existing,
                overwrite_existing,
            )
        except Exception as e:
            logger.error(f"Restore failed: {e}", exc_info=True)
            jobs.restore_jobs[job_id]["status"] = "failed"
            jobs.restore_jobs[job_id]["error"] = str(e)
