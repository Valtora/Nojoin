"""Owner-scoped meeting history for a person in People."""

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.models.speaker import GlobalSpeaker
from backend.models.speaker_public import PersonMeetingsRead
from backend.models.user import User
from backend.services.person_meetings import person_meetings

from .router import router


@router.get("/{speaker_id}/meetings", response_model=PersonMeetingsRead)
async def get_person_meetings(
    speaker_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PersonMeetingsRead:
    assert current_user.id is not None
    # Selecting scalars avoids loading the person's voiceprint or tag relations.
    result = await db.execute(
        select(GlobalSpeaker.name).where(
            GlobalSpeaker.id == speaker_id, GlobalSpeaker.user_id == current_user.id
        )
    )
    name = result.scalar_one_or_none()
    if name is None:
        raise HTTPException(status_code=404, detail="Speaker not found")
    return await person_meetings(
        db, speaker_id=speaker_id, user_id=current_user.id, person_name=name
    )
