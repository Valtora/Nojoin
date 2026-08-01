"""MCP server core for Nojoin.

Holds the FastMCP instance, the registration decorator, the scope guards,
and the recording/transcript read tools; the rest of the surface lives in
the ``tools_*`` modules imported at the bottom of this file. Every tool
delegates to the same endpoint coroutines and helpers the REST API uses
(ownership checks, canonical-transcript projection, speaker-name
resolution), so the MCP surface can never drift from what the web client
shows. All tools are scoped to the authenticated user resolved by
:class:`backend.mcp_server.auth.MCPAuthMiddleware`.

Three access tiers: ``mcp:read`` for every read tool, ``mcp:write`` for
recoverable mutations (organising recordings, tasks, transcript
corrections, notes regeneration, text documents, People maintenance), and
``mcp:destroy`` — granted only by an explicit consent-page opt-in — for
permanent recording deletion. Grants keep the scopes they were issued
with; tools beyond a grant's scopes refuse with reconnect instructions.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Icon
from sqlalchemy.orm import selectinload
from sqlmodel import select
from starlette.types import ASGIApp

from backend.core.security import MCP_DESTROY_SCOPE, MCP_WRITE_SCOPE
from backend.mcp_server.auth import (
    MCPAuthMiddleware,
    get_current_mcp_scopes,
    get_current_mcp_user,
)
from backend.mcp_server.tool_logging import logged_tool
from backend.utils.config_manager import get_trusted_web_origin

logger = logging.getLogger(__name__)

MCP_SERVER_INSTRUCTIONS = (
    "Full agentic access to the user's Nojoin meeting library: recordings, "
    "transcripts, AI meeting notes, attached documents, tags, tasks, "
    "per-meeting speakers, and the People library. Read tools cover all of "
    "these, plus search_context for semantic search across every meeting "
    "and document. Write tools (mcp:write) cover recoverable changes: "
    "organising recordings (rename, tag, archive, bin, restore), managing "
    "tasks, correcting transcripts, regenerating notes, attaching text "
    "documents, appending user notes, and maintaining People records. "
    "Permanent deletion exists only behind the separate mcp:destroy scope, "
    "which the user grants by an explicit opt-in. Recording identifiers "
    "are the string `id` values returned by list_recordings; person "
    "identifiers are the integer `id` values from list_people. Read "
    "transcripts with get_transcript for prose, or "
    "get_transcript_utterances for structured utterances with stable ids, "
    "timestamps, and a revision cursor for incremental sync."
)

_PUBLIC_ORIGIN = get_trusted_web_origin().rstrip("/")

mcp = FastMCP(
    name="Nojoin",
    instructions=MCP_SERVER_INSTRUCTIONS,
    # Surfaced by MCP clients next to the server name (e.g. Claude's
    # connector list); the logo is served by the web client at a public URL.
    website_url=_PUBLIC_ORIGIN,
    icons=[
        Icon(
            src=f"{_PUBLIC_ORIGIN}/assets/NojoinLogo.png",
            mimeType="image/png",
            sizes=["1024x1024"],
        )
    ],
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    # The SDK's DNS-rebinding Host check only knows localhost defaults and
    # would reject the deployment's public hostname. Nojoin's own
    # TrustedHostMiddleware already validates Host for the whole app.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def mcp_tool() -> Callable[[Callable], Callable]:
    """Register a coroutine as an MCP tool with call logging."""

    def decorator(func: Callable) -> Callable:
        return mcp.tool()(logged_tool(func))

    return decorator


def _parse_iso_datetime(value: Optional[str], field_name: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be an ISO 8601 date or datetime, got: {value!r}"
        ) from exc


def _require_write_scope(capability: str) -> None:
    """Guard a write tool: refuse grants that lack the mcp:write scope.

    Grants issued before mcp:write existed carry only mcp:read, so rather
    than failing opaquely the tool tells the assistant to have the user
    reconnect and consent to the wider scope.
    """
    if MCP_WRITE_SCOPE not in get_current_mcp_scopes():
        raise ToolError(
            "This connection is read-only: its grant predates the "
            f"{MCP_WRITE_SCOPE} scope. Ask the user to reconnect the Nojoin "
            f"connector (remove and re-authorise it) to enable {capability}."
        )


def _require_destroy_scope(capability: str) -> None:
    """Guard a permanent-deletion tool: refuse grants without mcp:destroy.

    The destroy scope is never part of the default grant; the user must
    tick the explicit opt-in on the consent page. The refusal tells the
    assistant how the user can enable it, and that everything else keeps
    working without it.
    """
    if MCP_DESTROY_SCOPE not in get_current_mcp_scopes():
        raise ToolError(
            "This connection cannot permanently delete anything: the "
            f"{MCP_DESTROY_SCOPE} scope was not granted. If the user wants "
            f"to enable {capability}, they must reconnect the Nojoin "
            "connector and tick 'Also allow permanent deletion' on the "
            "consent page. Archiving and moving to the bin still work "
            "without it."
        )


def _compact_recording(
    recording: Any,
    transcript_revision: int = 0,
) -> dict[str, Any]:
    """Compact a Recording ORM row for the MCP client.

    Expects ``tags`` (with each ``RecordingTag.tag``), ``speakers`` (with
    each ``RecordingSpeaker.global_speaker``), and ``transcript``
    eager-loaded: the web endpoint's serializer strips these unless
    explicitly asked, so list_recordings loads the ORM row itself rather
    than relying on that projection.
    """
    speakers = [
        speaker.local_name
        or (speaker.global_speaker.name if speaker.global_speaker else None)
        or speaker.name
        or speaker.diarization_label
        for speaker in recording.speakers
        if not speaker.merged_into_id
    ]
    tags = [
        recording_tag.tag.name
        for recording_tag in recording.tags
        if recording_tag.tag is not None
    ]
    transcript = recording.transcript
    return {
        "id": recording.public_id,
        "name": recording.name,
        "created_at": recording.created_at.isoformat(),
        "updated_at": recording.updated_at.isoformat(),
        "duration_seconds": recording.duration_seconds,
        "status": str(recording.status.value)
        if hasattr(recording.status, "value")
        else str(recording.status),
        "transcript_status": transcript.transcript_status if transcript else None,
        "notes_status": transcript.notes_status if transcript else None,
        "transcript_revision": transcript_revision,
        "tags": tags,
        "speakers": speakers,
        "is_archived": recording.is_archived,
        "is_deleted": recording.is_deleted,
    }


@mcp_tool()
async def list_recordings(
    limit: int = 20,
    skip: int = 0,
    query: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List and search the user's meeting recordings, newest first.

    Search covers the whole library: archived and soft-deleted recordings are
    included, so a search can find a meeting the user has archived or removed.
    Each result carries `is_archived` and `is_deleted` so their state is
    clear and the caller can filter them out when only active meetings matter.

    Each result also reports processing state (`status`, `transcript_status`,
    `notes_status`), `updated_at`, and the canonical `transcript_revision`
    cursor, so a caller can tell that a recording has finished processing
    (`status` PROCESSED and `notes_status` completed) or that its transcript
    changed since last seen, without re-fetching transcripts. Pass a stored
    `transcript_revision` to get_transcript_utterances to fetch only what
    changed.

    Args:
        limit: Maximum number of recordings to return (1-100).
        skip: Number of recordings to skip, for pagination.
        query: Optional free-text search across recording names, transcript
            text, speaker names, and tag names.
        start_date: Only recordings created on or after this ISO 8601
            date/datetime (e.g. 2026-06-01).
        end_date: Only recordings created on or before this ISO 8601
            date/datetime.
    """
    from sqlalchemy import func

    from backend.api.v1.endpoints.recordings.routes_query import (
        list_recordings as api_list_recordings,
    )
    from backend.core.db import async_session_maker
    from backend.models.pipeline import TranscriptUtteranceEvent
    from backend.models.recording import Recording
    from backend.models.speaker import RecordingSpeaker
    from backend.models.tag import RecordingTag

    user = get_current_mcp_user()
    limit = max(1, min(int(limit), 100))

    async with async_session_maker() as db:
        # api_list_recordings owns the search/date/ownership filtering and
        # ordering, but serializes without tags or speakers. Re-load the same
        # rows as ORM objects with those relationships eager-loaded so the
        # compact projection reports them instead of always-empty lists.
        results = await api_list_recordings(
            skip=max(0, int(skip)),
            limit=limit,
            q=query,
            start_date=_parse_iso_datetime(start_date, "start_date"),
            end_date=_parse_iso_datetime(end_date, "end_date"),
            speaker_ids=None,
            tag_ids=None,
            # Search spans the whole library; is_archived/is_deleted in the
            # result let the caller tell active meetings from the rest.
            include_archived=True,
            include_deleted=True,
            only_archived=False,
            only_deleted=False,
            status_filters=None,
            user_filter=None,
            db=db,
            current_user=user,
        )
        ordered_public_ids = [recording.id for recording in results]
        if not ordered_public_ids:
            return []

        orm_result = await db.execute(
            select(Recording)
            .where(
                Recording.user_id == user.id,
                Recording.public_id.in_(ordered_public_ids),
            )
            .options(
                selectinload(Recording.tags).selectinload(RecordingTag.tag),
                selectinload(Recording.speakers).selectinload(
                    RecordingSpeaker.global_speaker
                ),
                selectinload(Recording.transcript),
            )
        )
        by_public_id = {rec.public_id: rec for rec in orm_result.scalars()}

        # One grouped aggregate for the whole page: the canonical revision
        # cursor is max(event id) per recording, and a query per row would
        # be an N+1 against the busiest table in the schema.
        internal_ids = [rec.id for rec in by_public_id.values()]
        revision_result = await db.execute(
            select(
                TranscriptUtteranceEvent.recording_id,
                func.max(TranscriptUtteranceEvent.id),
            )
            .where(TranscriptUtteranceEvent.recording_id.in_(internal_ids))
            .group_by(TranscriptUtteranceEvent.recording_id)
        )
        revisions = {
            recording_id: int(revision)
            for recording_id, revision in revision_result.all()
        }

    return [
        _compact_recording(
            by_public_id[public_id],
            transcript_revision=revisions.get(by_public_id[public_id].id, 0),
        )
        for public_id in ordered_public_ids
        if public_id in by_public_id
    ]


@mcp_tool()
async def get_transcript(recording_id: str) -> dict[str, Any]:
    """Get the full speaker-attributed transcript of a recording.

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from backend.api.v1.endpoints.transcripts.helpers import (
        _build_speaker_map,
        _format_transcript_text,
        _get_owned_recording,
    )
    from backend.core.db import async_session_maker
    from backend.models.recording import Recording
    from backend.models.speaker import RecordingSpeaker
    from backend.utils.canonical_pipeline import build_transcript_segments_for_read

    user = get_current_mcp_user()
    async with async_session_maker() as db:
        recording = await _get_owned_recording(
            db,
            recording_id,
            user.id,
            options=(
                selectinload(Recording.speakers).options(
                    selectinload(RecordingSpeaker.global_speaker)
                ),
            ),
        )
        active_speakers = [
            speaker for speaker in recording.speakers if not speaker.merged_into_id
        ]
        segments = await db.run_sync(
            lambda sync_session: build_transcript_segments_for_read(
                sync_session, recording.id
            )
        )
        speaker_map = _build_speaker_map(active_speakers)
        transcript_text = _format_transcript_text(segments, speaker_map)

    return {
        "recording_id": recording_id,
        "name": recording.name,
        "created_at": recording.created_at.isoformat(),
        "duration_seconds": recording.duration_seconds,
        "transcript": transcript_text,
    }


@mcp_tool()
async def get_transcript_utterances(
    recording_id: str,
    after_revision: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Get the canonical transcript as structured utterances with delta sync.

    Where get_transcript returns formatted prose for reading, this returns
    the same structured contract the web client synchronises on: stable
    utterance ids, millisecond timestamps, per-utterance state, revision and
    edit-provenance flags, the recording's speaker list, a recording-level
    `revision` cursor, and `tombstones` (ids of utterances removed or
    superseded since the supplied cursor).

    Intended for tools that keep their own copy of a transcript in sync:
    store the returned `revision`, and pass it back as `after_revision` on
    the next call to receive only what changed (empty lists mean up to
    date). Omit `after_revision` for a full snapshot, which always has empty
    `tombstones`. The cursor is opaque and increases monotonically per
    recording; it never resets, including across reprocessing, though
    reprocessing may replace most utterance ids. The `speakers` list always
    reflects current names, so speaker renames are visible even when no
    utterance changed.

    Long transcripts are paged: `utterances` holds at most `limit` entries
    starting at `offset`, `total_utterances` is the full match count, and
    `next_offset` is the offset of the next page, or null when this page is
    the last. `tombstones` and `speakers` are complete on every page. Pages
    are consistent only within one `revision`: if `revision` differs between
    pages, the transcript changed mid-read, so restart from offset 0.

    Args:
        recording_id: The recording's string id from list_recordings.
        after_revision: The last `revision` cursor already seen, from a
            previous call or from list_recordings' `transcript_revision`.
            Omit for a full snapshot.
        limit: Maximum utterances per page (1-500, default 100).
        offset: Number of matching utterances to skip, for pagination.
    """
    from backend.api.v1.endpoints.transcripts.routes_utterances import (
        get_transcript_utterances as api_get_transcript_utterances,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    if after_revision is not None and after_revision < 0:
        raise ToolError("after_revision must be a non-negative integer.")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    async with async_session_maker() as db:
        payload = await api_get_transcript_utterances(
            recording_id,
            after_revision=after_revision,
            db=db,
            current_user=user,
        )

    result = payload.model_dump()
    utterances = result["utterances"]
    page_end = offset + limit
    result["utterances"] = utterances[offset:page_end]
    result["total_utterances"] = len(utterances)
    result["next_offset"] = page_end if page_end < len(utterances) else None
    return result


@mcp_tool()
async def get_meeting_notes(recording_id: str) -> dict[str, Any]:
    """Get the AI-generated meeting notes and the user's own notes.

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from backend.api.v1.endpoints.transcripts.helpers import _get_owned_recording
    from backend.core.db import async_session_maker
    from backend.models.transcript import Transcript

    user = get_current_mcp_user()
    async with async_session_maker() as db:
        recording = await _get_owned_recording(db, recording_id, user.id)
        result = await db.execute(
            select(Transcript).where(Transcript.recording_id == recording.id)
        )
        transcript = result.scalar_one_or_none()

    return {
        "recording_id": recording_id,
        "name": recording.name,
        "notes": transcript.notes if transcript else None,
        "user_notes": transcript.user_notes if transcript else None,
    }


_TAG_PAGE_SIZE = 200


@mcp_tool()
async def list_tags() -> list[dict[str, Any]]:
    """List the user's tags. Tag names can be used with list_recordings' query."""
    from backend.api.v1.endpoints.tags import read_tags
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    collected: list[dict[str, Any]] = []
    async with async_session_maker() as db:
        # read_tags caps each call at its own default limit, so page through
        # to the end rather than silently returning only the first page.
        skip = 0
        while True:
            page = await read_tags(
                skip=skip, limit=_TAG_PAGE_SIZE, db=db, current_user=user
            )
            collected.extend({"id": tag.id, "name": tag.name} for tag in page)
            if len(page) < _TAG_PAGE_SIZE:
                break
            skip += _TAG_PAGE_SIZE
    return collected


# A single document's reconstructed text is capped so a large attachment
# cannot flood the assistant's context; the flag lets it request more if
# it truly needs the tail.
_DOCUMENT_TEXT_CHAR_LIMIT = 20000


@mcp_tool()
async def get_documents(recording_id: str) -> list[dict[str, Any]]:
    """Get the documents attached to a recording, with their extracted text.

    These are the files uploaded to a meeting to ground its chat and notes.
    Text is assembled from the document's parsed pages and is truncated per
    document beyond a size limit.

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from backend.api.v1.endpoints.transcripts.helpers import _get_owned_recording
    from backend.core.db import async_session_maker
    from backend.models.document import Document
    from backend.models.document_page import DocumentPage

    user = get_current_mcp_user()
    async with async_session_maker() as db:
        recording = await _get_owned_recording(db, recording_id, user.id)
        docs_result = await db.execute(
            select(Document)
            .where(Document.recording_id == recording.id)
            .order_by(Document.created_at)
        )
        documents = docs_result.scalars().all()

        payload: list[dict[str, Any]] = []
        for document in documents:
            # Read the pages, not the chunks. Chunks used to be reassembled
            # with "".join(), which re-emitted the 50-character overlap at
            # every boundary and put a stutter into the middle of sentences.
            # Pages hold the text once, in order, with no overlap by design.
            page_result = await db.execute(
                select(
                    DocumentPage.page_number, DocumentPage.title, DocumentPage.content
                )
                .where(DocumentPage.document_id == document.id)
                .order_by(DocumentPage.page_number)
            )
            sections: list[str] = []
            for page_number, title, content in page_result.all():
                if not (content or "").strip():
                    continue
                heading = f"[Page {page_number}]"
                if title:
                    heading = f"[Page {page_number}: {title}]"
                sections.append(f"{heading}\n{content}")
            text = "\n\n".join(sections)
            truncated = len(text) > _DOCUMENT_TEXT_CHAR_LIMIT
            payload.append(
                {
                    "id": document.id,
                    "title": document.title,
                    "file_type": document.file_type,
                    "status": str(
                        document.status.value
                        if hasattr(document.status, "value")
                        else document.status
                    ),
                    "page_count": document.page_count,
                    "parse_warning": document.parse_warning,
                    "text": text[:_DOCUMENT_TEXT_CHAR_LIMIT],
                    "text_truncated": truncated,
                }
            )
    return payload


@mcp_tool()
async def append_meeting_notes(recording_id: str, text: str) -> dict[str, Any]:
    """Append text to a recording's user notes.

    Adds to the user-authored notes only; the AI-generated meeting notes are
    never modified. The text is appended after the existing user notes, so
    earlier content is preserved. Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        text: The note text to append.
    """
    from backend.api.v1.endpoints.transcripts.helpers import UserNotesUpdate
    from backend.api.v1.endpoints.transcripts.routes_notes import (
        get_user_notes,
        update_user_notes,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("note editing")
    addition = text.strip()
    if not addition:
        raise ToolError("text must not be empty.")

    async with async_session_maker() as db:
        current = await get_user_notes(recording_id, db=db, current_user=user)
        existing = (current.get("user_notes") or "").rstrip()
        combined = f"{existing}\n\n{addition}" if existing else addition
        saved = await update_user_notes(
            recording_id,
            UserNotesUpdate(user_notes=combined),
            db=db,
            current_user=user,
        )
    return {"recording_id": recording_id, "user_notes": saved.get("user_notes")}


class NormaliseMcpMountPathMiddleware:
    """Serve ``/mcp`` (no trailing slash) without a redirect.

    MCP clients POST to ``/mcp`` exactly, but a Starlette mount only
    matches ``/mcp/…``, so the outer router answers ``/mcp`` with a 307
    slash-redirect — which MCP clients do not reliably follow, and whose
    Location loses the HTTPS scheme behind the reverse proxy. This
    middleware runs before routing and rewrites the bare mount path so the
    request is served directly. Register it on the outer application.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


def build_mcp_asgi_app() -> ASGIApp:
    """The MCP Starlette app wrapped in bearer-token authentication."""
    return MCPAuthMiddleware(mcp.streamable_http_app())


@asynccontextmanager
async def mcp_session_manager_context():
    """Run the streamable-HTTP session manager for the app's lifetime.

    Must be entered from the parent application's lifespan; the MCP mount
    returns 500s if requests arrive while the session manager is not
    running.
    """
    # Ensure the session manager exists (created lazily by
    # streamable_http_app, which create_app calls before the lifespan runs).
    mcp.streamable_http_app()
    async with mcp.session_manager.run():
        yield


# Tool modules register themselves against `mcp` via the mcp_tool decorator
# they import from this module, so these imports are load-bearing: a module
# missing here has no tools. They sit at the bottom because the decorator
# and scope guards above must exist before the modules import them.
from backend.mcp_server import (
    tools_manage,  # noqa: E402,F401
    tools_people,  # noqa: E402,F401
    tools_search,  # noqa: E402,F401
    tools_tasks,  # noqa: E402,F401
)
