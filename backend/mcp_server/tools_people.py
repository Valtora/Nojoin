"""MCP tools for speakers and the People library.

Read tools resolve speaker display names and People records; the write
tools (``import_people``, ``set_speaker_name``) are additive and follow
the same delegation rule as every other tool: they call the endpoint
coroutines the REST API uses, so behaviour cannot drift from the web
client.
"""

import logging
from typing import Any, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.core.security import MCP_WRITE_SCOPE
from backend.mcp_server.auth import get_current_mcp_user
from backend.mcp_server.server import _require_write_scope, mcp_tool

logger = logging.getLogger(__name__)


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


@mcp_tool(scope=MCP_WRITE_SCOPE)
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


@mcp_tool(scope=MCP_WRITE_SCOPE)
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
