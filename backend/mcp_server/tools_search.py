"""MCP semantic search across the user's whole meeting library."""

import logging
from typing import Any, Literal, Optional

from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.mcp_server.auth import get_current_mcp_user
from backend.mcp_server.server import _parse_iso_datetime, mcp_tool

logger = logging.getLogger(__name__)

_SEARCH_LIMIT_MAX = 25


@mcp_tool()
async def search_context(  # noqa: PLR0913 - each parameter is a documented tool argument
    query: str,
    limit: int = 10,
    sources: Literal["all", "transcripts", "documents"] = "all",
    recording_ids: Optional[list[str]] = None,
    tag_ids: Optional[list[int]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict[str, Any]:
    """Semantic search across every meeting transcript and attached document.

    Embeds the query and ranks the user's indexed content by similarity,
    so it finds passages that match in meaning, not just wording. Results
    carry provenance: the recording, timestamps for transcript passages,
    and document title and page for document passages. Use this to answer
    "where did we discuss X" across the whole library; use list_recordings'
    query parameter for name and keyword lookups instead.

    Args:
        query: What to search for, as a natural-language phrase.
        limit: Maximum passages to return (1-25, default 10).
        sources: Restrict to "transcripts" or "documents", default both.
        recording_ids: Optional recording ids (from list_recordings) to
            search within.
        tag_ids: Optional tag ids (from list_tags) to search within.
        start_date: Only meetings created on or after this ISO 8601 date.
        end_date: Only meetings created on or before this ISO 8601 date.
    """
    from fastapi.concurrency import run_in_threadpool

    from backend.core.db import async_session_maker
    from backend.core.task_dispatch import dispatch_task
    from backend.models.recording import Recording
    from backend.models.speaker import RecordingSpeaker
    from backend.services.context_search import search_context_chunks

    user = get_current_mcp_user()
    text = query.strip()
    if not text:
        raise ToolError("query must not be empty.")
    limit = max(1, min(int(limit), _SEARCH_LIMIT_MAX))

    try:
        task = await dispatch_task(
            "backend.worker.tasks.get_text_embedding_task", args=[text]
        )
        embeddings = await run_in_threadpool(task.get, timeout=30)
        query_embedding = embeddings[0]
    except Exception as exc:  # noqa: BLE001 -- boundary: any dispatch failure means search is unavailable, not broken input
        logger.warning("MCP search_context embedding dispatch failed: %s", exc)
        raise ToolError(
            "Search is unavailable right now: the embedding service did not "
            "respond. Try again shortly."
        ) from exc

    async with async_session_maker() as db:
        scored = await search_context_chunks(
            db,
            user_id=user.id,
            query_embedding=query_embedding,
            limit=limit,
            sources=sources,
            recording_public_ids=recording_ids,
            tag_ids=tag_ids,
            start=_parse_iso_datetime(start_date, "start_date"),
            end=_parse_iso_datetime(end_date, "end_date"),
        )

        recording_ids_hit = {chunk.recording_id for chunk, _ in scored}
        recordings_result = await db.execute(
            select(Recording)
            .where(Recording.id.in_(recording_ids_hit or {0}))
            .options(
                selectinload(Recording.speakers).selectinload(
                    RecordingSpeaker.global_speaker
                )
            )
        )
        recordings = {rec.id: rec for rec in recordings_result.scalars()}

    from backend.api.v1.endpoints.transcripts.helpers import _build_speaker_map

    speaker_maps = {
        rec_id: _build_speaker_map([s for s in rec.speakers if not s.merged_into_id])
        for rec_id, rec in recordings.items()
    }

    results: list[dict[str, Any]] = []
    for chunk, dist in scored:
        rec = recordings.get(chunk.recording_id)
        meta = chunk.meta or {}
        content = chunk.content
        item: dict[str, Any] = {
            "recording_id": rec.public_id if rec else None,
            "recording_name": rec.name if rec else None,
            "distance": round(dist, 4),
        }
        if meta.get("source") == "transcript" or chunk.document_id is None:
            # Resolve raw diarization labels to display names, as chat does.
            for label, name in speaker_maps.get(chunk.recording_id, {}).items():
                if label and name and label != name:
                    content = content.replace(f"{label}:", f"{name}:")
            item["source"] = "transcript"
            item["start"] = meta.get("start")
            item["end"] = meta.get("end")
        else:
            item["source"] = "document"
            item["document_title"] = meta.get("document_title")
            item["page_number"] = meta.get("page_number")
            item["page_title"] = meta.get("page_title")
        item["content"] = content
        results.append(item)

    return {"query": text, "results": results}
