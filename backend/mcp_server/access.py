"""Which recordings an MCP tool may use: the same levels as the web app.

An assistant connected over MCP acts for its user, so it sees the meetings the
user owns and the ones shared with them, and changes only what the user could
change in the web app (services/recording_access.py). A tool asks for the level
it needs; a meeting the user cannot see is "not found", as for a stranger.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.recording import Recording
from backend.models.user import User
from backend.services.recording_access import (
    Access,
    get_recording_for,
    viewer_owns_library,
)

_TOO_LOW = {
    Access.EDIT: "This meeting is shared with you to view only, so it cannot be changed.",
    Access.OWNER: "Only the meeting's owner can do this.",
}


async def recording_for(
    db: AsyncSession,
    recording_id: str,
    user: User,
    need: Access,
    *,
    options: Optional[Sequence[Any]] = None,
) -> Recording:
    """The recording ``user`` may use at ``need``, or the refusal the tool reports."""
    try:
        recording, level = await get_recording_for(
            db, recording_id, user, need, options=options
        )
    except HTTPException as refusal:
        if refusal.status_code == status.HTTP_403_FORBIDDEN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=_TOO_LOW[need]
            ) from refusal
        raise
    viewer_owns_library.set(level is Access.OWNER)
    return recording
