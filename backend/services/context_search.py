"""Semantic search over a user's context chunks.

Backs the MCP search_context tool. The chat endpoint has its own, narrower
retrieval (current recording plus tag widening); this service is the
whole-library variant with optional filters. Both enforce the same
boundary: retrieval never leaves the calling user's own recordings.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.context_chunk import ContextChunk
from backend.models.recording import Recording
from backend.models.tag import RecordingTag
from backend.processing.text_embedding_version import TEXT_EMBEDDING_VERSION

SearchSources = Literal["all", "transcripts", "documents"]


def searchable_recording_ids(
    user_id: int,
    *,
    recording_public_ids: Optional[Sequence[str]] = None,
    tag_ids: Optional[Sequence[int]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
):
    """Subquery of recording ids the search may touch.

    The user_id filter is the security boundary: every other filter only
    narrows within the caller's own library. Soft-deleted recordings are
    excluded, matching what the rest of the product treats as gone.
    """
    statement = select(Recording.id).where(
        Recording.user_id == user_id,
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
    user_id: int,
    query_embedding: Sequence[float],
    limit: int,
    sources: SearchSources = "all",
    recording_public_ids: Optional[Sequence[str]] = None,
    tag_ids: Optional[Sequence[int]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[tuple[ContextChunk, float]]:
    """Rank the user's chunks by cosine distance to the query embedding.

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
                    user_id,
                    recording_public_ids=recording_public_ids,
                    tag_ids=tag_ids,
                    start=start,
                    end=end,
                )
            )
        )
        .where(ContextChunk.embedding_version == TEXT_EMBEDDING_VERSION)
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
