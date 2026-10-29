"""Owner-checked playback of a fixed prefix of uploaded browser audio."""

from fastapi import Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user_stream, get_db
from backend.models.user import User
from backend.utils.capture_playback import (
    load_capture_snapshot,
    snapshot_range,
    snapshot_spans,
    stream_snapshot,
)

from .helpers import _get_owned_recording
from .router import router


@router.get("/{recording_id}/capture-stream")
async def stream_capture(
    recording_id: str,
    request: Request,
    sequence: int = Query(..., ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user_stream),
) -> StreamingResponse:
    """Serve one WAV representation whose length is pinned by its sequence."""
    assert current_user.id is not None
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    assert recording.id is not None
    snapshot = await load_capture_snapshot(db, recording.id, sequence)
    # All subsequent work uses detached metadata. A slow player must not hold
    # an otherwise idle transaction and connection for its whole response.
    await db.rollback()
    range_header = request.headers.get("range")
    start, end = snapshot_range(range_header, snapshot.size)
    spans = snapshot_spans(snapshot, start, end)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store",
    }
    if range_header is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{snapshot.size}"
    return StreamingResponse(
        stream_snapshot(snapshot, start, end, spans),
        status_code=206 if range_header is not None else 200,
        headers=headers,
        media_type="audio/wav",
    )
