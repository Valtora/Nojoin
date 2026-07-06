"""Encrypted persistence for per-user subscription-CLI OAuth credentials.

Mirrors ``backend/services/calendar_service/persistence.py``: tokens are
encrypted with ``encrypt_secret`` on write and only ever decrypted in-process
via ``decrypt_credential_tokens``. Never store these tokens in
``User.settings`` (unencrypted JSONB).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.cli_oauth import (
    CliOAuthCredential,
    CliOAuthCredentialStatus,
    CliOAuthProvider,
)
from backend.utils.time import utc_now

DEFAULT_PROVIDER = CliOAuthProvider.CLAUDE_CODE.value


@dataclass(frozen=True)
class CliTokenBundle:
    """Plaintext tokens for one credential write (mirrors calendar TokenBundle).

    ``refresh_token`` left as ``None`` means "unchanged" on an update, so a token
    refresh that returns no new refresh token keeps the stored one.
    """

    access_token: Optional[str]
    refresh_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    oauth_client_id: Optional[str] = None


async def get_credential(
    db: AsyncSession,
    user_id: int,
    provider: str = DEFAULT_PROVIDER,
) -> Optional[CliOAuthCredential]:
    """Return the user's credential row for ``provider``, or ``None``."""
    statement = select(CliOAuthCredential).where(
        CliOAuthCredential.user_id == user_id,
        CliOAuthCredential.provider == provider,
    )
    return (await db.execute(statement)).scalar_one_or_none()


async def upsert_credential(
    db: AsyncSession,
    *,
    user_id: int,
    tokens: CliTokenBundle,
    provider: str = DEFAULT_PROVIDER,
    status: str = CliOAuthCredentialStatus.ACTIVE.value,
) -> CliOAuthCredential:
    """Create or update a credential, encrypting tokens at rest.

    The refresh token is only overwritten when ``tokens.refresh_token`` is
    provided, so a token refresh that returns no new refresh token leaves the
    stored one intact (same convention as the calendar connection upsert).
    """
    credential = await get_credential(db, user_id, provider)
    if credential is None:
        credential = CliOAuthCredential(user_id=user_id, provider=provider)

    credential.access_token_encrypted = encrypt_secret(tokens.access_token)
    if tokens.refresh_token is not None:
        credential.refresh_token_encrypted = encrypt_secret(tokens.refresh_token)
    credential.token_expires_at = tokens.token_expires_at
    credential.oauth_client_id = tokens.oauth_client_id
    credential.status = status
    credential.last_refreshed_at = utc_now()
    db.add(credential)
    await db.commit()
    await db.refresh(credential)
    return credential


async def delete_credential(
    db: AsyncSession,
    user_id: int,
    provider: str = DEFAULT_PROVIDER,
) -> bool:
    """Delete the user's credential row. Returns True if a row was removed.

    Wiping the per-user ``CLAUDE_CONFIG_DIR`` on revoke is handled by the auth
    endpoint (M2); this only removes the encrypted DB row.
    """
    credential = await get_credential(db, user_id, provider)
    if credential is None:
        return False
    await db.delete(credential)
    await db.commit()
    return True


def decrypt_credential_tokens(
    credential: CliOAuthCredential,
) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(access_token, refresh_token)`` decrypted for in-process use."""
    return (
        decrypt_secret(credential.access_token_encrypted),
        decrypt_secret(credential.refresh_token_encrypted),
    )
