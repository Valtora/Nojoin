"""OpenID Connect sign-in routes.

``GET /login/methods`` tells the login page what to offer.
``GET /login/oidc/start`` begins a sign-in, ``POST /login/oidc/link`` begins
linking an identity to the signed-in account, and ``GET /login/oidc/callback``
finishes both. The callback ends a sign-in by issuing the same session cookie
as password login (:func:`issue_browser_session`), so nothing after sign-in
knows or cares how the user authenticated.

Linking starts with a POST, behind the trusted-origin check that every cookie
request with an unsafe method passes, and asks the provider to re-authenticate.
A GET would let another site start a link in the victim's browser; if that
browser were signed in at the provider as the attacker (login CSRF there), the
attacker's identity would end up linked to the victim's account.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints.login import issue_browser_session
from backend.core.oidc_config import (
    PasswordLoginPolicy,
    get_oidc_settings,
    get_password_login_policy,
)
from backend.models.user import User
from backend.services import oidc_accounts, oidc_service
from backend.services.oidc_service import OIDCError
from backend.services.user_identities import identity_of
from backend.utils.rate_limit import enforce_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

STATE_COOKIE_NAME = "nojoin_oidc_state"
STATE_COOKIE_PATH = "/api/v1/login/oidc"
CALLBACK_RATE_LIMIT = 30
CALLBACK_RATE_LIMIT_WINDOW_SECONDS = 10 * 60
LINK_DONE_PATH = "/settings/profile"


def _require_settings():
    settings = get_oidc_settings()
    if settings is None:
        raise HTTPException(status_code=404, detail="SSO is not configured")
    return settings


def _set_state_cookie(response, token: str) -> None:
    response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(oidc_service.STATE_TOKEN_LIFETIME.total_seconds()),
        path=STATE_COOKIE_PATH,
    )


def _clear_state_cookie(response) -> None:
    response.delete_cookie(
        key=STATE_COOKIE_NAME,
        httponly=True,
        secure=True,
        samesite="lax",
        path=STATE_COOKIE_PATH,
    )


def _redirect(path: str, **params: str) -> RedirectResponse:
    target = path if not params else f"{path}?{urlencode(params)}"
    return RedirectResponse(target, status_code=303)


@router.get("/login/methods")
async def login_methods() -> dict[str, Any]:
    settings = get_oidc_settings()
    policy = get_password_login_policy()
    return {
        "password": policy is not PasswordLoginPolicy.DISABLED,
        "password_policy": policy.value,
        "oidc": {
            "enabled": settings is not None,
            "display_name": settings.display_name if settings else None,
        },
    }


@router.get("/login/oidc/start")
async def oidc_start(next: Optional[str] = Query(default=None)) -> RedirectResponse:
    settings = _require_settings()
    try:
        start = await oidc_service.begin_login(settings, next_path=next)
    except OIDCError as exc:
        logger.warning("OIDC sign-in could not start (%s): %s", exc.code, exc)
        return _redirect("/login", error=exc.code)
    response = RedirectResponse(start.authorization_url, status_code=303)
    _set_state_cookie(response, start.state_token)
    return response


@router.post("/login/oidc/link")
async def oidc_link(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    settings = _require_settings()
    assert current_user.id is not None
    if await identity_of(db, current_user.id) is not None:
        raise HTTPException(status_code=409, detail="sso_already_linked")
    try:
        start = await oidc_service.begin_login(
            settings,
            next_path=LINK_DONE_PATH,
            mode="link",
            link_user_id=current_user.id,
        )
    except OIDCError as exc:
        logger.warning("OIDC link could not start (%s): %s", exc.code, exc)
        raise HTTPException(status_code=502, detail=exc.code) from exc
    # prompt=login: the provider must authenticate the person now, rather than
    # reuse whatever session the browser happens to hold there.
    response = JSONResponse(
        {"authorization_url": f"{start.authorization_url}&prompt=login"}
    )
    _set_state_cookie(response, start.state_token)
    return response


@router.get("/login/oidc/callback")
async def oidc_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
) -> RedirectResponse:
    settings = _require_settings()
    await enforce_rate_limit(
        request,
        namespace="oidc-callback",
        limit=CALLBACK_RATE_LIMIT,
        window_seconds=CALLBACK_RATE_LIMIT_WINDOW_SECONDS,
        detail="Too many sign-in attempts. Please try again later.",
    )

    pending: Optional[oidc_service.PendingLogin] = None
    try:
        pending = oidc_service.read_state_token(
            request.cookies.get(STATE_COOKIE_NAME), state
        )
        if error:
            raise OIDCError("sso_cancelled", f"Provider returned error={error!r}")
        identity = await oidc_service.complete_login(settings, pending, code)
        user = await oidc_accounts.resolve_user(
            db,
            settings,
            identity,
            mode=pending.mode,
            link_user_id=pending.link_user_id,
        )
    except OIDCError as exc:
        logger.warning("OIDC sign-in failed (%s): %s", exc.code, exc)
        if pending is not None and pending.mode == "link":
            response = _redirect(LINK_DONE_PATH, sso_error=exc.code)
        else:
            response = _redirect("/login", error=exc.code)
        _clear_state_cookie(response)
        return response

    if pending.mode == "link":
        response = _redirect(LINK_DONE_PATH, sso_linked="1")
    else:
        response = _redirect(pending.next_path)
        issue_browser_session(response, user)
    _clear_state_cookie(response)
    return response
