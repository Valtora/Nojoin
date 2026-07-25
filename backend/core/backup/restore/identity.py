"""Reconciling an incoming row against what the target installation already holds.

These run before the insert decision. Each returns HANDLED when it has dealt with the
row itself, which for a conflict usually means mapping the backup's id onto the existing
record so the row's children follow the record that was kept.
"""

import logging
import os
from uuid import uuid4

from sqlmodel import select

from backend.core.backup import runtime
from backend.core.backup.format import SKIP_REASON_NO_IDENTITY
from backend.core.backup.paths import (
    _build_runtime_recording_audio_path,
    _get_recording_match_keys,
    _normalise_meeting_uid,
    _normalise_public_id,
)
from backend.core.backup.records import _restore_redacted_sensitive_data
from backend.core.backup.restore.context import RowOutcome, _RowContext

logger = logging.getLogger(__name__)


def _resolve_users_identity(ctx: "_RowContext") -> "RowOutcome":
    """Reconcile a users row against what the target already holds."""
    if ctx.item_data.get("settings"):
        ctx.item_data["settings"] = _restore_redacted_sensitive_data(
            ctx.item_data["settings"]
        )

    username = ctx.item_data.get("username")
    existing_user = ctx.session.exec(
        select(runtime.User).where(runtime.User.username == username)
    ).first()

    if existing_user:
        # User exists. Map old_id to existing_id.
        # Do NOT overwrite the user (security risk, plus passwords etc).
        if ctx.old_id is not None:
            ctx.state.id_map["users"][ctx.old_id] = existing_user.id
        return RowOutcome.HANDLED  # Skip inserting this user
    return RowOutcome.INSERT


def _index_existing_recordings(ctx: "_RowContext") -> None:
    """Index the target's recordings by every identity they own, once per restore.

    Loaded lazily on the first recording row rather than up front, so a metadata-only
    restore of another table never pays for it.
    """
    if ctx.state.existing_recordings_loaded:
        return

    for row in ctx.session.exec(select(runtime.Recording)).all():
        for key in _get_recording_match_keys(
            row.audio_path,
            getattr(row, "meeting_uid", None),
            getattr(row, "public_id", None),
        ):
            ctx.state.existing_recordings_by_identity[key] = row

    ctx.state.existing_recordings_loaded = True


def _normalise_recording_identifiers(
    ctx: "_RowContext", meeting_uid, public_id
) -> None:
    """Store the durable identifiers in canonical form, or drop them if unusable."""
    normalized_meeting_uid = _normalise_meeting_uid(meeting_uid)
    if normalized_meeting_uid:
        ctx.item_data["meeting_uid"] = normalized_meeting_uid
    else:
        ctx.item_data.pop("meeting_uid", None)

    normalized_public_id = _normalise_public_id(public_id)
    if normalized_public_id:
        ctx.item_data["public_id"] = normalized_public_id
    else:
        ctx.item_data.pop("public_id", None)


def _resolve_recordings_identity(ctx: "_RowContext") -> "RowOutcome":
    """Reconcile a recordings row against what the target already holds."""
    audio_path = ctx.item_data.get("audio_path")
    meeting_uid = ctx.item_data.get("meeting_uid")
    public_id = ctx.item_data.get("public_id")
    backup_keys = _get_recording_match_keys(
        audio_path,
        meeting_uid,
        public_id,
    )

    _index_existing_recordings(ctx)

    if not backup_keys:
        logger.warning(
            "Skipping recording restore because no usable identity "
            "(meeting_uid, public_id, or audio_path) was found."
        )
        ctx.state.record_skip(ctx.table_name, SKIP_REASON_NO_IDENTITY)
        return RowOutcome.HANDLED

    # DUPLICATE IN BACKUP CHECK:
    # If we already restored a recording in this session that shares ANY
    # identifier with this row, link old_id to that new_id and skip.
    duplicate_match_id = next(
        (
            ctx.state.restored_recording_keys[key]
            for key in backup_keys
            if key in ctx.state.restored_recording_keys
        ),
        None,
    )
    if duplicate_match_id is not None:
        logger.warning(
            f"Duplicate recording in backup JSON (audio_path={audio_path}, "
            f"meeting_uid={meeting_uid}, public_id={public_id}). "
            f"Linking old_id {ctx.old_id} to existing new_id {duplicate_match_id}"
        )
        if ctx.old_id is not None:
            ctx.state.id_map["recordings"][ctx.old_id] = duplicate_match_id
        return RowOutcome.HANDLED

    existing_rec = next(
        (
            ctx.state.existing_recordings_by_identity[key]
            for key in backup_keys
            if key in ctx.state.existing_recordings_by_identity
        ),
        None,
    )

    if existing_rec:
        # Identity conflict with a recording already present.
        if ctx.state.overwrite_existing:
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
                for k, v in ctx.state.existing_recordings_by_identity.items()
                if v is existing_rec
            ]:
                ctx.state.existing_recordings_by_identity.pop(stale_key, None)
            ctx.session.delete(existing_rec)
            ctx.session.flush()
        else:
            # Skip strategy (safe merge): map the backup row's
            # id onto the existing row and reuse it.
            if ctx.old_id is not None:
                ctx.state.id_map["recordings"][ctx.old_id] = existing_rec.id
                ctx.state.skipped_recording_ids.add(ctx.old_id)
                # Tracks as restored/processed so subsequent duplicates map to it,
                # registering every identity the existing row owns.
                for existing_key in _get_recording_match_keys(
                    existing_rec.audio_path,
                    getattr(existing_rec, "meeting_uid", None),
                    getattr(existing_rec, "public_id", None),
                ):
                    ctx.state.restored_recording_keys[existing_key] = existing_rec.id
                for backup_key in backup_keys:
                    ctx.state.restored_recording_keys[backup_key] = existing_rec.id
            return RowOutcome.HANDLED

    _normalise_recording_identifiers(ctx, meeting_uid, public_id)

    _clear_source_recording_state(ctx)
    _assign_recording_destination(ctx, audio_path)
    return RowOutcome.INSERT


def _clear_source_recording_state(ctx: "_RowContext") -> None:
    """Drop the state that belonged to the installation the backup came from.

    The celery_task_id in particular names a task on another broker, which the cancel
    path would otherwise try to revoke.
    """
    # Backups do not preserve proxy files; regenerate them after restore.
    ctx.item_data["proxy_path"] = None

    for transient_field, blank in (
        ("celery_task_id", None),
        ("client_status", None),
        ("processing_step", None),
        ("processing_progress", 0),
        ("upload_progress", 0),
    ):
        if transient_field in ctx.item_data:
            ctx.item_data[transient_field] = blank

    # Canonical utterances are rebuilt from the transcript projection rather than
    # archived, so restored recordings enter un-classified and are backfilled exactly
    # like legacy rows. The status is settled in the finalisation pass, once the
    # transcript rows exist.
    #
    # Set unconditionally, not only when the archive carried the column: a legacy
    # archive omits it entirely, and leaving it absent lets the model default reapply,
    # marking the recording canonical when it has no canonical rows at all.
    if hasattr(ctx.model_cls, "pipeline_generation"):
        ctx.item_data["pipeline_generation"] = None


def _claim_unique_public_id(ctx: "_RowContext") -> None:
    """Regenerate public_id if a stale row already holds it.

    Aborting the whole restore on a unique-constraint violation would be a poor trade
    for an identifier the target can mint afresh.
    """
    if not hasattr(ctx.model_cls, "public_id") or not ctx.item_data.get("public_id"):
        return

    conflicting = ctx.session.exec(
        select(ctx.model_cls).where(
            ctx.model_cls.public_id == ctx.item_data["public_id"]
        )
    ).first()
    if conflicting is None:
        return

    new_pid = str(uuid4())
    logger.warning(
        f"public_id collision for restored recording "
        f"(value={ctx.item_data['public_id']!r}); regenerating to {new_pid}."
    )
    ctx.item_data["public_id"] = new_pid


def _assign_recording_destination(ctx: "_RowContext", audio_path) -> None:
    """Decide where this recording's audio will live, and queue the staged file.

    audio_path is unique, so a colliding row picks a free destination instead. With
    staging there is no file to rename: only the destination of the pending move
    changes, and it is applied after the transaction commits. Under the Skip conflict
    mode this is never reached for a recording that already exists, which is why the
    existing copy's audio is left alone.
    """
    ctx.item_data["audio_path"] = (
        _build_runtime_recording_audio_path(audio_path, ctx.state.recordings_dir)
        or audio_path
    )

    _claim_unique_public_id(ctx)

    if ctx.item_data.get("audio_path"):
        conflicting_path_row = ctx.session.exec(
            select(ctx.model_cls).where(
                ctx.model_cls.audio_path == ctx.item_data["audio_path"]
            )
        ).first()
        if conflicting_path_row is not None:
            original_path = ctx.item_data["audio_path"]
            stem, ext = os.path.splitext(original_path)
            suffix = (
                ctx.item_data.get("meeting_uid")
                or ctx.item_data.get("public_id")
                or str(uuid4())
            )
            new_path = f"{stem}__{suffix}{ext}"
            logger.warning(
                f"audio_path collision for restored recording "
                f"({original_path}); storing it as {new_path}."
            )
            ctx.item_data["audio_path"] = new_path

    staged_audio = ctx.state.stage_path(audio_path or "")
    if audio_path and os.path.isfile(staged_audio):
        destination = ctx.item_data["audio_path"]
        if not ctx.state.claim_move(staged_audio, destination):
            stem, ext = os.path.splitext(destination)
            destination = f"{stem}__{uuid4()}{ext}"
            ctx.state.claim_move(staged_audio, destination)
            ctx.item_data["audio_path"] = destination
        ctx.pending_audio_source = staged_audio
    else:
        ctx.pending_audio_source = None


def _resolve_tags_identity(ctx: "_RowContext") -> "RowOutcome":
    """Reconcile a tags row against what the target already holds."""
    existing_tag = ctx.session.exec(
        select(runtime.Tag)
        .where(runtime.Tag.name == ctx.item_data.get("name"))
        .where(runtime.Tag.user_id == ctx.item_data.get("user_id"))
    ).first()

    if existing_tag:
        if ctx.old_id is not None:
            ctx.state.id_map["tags"][ctx.old_id] = existing_tag.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_p_tags_identity(ctx: "_RowContext") -> "RowOutcome":
    """Reconcile a p_tags row against what the target already holds."""
    existing_p_tag = ctx.session.exec(
        select(runtime.PeopleTag)
        .where(runtime.PeopleTag.name == ctx.item_data.get("name"))
        .where(runtime.PeopleTag.user_id == ctx.item_data.get("user_id"))
    ).first()

    if existing_p_tag:
        if ctx.old_id is not None:
            ctx.state.id_map["p_tags"][ctx.old_id] = existing_p_tag.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


#: Tables whose incoming rows must be reconciled with existing records first.
IDENTITY_RESOLVERS = {
    "users": _resolve_users_identity,
    "recordings": _resolve_recordings_identity,
    "tags": _resolve_tags_identity,
    "p_tags": _resolve_p_tags_identity,
}
