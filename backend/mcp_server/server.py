"""MCP tool surface for Nojoin.

Every tool delegates to the same endpoint coroutines and helpers the REST
API uses (ownership checks, canonical-transcript projection, speaker-name
resolution), so the MCP surface can never drift from what the web client
shows. All tools are scoped to the authenticated user resolved by
:class:`backend.mcp_server.auth.MCPAuthMiddleware`.

Most tools are read-only. The write tools (``import_people``,
``set_speaker_name``, ``append_meeting_notes``) are additive — they create,
link, or annotate, never delete — and each requires the ``mcp:write`` scope
on the grant. Every read tool works with ``mcp:read`` alone, so grants
issued before the write scope existed keep functioning unchanged.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable, Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Icon
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select
from starlette.types import ASGIApp

from backend.core.security import MCP_WRITE_SCOPE
from backend.mcp_server.auth import (
    MCPAuthMiddleware,
    get_current_mcp_scopes,
    get_current_mcp_user,
)
from backend.mcp_server.tool_logging import logged_tool
from backend.utils.config_manager import get_trusted_web_origin

logger = logging.getLogger(__name__)

MCP_SERVER_INSTRUCTIONS = (
    "Access to the user's Nojoin meeting library: recordings, transcripts, "
    "AI meeting notes, attached documents, tags, per-meeting speakers, and "
    "the People library (the user's saved people with voiceprints and "
    "contact details). Read tools cover all of these. Write tools are "
    "additive only: import_people creates or updates People records, "
    "set_speaker_name names a meeting's speaker and links it to a person, "
    "and append_meeting_notes adds to a meeting's user notes. Recording "
    "identifiers are the string `id` values returned by list_recordings; "
    "person identifiers are the integer `id` values from list_people. "
    "Read transcripts with get_transcript for prose, or "
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

    Args:
        recording_id: The recording's string id from list_recordings.
        after_revision: The last `revision` cursor already seen, from a
            previous call or from list_recordings' `transcript_revision`.
            Omit for a full snapshot.
    """
    from backend.api.v1.endpoints.transcripts.routes_utterances import (
        get_transcript_utterances as api_get_transcript_utterances,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    if after_revision is not None and after_revision < 0:
        raise ToolError("after_revision must be a non-negative integer.")

    async with async_session_maker() as db:
        payload = await api_get_transcript_utterances(
            recording_id,
            after_revision=after_revision,
            db=db,
            current_user=user,
        )

    return payload.model_dump()


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


def _compact_speaker(speaker: Any) -> dict[str, Any]:
    display_name = (
        speaker.local_name
        or (speaker.global_speaker.name if speaker.global_speaker else None)
        or speaker.name
        or speaker.diarization_label
    )
    person = None
    if speaker.global_speaker is not None:
        person = {
            "id": speaker.global_speaker.id,
            "name": speaker.global_speaker.name,
            "title": speaker.global_speaker.title,
            "company": speaker.global_speaker.company,
        }
    return {
        "display_name": display_name,
        "diarization_label": speaker.diarization_label,
        "person": person,
    }


@mcp_tool()
async def get_speakers(recording_id: str) -> dict[str, Any]:
    """Get the speakers in a recording.

    Each speaker carries the display name used in the transcript and, when
    the speaker is linked to a saved person, that person's People-library
    entry (usable with list_people and import_people).

    Args:
        recording_id: The recording's string id from list_recordings.
    """
    from backend.api.v1.endpoints.transcripts.helpers import _get_owned_recording
    from backend.core.db import async_session_maker
    from backend.models.recording import Recording
    from backend.models.speaker import RecordingSpeaker

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
        speakers = [
            _compact_speaker(speaker)
            for speaker in recording.speakers
            if not speaker.merged_into_id
        ]

    return {
        "recording_id": recording_id,
        "name": recording.name,
        "speakers": speakers,
    }


def _compact_person(person: Any) -> dict[str, Any]:
    return {
        "id": person.id,
        "name": person.name,
        "title": person.title,
        "company": person.company,
        "email": person.email,
        "phone_number": person.phone_number,
        "notes": person.notes,
        "tags": [tag.name for tag in person.tags],
        "recording_count": person.recording_count,
        "has_voiceprint": person.has_voiceprint,
    }


@mcp_tool()
async def list_people(
    query: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
) -> list[dict[str, Any]]:
    """List the user's People library (saved people), sorted by name.

    People are the user's global speaker records: name, contact details,
    notes, tags, whether a voiceprint is stored, and how many recordings
    they appear in.

    Args:
        query: Optional free-text search across name, email, company,
            notes, and title.
        limit: Maximum number of people to return (1-200).
        skip: Number of people to skip, for pagination.
    """
    from backend.api.v1.endpoints.speakers.routes_global import (
        list_global_speakers as api_list_people,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    limit = max(1, min(int(limit), 200))

    async with async_session_maker() as db:
        results = await api_list_people(
            skip=max(0, int(skip)),
            limit=limit,
            q=query,
            tags=None,
            db=db,
            current_user=user,
        )
    return [_compact_person(person) for person in results]


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
async def get_person(person_id: int) -> dict[str, Any]:
    """Get one person from the People library, with the meetings they are in.

    Args:
        person_id: The person's integer id from list_people.
    """
    from backend.core.db import async_session_maker
    from backend.models.recording import Recording
    from backend.models.speaker import GlobalSpeaker, RecordingSpeaker

    user = get_current_mcp_user()
    async with async_session_maker() as db:
        person = await db.get(GlobalSpeaker, person_id)
        if person is None or person.user_id != user.id:
            raise ToolError(f"No person with id {person_id} in your library.")

        meetings_result = await db.execute(
            select(Recording)
            .join(RecordingSpeaker, RecordingSpeaker.recording_id == Recording.id)
            .where(RecordingSpeaker.global_speaker_id == person_id)
            .where(Recording.is_deleted.is_(False))
            .where(RecordingSpeaker.merged_into_id.is_(None))
            .order_by(Recording.created_at.desc())
            .distinct()
        )
        meetings = [
            {
                "id": recording.public_id,
                "name": recording.name,
                "created_at": recording.created_at.isoformat(),
            }
            for recording in meetings_result.scalars()
        ]

    return {
        "id": person.id,
        "name": person.name,
        "title": person.title,
        "company": person.company,
        "email": person.email,
        "phone_number": person.phone_number,
        "notes": person.notes,
        "tags": [link.tag.name for link in person.tag_links if link.tag],
        "has_voiceprint": person.has_voiceprint,
        "meetings": meetings,
    }


class PersonImportEntry(BaseModel):
    """One person to create or update in the People library."""

    name: str
    title: Optional[str] = None
    company: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    notes: Optional[str] = None
    tags: list[str] = []


_IMPORT_BATCH_LIMIT = 200
_CRM_FIELDS = ("title", "company", "email", "phone_number", "notes")


async def _resolve_tag_ids(
    db: AsyncSession,
    user_id: int,
    tag_names: list[str],
    cache: dict[str, int],
) -> list[int]:
    """Map tag names to PeopleTag ids for the user, creating missing tags."""
    from backend.models.people_tag import PeopleTag

    tag_ids: list[int] = []
    for raw_name in tag_names:
        name = raw_name.strip()
        if not name:
            continue
        if name not in cache:
            result = await db.execute(
                select(PeopleTag).where(
                    PeopleTag.name == name, PeopleTag.user_id == user_id
                )
            )
            tag = result.scalars().first()
            if tag is None:
                tag = PeopleTag(name=name, user_id=user_id)
                db.add(tag)
                await db.flush()
            cache[name] = tag.id
        if cache[name] not in tag_ids:
            tag_ids.append(cache[name])
    return tag_ids


async def _link_person_tags(
    db: AsyncSession, global_speaker_id: int, tag_ids: list[int]
) -> None:
    from backend.models.people_tag import PeopleTagLink

    if not tag_ids:
        return
    result = await db.execute(
        select(PeopleTagLink.tag_id).where(
            PeopleTagLink.global_speaker_id == global_speaker_id
        )
    )
    existing = {row[0] for row in result.all()}
    for tag_id in tag_ids:
        if tag_id not in existing:
            db.add(PeopleTagLink(global_speaker_id=global_speaker_id, tag_id=tag_id))


async def _import_one_person(
    db: AsyncSession,
    user_id: int,
    entry: PersonImportEntry,
    on_conflict: str,
    tag_cache: dict[str, int],
) -> dict[str, Any]:
    from backend.models.speaker import GlobalSpeaker

    name = entry.name.strip()
    if not name:
        return {"name": entry.name, "action": "error", "detail": "name is required"}

    result = await db.execute(
        select(GlobalSpeaker).where(
            GlobalSpeaker.name == name, GlobalSpeaker.user_id == user_id
        )
    )
    person = result.scalars().first()
    if person is not None and on_conflict == "skip":
        return {"name": name, "action": "skipped", "detail": "person already exists"}

    tag_ids = await _resolve_tag_ids(db, user_id, entry.tags, tag_cache)
    if person is None:
        action = "created"
        person = GlobalSpeaker(name=name, user_id=user_id)
        db.add(person)
        await db.flush()
    else:
        action = "updated"
    # Only fields present in the entry are written; voiceprints, colors,
    # and existing tag links are never removed by an import.
    for field in _CRM_FIELDS:
        value = getattr(entry, field)
        if value is not None:
            setattr(person, field, value)
    await _link_person_tags(db, person.id, tag_ids)
    await db.commit()
    return {"name": name, "action": action, "id": person.id}


@mcp_tool()
async def import_people(
    people: list[PersonImportEntry],
    on_conflict: Literal["update", "skip"] = "update",
) -> dict[str, Any]:
    """Import people into the user's People library.

    Creates a People record for each entry, matching existing people by
    exact name. Use this to bring people in from external sources (a CRM
    export, a pasted list) by mapping them onto Nojoin's fields first.
    Imports never touch voiceprints or remove existing data: only the
    fields provided in an entry are written, and tags are added, not
    replaced. Requires the mcp:write scope.

    Args:
        people: Entries to import (at most 200 per call). Only name is
            required. tags is a list of People-tag names; missing tags are
            created.
        on_conflict: What to do when a person with the same name already
            exists: "update" (default) writes the provided fields onto the
            existing record, "skip" leaves it untouched.
    """
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("People imports")
    if not people:
        raise ToolError("people must contain at least one entry.")
    if len(people) > _IMPORT_BATCH_LIMIT:
        raise ToolError(
            f"At most {_IMPORT_BATCH_LIMIT} people can be imported per call; "
            f"got {len(people)}. Split the import into smaller batches."
        )

    results: list[dict[str, Any]] = []
    tag_cache: dict[str, int] = {}
    async with async_session_maker() as db:
        for entry in people:
            try:
                results.append(
                    await _import_one_person(db, user.id, entry, on_conflict, tag_cache)
                )
            except Exception as exc:  # noqa: BLE001 -- boundary: one bad entry must not abort the rest of the batch
                await db.rollback()
                # Tags created inside the rolled-back transaction are gone;
                # drop their cached ids so later entries re-create them.
                tag_cache.clear()
                logger.warning("MCP import_people entry %r failed: %s", entry.name, exc)
                results.append(
                    {"name": entry.name, "action": "error", "detail": str(exc)}
                )

    summary = {
        action: sum(1 for r in results if r["action"] == action)
        for action in ("created", "updated", "skipped", "error")
    }
    return {"summary": summary, "results": results}


@mcp_tool()
async def set_speaker_name(
    recording_id: str, diarization_label: str, name: str
) -> dict[str, Any]:
    """Name a speaker in a recording and link them to a person.

    Sets the display name for one diarised speaker (for example
    "SPEAKER_00") in a meeting. If a person with that exact name already
    exists in the People library, the speaker is linked to them (so the
    person's meeting list and voiceprint stay in sync); otherwise the name
    is applied to this recording only. To link to a new person, import them
    first with import_people, then call this. Requires the mcp:write scope.

    Args:
        recording_id: The recording's string id from list_recordings.
        diarization_label: The speaker's diarisation label from
            get_speakers (e.g. "SPEAKER_00").
        name: The name to apply, ideally matching a People-library entry.
    """
    from backend.api.v1.endpoints.speakers.helpers import SpeakerUpdate
    from backend.api.v1.endpoints.speakers.routes_recording import (
        update_recording_speaker,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    _require_write_scope("speaker naming")

    async with async_session_maker() as db:
        speakers = await update_recording_speaker(
            recording_id,
            SpeakerUpdate(
                diarization_label=diarization_label, global_speaker_name=name
            ),
            db=db,
            current_user=user,
        )

    updated = next(
        (s for s in speakers if s.diarization_label == diarization_label), None
    )
    linked_person = (
        updated.global_speaker.name
        if updated is not None and updated.global_speaker is not None
        else None
    )
    return {
        "recording_id": recording_id,
        "diarization_label": diarization_label,
        "name": name,
        "linked_person": linked_person,
    }


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
