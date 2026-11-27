"""Semantic search over a user's context chunks.

Backs the MCP search_context tool. The chat endpoint has its own, narrower
retrieval (current recording plus tag widening); this service is the
whole-library variant with optional filters. Both enforce the same
boundary: retrieval never leaves the calling user's own recordings.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Sequence

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.context_chunk import ContextChunk
from backend.models.recording import Recording
from backend.models.tag import RecordingTag
from backend.models.user import User
from backend.processing.text_embedding_version import TEXT_EMBEDDING_VERSION
from backend.services.recording_access import accessible_recordings_filter
from backend.services.user_identities import groups_of
from backend.utils.note_attachments import unread_attachment_ids

SearchSources = Literal["all", "transcripts", "documents"]


def searchable_recording_ids(  # noqa: PLR0913 - keyword-only search filters
    user: User,
    groups: Sequence[str] = (),
    *,
    recording_public_ids: Optional[Sequence[str]] = None,
    tag_ids: Optional[Sequence[int]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
):
    """Subquery of recording ids the search may touch.

    The access filter is the security boundary: the caller's own recordings
    and the ones shared with them, directly or through ``groups``, as in the
    recordings list. Every other filter only narrows within those.
    Soft-deleted recordings are excluded, matching what the rest of the
    product treats as gone.
    """
    statement = select(Recording.id).where(
        accessible_recordings_filter(user, groups),
        Recording.is_deleted.is_(False),
    )
    if recording_public_ids:
        statement = statement.where(Recording.public_id.in_(recording_public_ids))
    if tag_ids:
        statement = statement.where(
            Recording.id.in_(
                select(RecordingTag.recording_id).where(
                    RecordingTag.tag_id.in_(tag_ids)
                )
            )
        )
    if start is not None:
        statement = statement.where(Recording.created_at >= start)
    if end is not None:
        statement = statement.where(Recording.created_at <= end)
    return statement


async def search_context_chunks(  # noqa: PLR0913 - keyword-only search filters
    db: AsyncSession,
    *,
    user: User,
    query_embedding: Sequence[float],
    limit: int,
    sources: SearchSources = "all",
    recording_public_ids: Optional[Sequence[str]] = None,
    tag_ids: Optional[Sequence[int]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[tuple[ContextChunk, float]]:
    """Rank the chunks of the meetings ``user`` can open by cosine distance to
    the query embedding.

    Returns (chunk, distance) pairs, nearest first. Only vectors from the
    current embedding version are scored: older vectors are incomparable
    and would rank as noise.
    """
    distance = ContextChunk.embedding.cosine_distance(query_embedding)
    statement = (
        select(ContextChunk, distance.label("distance"))
        .where(
            ContextChunk.recording_id.in_(
                searchable_recording_ids(
                    user,
                    await groups_of(db, user),
                    recording_public_ids=recording_public_ids,
                    tag_ids=tag_ids,
                    start=start,
                    end=end,
                )
            )
        )
        .where(ContextChunk.embedding_version == TEXT_EMBEDDING_VERSION)
        # A note's file switched off is not read, even if it was parsed while on.
        .where(
            or_(
                ContextChunk.document_id.is_(None),
                ContextChunk.document_id.not_in(unread_attachment_ids()),
            )
        )
    )
    if sources == "transcripts":
        # A null document_id alone is not "transcript": indexed AI notes
        # also carry none, so filter on the chunk's own source metadata,
        # treating a missing key as transcript for legacy chunks.
        from sqlalchemy import func as sa_func

        statement = statement.where(ContextChunk.document_id.is_(None)).where(
            sa_func.coalesce(ContextChunk.meta.op("->>")("source"), "transcript")
            != "notes"
        )
    elif sources == "documents":
        statement = statement.where(ContextChunk.document_id.is_not(None))
    statement = statement.order_by(distance).limit(limit)

    rows = (await db.execute(statement)).all()
    return [(chunk, float(dist)) for chunk, dist in rows]
