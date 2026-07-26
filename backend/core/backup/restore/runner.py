"""The restore loop.

Walks the archive table by table in dependency order, handing each row to the resolver
for its table and inserting whatever survives. The ordering guarantees are what make a
single forward pass enough: a table only ever references tables restored before it, so
``id_map`` already holds the remapped target ids by the time a row needs them.
"""

import json
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from backend.core.backup import runtime
from backend.core.backup.format import (
    RESTORE_FOREIGN_KEYS,
    RESTORE_STAGING_DIRNAME,
    SKIP_REASON_INSERT_FAILED,
    SKIP_REASON_UNRESOLVED_OWNER,
)
from backend.core.backup.paths import _get_recording_match_keys
from backend.core.backup.records import (
    _adapt_record,
    _prepare_calendar_connection_for_restore,
    _prepare_calendar_provider_config_for_restore,
    _topological_sort,
)
from backend.core.backup.restore import jobs, stages
from backend.core.backup.restore.conflicts import CONFLICT_RESOLVERS
from backend.core.backup.restore.context import RowOutcome, _RowContext
from backend.core.backup.restore.identity import IDENTITY_RESOLVERS
from backend.core.backup.restore.state import _RestoreState

logger = logging.getLogger(__name__)


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


def _prepare_row(
    table_name: str, model_cls, item_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Turn a raw archive row into something this schema can accept.

    Calendar rows carry their secrets decrypted, so they are re-encrypted here with the
    target installation's key. Every row is then filtered to the columns the model still
    has, which is what lets an archive from an older schema restore.
    """
    if table_name == "calendar_provider_configs":
        item_data = _prepare_calendar_provider_config_for_restore(item_data)
    elif table_name == "calendar_connections":
        item_data = _prepare_calendar_connection_for_restore(item_data)

    return _adapt_record(model_cls, item_data)


def _insert_row(ctx: _RowContext):
    """Insert one row inside a savepoint, returning the instance or None.

    The savepoint means an integrity error on a single row is counted and skipped rather
    than rolling back the entire restore.
    """
    instance = ctx.model_cls.model_validate(ctx.item_data)
    try:
        with ctx.session.begin_nested():
            ctx.session.add(instance)
            ctx.session.flush()  # To get the new ID
    except Exception as insert_err:  # noqa: BLE001
        logger.error(
            f"Failed to insert restored {ctx.table_name} row "
            f"(old_id={ctx.old_id}): {insert_err}. Skipping."
        )
        ctx.state.record_skip(ctx.table_name, SKIP_REASON_INSERT_FAILED)
        return None

    return instance


def _record_restored_recording(ctx: _RowContext, instance) -> None:
    """Register a freshly inserted recording's identities and staged audio.

    Size and proxy flag come from the staged file, since nothing has moved into place
    yet. A recording restored without audio simply gets neither.
    """
    state = ctx.state
    state.restored_recording_ids.add(instance.id)

    for instance_key in _get_recording_match_keys(
        instance.audio_path,
        getattr(instance, "meeting_uid", None),
        getattr(instance, "public_id", None),
    ):
        state.restored_recording_keys[instance_key] = instance.id
        state.existing_recordings_by_identity[instance_key] = instance

    audio_source = ctx.pending_audio_source or (
        instance.audio_path if os.path.exists(instance.audio_path) else None
    )
    if not audio_source:
        return

    state.recordings_requiring_proxy.add(instance.id)
    # Report the size of what actually landed on disk. The archive's own figure would be
    # wrong whenever the audio was re-encoded on the way in.
    try:
        instance.file_size_bytes = os.path.getsize(audio_source)
        ctx.session.add(instance)
    except OSError:
        logger.warning("Could not size restored audio %s", audio_source)


def _restore_row(ctx: _RowContext) -> bool:
    """Bring one archive row across. Returns whether a row was actually inserted."""
    state = ctx.state

    # Under the safe-merge strategy a conflicting recording is kept as it already exists
    # in the target, and its children are not merged in. This has to be checked against
    # the backup's own recording id, so it runs before the remap below rewrites it.
    if (
        ctx.item_data.get("recording_id") in state.skipped_recording_ids
        and ctx.table_name != "recordings"
    ):
        return False

    # Rewrite foreign keys up front, before any conflict check, so every resolver
    # compares against target-database ids rather than the source system's. A row whose
    # owner cannot be resolved is dropped here and never reaches identity matching, so
    # it cannot adopt an existing row's id and pull its children along.
    skip_reason = _apply_foreign_keys(ctx.table_name, ctx.item_data, state.id_map)
    if skip_reason is not None:
        state.record_skip(ctx.table_name, skip_reason)
        return False

    resolver = IDENTITY_RESOLVERS.get(ctx.table_name)
    if resolver is not None and resolver(ctx) is RowOutcome.HANDLED:
        return False

    # Remove the id so the database generates a fresh one.
    ctx.item_data.pop("id", None)

    resolver = CONFLICT_RESOLVERS.get(ctx.table_name)
    if resolver is not None and resolver(ctx) is RowOutcome.HANDLED:
        return False

    instance = _insert_row(ctx)
    if instance is None:
        return False

    if ctx.old_id is not None:
        state.id_map[ctx.table_name][ctx.old_id] = instance.id

    if (
        ctx.table_name == "recording_speakers"
        and ctx.old_recording_speaker_merge_id is not None
    ):
        state.pending_recording_speaker_merges.append(
            (instance.id, ctx.old_recording_speaker_merge_id)
        )

    if ctx.table_name == "recordings" and getattr(instance, "audio_path", None):
        _record_restored_recording(ctx, instance)

    return True


def _restore_table(
    zipf, session, state: _RestoreState, table_name: str, model_cls
) -> int:
    """Restore every row of one table. Returns how many were inserted."""
    member = f"{table_name}.json"
    if member not in zipf.namelist():
        return 0

    try:
        data = json.loads(zipf.read(member))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to read/parse {member}: {e}")
        return 0

    # Tag hierarchies must list a parent before any child that references it.
    if table_name in ("tags", "p_tags"):
        data = _topological_sort(data)

    count = 0
    for raw_row in data:
        item_data = _prepare_row(table_name, model_cls, raw_row)
        ctx = _RowContext(
            session=session,
            state=state,
            table_name=table_name,
            model_cls=model_cls,
            item_data=item_data,
            old_id=item_data.get("id"),
        )
        if _restore_row(ctx):
            count += 1

    logger.info(f"Restored {count} records for {table_name}")
    return count


@dataclass
class _RestoreRequest:
    """Everything one restore needs, resolved before the archive is opened."""

    job_id: str
    zip_path: str
    recordings_dir: Path
    config_path: Path
    user_data_dir: Path
    documents_dir: Path
    clear_existing: bool = False
    overwrite_existing: bool = False
    progress_callback: Any = None


def _apply_deferred_speaker_merges(session, state: _RestoreState) -> None:
    """Reattach speaker merge targets once every speaker row exists.

    merged_into_id is self-referential, so the target may not have been inserted when
    the source row was. It is nulled on insert and reconnected here.
    """
    for recording_speaker_id, old_target_id in state.pending_recording_speaker_merges:
        new_target_id = state.id_map["recording_speakers"].get(old_target_id)
        if new_target_id is None:
            continue

        recording_speaker = session.get(runtime.RecordingSpeaker, recording_speaker_id)
        if recording_speaker is None:
            continue

        recording_speaker.merged_into_id = new_target_id
        session.add(recording_speaker)


def _remap_user_notes_template_settings(session, state: _RestoreState) -> None:
    """Repoint each restored user's default notes template at the restored row.

    ``settings.notes_template_id`` holds a primary key from the source
    installation, and restore reassigns ids. Left alone it would either dangle or,
    worse, match an unrelated template here and quietly change how that user's
    notes are written. Unmappable values are removed, which falls back to the
    install default and then to the built-in structure.
    """
    template_map = state.id_map.get("notes_templates", {})
    for old_user_id, new_user_id in state.id_map.get("users", {}).items():
        del old_user_id
        user = session.get(runtime.User, new_user_id)
        if user is None or not isinstance(user.settings, dict):
            continue
        if "notes_template_id" not in user.settings:
            continue

        settings = dict(user.settings)
        remapped = template_map.get(settings.get("notes_template_id"))
        if remapped is None:
            settings.pop("notes_template_id", None)
        else:
            settings["notes_template_id"] = remapped
        user.settings = settings
        session.add(user)


def _finish_restore_job(state: _RestoreState) -> None:
    """Publish the terminal status and skip report for a completed restore."""
    skip_summary = state.skip_summary()
    job = jobs.restore_jobs[state.job_id]
    job["warnings"] = {"skipped": skip_summary}
    job["progress"] = "Done"

    if not skip_summary:
        logger.info("Restore process finished successfully.")
        job["status"] = "completed"
        return

    # The restore succeeded, but rows did not come across. Reporting this as a distinct
    # terminal status keeps the client from treating it as a clean run.
    total_skipped = sum(sum(reasons.values()) for reasons in skip_summary.values())
    logger.warning(
        "Restore process finished with %s skipped rows: %s",
        total_skipped,
        skip_summary,
    )
    job["status"] = "completed_with_warnings"


def _restore_backup_sync(request: _RestoreRequest):
    """
    Synchronous implementation of backup restoration.

    The shape matters as much as the steps. Everything destructive is deferred until
    after a single database transaction commits: the archive is validated first, its
    payload is unpacked into staging, the clear and the insert share one transaction,
    and only once that commits do any files move or get deleted. A failure at any
    point before the commit leaves the installation exactly as it was.
    """
    job = jobs.restore_jobs.setdefault(request.job_id, {})
    job["status"] = "processing"
    job.setdefault("error", None)
    job.setdefault("warnings", None)
    logger.info(f"Starting synchronous restore process for {request.zip_path}")

    recordings_dir = request.recordings_dir
    state = _RestoreState(
        job_id=request.job_id,
        clear_existing=request.clear_existing,
        overwrite_existing=request.overwrite_existing,
        recordings_dir=recordings_dir,
        config_path=request.config_path,
        user_data_dir=request.user_data_dir,
        documents_dir=request.documents_dir,
        id_map={name: {} for name, _ in runtime.MODELS},
        staging_dir=os.path.join(
            os.fspath(request.user_data_dir), RESTORE_STAGING_DIRNAME, request.job_id
        ),
        progress_callback=request.progress_callback,
    )
    state.report("Starting...")

    try:
        with zipfile.ZipFile(request.zip_path, "r") as zipf:
            stages._restore_preflight_validate(zipf, state)
            stages._restore_extract_files(zipf, state)

            # Restore Database
            logger.info("Restoring database records...")
            state.report("Restoring database...")
            from sqlmodel import Session

            with Session(runtime.sync_engine) as session:
                stages._restore_clear_existing_data(session, state)
                stages._restore_preflight_overwrite(zipf, session, state)
                for table_name, model_cls in runtime.MODELS:
                    _restore_table(zipf, session, state, table_name, model_cls)

                _apply_deferred_speaker_merges(session, state)
                _remap_user_notes_template_settings(session, state)

                stages._normalise_restored_recording_state(session, state)

                session.commit()

            logger.info("Database restore complete.")
            state.report("Database restored")

            # Past the point of no return. Everything below changes files on disk
            # and is safe to do only now that the database has accepted the restore.
            if state.clear_existing:
                if recordings_dir.exists():
                    shutil.rmtree(recordings_dir)
                recordings_dir.mkdir(parents=True, exist_ok=True)

            stages._restore_apply_pending_moves(state)
            stages._restore_config(zipf, state)
    finally:
        # Whatever is left in staging belongs to rows that were not inserted, and a
        # failed restore must not leave a half-unpacked archive behind either.
        stages._restore_cleanup_staging(state)

    stages._restore_enqueue_finalization(state)
    _finish_restore_job(state)
