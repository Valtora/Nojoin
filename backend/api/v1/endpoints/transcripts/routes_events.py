"""Authenticated transcript wake-ups; all transcript data uses ordinary reads."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from starlette.types import Receive, Scope, Send

from backend.api.deps import get_current_user, reusable_oauth2
from backend.core.db import get_session
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services.transcript_events import TranscriptEventStream

from .helpers import _get_owned_recording
from .router import router


async def get_stream_db() -> AsyncIterator[AsyncSession]:
    # Explicitly close the inner generator even when authentication or the
    # endpoint raises, rather than deferring its finaliser to async-generator GC.
    async with aclosing(get_session()) as sessions:
        async for session in sessions:
            yield session


async def get_stream_current_user(
    request: Request,
    db: AsyncSession = Depends(get_stream_db, scope="function"),
    token: str | None = Depends(reusable_oauth2),
) -> User:
    return await get_current_user(request, db, token)


class TranscriptEventResponse(StreamingResponse):
    stream: TranscriptEventStream

    def __init__(self, stream: TranscriptEventStream, sequence: int) -> None:
        self.stream = stream
        super().__init__(
            stream.frames(sequence),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Include disconnects while sending headers, before the generator
            # gets its first turn and can enter its own cleanup block.
            await self.stream.close()


@router.get("/{recording_id}/events")
async def stream_transcript_events(
    recording_id: str,
    db: AsyncSession = Depends(get_stream_db, scope="function"),
    current_user: User = Depends(get_stream_current_user, scope="function"),
) -> StreamingResponse:
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    internal_id = recording.id
    await db.close()
    stream = TranscriptEventStream(internal_id)
    try:
        await stream.open()
        try:
            result = await db.execute(
                select(Transcript.change_sequence).where(
                    Transcript.recording_id == internal_id
                )
            )
            sequence = result.scalar_one_or_none()
        finally:
            await db.close()
        if sequence is None:
            raise HTTPException(status_code=404, detail="Transcript not found")
    except (RedisError, OSError, TimeoutError) as exc:
        await stream.close()
        raise HTTPException(
            status_code=503, detail="Transcript stream unavailable"
        ) from exc
    except BaseException:
        await stream.close()
        raise
    return TranscriptEventResponse(stream, sequence)
