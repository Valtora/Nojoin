"""Reads of ``user_identities``: groups, display names, SSO-created accounts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.models.user import User
from backend.models.user_identity import UserIdentity

# Cached on the User instance for the request, so one request that checks
# access several times reads the groups once.
_GROUPS_ATTRIBUTE = "_nojoin_identity_groups"


async def identity_of(db: AsyncSession, user_id: int) -> Optional[UserIdentity]:
    result = await db.execute(
        select(UserIdentity).where(UserIdentity.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def groups_of(db: AsyncSession, user: User) -> list[str]:
    """The provider groups ``user`` had at their last sign-in. Empty for local
    accounts that were never linked."""
    cached = user.__dict__.get(_GROUPS_ATTRIBUTE)
    if cached is not None:
        return cached
    groups: list[str] = []
    if user.id is not None:
        result = await db.execute(
            select(UserIdentity.groups).where(UserIdentity.user_id == user.id)
        )
        stored = result.scalar_one_or_none() or []
        groups = [group for group in stored if isinstance(group, str) and group]
    user.__dict__[_GROUPS_ATTRIBUTE] = groups
    return groups


def forget_cached_groups(user: User) -> None:
    user.__dict__.pop(_GROUPS_ATTRIBUTE, None)


async def display_names(
    db: AsyncSession, user_ids: Iterable[int]
) -> dict[int, Optional[str]]:
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return {}
    result = await db.execute(
        select(UserIdentity.user_id, UserIdentity.display_name).where(
            col(UserIdentity.user_id).in_(ids)
        )
    )
    return dict(result.all())


async def has_no_password(db: AsyncSession, user_id: int) -> bool:
    """Whether the account was created by the identity provider."""
    result = await db.execute(
        select(UserIdentity.created_account).where(UserIdentity.user_id == user_id)
    )
    return bool(result.scalar_one_or_none())
