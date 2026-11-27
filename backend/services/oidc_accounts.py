"""Map a verified OpenID Connect identity to a Nojoin account.

An identity is matched by ``(issuer, subject)`` in ``user_identities`` and
nothing else. It is never attached to an existing account because a username
or email matches: many providers let users choose those, and matching on them
would let anyone who can register at the provider take over the account with
that name, the owner's included. A local account is linked to an identity only
by its signed-in user starting the flow in link mode.
"""

from __future__ import annotations

import logging
import re
import secrets
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core import security
from backend.core.oidc_config import OIDCSettings
from backend.models.user import User, UserRole
from backend.models.user_identity import UserIdentity
from backend.services.jwt_revocation_service import bump_user_token_version
from backend.services.oidc_service import OIDCError, VerifiedIdentity
from backend.services.user_identities import forget_cached_groups, identity_of
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)

USERNAME_MAX_LENGTH = 64
_USERNAME_DISALLOWED = re.compile(r"[^A-Za-z0-9._@-]+")


def _username_base(identity: VerifiedIdentity) -> str:
    raw = identity.username or (identity.email or "").split("@")[0] or "user"
    cleaned = _USERNAME_DISALLOWED.sub("-", raw).strip("-.")
    return (cleaned or "user")[: USERNAME_MAX_LENGTH - 4]


async def _free_username(db: AsyncSession, base: str) -> str:
    candidate = base
    for suffix in range(2, 1000):
        existing = await db.execute(select(User.id).where(User.username == candidate))
        if existing.scalar_one_or_none() is None:
            return candidate
        candidate = f"{base}-{suffix}"
    return f"{base}-{secrets.token_hex(4)}"


def _unusable_password_hash() -> str:
    # A hash of a random secret nobody holds. hashed_password stays NOT NULL
    # and every password check against it fails like a wrong password.
    return security.get_password_hash(secrets.token_urlsafe(32))


def _role_from_groups(
    user: User, groups: tuple[str, ...], settings: OIDCSettings
) -> str:
    """The role the provider's groups give ``user``.

    The owner role is never changed from the provider, and when no admin
    groups are configured the role is left as the administrator set it.
    """
    if user.role == UserRole.OWNER or not settings.admin_groups:
        return user.role
    if settings.admin_groups.intersection(groups):
        return UserRole.ADMIN.value
    return UserRole.USER.value


async def _find_by_identity(
    db: AsyncSession, identity: VerifiedIdentity
) -> Optional[UserIdentity]:
    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.issuer == identity.issuer,
            UserIdentity.subject == identity.subject,
        )
    )
    return result.scalar_one_or_none()


async def _link_identity(
    db: AsyncSession,
    identity: VerifiedIdentity,
    *,
    existing: Optional[UserIdentity],
    link_user_id: Optional[int],
) -> tuple[User, UserIdentity]:
    """Attach ``identity`` to the signed-in account that started a link."""
    if link_user_id is None:
        raise OIDCError("sso_state", "Link mode without a signed-in user")
    target = await db.get(User, link_user_id)
    if target is None or not target.is_active:
        raise OIDCError("sso_no_account", "The account to link no longer exists")
    assert target.id is not None
    if existing is not None and existing.user_id != target.id:
        raise OIDCError(
            "sso_identity_in_use",
            "That identity is already linked to another account",
        )
    if existing is not None:
        return target, existing
    if await identity_of(db, target.id) is not None:
        raise OIDCError(
            "sso_already_linked", "This account is linked to another identity"
        )
    logger.info("Linked user %s to an OIDC identity", target.id)
    return target, UserIdentity(
        user_id=target.id,
        issuer=identity.issuer,
        subject=identity.subject,
        created_account=False,
    )


async def _create_account(
    db: AsyncSession, settings: OIDCSettings, identity: VerifiedIdentity
) -> tuple[User, UserIdentity]:
    if not settings.auto_create:
        raise OIDCError("sso_no_account", "No account is linked and auto-create is off")
    # First-run setup creates the owner behind FIRST_RUN_PASSWORD, and only
    # while no account exists. An account made here first would shut setup out
    # and be promoted to Owner at the next api start (ensure_owner_exists).
    if (await db.execute(select(User.id).limit(1))).first() is None:
        raise OIDCError(
            "sso_setup_required", "First-run setup has not created the owner yet"
        )
    user = User(
        username=await _free_username(db, _username_base(identity)),
        hashed_password=_unusable_password_hash(),
        role=UserRole.USER.value,
    )
    db.add(user)
    await db.flush()
    assert user.id is not None
    logger.info("Created an account from an OIDC sign-in: %s", user.username)
    return user, UserIdentity(
        user_id=user.id,
        issuer=identity.issuer,
        subject=identity.subject,
        created_account=True,
    )


async def resolve_user(
    db: AsyncSession,
    settings: OIDCSettings,
    identity: VerifiedIdentity,
    *,
    mode: str,
    link_user_id: Optional[int],
) -> User:
    """Find, link or create the account for ``identity`` and refresh it.

    Raises :class:`OIDCError` when the identity may not sign in. Commits.
    """
    if settings.allowed_groups and not settings.allowed_groups.intersection(
        identity.groups
    ):
        raise OIDCError(
            "sso_not_allowed",
            f"Subject {identity.subject!r} is in none of the allowed groups",
        )

    existing = await _find_by_identity(db, identity)
    if mode == "link":
        user, row = await _link_identity(
            db, identity, existing=existing, link_user_id=link_user_id
        )
    elif existing is not None:
        found = await db.get(User, existing.user_id)
        if found is None:
            raise OIDCError("sso_no_account", "The linked account no longer exists")
        user, row = found, existing
    else:
        user, row = await _create_account(db, settings, identity)

    if not user.is_active:
        raise OIDCError("sso_inactive", "The account is deactivated")

    row.email = identity.email
    row.display_name = identity.display_name
    row.groups = list(identity.groups)
    row.last_sign_in_at = utc_now()
    db.add(row)
    forget_cached_groups(user)

    new_role = _role_from_groups(user, identity.groups, settings)
    role_changed = new_role != user.role
    user.role = new_role
    db.add(user)
    await db.flush()
    if role_changed:
        # Sessions minted under the old role stop working; the one about to be
        # issued carries the new version.
        await bump_user_token_version(db, user, commit=False)
    await db.commit()
    await db.refresh(user)
    return user
