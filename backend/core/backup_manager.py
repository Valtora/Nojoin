import json
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from uuid import uuid4

from sqlmodel import delete, select

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
    BACKUP_EXPORT_DIR,
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

logger = logging.getLogger(__name__)


@dataclass
class _RestoreState:
    """Mutable state threaded through the restore stages.

    Collecting the additive-restore bookkeeping here lets ``_restore_backup_sync``
    delegate to cohesive validation/preflight/extraction/finalization stages while
    preserving the exact cross-stage invariants (id remapping, identity matching,
    deferred speaker merges, proxy regeneration) the monolithic implementation relied on.
    """

    job_id: str
    clear_existing: bool
    overwrite_existing: bool
    recordings_dir: Any
    config_path: Any
    user_data_dir: Any
    documents_dir: Any
    # table_name -> { old_id: new_id } for additive foreign-key remapping.
    id_map: Dict[str, Dict[int, int]]
    # Identity key (meeting_uid:/public_id:/audio_path:) -> new recording id, so later
    # backup rows sharing any identifier collapse onto the same restored recording.
    restored_recording_keys: Dict[str, int] = field(default_factory=dict)
    # Identity key -> existing recording row already present in the target database.
    existing_recordings_by_identity: Dict[str, Any] = field(default_factory=dict)
    # old_id set for recordings skipped under the safe-merge strategy; their children skip too.
    skipped_recording_ids: Set[int] = field(default_factory=set)
    # Deferred self-referential remaps for recording-speaker merges (new_id, old_target_id).
    pending_recording_speaker_merges: List[Tuple[int, int]] = field(
        default_factory=list
    )
    # Restored recordings whose audio landed on disk need a regenerated playback proxy.
    recordings_requiring_proxy: Set[int] = field(default_factory=set)
    # Every recording newly inserted by this restore, for post-restore finalisation.
    restored_recording_ids: Set[int] = field(default_factory=set)
    # Directory the archive's payload is unpacked into. Files only move from here into
    # their real homes once the database transaction has committed, so a failed restore
    # cannot have touched a single existing file.
    staging_dir: Any = None
    # (staging path, final path) pairs applied after the commit succeeds.
    pending_moves: List[Tuple[str, str]] = field(default_factory=list)
    # Final paths already claimed by a pending move, so two restored rows cannot both
    # try to land on the same file.
    claimed_destinations: Set[str] = field(default_factory=set)
    # Progress sink, so a Celery-hosted restore can stream status to its result backend.
    progress_callback: Any = None
    # table_name -> reason -> count, surfaced to the operator when the restore finishes.
    skipped: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def report(self, progress: str) -> None:
        """Publish a progress string to the job record and any external sink."""
        job = BackupManager.restore_jobs.get(self.job_id)
        if job is not None:
            job["progress"] = progress
        if self.progress_callback is not None:
            try:
                self.progress_callback(progress)
            except Exception:  # noqa: BLE001 -- progress reporting must never fail a restore
                logger.debug("Restore progress callback failed", exc_info=True)

    def stage_path(self, member: str) -> str:
        """Absolute path an archive member was unpacked to."""
        return os.path.abspath(os.path.join(os.fspath(self.staging_dir), member))

    def claim_move(self, staged: str, destination: str) -> bool:
        """Queue a staged file to be moved into place after the commit.

        Returns False when the destination is already spoken for, letting the caller
        pick another name rather than have two rows overwrite one file.
        """
        destination = os.path.abspath(destination)
        if destination in self.claimed_destinations:
            return False
        self.claimed_destinations.add(destination)
        self.pending_moves.append((staged, destination))
        return True

    def record_skip(self, table_name: str, reason: str) -> None:
        """Count one row the restore could not bring across, by table and reason."""
        self.skipped.setdefault(table_name, {})
        self.skipped[table_name][reason] = self.skipped[table_name].get(reason, 0) + 1

    def skip_summary(self) -> Dict[str, Dict[str, int]]:
        """The skip tally, with empty tables omitted so a clean restore reports ``{}``."""
        return {
            table: dict(reasons) for table, reasons in self.skipped.items() if reasons
        }


class BackupManager:
    # Job tracking: job_id -> {status: str, progress: str, error: str, result: Dict}
    restore_jobs: Dict[str, Dict[str, Any]] = {}

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
    def _apply_foreign_keys(
        table_name: str,
        item_data: Dict[str, Any],
        id_map: Dict[str, Dict[int, int]],
    ) -> str | None:
        """Rewrite this row's foreign keys in place to point at the restored rows.

        Returns a skip reason when an ownership link cannot be resolved, or ``None`` when
        the row is ready to insert. Enrichment links that cannot be resolved are nulled.
        See :class:`_ForeignKeySpec` for why ownership, not nullability, is the test.
        """
        for spec in RESTORE_FOREIGN_KEYS.get(table_name, ()):
            old_value = item_data.get(spec.column)

            if old_value is None:
                # A backup row that never had an owner cannot gain one here, and an
                # ownerless row would be invisible to every user.
                if spec.ownership:
                    return SKIP_REASON_UNRESOLVED_OWNER
                continue

            new_value = id_map.get(spec.target_table, {}).get(old_value)
            if new_value is not None:
                item_data[spec.column] = new_value
                continue

            if spec.ownership:
                return SKIP_REASON_UNRESOLVED_OWNER

            item_data[spec.column] = None

        return None

    @staticmethod
    def _enqueue_proxy_generation(recording_id: int) -> None:
        from backend.worker.tasks import generate_proxy_task

        generate_proxy_task.delay(recording_id)

    @staticmethod
    def _parse_version(value: Any) -> Tuple[int, ...]:
        """Parse a dotted version into comparable integers, ignoring any suffix.

        String comparison is wrong here: ``"0.10.0" > "0.9.0"`` is False lexicographically,
        so the newer-backup warning misfired on most real version bumps.
        """
        parts: List[int] = []
        for chunk in str(value or "").strip().split("."):
            digits = ""
            for char in chunk:
                if not char.isdigit():
                    break
                digits += char
            if not digits:
                break
            parts.append(int(digits))
        return tuple(parts)

    @staticmethod
    def _restore_check_version(zipf: zipfile.ZipFile) -> None:
        """Validation stage: refuse archives this build cannot read, log the rest.

        A newer archive format is rejected here, at the door, rather than failing
        somewhere deep in the insert loop with an error nobody can act on.
        """
        if "backup_info.json" not in zipf.namelist():
            # Archives predating backup_info.json are format version 1 by definition.
            return

        try:
            info = json.loads(zipf.read("backup_info.json"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to read backup info: {e}")
            return

        format_version = info.get("format_version", LEGACY_BACKUP_FORMAT_VERSION)
        try:
            format_version = int(format_version)
        except (TypeError, ValueError):
            format_version = LEGACY_BACKUP_FORMAT_VERSION

        if format_version > BACKUP_FORMAT_VERSION:
            raise ValueError(
                f"This backup uses archive format version {format_version}, but this "
                f"installation only understands up to version {BACKUP_FORMAT_VERSION}. "
                "Upgrade Nojoin on this server before restoring it."
            )

        backup_version = info.get("version", "0.0.0")
        current_version = runtime.get_app_version()

        if backup_version != current_version:
            logger.info(
                f"Restoring backup from version {backup_version} to {current_version}"
            )
            if BackupManager._parse_version(
                backup_version
            ) > BackupManager._parse_version(current_version):
                logger.warning(
                    f"Restoring a backup from a NEWER application version "
                    f"({backup_version}) onto an OLDER one ({current_version}). "
                    "This may cause issues."
                )

    @staticmethod
    def _restore_preflight_validate(
        zipf: zipfile.ZipFile, state: "_RestoreState"
    ) -> None:
        """Validate the whole archive before anything destructive happens.

        Everything that can be checked cheaply is checked here: the format version, that
        every JSON member parses, and that the unpacked payload will fit on disk. A
        clearing restore destroys the existing library, so a malformed or oversized
        archive must be refused before that point, not discovered halfway through it.
        """
        state.report("Validating backup...")

        BackupManager._restore_check_version(zipf)

        for member in zipf.namelist():
            if not member.endswith(".json"):
                continue
            try:
                json.loads(zipf.read(member))
            except Exception as e:  # noqa: BLE001
                raise ValueError(
                    f"This backup is damaged: {member} is not readable ({e})."
                ) from e

        payload_bytes = sum(
            info.file_size
            for info in zipf.infolist()
            if info.filename.startswith(("recordings/", "documents/"))
        )
        if payload_bytes:
            free_bytes = shutil.disk_usage(os.fspath(state.user_data_dir)).free
            # Payload plus headroom, because the staged copy and the final copy briefly
            # coexist and the database needs room to work.
            required = payload_bytes + max(
                int(payload_bytes * 0.1), RESTORE_DISK_HEADROOM_BYTES
            )
            if free_bytes < required:
                raise ValueError(
                    "Not enough free disk space to restore this backup: it needs about "
                    f"{required // (1024 * 1024)} MB including headroom, but only "
                    f"{free_bytes // (1024 * 1024)} MB is available."
                )

    @staticmethod
    def _restore_clear_existing_data(session: Any, state: "_RestoreState") -> None:
        """Wipe non-user tables inside the restore's own transaction.

        Joining the transaction is what makes a failed clearing restore recoverable: if
        anything later goes wrong the delete rolls back with it, instead of leaving an
        empty installation. Users are intentionally preserved to avoid lockout, and the
        recordings directory is wiped only after the commit succeeds.
        """
        if not state.clear_existing:
            return

        logger.info("Clearing existing data...")
        for table_name, model_cls in reversed(runtime.MODELS):
            if table_name == "users":
                continue
            session.exec(delete(model_cls))

        state.report("Old data cleared")

    @staticmethod
    def _restore_extract_files(zipf: zipfile.ZipFile, state: "_RestoreState") -> None:
        """Unpack the archive payload into a staging directory.

        Nothing is written to the recordings or documents directories here. Files move
        into place only after the database transaction commits, which is what makes the
        Skip conflict mode honest: a recording the restore decides to keep as-is never
        has its audio overwritten, because the archive's copy never leaves staging.

        A zip-slip path aborts the restore with a ``ValueError``.
        """
        logger.info("Extracting files...")
        state.report("Extracting files...")

        staging_root = os.path.abspath(os.fspath(state.staging_dir))
        os.makedirs(staging_root, exist_ok=True)

        for member in zipf.namelist():
            if not member.startswith(("recordings/", "documents/")):
                continue

            target_path = os.path.abspath(os.path.join(staging_root, member))
            if not target_path.startswith(staging_root + os.sep):
                error_msg = f"Zip Slip detected: Skipping malicious file path {member}"
                logger.error(error_msg)
                raise ValueError(error_msg)

            if member.endswith("/"):
                continue

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with zipf.open(member) as source, open(target_path, "wb") as target:
                shutil.copyfileobj(source, target)

        logger.info("File extraction complete.")

    @staticmethod
    def _restore_config(zipf: zipfile.ZipFile, state: "_RestoreState") -> None:
        """Merge the archived config in, on a clearing restore only.

        config.json is a flat map that ConfigManager filters to known keys on load and
        which never holds secrets, so a plain update is enough.
        """
        if not state.clear_existing or "config.json" not in zipf.namelist():
            return

        try:
            new_config = json.loads(zipf.read("config.json"))
            if not isinstance(new_config, dict):
                return

            config_path = state.config_path
            if config_path.exists():
                current_config = json.loads(config_path.read_text())
                current_config.update(new_config)
                config_path.write_text(json.dumps(current_config, indent=2))
            else:
                config_path.write_text(json.dumps(new_config, indent=2))
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to restore config: {e}")

    @staticmethod
    def _restore_apply_pending_moves(state: "_RestoreState") -> None:
        """Move staged files into place, after the transaction has committed."""
        for staged_path, destination in state.pending_moves:
            try:
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.move(staged_path, destination)
            except OSError as e:
                logger.error(
                    "Failed to move restored file %s into place at %s: %s",
                    staged_path,
                    destination,
                    e,
                )

    @staticmethod
    def _restore_cleanup_staging(state: "_RestoreState") -> None:
        """Delete the staging directory.

        Whatever is left in it belongs to rows the restore did not insert, so this is the
        whole of orphan cleanup: there is no sweep over live directories to get wrong.
        """
        state.report("Cleaning up...")
        staging_dir = state.staging_dir
        if staging_dir is None:
            return
        try:
            shutil.rmtree(os.fspath(staging_dir), ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to remove restore staging directory: %s", e)

    @staticmethod
    def _restore_preflight_overwrite(
        zipf: zipfile.ZipFile, session: Any, state: "_RestoreState"
    ) -> None:
        """Under overwrite, pre-delete conflicting recordings inside the transaction.

        Deleting through the ``Recording`` table lets the configured ON DELETE cascades
        remove dependent rows so the incoming backup rows insert without a unique clash.
        Runs in the restore's own session so it rolls back with everything else.
        """
        if not state.overwrite_existing or state.clear_existing:
            return
        if "recordings.json" not in zipf.namelist():
            return

        try:
            rec_data = json.loads(zipf.read("recordings.json"))
            backup_recording_keys: set[str] = set()
            for item in rec_data:
                backup_recording_keys.update(
                    _get_recording_match_keys(
                        item.get("audio_path"),
                        item.get("meeting_uid"),
                        item.get("public_id"),
                    )
                )

            if not backup_recording_keys:
                return

            existing_rows = session.exec(select(runtime.Recording)).all()
            existing_ids = [
                row.id
                for row in existing_rows
                if _get_recording_match_keys(
                    row.audio_path,
                    getattr(row, "meeting_uid", None),
                    getattr(row, "public_id", None),
                )
                & backup_recording_keys
            ]

            if existing_ids:
                logger.info(
                    f"Overwrite: Pre-deleting {len(existing_ids)} conflicting recordings."
                )
                # Delete via the Recording table so the configured
                # ON DELETE cascades remove dependent rows.
                session.exec(
                    delete(runtime.Recording).where(
                        runtime.Recording.id.in_(existing_ids)
                    )
                )
                session.flush()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Pre-flight cleanup failed: {e}")

    @staticmethod
    def _enqueue_recording_finalization(recording_id: int, needs_proxy: bool) -> None:
        from backend.worker.tasks import finalize_restored_recording_task

        finalize_restored_recording_task.delay(recording_id, needs_proxy=needs_proxy)

    @staticmethod
    def _normalise_restored_recording_state(
        session: Any, state: "_RestoreState"
    ) -> None:
        """Finalization stage: settle the status of every newly restored recording.

        A recording captured mid-flight carries a status this installation can never
        advance. Recordings whose transcript came across completed are marked processed;
        the rest are marked errored so the operator can reprocess them deliberately.
        """
        for recording_id in sorted(state.restored_recording_ids):
            recording = session.get(runtime.Recording, recording_id)
            if recording is None:
                continue

            status = getattr(recording, "status", None)
            status_value = getattr(status, "value", status)
            if status_value not in TRANSIENT_RECORDING_STATUSES:
                continue

            transcript = session.exec(
                select(runtime.Transcript).where(
                    runtime.Transcript.recording_id == recording_id
                )
            ).first()
            transcript_complete = bool(transcript) and (
                getattr(transcript, "transcript_status", "completed") == "completed"
            )

            if transcript_complete:
                recording.status = RESTORED_RECORDING_TERMINAL_STATUS
            else:
                recording.status = RESTORED_RECORDING_INTERRUPTED_STATUS
                if transcript is not None and hasattr(transcript, "error_message"):
                    transcript.error_message = RESTORED_RECORDING_INTERRUPTED_MESSAGE
                    session.add(transcript)

            logger.info(
                "Restored recording %s carried in-flight status %s; settled as %s.",
                recording_id,
                status_value,
                recording.status,
            )
            session.add(recording)

    @staticmethod
    def _restore_enqueue_finalization(state: "_RestoreState") -> None:
        """Finalization stage: queue the per-recording rebuild of derived artefacts.

        One task per recording rather than one per concern. It runs on the io lane and
        dispatches proxy generation to the cpu lane, so the queue separation survives.
        """
        for recording_id in sorted(state.restored_recording_ids):
            try:
                BackupManager._enqueue_recording_finalization(
                    recording_id,
                    needs_proxy=recording_id in state.recordings_requiring_proxy,
                )
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "Failed to enqueue finalization for restored recording %s: %s",
                    recording_id,
                    e,
                )

    @staticmethod
    def _restore_backup_sync(
        job_id: str,
        zip_path: str,
        clear_existing: bool,
        overwrite_existing: bool,
        recordings_dir: Path,
        config_path: Path,
        user_data_dir: Path,
        documents_dir: Path,
        progress_callback: Any = None,
    ):
        """
        Synchronous implementation of backup restoration.

        The shape matters as much as the steps. Everything destructive is deferred until
        after a single database transaction commits: the archive is validated first, its
        payload is unpacked into staging, the clear and the insert share one transaction,
        and only once that commits do any files move or get deleted. A failure at any
        point before the commit leaves the installation exactly as it was.
        """
        job = BackupManager.restore_jobs.setdefault(job_id, {})
        job["status"] = "processing"
        job.setdefault("error", None)
        job.setdefault("warnings", None)
        logger.info(f"Starting synchronous restore process for {zip_path}")

        state = _RestoreState(
            job_id=job_id,
            clear_existing=clear_existing,
            overwrite_existing=overwrite_existing,
            recordings_dir=recordings_dir,
            config_path=config_path,
            user_data_dir=user_data_dir,
            documents_dir=documents_dir,
            id_map={name: {} for name, _ in runtime.MODELS},
            staging_dir=os.path.join(
                os.fspath(user_data_dir), RESTORE_STAGING_DIRNAME, job_id
            ),
            progress_callback=progress_callback,
        )
        state.report("Starting...")

        # Local aliases share the same mutable objects as ``state`` so the inline
        # database-restore loop and the extracted stages observe one set of bookkeeping.
        id_map = state.id_map
        restored_recording_keys = state.restored_recording_keys
        existing_recordings_by_identity = state.existing_recordings_by_identity
        existing_recordings_loaded = False
        skipped_recording_ids = state.skipped_recording_ids
        pending_recording_speaker_merges = state.pending_recording_speaker_merges
        recordings_requiring_proxy = state.recordings_requiring_proxy

        try:
            with zipfile.ZipFile(zip_path, "r") as zipf:
                BackupManager._restore_preflight_validate(zipf, state)
                BackupManager._restore_extract_files(zipf, state)

                # Restore Database
                logger.info("Restoring database records...")
                state.report("Restoring database...")
                from sqlmodel import Session

                with Session(runtime.sync_engine) as session:
                    BackupManager._restore_clear_existing_data(session, state)
                    BackupManager._restore_preflight_overwrite(zipf, session, state)
                    for table_name, model_cls in runtime.MODELS:
                        if f"{table_name}.json" not in zipf.namelist():
                            continue

                        try:
                            data = json.loads(zipf.read(f"{table_name}.json"))
                        except Exception as e:  # noqa: BLE001
                            logger.error(f"Failed to read/parse {table_name}.json: {e}")
                            continue

                        # Topological Sort for Tags to ensure Parents are created first
                        if table_name in ["tags", "p_tags"]:
                            data = _topological_sort(data)

                        count = 0
                        for item_data in data:
                            if table_name == "calendar_provider_configs":
                                item_data = (
                                    _prepare_calendar_provider_config_for_restore(
                                        item_data
                                    )
                                )
                            elif table_name == "calendar_connections":
                                item_data = _prepare_calendar_connection_for_restore(
                                    item_data
                                )

                            # Adapt record to current schema (handle removed columns)
                            item_data = _adapt_record(model_cls, item_data)

                            old_id = item_data.get("id")
                            # Staged file backing this row, if any, so the post-insert
                            # bookkeeping can size it and flag it for a proxy rebuild.
                            pending_audio_source = None

                            # Under the safe-merge strategy a conflicting recording is kept as
                            # it already exists in the target, and its children are not merged
                            # in. This has to be checked against the backup's own recording id,
                            # so it runs before the remap below rewrites it.
                            if (
                                item_data.get("recording_id") in skipped_recording_ids
                                and table_name != "recordings"
                            ):
                                continue

                            # Rewrite foreign keys up front, before any conflict check, so
                            # every branch below compares against target-database ids rather
                            # than the source system's. A row whose owner cannot be resolved
                            # is dropped here and never reaches the identity matching, so it
                            # cannot adopt an existing row's id and pull its children along.
                            skip_reason = BackupManager._apply_foreign_keys(
                                table_name, item_data, id_map
                            )
                            if skip_reason is not None:
                                state.record_skip(table_name, skip_reason)
                                continue

                            # Handle Conflict / Additive Logic

                            # Special handling for Users: Resolve by username
                            if table_name == "users":
                                if item_data.get("settings"):
                                    item_data["settings"] = (
                                        _restore_redacted_sensitive_data(
                                            item_data["settings"]
                                        )
                                    )

                                username = item_data.get("username")
                                existing_user = session.exec(
                                    select(runtime.User).where(
                                        runtime.User.username == username
                                    )
                                ).first()

                                if existing_user:
                                    # User exists. Map old_id to existing_id.
                                    # Do NOT overwrite the user (security risk, plus passwords etc).
                                    if old_id is not None:
                                        id_map["users"][old_id] = existing_user.id
                                    continue  # Skip inserting this user

                            # Special handling for Recordings: Resolve by durable identifiers
                            elif table_name == "recordings":
                                audio_path = item_data.get("audio_path")
                                meeting_uid = item_data.get("meeting_uid")
                                public_id = item_data.get("public_id")
                                backup_keys = _get_recording_match_keys(
                                    audio_path,
                                    meeting_uid,
                                    public_id,
                                )

                                if not existing_recordings_loaded:
                                    existing_rows = session.exec(
                                        select(runtime.Recording)
                                    ).all()
                                    for row in existing_rows:
                                        for key in _get_recording_match_keys(
                                            row.audio_path,
                                            getattr(row, "meeting_uid", None),
                                            getattr(row, "public_id", None),
                                        ):
                                            existing_recordings_by_identity[key] = row
                                    existing_recordings_loaded = True

                                if not backup_keys:
                                    logger.warning(
                                        "Skipping recording restore because no usable identity "
                                        "(meeting_uid, public_id, or audio_path) was found."
                                    )
                                    state.record_skip(
                                        table_name, SKIP_REASON_NO_IDENTITY
                                    )
                                    continue

                                # DUPLICATE IN BACKUP CHECK:
                                # If we already restored a recording in this session that shares ANY
                                # identifier with this row, link old_id to that new_id and skip.
                                duplicate_match_id = next(
                                    (
                                        restored_recording_keys[key]
                                        for key in backup_keys
                                        if key in restored_recording_keys
                                    ),
                                    None,
                                )
                                if duplicate_match_id is not None:
                                    logger.warning(
                                        f"Duplicate recording in backup JSON (audio_path={audio_path}, "
                                        f"meeting_uid={meeting_uid}, public_id={public_id}). "
                                        f"Linking old_id {old_id} to existing new_id {duplicate_match_id}"
                                    )
                                    if old_id is not None:
                                        id_map["recordings"][old_id] = (
                                            duplicate_match_id
                                        )
                                    continue

                                existing_rec = next(
                                    (
                                        existing_recordings_by_identity[key]
                                        for key in backup_keys
                                        if key in existing_recordings_by_identity
                                    ),
                                    None,
                                )

                                if existing_rec:
                                    # Identity conflict with a recording already present.
                                    if overwrite_existing:
                                        # Overwrite mode: pre-flight cleanup removes conflicting
                                        # rows up front; delete any that survived it so this
                                        # backup row can be inserted without a unique clash.
                                        logger.warning(
                                            f"Fallback delete triggered for recording match (audio_path={audio_path}, "
                                            f"meeting_uid={meeting_uid}, public_id={public_id}). Deleting ID {existing_rec.id}."
                                        )
                                        # Drop every cached key pointing at the deleted row so a later
                                        # backup row that shares some other identifier does not re-match it.
                                        for stale_key in [
                                            k
                                            for k, v in existing_recordings_by_identity.items()
                                            if v is existing_rec
                                        ]:
                                            existing_recordings_by_identity.pop(
                                                stale_key, None
                                            )
                                        session.delete(existing_rec)
                                        session.flush()
                                    else:
                                        # Skip strategy (safe merge): map the backup row's
                                        # id onto the existing row and reuse it.
                                        if old_id is not None:
                                            id_map["recordings"][old_id] = (
                                                existing_rec.id
                                            )
                                            skipped_recording_ids.add(old_id)
                                            # Tracks as restored/processed so subsequent duplicates map to it,
                                            # registering every identity the existing row owns.
                                            for (
                                                existing_key
                                            ) in _get_recording_match_keys(
                                                existing_rec.audio_path,
                                                getattr(
                                                    existing_rec, "meeting_uid", None
                                                ),
                                                getattr(
                                                    existing_rec, "public_id", None
                                                ),
                                            ):
                                                restored_recording_keys[
                                                    existing_key
                                                ] = existing_rec.id
                                            for backup_key in backup_keys:
                                                restored_recording_keys[backup_key] = (
                                                    existing_rec.id
                                                )
                                        continue

                                normalized_meeting_uid = _normalise_meeting_uid(
                                    meeting_uid
                                )
                                if normalized_meeting_uid:
                                    item_data["meeting_uid"] = normalized_meeting_uid
                                else:
                                    item_data.pop("meeting_uid", None)

                                normalized_public_id = _normalise_public_id(public_id)
                                if normalized_public_id:
                                    item_data["public_id"] = normalized_public_id
                                else:
                                    item_data.pop("public_id", None)

                                # Backups do not preserve proxy files; regenerate them after restore.
                                item_data["proxy_path"] = None

                                # Drop state that belongs to the source installation. The
                                # celery_task_id in particular names a task on another
                                # broker, which the cancel path would otherwise try to revoke.
                                for transient_field, blank in (
                                    ("celery_task_id", None),
                                    ("client_status", None),
                                    ("processing_step", None),
                                    ("processing_progress", 0),
                                    ("upload_progress", 0),
                                ):
                                    if transient_field in item_data:
                                        item_data[transient_field] = blank

                                # Canonical utterances are rebuilt from the transcript
                                # projection rather than archived, so restored recordings
                                # enter as un-classified and are backfilled exactly like
                                # legacy rows. The status is settled in the finalisation
                                # pass, once the transcript rows exist.
                                # Set unconditionally, not only when the archive carried the
                                # column: a legacy archive omits it entirely, and leaving it
                                # absent lets the model default reapply, marking the recording
                                # canonical when it has no canonical rows at all.
                                if hasattr(model_cls, "pipeline_generation"):
                                    item_data["pipeline_generation"] = None

                                runtime_audio_path = (
                                    _build_runtime_recording_audio_path(
                                        audio_path,
                                        recordings_dir,
                                    )
                                    or audio_path
                                )
                                item_data["audio_path"] = runtime_audio_path

                                # Proactive collision handling for unique columns.
                                # If a stale row in the target shares ``public_id`` (without sharing
                                # any identity we could match), regenerate ours rather than aborting
                                # the restore on a unique-constraint violation.
                                if hasattr(model_cls, "public_id") and item_data.get(
                                    "public_id"
                                ):
                                    conflicting_pid_row = session.exec(
                                        select(model_cls).where(
                                            model_cls.public_id
                                            == item_data["public_id"]
                                        )
                                    ).first()
                                    if conflicting_pid_row is not None:
                                        new_pid = str(uuid4())
                                        logger.warning(
                                            f"public_id collision for restored recording "
                                            f"(value={item_data['public_id']!r}); regenerating to {new_pid}."
                                        )
                                        item_data["public_id"] = new_pid

                                # Same defensive treatment for audio_path, which is unique.
                                # With staging there is no file to rename: the row simply
                                # picks a free destination and the staged copy is moved
                                # there once the transaction commits.
                                if item_data.get("audio_path"):
                                    conflicting_path_row = session.exec(
                                        select(model_cls).where(
                                            model_cls.audio_path
                                            == item_data["audio_path"]
                                        )
                                    ).first()
                                    if conflicting_path_row is not None:
                                        original_path = item_data["audio_path"]
                                        stem, ext = os.path.splitext(original_path)
                                        suffix = (
                                            item_data.get("meeting_uid")
                                            or item_data.get("public_id")
                                            or str(uuid4())
                                        )
                                        new_path = f"{stem}__{suffix}{ext}"
                                        logger.warning(
                                            f"audio_path collision for restored recording "
                                            f"({original_path}); storing it as {new_path}."
                                        )
                                        item_data["audio_path"] = new_path

                                # Queue the staged audio to be moved into place after the
                                # commit. Under the Skip conflict mode this is never
                                # reached for a recording that already exists, which is
                                # why the existing copy's audio is now left alone.
                                staged_audio = state.stage_path(audio_path or "")
                                if audio_path and os.path.isfile(staged_audio):
                                    destination = item_data["audio_path"]
                                    if not state.claim_move(staged_audio, destination):
                                        stem, ext = os.path.splitext(destination)
                                        destination = f"{stem}__{uuid4()}{ext}"
                                        state.claim_move(staged_audio, destination)
                                        item_data["audio_path"] = destination
                                    pending_audio_source = staged_audio
                                else:
                                    pending_audio_source = None

                            else:
                                # Tag names are unique per owner. user_id is already the
                                # target-database id at this point.
                                if table_name == "tags":
                                    existing_tag = session.exec(
                                        select(runtime.Tag)
                                        .where(
                                            runtime.Tag.name == item_data.get("name")
                                        )
                                        .where(
                                            runtime.Tag.user_id
                                            == item_data.get("user_id")
                                        )
                                    ).first()

                                    if existing_tag:
                                        if old_id is not None:
                                            id_map["tags"][old_id] = existing_tag.id
                                        continue

                                elif table_name == "p_tags":
                                    existing_p_tag = session.exec(
                                        select(runtime.PeopleTag)
                                        .where(
                                            runtime.PeopleTag.name
                                            == item_data.get("name")
                                        )
                                        .where(
                                            runtime.PeopleTag.user_id
                                            == item_data.get("user_id")
                                        )
                                    ).first()

                                    if existing_p_tag:
                                        if old_id is not None:
                                            id_map["p_tags"][old_id] = existing_p_tag.id
                                        continue

                            # Removes ID to allow the database to generate a new one.
                            if "id" in item_data:
                                del item_data["id"]

                            old_recording_speaker_merge_id = None

                            # Per-table conflict resolution. Foreign keys are already
                            # remapped by _apply_foreign_keys above, so every comparison
                            # below is against target-database ids.
                            if table_name == "calendar_provider_configs":
                                candidate = model_cls.model_validate(item_data)
                                existing_config = session.exec(
                                    select(runtime.CalendarProviderConfig).where(
                                        runtime.CalendarProviderConfig.provider
                                        == candidate.provider
                                    )
                                ).first()

                                if existing_config:
                                    if overwrite_existing:
                                        existing_config.client_id = candidate.client_id
                                        existing_config.client_secret_encrypted = (
                                            candidate.client_secret_encrypted
                                        )
                                        existing_config.tenant_id = candidate.tenant_id
                                        existing_config.enabled = candidate.enabled
                                        session.add(existing_config)
                                    else:
                                        updated = False
                                        if (
                                            not existing_config.client_id
                                            and candidate.client_id
                                        ):
                                            existing_config.client_id = (
                                                candidate.client_id
                                            )
                                            updated = True
                                        if (
                                            not existing_config.client_secret_encrypted
                                            and candidate.client_secret_encrypted
                                        ):
                                            existing_config.client_secret_encrypted = (
                                                candidate.client_secret_encrypted
                                            )
                                            updated = True
                                        if (
                                            candidate.provider
                                            == runtime.CalendarProvider.MICROSOFT.value
                                            and not existing_config.tenant_id
                                            and candidate.tenant_id
                                        ):
                                            existing_config.tenant_id = (
                                                candidate.tenant_id
                                            )
                                            updated = True
                                        if updated:
                                            session.add(existing_config)

                                    if old_id is not None:
                                        id_map["calendar_provider_configs"][old_id] = (
                                            existing_config.id
                                        )
                                    continue

                            elif table_name == "calendar_connections":
                                candidate = model_cls.model_validate(item_data)
                                existing_connection = session.exec(
                                    select(runtime.CalendarConnection)
                                    .where(
                                        runtime.CalendarConnection.user_id
                                        == candidate.user_id
                                    )
                                    .where(
                                        runtime.CalendarConnection.provider
                                        == candidate.provider
                                    )
                                    .where(
                                        runtime.CalendarConnection.provider_account_id
                                        == candidate.provider_account_id
                                    )
                                ).first()

                                if existing_connection:
                                    if overwrite_existing:
                                        existing_connection.email = candidate.email
                                        existing_connection.display_name = (
                                            candidate.display_name
                                        )
                                        existing_connection.access_token_encrypted = (
                                            candidate.access_token_encrypted
                                        )
                                        existing_connection.refresh_token_encrypted = (
                                            candidate.refresh_token_encrypted
                                        )
                                        existing_connection.granted_scopes = (
                                            candidate.granted_scopes
                                        )
                                        existing_connection.token_expires_at = (
                                            candidate.token_expires_at
                                        )
                                        existing_connection.sync_status = (
                                            candidate.sync_status
                                        )
                                        existing_connection.sync_error = (
                                            candidate.sync_error
                                        )
                                        existing_connection.last_sync_started_at = (
                                            candidate.last_sync_started_at
                                        )
                                        existing_connection.last_sync_completed_at = (
                                            candidate.last_sync_completed_at
                                        )
                                        existing_connection.last_synced_at = (
                                            candidate.last_synced_at
                                        )
                                        session.add(existing_connection)
                                    else:
                                        updated = False
                                        for field in (
                                            "email",
                                            "display_name",
                                            "access_token_encrypted",
                                            "refresh_token_encrypted",
                                            "token_expires_at",
                                        ):
                                            if not getattr(
                                                existing_connection, field
                                            ) and getattr(candidate, field):
                                                setattr(
                                                    existing_connection,
                                                    field,
                                                    getattr(candidate, field),
                                                )
                                                updated = True

                                        if (
                                            not existing_connection.granted_scopes
                                            and candidate.granted_scopes
                                        ):
                                            existing_connection.granted_scopes = (
                                                candidate.granted_scopes
                                            )
                                            updated = True

                                        existing_sync_marker = (
                                            existing_connection.last_synced_at
                                            or existing_connection.last_sync_completed_at
                                        )
                                        candidate_sync_marker = (
                                            candidate.last_synced_at
                                            or candidate.last_sync_completed_at
                                        )
                                        if candidate_sync_marker and (
                                            existing_sync_marker is None
                                            or candidate_sync_marker
                                            >= existing_sync_marker
                                        ):
                                            existing_connection.sync_status = (
                                                candidate.sync_status
                                            )
                                            existing_connection.sync_error = (
                                                candidate.sync_error
                                            )
                                            existing_connection.last_sync_started_at = (
                                                candidate.last_sync_started_at
                                            )
                                            existing_connection.last_sync_completed_at = candidate.last_sync_completed_at
                                            existing_connection.last_synced_at = (
                                                candidate.last_synced_at
                                            )
                                            updated = True

                                        if updated:
                                            session.add(existing_connection)

                                    if old_id is not None:
                                        id_map["calendar_connections"][old_id] = (
                                            existing_connection.id
                                        )
                                    continue

                            elif table_name == "calendar_sources":
                                candidate = model_cls.model_validate(item_data)
                                existing_source = session.exec(
                                    select(runtime.CalendarSource)
                                    .where(
                                        runtime.CalendarSource.connection_id
                                        == candidate.connection_id
                                    )
                                    .where(
                                        runtime.CalendarSource.provider_calendar_id
                                        == candidate.provider_calendar_id
                                    )
                                ).first()

                                if existing_source:
                                    if overwrite_existing:
                                        existing_source.name = candidate.name
                                        existing_source.description = (
                                            candidate.description
                                        )
                                        existing_source.time_zone = candidate.time_zone
                                        existing_source.colour = candidate.colour
                                        existing_source.user_colour = (
                                            candidate.user_colour
                                        )
                                        existing_source.is_primary = (
                                            candidate.is_primary
                                        )
                                        existing_source.is_read_only = (
                                            candidate.is_read_only
                                        )
                                        existing_source.is_selected = (
                                            candidate.is_selected
                                        )
                                        existing_source.sync_cursor = (
                                            candidate.sync_cursor
                                        )
                                        existing_source.last_synced_at = (
                                            candidate.last_synced_at
                                        )
                                        existing_source.sync_window_start = (
                                            candidate.sync_window_start
                                        )
                                        existing_source.sync_window_end = (
                                            candidate.sync_window_end
                                        )
                                        session.add(existing_source)
                                    else:
                                        updated = False
                                        for field in (
                                            "name",
                                            "description",
                                            "time_zone",
                                        ):
                                            candidate_value = getattr(candidate, field)
                                            if (
                                                candidate_value
                                                and getattr(existing_source, field)
                                                != candidate_value
                                            ):
                                                setattr(
                                                    existing_source,
                                                    field,
                                                    candidate_value,
                                                )
                                                updated = True

                                        if (
                                            candidate.colour
                                            and existing_source.colour
                                            != candidate.colour
                                        ):
                                            existing_source.colour = candidate.colour
                                            updated = True
                                        if (
                                            not existing_source.user_colour
                                            and candidate.user_colour
                                        ):
                                            existing_source.user_colour = (
                                                candidate.user_colour
                                            )
                                            updated = True
                                        if (
                                            candidate.is_primary
                                            and not existing_source.is_primary
                                        ):
                                            existing_source.is_primary = True
                                            updated = True
                                        if (
                                            candidate.is_read_only
                                            and not existing_source.is_read_only
                                        ):
                                            existing_source.is_read_only = True
                                            updated = True
                                        if (
                                            candidate.is_selected
                                            and not existing_source.is_selected
                                        ):
                                            existing_source.is_selected = True
                                            updated = True

                                        if candidate.last_synced_at and (
                                            existing_source.last_synced_at is None
                                            or candidate.last_synced_at
                                            >= existing_source.last_synced_at
                                        ):
                                            existing_source.sync_cursor = (
                                                candidate.sync_cursor
                                            )
                                            existing_source.last_synced_at = (
                                                candidate.last_synced_at
                                            )
                                            existing_source.sync_window_start = (
                                                candidate.sync_window_start
                                            )
                                            existing_source.sync_window_end = (
                                                candidate.sync_window_end
                                            )
                                            updated = True

                                        if updated:
                                            session.add(existing_source)

                                    if old_id is not None:
                                        id_map["calendar_sources"][old_id] = (
                                            existing_source.id
                                        )
                                    continue

                            elif table_name == "calendar_events":
                                candidate = model_cls.model_validate(item_data)
                                existing_event = session.exec(
                                    select(runtime.CalendarEvent)
                                    .where(
                                        runtime.CalendarEvent.calendar_id
                                        == candidate.calendar_id
                                    )
                                    .where(
                                        runtime.CalendarEvent.provider_event_id
                                        == candidate.provider_event_id
                                    )
                                ).first()

                                if existing_event:
                                    should_replace_event = overwrite_existing or (
                                        candidate.external_updated_at is not None
                                        and (
                                            existing_event.external_updated_at is None
                                            or candidate.external_updated_at
                                            >= existing_event.external_updated_at
                                        )
                                    )

                                    if should_replace_event:
                                        existing_event.title = candidate.title
                                        existing_event.status = candidate.status
                                        existing_event.is_all_day = candidate.is_all_day
                                        existing_event.starts_at = candidate.starts_at
                                        existing_event.ends_at = candidate.ends_at
                                        existing_event.start_date = candidate.start_date
                                        existing_event.end_date = candidate.end_date
                                        existing_event.location_text = (
                                            candidate.location_text
                                        )
                                        existing_event.meeting_url = (
                                            candidate.meeting_url
                                        )
                                        existing_event.source_url = candidate.source_url
                                        existing_event.external_updated_at = (
                                            candidate.external_updated_at
                                        )
                                        session.add(existing_event)
                                    else:
                                        updated = False
                                        for field in (
                                            "location_text",
                                            "meeting_url",
                                            "source_url",
                                        ):
                                            if not getattr(
                                                existing_event, field
                                            ) and getattr(candidate, field):
                                                setattr(
                                                    existing_event,
                                                    field,
                                                    getattr(candidate, field),
                                                )
                                                updated = True
                                        if updated:
                                            session.add(existing_event)

                                    if old_id is not None:
                                        id_map["calendar_events"][old_id] = (
                                            existing_event.id
                                        )
                                    continue

                            elif table_name == "global_speakers":
                                # Checks for existing duplicates to prevent redundant entries.
                                existing_speaker = session.exec(
                                    select(runtime.GlobalSpeaker)
                                    .where(
                                        runtime.GlobalSpeaker.name
                                        == item_data.get("name")
                                    )
                                    .where(
                                        runtime.GlobalSpeaker.user_id
                                        == item_data.get("user_id")
                                    )
                                ).first()

                                if existing_speaker:
                                    if overwrite_existing:
                                        # Updates existing speaker details from backup.
                                        existing_speaker.title = item_data.get("title")
                                        existing_speaker.company = item_data.get(
                                            "company"
                                        )
                                        existing_speaker.email = item_data.get("email")
                                        existing_speaker.phone_number = item_data.get(
                                            "phone_number"
                                        )
                                        existing_speaker.notes = item_data.get("notes")
                                        existing_speaker.color = item_data.get("color")
                                        if item_data.get("embedding"):
                                            existing_speaker.embedding = item_data.get(
                                                "embedding"
                                            )

                                        session.add(existing_speaker)
                                    else:
                                        # INTELLIGENT MERGE: Fill in missing fields only
                                        updated = False

                                        # CRM Fields
                                        for field in [
                                            "title",
                                            "company",
                                            "email",
                                            "phone_number",
                                            "notes",
                                            "color",
                                        ]:
                                            if not getattr(
                                                existing_speaker, field
                                            ) and item_data.get(field):
                                                setattr(
                                                    existing_speaker,
                                                    field,
                                                    item_data.get(field),
                                                )
                                                updated = True

                                        # Voice Embedding: Restore only if missing locally
                                        if (
                                            not existing_speaker.embedding
                                            or len(existing_speaker.embedding) == 0
                                        ) and item_data.get("embedding"):
                                            existing_speaker.embedding = item_data.get(
                                                "embedding"
                                            )
                                            updated = True

                                        if updated:
                                            session.add(existing_speaker)

                                    if old_id is not None:
                                        id_map["global_speakers"][old_id] = (
                                            existing_speaker.id
                                        )
                                    continue

                            elif table_name == "people_tag_links":
                                # Checks for duplicates
                                existing_link = session.exec(
                                    select(runtime.PeopleTagLink)
                                    .where(
                                        runtime.PeopleTagLink.global_speaker_id
                                        == item_data["global_speaker_id"]
                                    )
                                    .where(
                                        runtime.PeopleTagLink.tag_id
                                        == item_data["tag_id"]
                                    )
                                ).first()

                                if existing_link:
                                    if old_id is not None:
                                        id_map["people_tag_links"][old_id] = (
                                            existing_link.id
                                        )
                                    continue

                            elif table_name == "user_task_tags":
                                existing_link = session.exec(
                                    select(runtime.UserTaskTag)
                                    .where(
                                        runtime.UserTaskTag.task_id
                                        == item_data["task_id"]
                                    )
                                    .where(
                                        runtime.UserTaskTag.tag_id
                                        == item_data["tag_id"]
                                    )
                                ).first()

                                if existing_link:
                                    if old_id is not None:
                                        id_map["user_task_tags"][old_id] = (
                                            existing_link.id
                                        )
                                    continue

                            elif table_name == "user_task_recordings":
                                existing_link = session.exec(
                                    select(runtime.UserTaskRecording)
                                    .where(
                                        runtime.UserTaskRecording.task_id
                                        == item_data["task_id"]
                                    )
                                    .where(
                                        runtime.UserTaskRecording.recording_id
                                        == item_data["recording_id"]
                                    )
                                ).first()

                                if existing_link:
                                    if old_id is not None:
                                        id_map["user_task_recordings"][old_id] = (
                                            existing_link.id
                                        )
                                    continue

                            elif table_name == "recording_speakers":
                                # merged_into_id is self-referential: the target row may not
                                # exist yet, so it is nulled here and reattached by the
                                # deferred pass once the whole table has been inserted.
                                old_recording_speaker_merge_id = item_data.get(
                                    "merged_into_id"
                                )
                                if old_recording_speaker_merge_id is not None:
                                    item_data["merged_into_id"] = None

                            elif table_name == "recording_tags":
                                # DUPLICATE CHECK
                                existing_link = session.exec(
                                    select(runtime.RecordingTag)
                                    .where(
                                        runtime.RecordingTag.recording_id
                                        == item_data["recording_id"]
                                    )
                                    .where(
                                        runtime.RecordingTag.tag_id
                                        == item_data["tag_id"]
                                    )
                                ).first()

                                if existing_link:
                                    if old_id is not None:
                                        id_map["recording_tags"][old_id] = (
                                            existing_link.id
                                        )
                                    continue

                            elif table_name == "transcripts":
                                # DUPLICATE CHECK
                                existing_transcript = session.exec(
                                    select(runtime.Transcript).where(
                                        runtime.Transcript.recording_id
                                        == item_data["recording_id"]
                                    )
                                ).first()

                                if existing_transcript:
                                    if old_id is not None:
                                        id_map["transcripts"][old_id] = (
                                            existing_transcript.id
                                        )
                                    continue

                            elif table_name == "documents":
                                archived_file_path = item_data.get("file_path")

                                # Point the row at where the file will land once staging
                                # is applied.
                                item_data["file_path"] = (
                                    _build_runtime_document_path(
                                        archived_file_path, state.documents_dir
                                    )
                                    or archived_file_path
                                )

                                # file_path is unique. A stale row holding the same path
                                # would abort the insert, so pick a free destination
                                # rather than lose the document.
                                if item_data.get("file_path"):
                                    conflicting_doc = session.exec(
                                        select(model_cls).where(
                                            model_cls.file_path
                                            == item_data["file_path"]
                                        )
                                    ).first()
                                    if conflicting_doc is not None:
                                        original_path = item_data["file_path"]
                                        stem, ext = os.path.splitext(original_path)
                                        new_path = f"{stem}__{uuid4()}{ext}"
                                        logger.warning(
                                            f"file_path collision for restored document "
                                            f"({original_path}); storing it as {new_path}."
                                        )
                                        item_data["file_path"] = new_path

                                staged_document = state.stage_path(
                                    archived_file_path or ""
                                )
                                if archived_file_path and os.path.isfile(
                                    staged_document
                                ):
                                    destination = item_data["file_path"]
                                    if not state.claim_move(
                                        staged_document, destination
                                    ):
                                        stem, ext = os.path.splitext(destination)
                                        destination = f"{stem}__{uuid4()}{ext}"
                                        state.claim_move(staged_document, destination)
                                        item_data["file_path"] = destination

                            # Create instance inside a savepoint so an integrity error on a single
                            # row (e.g. unforeseen unique-constraint collision) is logged and the
                            # row is skipped, rather than rolling back the whole restore.
                            instance = model_cls.model_validate(item_data)
                            try:
                                with session.begin_nested():
                                    session.add(instance)
                                    session.flush()  # To get the new ID
                            except Exception as insert_err:  # noqa: BLE001
                                logger.error(
                                    f"Failed to insert restored {table_name} row "
                                    f"(old_id={old_id}): {insert_err}. Skipping."
                                )
                                state.record_skip(table_name, SKIP_REASON_INSERT_FAILED)
                                continue

                            if old_id is not None:
                                id_map[table_name][old_id] = instance.id

                            if (
                                table_name == "recording_speakers"
                                and old_recording_speaker_merge_id is not None
                            ):
                                pending_recording_speaker_merges.append(
                                    (instance.id, old_recording_speaker_merge_id)
                                )

                            # Track restored recording identities for duplicate detection.
                            if (
                                table_name == "recordings"
                                and hasattr(instance, "audio_path")
                                and instance.audio_path
                            ):
                                state.restored_recording_ids.add(instance.id)
                                for instance_key in _get_recording_match_keys(
                                    instance.audio_path,
                                    getattr(instance, "meeting_uid", None),
                                    getattr(instance, "public_id", None),
                                ):
                                    restored_recording_keys[instance_key] = instance.id
                                    existing_recordings_by_identity[instance_key] = (
                                        instance
                                    )

                                # Size and proxy flag come from the staged file, since
                                # nothing has moved into place yet. A recording restored
                                # without audio simply gets neither.
                                audio_source = pending_audio_source or (
                                    instance.audio_path
                                    if os.path.exists(instance.audio_path)
                                    else None
                                )
                                if audio_source:
                                    recordings_requiring_proxy.add(instance.id)
                                    # Report the size of what actually landed on disk. The
                                    # archive's own figure would be wrong whenever the audio
                                    # was re-encoded on the way in.
                                    try:
                                        instance.file_size_bytes = os.path.getsize(
                                            audio_source
                                        )
                                        session.add(instance)
                                    except OSError:
                                        logger.warning(
                                            "Could not size restored audio %s",
                                            audio_source,
                                        )

                            count += 1

                        logger.info(f"Restored {count} records for {table_name}")

                    for (
                        recording_speaker_id,
                        old_merge_target_id,
                    ) in pending_recording_speaker_merges:
                        new_merge_target_id = id_map["recording_speakers"].get(
                            old_merge_target_id
                        )
                        if new_merge_target_id is None:
                            continue

                        recording_speaker = session.get(
                            runtime.RecordingSpeaker, recording_speaker_id
                        )
                        if recording_speaker is None:
                            continue

                        recording_speaker.merged_into_id = new_merge_target_id
                        session.add(recording_speaker)

                    BackupManager._normalise_restored_recording_state(session, state)

                    session.commit()

                logger.info("Database restore complete.")
                state.report("Database restored")

                # Past the point of no return. Everything below changes files on disk
                # and is safe to do only now that the database has accepted the restore.
                if state.clear_existing:
                    if recordings_dir.exists():
                        shutil.rmtree(recordings_dir)
                    recordings_dir.mkdir(parents=True, exist_ok=True)

                BackupManager._restore_apply_pending_moves(state)
                BackupManager._restore_config(zipf, state)
        finally:
            # Whatever is left in staging belongs to rows that were not inserted, and a
            # failed restore must not leave a half-unpacked archive behind either.
            BackupManager._restore_cleanup_staging(state)

        BackupManager._restore_enqueue_finalization(state)

        skip_summary = state.skip_summary()
        job = BackupManager.restore_jobs[job_id]
        job["warnings"] = {"skipped": skip_summary}
        job["progress"] = "Done"

        if skip_summary:
            # The restore succeeded, but rows did not come across. Reporting this as a
            # distinct terminal status keeps the client from treating it as a clean run.
            total_skipped = sum(
                sum(reasons.values()) for reasons in skip_summary.values()
            )
            logger.warning(
                "Restore process finished with %s skipped rows: %s",
                total_skipped,
                skip_summary,
            )
            job["status"] = "completed_with_warnings"
        else:
            logger.info("Restore process finished successfully.")
            job["status"] = "completed"

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

        BackupManager.restore_jobs.setdefault(
            job_id,
            {
                "status": "pending",
                "progress": "Initializing...",
                "error": None,
                "warnings": None,
            },
        )

        BackupManager._restore_backup_sync(
            job_id,
            zip_path,
            clear_existing,
            overwrite_existing,
            path_manager.recordings_directory,
            path_manager.config_path,
            path_manager.user_data_directory,
            runtime.documents_directory(path_manager),
            progress_callback,
        )

        return dict(BackupManager.restore_jobs[job_id])

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
        if job_id not in BackupManager.restore_jobs:
            BackupManager.restore_jobs[job_id] = {
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
            BackupManager.restore_jobs[job_id]["status"] = "failed"
            BackupManager.restore_jobs[job_id]["error"] = str(e)
