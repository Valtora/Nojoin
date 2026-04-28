import logging

from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.recording import Recording, generate_meeting_uid, generate_public_id

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


async def ensure_recording_public_ids(session: AsyncSession) -> int:
    statement = select(Recording).where(
        or_(Recording.public_id.is_(None), Recording.public_id == "")
    )
    result = await session.execute(statement)
    recordings = result.scalars().all()

    for recording in recordings:
        recording.public_id = generate_public_id()
        session.add(recording)

    if recordings:
        await session.commit()
        logger.info("Backfilled public_id for %s recording(s).", len(recordings))

    return len(recordings)


async def get_recording_by_public_id(
    session: AsyncSession,
    public_id: str,
    *,
    user_id: int | None = None,
    options: tuple | None = None,
) -> Recording | None:
    statement = select(Recording).where(Recording.public_id == public_id)

    if user_id is not None:
        statement = statement.where(Recording.user_id == user_id)

    for option in options or ():
        statement = statement.options(option)

    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def get_recordings_by_public_ids(
    session: AsyncSession,
    public_ids: list[str],
    *,
    user_id: int,
) -> list[Recording]:
    if not public_ids:
        return []

    statement = select(Recording).where(
        Recording.public_id.in_(public_ids),
        Recording.user_id == user_id,
    )
    result = await session.execute(statement)
    return result.scalars().all()
