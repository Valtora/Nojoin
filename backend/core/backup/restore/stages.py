"""The discrete phases of a restore.

Ordering is the point. Everything destructive waits until after the database
transaction commits: the archive is validated, its payload is unpacked into staging, the
clear and the insert share one transaction, and only then do files move or get deleted.
A failure anywhere before the commit leaves the installation exactly as it was.
"""

import json
import logging
import os
import shutil
import zipfile
from typing import Any, List, Tuple

from sqlmodel import delete, select

from backend.core.backup import runtime
from backend.core.backup.format import (
    BACKUP_FORMAT_VERSION,
    LEGACY_BACKUP_FORMAT_VERSION,
    RESTORE_DISK_HEADROOM_BYTES,
    RESTORED_RECORDING_INTERRUPTED_MESSAGE,
    RESTORED_RECORDING_INTERRUPTED_STATUS,
    RESTORED_RECORDING_TERMINAL_STATUS,
    TRANSIENT_RECORDING_STATUSES,
)
from backend.core.backup.paths import _get_recording_match_keys
from backend.core.backup.restore.state import _RestoreState

logger = logging.getLogger(__name__)


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
        if _parse_version(backup_version) > _parse_version(current_version):
            logger.warning(
                f"Restoring a backup from a NEWER application version "
                f"({backup_version}) onto an OLDER one ({current_version}). "
                "This may cause issues."
            )


def _restore_preflight_validate(zipf: zipfile.ZipFile, state: "_RestoreState") -> None:
    """Validate the whole archive before anything destructive happens.

    Everything that can be checked cheaply is checked here: the format version, that
    every JSON member parses, and that the unpacked payload will fit on disk. A
    clearing restore destroys the existing library, so a malformed or oversized
    archive must be refused before that point, not discovered halfway through it.
    """
    state.report("Validating backup...")

    _restore_check_version(zipf)

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


def _restore_config(zipf: zipfile.ZipFile, state: "_RestoreState") -> None:
    """Merge the archived config in, on a clearing restore only.

    config.json is a flat map that ConfigManager filters to known keys on load and
    which never holds secrets, so a plain update is enough -- with one exception:
    install_notes_template_id is a primary key from the source installation, and
    ids are reassigned on restore. Carried across unchanged it would point at
    whichever template happens to hold that id here, silently applying the wrong
    structure install-wide, so it is remapped or dropped.
    """
    if not state.clear_existing or "config.json" not in zipf.namelist():
        return

    try:
        new_config = json.loads(zipf.read("config.json"))
        if not isinstance(new_config, dict):
            return

        if "install_notes_template_id" in new_config:
            old_id = new_config.get("install_notes_template_id")
            new_config["install_notes_template_id"] = state.id_map.get(
                "notes_templates", {}
            ).get(old_id)

        config_path = state.config_path
        if config_path.exists():
            current_config = json.loads(config_path.read_text())
            current_config.update(new_config)
            config_path.write_text(json.dumps(current_config, indent=2))
        else:
            config_path.write_text(json.dumps(new_config, indent=2))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to restore config: {e}")


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
                delete(runtime.Recording).where(runtime.Recording.id.in_(existing_ids))
            )
            session.flush()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Pre-flight cleanup failed: {e}")


def _enqueue_recording_finalization(recording_id: int, needs_proxy: bool) -> None:
    from backend.worker.tasks import finalize_restored_recording_task

    finalize_restored_recording_task.delay(recording_id, needs_proxy=needs_proxy)


def _normalise_restored_recording_state(session: Any, state: "_RestoreState") -> None:
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


def _restore_enqueue_finalization(state: "_RestoreState") -> None:
    """Finalization stage: queue the per-recording rebuild of derived artefacts.

    One task per recording rather than one per concern. It runs on the io lane and
    dispatches proxy generation to the cpu lane, so the queue separation survives.
    """
    for recording_id in sorted(state.restored_recording_ids):
        try:
            _enqueue_recording_finalization(
                recording_id,
                needs_proxy=recording_id in state.recordings_requiring_proxy,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Failed to enqueue finalization for restored recording %s: %s",
                recording_id,
                e,
            )
