"""Per-user CLI OAuth (Claude subscription) connect endpoints.

Nojoin-driven PKCE OAuth (see docs/plans/cli-oauth-ai-mode.md): ``/start``
generates the PKCE verifier + CSRF state (stashed in Redis with a short TTL) and
returns the Anthropic authorize URL; the user grants access, copies the code
from Anthropic's callback page, and pastes it into a modal; ``/complete``
exchanges the code + verifier for tokens and stores them encrypted in
``CliOAuthCredential`` (never in ``User.settings``, never echoed back).

There is no device-code flow in Claude Code and server-initiated browser OAuth
is unsupported, so Nojoin performs the PKCE exchange itself against the public
Claude Code OAuth client. The exchange yields an ~8h access token + rotating
refresh token; on first use the manager refreshes on demand.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.models.cli_oauth import (
    CliOAuthCredential,
    CliOAuthCredentialStatus,
    CliOAuthProvider,
)
from backend.models.user import User
from backend.services.cli_oauth import oauth
from backend.services.cli_oauth.persistence import (
    CliTokenBundle,
    delete_credential,
    get_credential,
    upsert_credential,
)
from backend.utils.time import utc_now

router = APIRouter()
logger = logging.getLogger(__name__)

_STATUS_NOT_CONNECTED = "not_connected"


class CliOAuthStartRead(BaseModel):
    authorize_url: str


class CliOAuthCompleteRequest(BaseModel):
    code: str


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


@router.post("/start", response_model=CliOAuthStartRead)
async def start_cli_oauth(
    current_user: User = Depends(get_current_user),
) -> CliOAuthStartRead:
    verifier, challenge = oauth.generate_pkce()
    state = oauth.generate_state()
    await oauth.store_pending_pkce(current_user.id, verifier, state)
    logger.info("Started CLI OAuth PKCE flow for user %s.", current_user.id)
    return CliOAuthStartRead(authorize_url=oauth.build_authorize_url(challenge, state))


@router.post("/complete", response_model=CliOAuthStatusRead)
async def complete_cli_oauth(
    payload: CliOAuthCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CliOAuthStatusRead:
    pending = await oauth.pop_pending_pkce(current_user.id)
    if not pending:
        raise HTTPException(
            status_code=400,
            detail="No sign-in in progress, or it expired. Start again.",
        )

    code, pasted_state = oauth.parse_pasted_code(payload.code)
    if not code:
        raise HTTPException(status_code=400, detail="No authorization code found.")
    if pasted_state and pasted_state != pending["state"]:
        raise HTTPException(status_code=400, detail="Sign-in state mismatch. Start again.")

    try:
        tokens = await oauth.exchange_code(code, pending["verifier"], pending["state"])
    except oauth.CliOAuthExchangeError as exc:
        # The code is single-use and expires within ~60s; the usual cause is a
        # stale/rejected code. Keep the client message generic.
        logger.warning("CLI OAuth exchange failed for user %s: %s", current_user.id, exc)
        raise HTTPException(
            status_code=400,
            detail="Could not complete sign-in. The code may have expired — start again.",
        )

    expires_at = (
        utc_now() + timedelta(seconds=tokens.expires_in)
        if tokens.expires_in
        else None
    )
    credential = await upsert_credential(
        db,
        user_id=current_user.id,
        tokens=CliTokenBundle(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_expires_at=expires_at,
        ),
        status=CliOAuthCredentialStatus.ACTIVE.value,
    )
    logger.info("Completed CLI OAuth connect for user %s.", current_user.id)
    return _status_from_credential(credential)


@router.delete("/token", response_model=CliOAuthStatusRead)
async def disconnect_cli_oauth(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CliOAuthStatusRead:
    await delete_credential(db, current_user.id)
    logger.info("Disconnected CLI OAuth for user %s.", current_user.id)
    return _status_from_credential(None)
