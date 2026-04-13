import logging

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.recording import Recording, generate_meeting_uid

logger = logging.getLogger(__name__)


async def ensure_recording_meeting_uids(session: AsyncSession) -> int:
    statement = select(Recording).where(
        or_(Recording.meeting_uid.is_(None), Recording.meeting_uid == "")
    )
    result = await session.execute(statement)
    recordings = result.scalars().all()

    for recording in recordings:
        recording.meeting_uid = generate_meeting_uid()
        session.add(recording)

    if recordings:
        await session.commit()
        logger.info("Backfilled meeting_uid for %s recording(s).", len(recordings))

    return len(recordings)