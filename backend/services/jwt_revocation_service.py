"""Helpers for managing JWT revocation state.

Provides utilities to:
- bump a user's ``token_version`` (kill-switch for all that user's revocable JWTs),
- record a single ``jti`` in the revoked JWT denylist (surgical revocation),
- prune expired revoked JWT rows opportunistically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.revoked_jwt import RevokedJwt
from backend.models.user import User
from backend.utils.time import utc_now


REVOKABLE_TOKEN_TYPES = frozenset({"session", "api"})


async def bump_user_token_version(
    db: AsyncSession,
    user: User,
    *,
    commit: bool = True,
) -> int:
    """Increment ``user.token_version`` and return the new value.

    All previously issued revocable JWTs for this user become invalid because
    their ``tv`` claim no longer matches.
    """
    new_value = (user.token_version or 0) + 1
    await db.execute(
        update(User).where(User.id == user.id).values(token_version=new_value)
    )
    user.token_version = new_value
    if commit:
        await db.commit()
        await db.refresh(user)
    return new_value


def _payload_expiry(payload: dict[str, Any]) -> datetime:
    exp = payload.get("exp")
    if isinstance(exp, datetime):
        if exp.tzinfo is not None:
            exp = exp.astimezone(timezone.utc).replace(tzinfo=None)
        return exp
    if isinstance(exp, (int, float)):
        return datetime.fromtimestamp(int(exp), tz=timezone.utc).replace(tzinfo=None)
    # Fallback: treat as already expired so the row is harmless.
    return utc_now()


async def revoke_jwt_by_payload(
    db: AsyncSession,
    payload: dict[str, Any],
    user: User,
    *,
    reason: Optional[str] = None,
    commit: bool = True,
) -> bool:
    """Insert a row into ``revoked_jwts`` for the given decoded payload.

    No-op (returns False) if the token has no ``jti`` claim or its type is
    not revocable. Idempotent on duplicate ``jti``.
    """
    jti = payload.get("jti")
    token_type = payload.get("token_type")
    if not isinstance(jti, str) or not jti:
        return False
    if token_type not in REVOKABLE_TOKEN_TYPES:
        return False

    expires_at = _payload_expiry(payload)
    if expires_at <= utc_now():
        return False

    existing = await db.execute(select(RevokedJwt).where(RevokedJwt.jti == jti))
    if existing.scalar_one_or_none() is not None:
        return False

    db.add(
        RevokedJwt(
            jti=jti,
            user_id=user.id,
            token_type=token_type,
            expires_at=expires_at,
            revoked_at=utc_now(),
            reason=reason,
        )
    )
    try:
        if commit:
            await db.commit()
        else:
            await db.flush()
    except IntegrityError:
        await db.rollback()
        return False
    return True


async def revoke_jti(
    db: AsyncSession,
    *,
    jti: str,
    user_id: int,
    token_type: str,
    expires_at: datetime,
    reason: Optional[str] = None,
    commit: bool = True,
) -> bool:
    if token_type not in REVOKABLE_TOKEN_TYPES:
        return False
    if expires_at <= utc_now():
        return False

    existing = await db.execute(select(RevokedJwt).where(RevokedJwt.jti == jti))
    if existing.scalar_one_or_none() is not None:
        return False

    db.add(
        RevokedJwt(
            jti=jti,
            user_id=user_id,
            token_type=token_type,
            expires_at=expires_at,
            revoked_at=utc_now(),
            reason=reason,
        )
    )
    try:
        if commit:
            await db.commit()
        else:
            await db.flush()
    except IntegrityError:
        await db.rollback()
        return False
    return True


async def prune_expired_revoked_jwts(
    db: AsyncSession,
    *,
    commit: bool = True,
) -> int:
    """Delete rows whose ``expires_at`` is in the past. Returns row count."""
    result = await db.execute(
        delete(RevokedJwt).where(RevokedJwt.expires_at < utc_now())
    )
    if commit:
        await db.commit()
    return int(result.rowcount or 0)


async def list_user_active_revoked_jwts(
    db: AsyncSession,
    user_id: int,
) -> list[RevokedJwt]:
    result = await db.execute(
        select(RevokedJwt).where(
            RevokedJwt.user_id == user_id,
            RevokedJwt.expires_at >= utc_now(),
        )
    )
    return list(result.scalars().all())
