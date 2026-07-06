"""Per-user CLI OAuth (Claude subscription) connect endpoints.

M2 connect model (see docs/plans/cli-oauth-ai-mode.md): the user runs
``claude setup-token`` on their own machine and pastes the resulting long-lived
``CLAUDE_CODE_OAUTH_TOKEN`` here. There is no device-code flow in Claude Code,
and server-initiated browser OAuth is unsupported/fragile, so this is a
BYOK-style paste that Nojoin stores encrypted (in ``CliOAuthCredential``, never
in ``User.settings``). The token is write-only: it is never logged or returned.

Routing inference through the stored token (materialising it into the subprocess
env) lands in a later milestone; a real validity check happens on first use.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.models.cli_oauth import (
    CliOAuthCredential,
    CliOAuthCredentialStatus,
    CliOAuthProvider,
)
from backend.models.user import User
from backend.services.cli_oauth.persistence import (
    CliTokenBundle,
    delete_credential,
    get_credential,
    upsert_credential,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Lower bound to catch empty/fat-finger pastes without hard-coding a fragile
# token format; the real validity check happens when inference first runs.
_MIN_TOKEN_LENGTH = 20

_STATUS_NOT_CONNECTED = "not_connected"


class CliOAuthTokenUpdate(BaseModel):
    token: str

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        token = (value or "").strip()
        if len(token) < _MIN_TOKEN_LENGTH:
            raise ValueError(
                "Token looks too short. Paste the full output of `claude setup-token`."
            )
        return token


class CliOAuthStatusRead(BaseModel):
    connected: bool
    status: str
    provider: str
    token_expires_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None


def _status_from_credential(
    credential: Optional[CliOAuthCredential],
) -> CliOAuthStatusRead:
    if credential is None:
        return CliOAuthStatusRead(
            connected=False,
            status=_STATUS_NOT_CONNECTED,
            provider=CliOAuthProvider.CLAUDE_CODE.value,
        )
    return CliOAuthStatusRead(
        connected=credential.status == CliOAuthCredentialStatus.ACTIVE.value,
        status=credential.status,
        provider=credential.provider,
        token_expires_at=credential.token_expires_at,
        connected_at=credential.last_refreshed_at,
    )


@router.get("/status", response_model=CliOAuthStatusRead)
async def get_cli_oauth_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CliOAuthStatusRead:
    credential = await get_credential(db, current_user.id)
    return _status_from_credential(credential)


@router.put("/token", response_model=CliOAuthStatusRead)
async def set_cli_oauth_token(
    payload: CliOAuthTokenUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CliOAuthStatusRead:
    credential = await upsert_credential(
        db,
        user_id=current_user.id,
        tokens=CliTokenBundle(access_token=payload.token),
        status=CliOAuthCredentialStatus.ACTIVE.value,
    )
    # Never log the token itself.
    logger.info(
        "Stored CLI OAuth token for user %s (provider=%s).",
        current_user.id,
        credential.provider,
    )
    return _status_from_credential(credential)


@router.delete("/token", response_model=CliOAuthStatusRead)
async def disconnect_cli_oauth(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CliOAuthStatusRead:
    await delete_credential(db, current_user.id)
    logger.info("Disconnected CLI OAuth for user %s.", current_user.id)
    return _status_from_credential(None)
