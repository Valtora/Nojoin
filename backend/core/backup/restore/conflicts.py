"""Per-table conflict resolution, applied just before a row is inserted.

Foreign keys are already remapped by the time these run, so every comparison here is
against target-database ids. The merge semantics differ per table on purpose: calendar
rows merge field by field so a restore does not clobber fresher sync state, link tables
deduplicate, and transcripts defer to whatever the target already has.
"""

import logging
import os
from uuid import uuid4

from sqlmodel import select

from backend.core.backup import runtime
from backend.core.backup.paths import _build_runtime_document_path
from backend.core.backup.restore.context import RowOutcome, _RowContext

logger = logging.getLogger(__name__)


def _resolve_calendar_provider_configs(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a calendar_provider_configs row before insert."""
    candidate = ctx.model_cls.model_validate(ctx.item_data)
    existing_config = ctx.session.exec(
        select(runtime.CalendarProviderConfig).where(
            runtime.CalendarProviderConfig.provider == candidate.provider
        )
    ).first()

    if existing_config:
        if ctx.state.overwrite_existing:
            existing_config.client_id = candidate.client_id
            existing_config.client_secret_encrypted = candidate.client_secret_encrypted
            existing_config.tenant_id = candidate.tenant_id
            existing_config.enabled = candidate.enabled
            ctx.session.add(existing_config)
        else:
            updated = False
            if not existing_config.client_id and candidate.client_id:
                existing_config.client_id = candidate.client_id
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
                candidate.provider == runtime.CalendarProvider.MICROSOFT.value
                and not existing_config.tenant_id
                and candidate.tenant_id
            ):
                existing_config.tenant_id = candidate.tenant_id
                updated = True
            if updated:
                ctx.session.add(existing_config)

        if ctx.old_id is not None:
            ctx.state.id_map["calendar_provider_configs"][ctx.old_id] = (
                existing_config.id
            )
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_calendar_connections(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a calendar_connections row before insert."""
    candidate = ctx.model_cls.model_validate(ctx.item_data)
    existing_connection = ctx.session.exec(
        select(runtime.CalendarConnection)
        .where(runtime.CalendarConnection.user_id == candidate.user_id)
        .where(runtime.CalendarConnection.provider == candidate.provider)
        .where(
            runtime.CalendarConnection.provider_account_id
            == candidate.provider_account_id
        )
    ).first()

    if existing_connection:
        if ctx.state.overwrite_existing:
            existing_connection.email = candidate.email
            existing_connection.display_name = candidate.display_name
            existing_connection.access_token_encrypted = (
                candidate.access_token_encrypted
            )
            existing_connection.refresh_token_encrypted = (
                candidate.refresh_token_encrypted
            )
            existing_connection.granted_scopes = candidate.granted_scopes
            existing_connection.token_expires_at = candidate.token_expires_at
            existing_connection.sync_status = candidate.sync_status
            existing_connection.sync_error = candidate.sync_error
            existing_connection.last_sync_started_at = candidate.last_sync_started_at
            existing_connection.last_sync_completed_at = (
                candidate.last_sync_completed_at
            )
            existing_connection.last_synced_at = candidate.last_synced_at
            ctx.session.add(existing_connection)
        else:
            updated = False
            for field in (
                "email",
                "display_name",
                "access_token_encrypted",
                "refresh_token_encrypted",
                "token_expires_at",
            ):
                if not getattr(existing_connection, field) and getattr(
                    candidate, field
                ):
                    setattr(
                        existing_connection,
                        field,
                        getattr(candidate, field),
                    )
                    updated = True

            if not existing_connection.granted_scopes and candidate.granted_scopes:
                existing_connection.granted_scopes = candidate.granted_scopes
                updated = True

            existing_sync_marker = (
                existing_connection.last_synced_at
                or existing_connection.last_sync_completed_at
            )
            candidate_sync_marker = (
                candidate.last_synced_at or candidate.last_sync_completed_at
            )
            if candidate_sync_marker and (
                existing_sync_marker is None
                or candidate_sync_marker >= existing_sync_marker
            ):
                existing_connection.sync_status = candidate.sync_status
                existing_connection.sync_error = candidate.sync_error
                existing_connection.last_sync_started_at = (
                    candidate.last_sync_started_at
                )
                existing_connection.last_sync_completed_at = (
                    candidate.last_sync_completed_at
                )
                existing_connection.last_synced_at = candidate.last_synced_at
                updated = True

            if updated:
                ctx.session.add(existing_connection)

        if ctx.old_id is not None:
            ctx.state.id_map["calendar_connections"][ctx.old_id] = (
                existing_connection.id
            )
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _merge_calendar_source_fields(existing_source, candidate) -> bool:
    """Fill a calendar source's gaps from the archive without clobbering fresher state.

    Sync cursors and windows only move when the archive's sync is at least as recent as
    the target's, so restoring an older backup cannot rewind a calendar that has synced
    since. Returns whether anything changed.
    """
    updated = False

    for field in ("name", "description", "time_zone"):
        candidate_value = getattr(candidate, field)
        if candidate_value and getattr(existing_source, field) != candidate_value:
            setattr(existing_source, field, candidate_value)
            updated = True

    if candidate.colour and existing_source.colour != candidate.colour:
        existing_source.colour = candidate.colour
        updated = True
    if not existing_source.user_colour and candidate.user_colour:
        existing_source.user_colour = candidate.user_colour
        updated = True

    for flag in ("is_primary", "is_read_only", "is_selected"):
        if getattr(candidate, flag) and not getattr(existing_source, flag):
            setattr(existing_source, flag, True)
            updated = True

    if candidate.last_synced_at and (
        existing_source.last_synced_at is None
        or candidate.last_synced_at >= existing_source.last_synced_at
    ):
        existing_source.sync_cursor = candidate.sync_cursor
        existing_source.last_synced_at = candidate.last_synced_at
        existing_source.sync_window_start = candidate.sync_window_start
        existing_source.sync_window_end = candidate.sync_window_end
        updated = True

    return updated


def _resolve_calendar_sources(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a calendar_sources row before insert."""
    candidate = ctx.model_cls.model_validate(ctx.item_data)
    existing_source = ctx.session.exec(
        select(runtime.CalendarSource)
        .where(runtime.CalendarSource.connection_id == candidate.connection_id)
        .where(
            runtime.CalendarSource.provider_calendar_id
            == candidate.provider_calendar_id
        )
    ).first()

    if existing_source:
        if ctx.state.overwrite_existing:
            existing_source.name = candidate.name
            existing_source.description = candidate.description
            existing_source.time_zone = candidate.time_zone
            existing_source.colour = candidate.colour
            existing_source.user_colour = candidate.user_colour
            existing_source.is_primary = candidate.is_primary
            existing_source.is_read_only = candidate.is_read_only
            existing_source.is_selected = candidate.is_selected
            existing_source.sync_cursor = candidate.sync_cursor
            existing_source.last_synced_at = candidate.last_synced_at
            existing_source.sync_window_start = candidate.sync_window_start
            existing_source.sync_window_end = candidate.sync_window_end
            ctx.session.add(existing_source)
        else:
            if _merge_calendar_source_fields(existing_source, candidate):
                ctx.session.add(existing_source)

        if ctx.old_id is not None:
            ctx.state.id_map["calendar_sources"][ctx.old_id] = existing_source.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_calendar_events(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a calendar_events row before insert."""
    candidate = ctx.model_cls.model_validate(ctx.item_data)
    existing_event = ctx.session.exec(
        select(runtime.CalendarEvent)
        .where(runtime.CalendarEvent.calendar_id == candidate.calendar_id)
        .where(runtime.CalendarEvent.provider_event_id == candidate.provider_event_id)
    ).first()

    if existing_event:
        should_replace_event = ctx.state.overwrite_existing or (
            candidate.external_updated_at is not None
            and (
                existing_event.external_updated_at is None
                or candidate.external_updated_at >= existing_event.external_updated_at
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
            existing_event.location_text = candidate.location_text
            existing_event.meeting_url = candidate.meeting_url
            existing_event.source_url = candidate.source_url
            existing_event.external_updated_at = candidate.external_updated_at
            ctx.session.add(existing_event)
        else:
            updated = False
            for field in (
                "location_text",
                "meeting_url",
                "source_url",
            ):
                if not getattr(existing_event, field) and getattr(candidate, field):
                    setattr(
                        existing_event,
                        field,
                        getattr(candidate, field),
                    )
                    updated = True
            if updated:
                ctx.session.add(existing_event)

        if ctx.old_id is not None:
            ctx.state.id_map["calendar_events"][ctx.old_id] = existing_event.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_global_speakers(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a global_speakers row before insert."""
    existing_speaker = ctx.session.exec(
        select(runtime.GlobalSpeaker)
        .where(runtime.GlobalSpeaker.name == ctx.item_data.get("name"))
        .where(runtime.GlobalSpeaker.user_id == ctx.item_data.get("user_id"))
    ).first()

    if existing_speaker:
        if ctx.state.overwrite_existing:
            # Updates existing speaker details from backup.
            existing_speaker.title = ctx.item_data.get("title")
            existing_speaker.company = ctx.item_data.get("company")
            existing_speaker.email = ctx.item_data.get("email")
            existing_speaker.phone_number = ctx.item_data.get("phone_number")
            existing_speaker.notes = ctx.item_data.get("notes")
            existing_speaker.color = ctx.item_data.get("color")
            if ctx.item_data.get("embedding"):
                existing_speaker.embedding = ctx.item_data.get("embedding")

            ctx.session.add(existing_speaker)
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
                if not getattr(existing_speaker, field) and ctx.item_data.get(field):
                    setattr(
                        existing_speaker,
                        field,
                        ctx.item_data.get(field),
                    )
                    updated = True

            # Voice Embedding: Restore only if missing locally
            if (
                not existing_speaker.embedding or len(existing_speaker.embedding) == 0
            ) and ctx.item_data.get("embedding"):
                existing_speaker.embedding = ctx.item_data.get("embedding")
                updated = True

            if updated:
                ctx.session.add(existing_speaker)

        if ctx.old_id is not None:
            ctx.state.id_map["global_speakers"][ctx.old_id] = existing_speaker.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_people_tag_links(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a people_tag_links row before insert."""
    existing_link = ctx.session.exec(
        select(runtime.PeopleTagLink)
        .where(
            runtime.PeopleTagLink.global_speaker_id
            == ctx.item_data["global_speaker_id"]
        )
        .where(runtime.PeopleTagLink.tag_id == ctx.item_data["tag_id"])
    ).first()

    if existing_link:
        if ctx.old_id is not None:
            ctx.state.id_map["people_tag_links"][ctx.old_id] = existing_link.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_user_task_tags(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a user_task_tags row before insert."""
    existing_link = ctx.session.exec(
        select(runtime.UserTaskTag)
        .where(runtime.UserTaskTag.task_id == ctx.item_data["task_id"])
        .where(runtime.UserTaskTag.tag_id == ctx.item_data["tag_id"])
    ).first()

    if existing_link:
        if ctx.old_id is not None:
            ctx.state.id_map["user_task_tags"][ctx.old_id] = existing_link.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_user_task_recordings(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a user_task_recordings row before insert."""
    existing_link = ctx.session.exec(
        select(runtime.UserTaskRecording)
        .where(runtime.UserTaskRecording.task_id == ctx.item_data["task_id"])
        .where(runtime.UserTaskRecording.recording_id == ctx.item_data["recording_id"])
    ).first()

    if existing_link:
        if ctx.old_id is not None:
            ctx.state.id_map["user_task_recordings"][ctx.old_id] = existing_link.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_recording_speakers(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a recording_speakers row before insert."""
    ctx.old_recording_speaker_merge_id = ctx.item_data.get("merged_into_id")
    if ctx.old_recording_speaker_merge_id is not None:
        ctx.item_data["merged_into_id"] = None
    return RowOutcome.INSERT


def _resolve_recording_tags(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a recording_tags row before insert."""
    existing_link = ctx.session.exec(
        select(runtime.RecordingTag)
        .where(runtime.RecordingTag.recording_id == ctx.item_data["recording_id"])
        .where(runtime.RecordingTag.tag_id == ctx.item_data["tag_id"])
    ).first()

    if existing_link:
        if ctx.old_id is not None:
            ctx.state.id_map["recording_tags"][ctx.old_id] = existing_link.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_transcripts(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a transcripts row before insert."""
    existing_transcript = ctx.session.exec(
        select(runtime.Transcript).where(
            runtime.Transcript.recording_id == ctx.item_data["recording_id"]
        )
    ).first()

    if existing_transcript:
        if ctx.old_id is not None:
            ctx.state.id_map["transcripts"][ctx.old_id] = existing_transcript.id
        return RowOutcome.HANDLED
    return RowOutcome.INSERT


def _resolve_documents(ctx: "_RowContext") -> "RowOutcome":
    """Resolve conflicts for a documents row before insert."""
    archived_file_path = ctx.item_data.get("file_path")

    # Point the row at where the file will land once staging
    # is applied.
    ctx.item_data["file_path"] = (
        _build_runtime_document_path(archived_file_path, ctx.state.documents_dir)
        or archived_file_path
    )

    # file_path is unique. A stale row holding the same path
    # would abort the insert, so pick a free destination
    # rather than lose the document.
    if ctx.item_data.get("file_path"):
        conflicting_doc = ctx.session.exec(
            select(ctx.model_cls).where(
                ctx.model_cls.file_path == ctx.item_data["file_path"]
            )
        ).first()
        if conflicting_doc is not None:
            original_path = ctx.item_data["file_path"]
            stem, ext = os.path.splitext(original_path)
            new_path = f"{stem}__{uuid4()}{ext}"
            logger.warning(
                f"file_path collision for restored document "
                f"({original_path}); storing it as {new_path}."
            )
            ctx.item_data["file_path"] = new_path

    staged_document = ctx.state.stage_path(archived_file_path or "")
    if archived_file_path and os.path.isfile(staged_document):
        destination = ctx.item_data["file_path"]
        if not ctx.state.claim_move(staged_document, destination):
            stem, ext = os.path.splitext(destination)
            destination = f"{stem}__{uuid4()}{ext}"
            ctx.state.claim_move(staged_document, destination)
            ctx.item_data["file_path"] = destination
    return RowOutcome.INSERT


#: Tables needing conflict resolution immediately before insert.
CONFLICT_RESOLVERS = {
    "calendar_provider_configs": _resolve_calendar_provider_configs,
    "calendar_connections": _resolve_calendar_connections,
    "calendar_sources": _resolve_calendar_sources,
    "calendar_events": _resolve_calendar_events,
    "global_speakers": _resolve_global_speakers,
    "people_tag_links": _resolve_people_tag_links,
    "user_task_tags": _resolve_user_task_tags,
    "user_task_recordings": _resolve_user_task_recordings,
    "recording_speakers": _resolve_recording_speakers,
    "recording_tags": _resolve_recording_tags,
    "transcripts": _resolve_transcripts,
    "documents": _resolve_documents,
}
