"""Read-only MCP tool surface for Nojoin.

Every tool delegates to the same endpoint coroutines and helpers the REST
API uses (ownership checks, canonical-transcript projection, speaker-name
resolution), so the MCP surface can never drift from what the web client
shows. All tools are scoped to the authenticated user resolved by
:class:`backend.mcp_server.auth.MCPAuthMiddleware`.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Icon
from sqlalchemy.orm import selectinload
from sqlmodel import select
from starlette.types import ASGIApp

from backend.mcp_server.auth import MCPAuthMiddleware, get_current_mcp_user
from backend.utils.config_manager import get_trusted_web_origin

logger = logging.getLogger(__name__)

MCP_SERVER_INSTRUCTIONS = (
    "Read-only access to the user's Nojoin meeting library: recordings, "
    "transcripts, AI meeting notes, and tags. Recording identifiers are the "
    "string `id` values returned by list_recordings."
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


def _parse_iso_datetime(value: Optional[str], field_name: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be an ISO 8601 date or datetime, got: {value!r}"
        ) from exc


def _compact_recording(recording: Any) -> dict[str, Any]:
    speakers = [
        speaker.local_name
        or (speaker.global_speaker.name if speaker.global_speaker else None)
        or speaker.name
        or speaker.diarization_label
        for speaker in recording.speakers
        if not speaker.merged_into_id
    ]
    return {
        "id": recording.id,
        "name": recording.name,
        "created_at": recording.created_at.isoformat(),
        "duration_seconds": recording.duration_seconds,
        "status": str(recording.status.value)
        if hasattr(recording.status, "value")
        else str(recording.status),
        "tags": [tag.name for tag in recording.tags],
        "speakers": speakers,
        "is_archived": recording.is_archived,
    }


@mcp.tool()
async def list_recordings(
    limit: int = 20,
    skip: int = 0,
    query: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict[str, Any]]:
    """List the user's meeting recordings, newest first.

    Archived and deleted recordings are excluded.

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
    from backend.api.v1.endpoints.recordings.routes_query import (
        list_recordings as api_list_recordings,
    )
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    limit = max(1, min(int(limit), 100))

    async with async_session_maker() as db:
        results = await api_list_recordings(
            skip=max(0, int(skip)),
            limit=limit,
            q=query,
            start_date=_parse_iso_datetime(start_date, "start_date"),
            end_date=_parse_iso_datetime(end_date, "end_date"),
            speaker_ids=None,
            tag_ids=None,
            include_archived=False,
            include_deleted=False,
            only_archived=False,
            only_deleted=False,
            status_filters=None,
            user_filter=None,
            db=db,
            current_user=user,
        )
    return [_compact_recording(recording) for recording in results]


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
async def list_tags() -> list[dict[str, Any]]:
    """List the user's tags. Tag names can be used with list_recordings' query."""
    from backend.api.v1.endpoints.tags import read_tags
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    async with async_session_maker() as db:
        tags = await read_tags(db=db, current_user=user)
    return [{"id": tag.id, "name": tag.name} for tag in tags]


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
