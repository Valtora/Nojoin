"""Per-user CLI OAuth (subscription) connect endpoints — Claude and Codex.

Two providers, two connect UXes behind one provider-aware API:

- **claude_code** — Nojoin-driven PKCE (Claude Code has no device-code flow):
  ``/start`` returns the Anthropic authorize URL, the user pastes back the code
  Anthropic shows, and ``/complete`` exchanges it.
- **codex** — RFC 8628 device grant (the Codex CLI supports it natively):
  ``/start`` returns a verification URL + user code, the user approves in a
  browser, and ``/poll`` exchanges once the approval lands.

Tokens are stored encrypted in ``CliOAuthCredential`` (one row per
``(user, provider)``), never in ``User.settings`` and never echoed back.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_admin_user, get_current_user, get_db
from backend.celery_app import celery_app
from backend.models.cli_oauth import (
    CliOAuthCredential,
    CliOAuthCredentialStatus,
    CliOAuthProvider,
    CliUsageDaily,
)
from backend.models.user import User
from backend.services.cli_oauth import codex_oauth, oauth
from backend.services.cli_oauth.persistence import (
    CliTokenBundle,
    delete_credential,
    upsert_credential,
    wipe_user_cli_dir,
)
from backend.utils.time import utc_now

router = APIRouter()
logger = logging.getLogger(__name__)

_STATUS_NOT_CONNECTED = "not_connected"

# Providers a user can connect, in display order (Claude first — the original).
SUPPORTED_CLI_PROVIDERS = (
    CliOAuthProvider.CLAUDE_CODE.value,
    CliOAuthProvider.CODEX.value,
)


def _validate_provider(provider: str) -> str:
    if provider not in SUPPORTED_CLI_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    return provider


# Friendly names for user-facing messages.
_PROVIDER_LABEL = {
    CliOAuthProvider.CLAUDE_CODE.value: "Claude",
    CliOAuthProvider.CODEX.value: "ChatGPT",
}


async def _other_connected_provider(
    db: AsyncSession, user_id: int, provider: str
) -> Optional[str]:
    """A DIFFERENT provider that is already connected (active), else None — used
    to enforce a single connected subscription at a time."""
    result = await db.execute(
        select(CliOAuthCredential.provider).where(
            CliOAuthCredential.user_id == user_id,
            CliOAuthCredential.provider != provider,
            CliOAuthCredential.status == CliOAuthCredentialStatus.ACTIVE.value,
        )
    )
    return result.scalars().first()


async def _await_codex_code(
    user_id: int, timeout: float = 15.0, interval: float = 0.7
) -> Optional[dict]:
    """Wait briefly for the worker login task to publish the verification URL +
    code to Redis. Returns the ``awaiting`` state, or None on failure/timeout."""
    waited = 0.0
    while waited < timeout:
        state = await codex_oauth.read_login_state(user_id)
        if state:
            status = state.get("status")
            if status == codex_oauth.STATUS_AWAITING and state.get("user_code"):
                return state
            if status == codex_oauth.STATUS_FAILED:
                return None
        await asyncio.sleep(interval)
        waited += interval
    return None


# --- request/response models ---


class CliOAuthStartRequest(BaseModel):
    provider: str = CliOAuthProvider.CLAUDE_CODE.value


class CliOAuthStartRead(BaseModel):
    provider: str
    # "paste_code" (Claude authorize URL) or "device" (Codex device grant).
    kind: str
    # paste_code (Claude)
    authorize_url: Optional[str] = None
    # device (Codex)
    verification_uri: Optional[str] = None
    verification_uri_complete: Optional[str] = None
    user_code: Optional[str] = None
    interval: Optional[int] = None
    expires_in: Optional[int] = None


class CliOAuthCompleteRequest(BaseModel):
    code: str
    provider: str = CliOAuthProvider.CLAUDE_CODE.value


class CliOAuthPollRequest(BaseModel):
    provider: str = CliOAuthProvider.CODEX.value


class CliOAuthPollRead(BaseModel):
    provider: str
    # "pending" (keep polling), "connected" (done), or "expired" (restart).
    status: str


class CliOAuthProviderStatus(BaseModel):
    provider: str
    connected: bool
    status: str
    token_expires_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    # Set while a subscription usage limit is still in effect (best-effort reset
    # time); None once past, so the UI clears itself.
    usage_limited_until: Optional[datetime] = None
    # This user's recorded token usage for THIS provider (input + output).
    tokens_7d: Optional[int] = None
    tokens_total: Optional[int] = None


class CliOAuthStatusRead(BaseModel):
    providers: list[CliOAuthProviderStatus]
    # Sum across providers (kept for back-compat); the UI shows per-provider usage
    # from each entry above. None until the first turn.
    tokens_7d: Optional[int] = None
    tokens_total: Optional[int] = None


class CliUsageRow(BaseModel):
    """One user's CLI usage + quota status for the admin overview table."""

    user_id: int
    username: str
    connected: bool
    tokens_total: int
    tokens_7d: int
    tokens_30d: int
    requests_total: int
    last_used_on: Optional[date] = None
    # Latest-known rate-limit reading (advisory; a subscription exposes no
    # absolute remaining quota). utilization is 0.0-1.0 of the current window.
    rate_limit_status: Optional[str] = None
    rate_limit_type: Optional[str] = None
    utilization: Optional[float] = None
    usage_limited_until: Optional[datetime] = None


class CliUsageOverviewRead(BaseModel):
    items: list[CliUsageRow]
    total: int


def _provider_status(
    provider: str, credential: Optional[CliOAuthCredential]
) -> CliOAuthProviderStatus:
    if credential is None:
        return CliOAuthProviderStatus(
            provider=provider,
            connected=False,
            status=_STATUS_NOT_CONNECTED,
        )
    limited_until = credential.usage_limited_until
    return CliOAuthProviderStatus(
        provider=provider,
        connected=credential.status == CliOAuthCredentialStatus.ACTIVE.value,
        status=credential.status,
        token_expires_at=credential.token_expires_at,
        connected_at=credential.last_refreshed_at,
        usage_limited_until=(
            limited_until if limited_until and limited_until > utc_now() else None
        ),
    )


async def _provider_usage(db: AsyncSession, user_id: int) -> dict[str, dict]:
    """This user's token usage (input + output) per provider, over the 7-day and
    all-time windows, keyed by provider value."""
    today = utc_now().date()
    seven = today - timedelta(days=6)
    tokens = CliUsageDaily.input_tokens + CliUsageDaily.output_tokens
    stmt = (
        select(
            CliUsageDaily.provider,
            func.coalesce(func.sum(tokens), 0),
            func.coalesce(
                func.sum(case((CliUsageDaily.usage_date >= seven, tokens), else_=0)), 0
            ),
        )
        .where(CliUsageDaily.user_id == user_id)
        .group_by(CliUsageDaily.provider)
    )
    result = await db.execute(stmt)
    return {
        provider: {"tokens_total": int(total or 0), "tokens_7d": int(t7 or 0)}
        for provider, total, t7 in result.all()
    }


async def _full_status(db: AsyncSession, user_id: int) -> CliOAuthStatusRead:
    """Per-provider connection status + per-provider token usage."""
    credentials = (
        (
            await db.execute(
                select(CliOAuthCredential).where(CliOAuthCredential.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    cred_by_provider = {credential.provider: credential for credential in credentials}
    usage = await _provider_usage(db, user_id)

    providers: list[CliOAuthProviderStatus] = []
    for provider in SUPPORTED_CLI_PROVIDERS:
        entry = _provider_status(provider, cred_by_provider.get(provider))
        provider_usage = usage.get(provider)
        if provider_usage is not None:
            entry.tokens_7d = provider_usage["tokens_7d"]
            entry.tokens_total = provider_usage["tokens_total"]
        providers.append(entry)

    status = CliOAuthStatusRead(providers=providers)
    if usage:
        status.tokens_7d = sum(u["tokens_7d"] for u in usage.values())
        status.tokens_total = sum(u["tokens_total"] for u in usage.values())
    return status


async def _usage_by_user(
    db: AsyncSession, user_ids: Optional[list[int]] = None
) -> dict[int, dict]:
    """Per-user token-usage aggregates (input + output) over 7-day, 30-day, and
    all-time windows, summed in the DB. Restrict to ``user_ids`` for the
    self-view; omit it to cover every user for the admin overview."""
    today = utc_now().date()
    seven = today - timedelta(days=6)
    thirty = today - timedelta(days=29)
    tokens = CliUsageDaily.input_tokens + CliUsageDaily.output_tokens
    stmt = select(
        CliUsageDaily.user_id,
        func.coalesce(func.sum(tokens), 0),
        func.coalesce(
            func.sum(case((CliUsageDaily.usage_date >= seven, tokens), else_=0)), 0
        ),
        func.coalesce(
            func.sum(case((CliUsageDaily.usage_date >= thirty, tokens), else_=0)), 0
        ),
        func.coalesce(func.sum(CliUsageDaily.request_count), 0),
        func.max(CliUsageDaily.usage_date),
    ).group_by(CliUsageDaily.user_id)
    if user_ids is not None:
        if not user_ids:
            return {}
        stmt = stmt.where(CliUsageDaily.user_id.in_(user_ids))
    result = await db.execute(stmt)
    out: dict[int, dict] = {}
    for uid, total, t7, t30, requests, last_used in result.all():
        out[uid] = {
            "tokens_total": int(total or 0),
            "tokens_7d": int(t7 or 0),
            "tokens_30d": int(t30 or 0),
            "requests_total": int(requests or 0),
            "last_used_on": last_used,
        }
    return out


def _usage_row(
    user: User,
    agg: Optional[dict],
    credential: Optional[CliOAuthCredential],
) -> CliUsageRow:
    agg = agg or {}
    limited_until = credential.usage_limited_until if credential else None
    return CliUsageRow(
        user_id=user.id,
        username=user.username,
        connected=bool(
            credential and credential.status == CliOAuthCredentialStatus.ACTIVE.value
        ),
        tokens_total=agg.get("tokens_total", 0),
        tokens_7d=agg.get("tokens_7d", 0),
        tokens_30d=agg.get("tokens_30d", 0),
        requests_total=agg.get("requests_total", 0),
        last_used_on=agg.get("last_used_on"),
        rate_limit_status=credential.last_rate_limit_status if credential else None,
        rate_limit_type=credential.last_rate_limit_type if credential else None,
        utilization=credential.last_utilization if credential else None,
        usage_limited_until=(
            limited_until if limited_until and limited_until > utc_now() else None
        ),
    )


@router.get("/status", response_model=CliOAuthStatusRead)
async def get_cli_oauth_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CliOAuthStatusRead:
    return await _full_status(db, current_user.id)


@router.get("/admin/usage", response_model=CliUsageOverviewRead)
async def get_cli_usage_overview(
    skip: int = 0,
    limit: int = 25,
    search: str = "",
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> CliUsageOverviewRead:
    """Admin-only per-user CLI token usage + rate-limit status.

    Lists users who have a CLI OAuth credential or any recorded usage. Token sums
    are aggregated in the DB; a self-hosted install has few such users, so the
    candidate list is sorted and paginated in Python. A user with both providers
    connected shows a single row (usage is summed across providers; the displayed
    rate-limit reading is whichever credential row is iterated last)."""
    limit = max(1, min(limit, 100))
    skip = max(0, skip)

    usage = await _usage_by_user(db)
    credentials = (await db.execute(select(CliOAuthCredential))).scalars().all()
    cred_by_user = {credential.user_id: credential for credential in credentials}

    candidate_ids = set(usage) | set(cred_by_user)
    if not candidate_ids:
        return CliUsageOverviewRead(items=[], total=0)

    user_stmt = select(User).where(User.id.in_(candidate_ids))
    term = search.strip()
    if term:
        user_stmt = user_stmt.where(User.username.ilike(f"%{term}%"))
    users = list((await db.execute(user_stmt)).scalars().all())

    # Highest consumers first; ties broken by username for a stable order.
    users.sort(
        key=lambda u: (
            -usage.get(u.id, {}).get("tokens_total", 0),
            u.username.lower(),
        )
    )
    total = len(users)
    page = users[skip : skip + limit]
    items = [_usage_row(u, usage.get(u.id), cred_by_user.get(u.id)) for u in page]
    return CliUsageOverviewRead(items=items, total=total)


@router.post("/start", response_model=CliOAuthStartRead)
async def start_cli_oauth(
    payload: CliOAuthStartRequest = CliOAuthStartRequest(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CliOAuthStartRead:
    provider = _validate_provider(payload.provider)

    # One subscription at a time: refuse to start a second provider's sign-in
    # while another is connected, so the user disconnects the first.
    other = await _other_connected_provider(db, current_user.id, provider)
    if other is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Disconnect your {_PROVIDER_LABEL.get(other, other)} subscription "
                "first — only one can be connected at a time."
            ),
        )

    if provider == CliOAuthProvider.CODEX.value:
        # Codex connect is driven by the codex CLI in worker-io (OpenAI's device
        # flow is an undocumented, header-gated protocol — see ADR-0002). Dispatch
        # the login task, then wait briefly for it to publish the URL + code.
        await codex_oauth.clear_login_state(current_user.id)
        celery_app.send_task(
            "backend.worker.tasks.codex_device_login_task",
            args=[current_user.id],
        )
        grant = await _await_codex_code(current_user.id)
        if grant is None:
            logger.warning(
                "Codex device login produced no code for user %s.", current_user.id
            )
            raise HTTPException(
                status_code=502,
                detail="Could not start ChatGPT sign-in. Please try again.",
            )
        logger.info("Started Codex device login for user %s.", current_user.id)
        return CliOAuthStartRead(
            provider=provider,
            kind="device",
            verification_uri=grant.get("verification_uri"),
            user_code=grant.get("user_code"),
        )

    # claude_code: Nojoin-driven PKCE (paste-code)
    verifier, challenge = oauth.generate_pkce()
    state = oauth.generate_state()
    await oauth.store_pending_pkce(current_user.id, verifier, state)
    logger.info("Started CLI OAuth PKCE flow for user %s.", current_user.id)
    return CliOAuthStartRead(
        provider=provider,
        kind="paste_code",
        authorize_url=oauth.build_authorize_url(challenge, state),
    )


@router.post("/complete", response_model=CliOAuthStatusRead)
async def complete_cli_oauth(
    payload: CliOAuthCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CliOAuthStatusRead:
    provider = _validate_provider(payload.provider)
    if provider != CliOAuthProvider.CLAUDE_CODE.value:
        raise HTTPException(
            status_code=400,
            detail="This provider uses device sign-in; poll to complete it.",
        )

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
        raise HTTPException(
            status_code=400, detail="Sign-in state mismatch. Start again."
        )

    try:
        tokens = await oauth.exchange_code(code, pending["verifier"], pending["state"])
    except oauth.CliOAuthExchangeError as exc:
        # The code is single-use and expires within ~60s; the usual cause is a
        # stale/rejected code. Keep the client message generic.
        logger.warning(
            "CLI OAuth exchange failed for user %s: %s", current_user.id, exc
        )
        raise HTTPException(
            status_code=400,
            detail="Could not complete sign-in. The code may have expired — start again.",
        )

    expires_at = (
        utc_now() + timedelta(seconds=tokens.expires_in) if tokens.expires_in else None
    )
    await upsert_credential(
        db,
        user_id=current_user.id,
        tokens=CliTokenBundle(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            token_expires_at=expires_at,
        ),
        provider=provider,
        status=CliOAuthCredentialStatus.ACTIVE.value,
    )
    logger.info("Completed CLI OAuth connect for user %s.", current_user.id)
    return await _full_status(db, current_user.id)


@router.post("/poll", response_model=CliOAuthPollRead)
async def poll_cli_oauth(
    payload: CliOAuthPollRequest = CliOAuthPollRequest(),
    current_user: User = Depends(get_current_user),
) -> CliOAuthPollRead:
    """Poll the Codex device login. Returns pending/connected/expired.

    Reads the state the worker login task publishes to Redis. On ``connected``
    the task has already stored the credential; the browser then refetches
    ``/status`` (which reflects it)."""
    provider = _validate_provider(payload.provider)
    if provider != CliOAuthProvider.CODEX.value:
        raise HTTPException(
            status_code=400,
            detail="This provider uses paste-code sign-in; complete it instead.",
        )

    state = await codex_oauth.read_login_state(current_user.id)
    if not state:
        return CliOAuthPollRead(provider=provider, status="expired")
    status = state.get("status")
    if status == codex_oauth.STATUS_CONNECTED:
        await codex_oauth.clear_login_state(current_user.id)
        logger.info("Codex device connect completed for user %s.", current_user.id)
        return CliOAuthPollRead(provider=provider, status="connected")
    if status == codex_oauth.STATUS_FAILED:
        await codex_oauth.clear_login_state(current_user.id)
        return CliOAuthPollRead(provider=provider, status="expired")
    return CliOAuthPollRead(provider=provider, status="pending")


@router.delete("/token", response_model=CliOAuthStatusRead)
async def disconnect_cli_oauth(
    provider: str = CliOAuthProvider.CLAUDE_CODE.value,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CliOAuthStatusRead:
    provider = _validate_provider(provider)
    await delete_credential(db, current_user.id, provider)
    # Wipe the per-user CLI working dir so no subprocess scratch survives revoke.
    # Scratch (and any injected auth) is re-materialised per inference, so wiping
    # it while another provider stays connected is harmless.
    wipe_user_cli_dir(current_user.id)
    logger.info("Disconnected CLI OAuth (%s) for user %s.", provider, current_user.id)
    return await _full_status(db, current_user.id)
