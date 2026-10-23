"""The Owner whose own settings stand in for the installation's.

An installation can have several Owners. Some choices that are install-wide now
were once kept on the Owner's account, such as the AI provider and the
transcription engine, and the code that reads them still falls back to that
account. Every such reader takes the same one: the Owner with the lowest id,
normally the account made at first-run setup. Whether it is active does not
matter, since the choices stay on its row either way.

A query that expects exactly one Owner raises as soon as there are two, which
once kept the API from starting (#639), so read the Owner only through here.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, col, select
from sqlmodel.sql.expression import SelectOfScalar

from backend.models.user import User, UserRole


def primary_owner_query() -> SelectOfScalar[User]:
    return (
        select(User).where(User.role == UserRole.OWNER).order_by(col(User.id)).limit(1)
    )


def get_primary_owner(session: Session) -> User | None:
    return session.exec(primary_owner_query()).first()


async def get_primary_owner_async(db: AsyncSession) -> User | None:
    owner: User | None = (await db.execute(primary_owner_query())).scalars().first()
    return owner
