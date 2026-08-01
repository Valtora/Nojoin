"""MCP tools that manage the user's library: recoverable mutations plus
the one destructive tool.

Every tool delegates to the endpoint coroutine the web client uses, so
validation, ownership checks, and side effects stay identical across
surfaces. Recoverable verbs (rename, tag, archive, bin, restore,
reprocess, notes regeneration, document attachment, transcript
correction, calendar linking) require mcp:write; permanent deletion alone
requires mcp:destroy.
"""

import logging
from typing import Any, Optional

from mcp.server.fastmcp.exceptions import ToolError
from sqlmodel import select

from backend.mcp_server.auth import get_current_mcp_user
from backend.mcp_server.server import (
    _parse_iso_datetime,
    _require_destroy_scope,
    _require_write_scope,
    mcp_tool,
)

logger = logging.getLogger(__name__)

_ATTACH_CONTENT_CHAR_LIMIT = 200_000
_CALENDAR_EVENTS_LIMIT_MAX = 100


def _lifecycle_payload(recording: Any) -> dict[str, Any]:
    return {
        "id": recording.id,
        "name": recording.name,
        "is_archived": recording.is_archived,
        "is_deleted": recording.is_deleted,
    }


@mcp_tool()
async def rename_recording(recording_id: str, name: str) -> dict[str, Any]:
    """Rename a recording. Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        name: The new recording name.
    """
    from backend.api.v1.endpoints.recordings.routes_actions import update_recording
    from backend.core.db import async_session_maker
    from backend.models.recording import RecordingUpdate

    user = get_current_mcp_user()
    _require_write_scope("renaming recordings")
    if not name.strip():
        raise ToolError("name must not be empty.")

    async with async_session_maker() as db:
        updated = await update_recording(
            recording_id,
            RecordingUpdate(name=name.strip()),
            db=db,
            current_user=user,
        )
    return {"id": updated.id, "name": updated.name}


@mcp_tool()
async def tag_recording(
    recording_id: str, tag_name: str, color: Optional[str] = None
) -> dict[str, Any]:
    """Add a tag to a recording, creating the tag if it does not exist.

    Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        tag_name: The tag to apply; created if the user has no tag with
            this name.
        color: Optional colour for a newly created tag.
    """
    from backend.api.v1.endpoints.tags import add_tag_to_recording
    from backend.core.db import async_session_maker
    from backend.models.tag import TagCreate

    user = get_current_mcp_user()
    _require_write_scope("tagging recordings")
    if not tag_name.strip():
        raise ToolError("tag_name must not be empty.")

    async with async_session_maker() as db:
        tag = await add_tag_to_recording(
            recording_id,
            TagCreate(name=tag_name.strip(), color=color),
            db=db,
            current_user=user,
        )
    return {"recording_id": recording_id, "tag": {"id": tag.id, "name": tag.name}}


@mcp_tool()
async def untag_recording(recording_id: str, tag_name: str) -> dict[str, Any]:
    """Remove a tag from a recording. Requires the mcp:write scope.

    Removes the association only; the tag itself is kept and can still be
    used on other recordings.

    Args:
        recording_id: The recording's string id from list_recordings.
        tag_name: The tag name to remove from this recording.
    """
    from backend.api.v1.endpoints.tags import remove_tag_from_recording
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("untagging recordings")

    async with async_session_maker() as db:
        await remove_tag_from_recording(
            recording_id, tag_name, db=db, current_user=user
        )
    return {"recording_id": recording_id, "removed_tag": tag_name}


@mcp_tool()
async def archive_recording(recording_id: str) -> dict[str, Any]:
    """Archive a recording. Fully reversible with restore_recording.

    Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from backend.api.v1.endpoints.recordings.routes_actions import (
        archive_recording as api_archive_recording,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("archiving recordings")
    async with async_session_maker() as db:
        updated = await api_archive_recording(recording_id, db=db, current_user=user)
    return _lifecycle_payload(updated)


@mcp_tool()
async def restore_recording(recording_id: str) -> dict[str, Any]:
    """Restore a recording from the archive or the bin.

    Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from backend.api.v1.endpoints.recordings.routes_actions import (
        restore_recording as api_restore_recording,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("restoring recordings")
    async with async_session_maker() as db:
        updated = await api_restore_recording(recording_id, db=db, current_user=user)
    return _lifecycle_payload(updated)


@mcp_tool()
async def trash_recording(recording_id: str) -> dict[str, Any]:
    """Move a recording to the bin (soft delete). Reversible with
    restore_recording; nothing is destroyed.

    Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from backend.api.v1.endpoints.recordings.routes_actions import (
        soft_delete_recording as api_soft_delete_recording,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("binning recordings")
    async with async_session_maker() as db:
        updated = await api_soft_delete_recording(
            recording_id, db=db, current_user=user
        )
    return _lifecycle_payload(updated)


@mcp_tool()
async def destroy_recording(recording_id: str) -> dict[str, Any]:
    """PERMANENTLY delete a recording, its audio, transcript, and notes.

    This is irreversible: the audio file and every derived artefact are
    destroyed. Requires the mcp:destroy scope, which the user grants only
    through an explicit opt-in on the consent page. Prefer trash_recording
    unless permanent destruction is genuinely intended.

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from backend.api.v1.endpoints.recordings.routes_actions import (
        permanently_delete_recording,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_destroy_scope("permanent recording deletion")
    async with async_session_maker() as db:
        await permanently_delete_recording(recording_id, db=db, current_user=user)
    return {"id": recording_id, "destroyed": True}


@mcp_tool()
async def reprocess_recording(
    recording_id: str,
    transcription_backend: str,
    max_speakers: Optional[int] = None,
) -> dict[str, Any]:
    """Re-run the full processing pipeline for a recording.

    Replaces the transcript and regenerates downstream artefacts; most
    utterance ids will change, though the revision cursor keeps
    increasing. Processing occupies the deployment's GPU, so avoid
    triggering this while a live meeting is being captured. Requires the
    mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        transcription_backend: The ASR backend to use, matching the
            deployment's configured engines (for example "whisper",
            "parakeet", or "canary").
        max_speakers: Optional upper bound on diarised speakers; omit to
            auto-detect.
    """
    from backend.api.v1.endpoints.recordings.routes_actions import (
        ReprocessRequest,
    )
    from backend.api.v1.endpoints.recordings.routes_actions import (
        reprocess_recording as api_reprocess_recording,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("reprocessing recordings")

    body_kwargs: dict[str, Any] = {"transcription_backend": transcription_backend}
    if max_speakers is not None:
        body_kwargs["max_speakers"] = max_speakers

    async with async_session_maker() as db:
        updated = await api_reprocess_recording(
            recording_id,
            ReprocessRequest(**body_kwargs),
            db=db,
            current_user=user,
        )
    return {"id": updated.id, "status": str(updated.status)}


@mcp_tool()
async def regenerate_notes(
    recording_id: str, notes_template_id: Optional[int] = None
) -> dict[str, Any]:
    """Regenerate the AI meeting notes for a recording.

    Re-runs Nojoin's own notes pipeline over the transcript, replacing the
    current AI-generated notes; user notes are untouched. The agent never
    authors note content directly. Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        notes_template_id: Optional notes template to use instead of the
            default.
    """
    from backend.api.v1.endpoints.transcripts.routes_notes import (
        GenerateNotesRequest,
        generate_notes,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("notes regeneration")

    async with async_session_maker() as db:
        result = await generate_notes(
            recording_id,
            GenerateNotesRequest(notes_template_id=notes_template_id),
            db=db,
            current_user=user,
        )
    return {
        "recording_id": recording_id,
        "notes_status": result.get("notes_status"),
        "message": result.get("message"),
    }


@mcp_tool()
async def attach_document(
    recording_id: str, title: str, content: str
) -> dict[str, Any]:
    """Attach authored text to a recording as a document.

    The text is stored as a markdown document and indexed into the
    meeting's context like an uploaded file, so chat, notes, and
    search_context can ground on it. Binary files can only be uploaded
    through the web app. Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        title: A short document title.
        content: The document text (markdown or plain text).
    """
    from backend.api.v1.endpoints.documents import (
        _get_owned_recording as get_owned_recording_for_documents,
    )
    from backend.api.v1.endpoints.documents import create_text_document
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("document attachment")
    if not title.strip():
        raise ToolError("title must not be empty.")
    if not content.strip():
        raise ToolError("content must not be empty.")
    if len(content) > _ATTACH_CONTENT_CHAR_LIMIT:
        raise ToolError(
            f"content is limited to {_ATTACH_CONTENT_CHAR_LIMIT} characters "
            f"per document; got {len(content)}."
        )

    async with async_session_maker() as db:
        recording = await get_owned_recording_for_documents(db, recording_id, user.id)
        document = await create_text_document(
            db,
            recording=recording,
            title=title.strip(),
            content=content,
            user_id=user.id,
        )
    return {
        "recording_id": recording_id,
        "document_id": document.id,
        "title": document.title,
        "status": str(
            document.status.value
            if hasattr(document.status, "value")
            else document.status
        ),
    }


async def _load_recording_for_correction(db, recording_id: str, user_id: int):
    from fastapi import HTTPException

    from backend.api.v1.endpoints.transcripts.helpers import (
        _get_owned_recording,
        _get_recording_transcript,
        _require_recording_transcript_mutations_supported,
    )

    recording = await _get_owned_recording(db, recording_id, user_id)
    transcript = await _get_recording_transcript(db, recording.id)
    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    _require_recording_transcript_mutations_supported(recording)
    return recording, transcript


def _require_canonical_writes() -> None:
    from backend.api.v1.endpoints.transcripts.helpers import (
        _canonical_transcript_writes_enabled,
    )

    if not _canonical_transcript_writes_enabled():
        raise ToolError(
            "Transcript corrections are not available: this deployment has "
            "canonical transcript writes disabled."
        )


@mcp_tool()
async def correct_utterance_text(
    recording_id: str,
    utterance_id: str,
    text: str,
    expected_revision: Optional[int] = None,
) -> dict[str, Any]:
    """Correct the text of one transcript utterance.

    The edit is recorded in the transcript's event log attributed to this
    connection (source "mcp"), bumps the revision cursor, and locks the
    utterance against being overwritten by reprocessing, exactly like an
    edit made in the web app. Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        utterance_id: The utterance's string id from
            get_transcript_utterances.
        text: The corrected utterance text.
        expected_revision: Optional per-utterance revision from
            get_transcript_utterances; the edit is refused if the
            utterance changed since.
    """
    from backend.api.v1.endpoints.transcripts.helpers import (
        TranscriptUtteranceTextPatch,
    )
    from backend.api.v1.endpoints.transcripts.routes_utterances import (
        apply_canonical_utterance_text_update,
    )
    from backend.core.db import async_session_maker
    from backend.utils.canonical_pipeline import get_canonical_transcript_revision
    from backend.utils.config_manager import is_meeting_edge_enabled

    user = get_current_mcp_user()
    _require_write_scope("transcript corrections")
    _require_canonical_writes()
    if not text.strip():
        raise ToolError("text must not be empty.")

    async with async_session_maker() as db:
        recording, transcript = await _load_recording_for_correction(
            db, recording_id, user.id
        )
        await apply_canonical_utterance_text_update(
            db,
            recording=recording,
            transcript=transcript,
            utterance_id=utterance_id,
            update=TranscriptUtteranceTextPatch(
                text=text, expected_revision=expected_revision
            ),
            actor_user_id=user.id,
            meeting_edge_enabled=is_meeting_edge_enabled(
                getattr(user, "settings", None)
            ),
            source="mcp",
        )
        revision = await db.run_sync(
            lambda s: get_canonical_transcript_revision(s, recording.id)
        )
    return {
        "recording_id": recording_id,
        "utterance_id": utterance_id,
        "revision": revision,
    }


@mcp_tool()
async def correct_utterance_speaker(
    recording_id: str,
    utterance_id: str,
    speaker_name: str,
    only_this_utterance: bool = False,
    expected_revision: Optional[int] = None,
) -> dict[str, Any]:
    """Correct which speaker an utterance is attributed to.

    By default the correction applies to every utterance by that speaker
    in the recording, matching the web app's behaviour; set
    only_this_utterance for a single-line fix. The edit is recorded in the
    event log attributed to this connection (source "mcp"). Requires the
    mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        utterance_id: The utterance's string id from
            get_transcript_utterances.
        speaker_name: The speaker name to attribute the utterance to.
        only_this_utterance: Correct just this utterance rather than the
            speaker everywhere in the recording.
        expected_revision: Optional per-utterance revision from
            get_transcript_utterances; the edit is refused if the
            utterance changed since.
    """
    from backend.api.v1.endpoints.transcripts.helpers import (
        TranscriptUtteranceSpeakerPatch,
    )
    from backend.api.v1.endpoints.transcripts.routes_utterances import (
        apply_canonical_utterance_speaker_update,
    )
    from backend.core.db import async_session_maker
    from backend.utils.canonical_pipeline import (
        SpeakerCorrectionScope,
        get_canonical_transcript_revision,
    )
    from backend.utils.config_manager import is_meeting_edge_enabled

    user = get_current_mcp_user()
    _require_write_scope("transcript corrections")
    _require_canonical_writes()
    if not speaker_name.strip():
        raise ToolError("speaker_name must not be empty.")

    scope = (
        SpeakerCorrectionScope.UTTERANCE_ONLY
        if only_this_utterance
        else SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING
    )
    async with async_session_maker() as db:
        recording, transcript = await _load_recording_for_correction(
            db, recording_id, user.id
        )
        await apply_canonical_utterance_speaker_update(
            db,
            recording=recording,
            transcript=transcript,
            utterance_id=utterance_id,
            update=TranscriptUtteranceSpeakerPatch(
                new_speaker_name=speaker_name,
                scope=scope,
                expected_revision=expected_revision,
            ),
            actor_user_id=user.id,
            meeting_edge_enabled=is_meeting_edge_enabled(
                getattr(user, "settings", None)
            ),
            source="mcp",
        )
        revision = await db.run_sync(
            lambda s: get_canonical_transcript_revision(s, recording.id)
        )
    return {
        "recording_id": recording_id,
        "utterance_id": utterance_id,
        "speaker_name": speaker_name,
        "revision": revision,
    }


@mcp_tool()
async def list_calendar_events(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """List the user's synced calendar events, soonest first.

    Use the numeric event id with link_calendar_event to associate a
    recording with its meeting.

    Args:
        start_date: Only events starting on or after this ISO 8601
            date/datetime.
        end_date: Only events starting on or before this ISO 8601
            date/datetime.
        limit: Maximum events to return (1-100, default 25).
    """
    from backend.core.db import async_session_maker
    from backend.models.calendar import (
        CalendarConnection,
        CalendarEvent,
        CalendarSource,
    )

    user = get_current_mcp_user()
    limit = max(1, min(int(limit), _CALENDAR_EVENTS_LIMIT_MAX))
    start = _parse_iso_datetime(start_date, "start_date")
    end = _parse_iso_datetime(end_date, "end_date")

    statement = (
        select(CalendarEvent)
        .join(CalendarSource, CalendarSource.id == CalendarEvent.calendar_id)
        .join(
            CalendarConnection,
            CalendarConnection.id == CalendarSource.connection_id,
        )
        .where(CalendarConnection.user_id == user.id)
    )
    if start is not None:
        statement = statement.where(CalendarEvent.starts_at >= start)
    if end is not None:
        statement = statement.where(CalendarEvent.starts_at <= end)
    statement = statement.order_by(CalendarEvent.starts_at).limit(limit)

    async with async_session_maker() as db:
        events = (await db.execute(statement)).scalars().all()

    return [
        {
            "id": event.id,
            "title": event.title,
            "starts_at": event.starts_at.isoformat() if event.starts_at else None,
            "ends_at": event.ends_at.isoformat() if event.ends_at else None,
            "is_all_day": event.is_all_day,
            "status": event.status,
            "location": event.location_text,
            "meeting_url": event.meeting_url,
        }
        for event in events
    ]


@mcp_tool()
async def link_calendar_event(
    recording_id: str, calendar_event_id: Optional[int] = None
) -> dict[str, Any]:
    """Link a recording to a calendar event, or unlink it.

    Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        calendar_event_id: The event's numeric id from
            list_calendar_events; omit to unlink the recording from its
            current event.
    """
    from backend.api.v1.endpoints.recordings.routes_actions import (
        CalendarEventLink,
        link_recording_calendar_event,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("calendar linking")

    async with async_session_maker() as db:
        updated = await link_recording_calendar_event(
            recording_id,
            CalendarEventLink(calendar_event_id=calendar_event_id),
            db=db,
            current_user=user,
        )
    return {
        "id": updated.id,
        "calendar_event_id": calendar_event_id,
        "linked": calendar_event_id is not None,
    }
