"""Connecting a Proton account: sign-in, human verification, 2FA and unlock.

Proton has no OAuth, so the user types their credentials into Nojoin, which
hands them to the helper. The browser keeps the password in the modal's
memory and sends it with each step, because the key passphrase can only be
derived once 2FA is done; the server never stores it. Between steps the
partial session (UID and tokens of a session waiting for 2FA) is kept for ten
minutes, encrypted with DATA_ENCRYPTION_KEY and bound to the Nojoin user and
a random flow id.

Kept, encrypted: the session UID, the access and refresh tokens and the
salted key passphrase. Discarded: the password, the mailbox password, the
TOTP code, the verification token and every SRP value.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.core.task_dispatch import dispatch_task_best_effort
from backend.models.calendar import (
    CalendarConnection,
    CalendarProvider,
    CalendarSyncStatus,
)
from backend.models.user import User

from .models_dto import ProviderIdentity, TokenBundle
from .oauth import _pop_oauth_state, _save_oauth_state
from .persistence import _refresh_connection_calendars, _upsert_connection
from .proton_helper import READ_TIMEOUT_SECONDS, HelperError, call_helper
from .proton_session import seal_secrets
from .proton_sync import calendar_records
from .text_utils import _utc_now

logger = logging.getLogger(__name__)
_FLOW_PREFIX = "proton-connect:"
_UNLOCK_TIMEOUT_SECONDS = 60.0
# Helper codes the modal can act on; anything else is "could not connect".
_CONNECT_MESSAGES = {
    "login_failed": "Proton did not accept that username or password.",
    "human_verification_required": "Proton wants to check that you are a person.",
    "two_factor_failed": "Proton did not accept that code. Try the next one.",
    "two_factor_unsupported": (
        "This account's only second factor is a security key, which Nojoin cannot "
        "use. Add an authenticator app in Proton's settings, then connect again."
    ),
    "wrong_mailbox_password": "That mailbox password did not open the account's keys.",
    "rate_limited": "Proton asked Nojoin to slow down. Wait a minute, then try again.",
    "provider_rejected": "Proton refused the sign-in request.",
}


class ProtonConnectError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class ConnectStep:
    """What the modal shows next: ``two_factor``, ``mailbox_password`` or ``done``."""

    step: str
    flow_id: str | None = None
    connection: CalendarConnection | None = None
    warnings: list[str] = field(default_factory=list)


def _connect_error(error: HelperError) -> ProtonConnectError:
    details: dict[str, Any] = {}
    if error.code == "human_verification_required":
        details = {
            "verify_url": error.details.get("verify_url"),
            "token": error.details.get("token"),
            "methods": error.details.get("methods") or [],
        }
    message = _CONNECT_MESSAGES.get(
        error.code, "Nojoin could not connect to Proton. Try again in a few minutes."
    )
    if error.code == "provider_rejected" and error.provider_code is not None:
        # Proton's own code is what a bug report needs; it holds no secret.
        message = f"{message} Proton's code: {error.provider_code}."
    return ProtonConnectError(error.code, message, details)


async def _helper(
    op: str, *, session: dict[str, Any], args: dict[str, Any], timeout: float
) -> dict[str, Any]:
    try:
        return await call_helper(op, session=session, args=args, timeout=timeout)
    except HelperError as error:
        logger.info(
            "Proton connect step %s refused: %s (Proton code %s, HTTP %s)",
            op,
            error.code,
            error.provider_code,
            error.http_status,
        )
        raise _connect_error(error) from error


async def _save_flow(
    user: User, flow: dict[str, Any], flow_id: str | None = None
) -> str:
    flow_id = flow_id or secrets.token_urlsafe(24)
    await _save_oauth_state(
        f"{_FLOW_PREFIX}{flow_id}",
        {
            "user_id": user.id,
            "provider": CalendarProvider.PROTON.value,
            "sealed": encrypt_secret(json.dumps(flow)),
        },
    )
    return flow_id


async def _take_flow(user: User, flow_id: str) -> dict[str, Any]:
    payload = await _pop_oauth_state(f"{_FLOW_PREFIX}{flow_id}")
    if (
        not payload
        or int(payload.get("user_id", -1)) != user.id
        or payload.get("provider") != CalendarProvider.PROTON.value
    ):
        raise ProtonConnectError(
            "flow_expired", "The sign-in expired. Start again from Connect."
        )
    return json.loads(decrypt_secret(payload["sealed"]) or "{}")


def _session_part(session: dict[str, Any]) -> dict[str, Any]:
    return {"uid": session.get("uid"), "access_token": session.get("access_token")}


async def start(
    db: AsyncSession,
    user: User,
    *,
    username: str,
    password: str,
    verification: dict[str, str] | None = None,
) -> ConnectStep:
    result = await _helper(
        "login_start",
        session={},
        args={
            "username": username,
            "password": password,
            "human_verification": verification,
        },
        timeout=READ_TIMEOUT_SECONDS,
    )
    flow = {
        "session": result.get("session") or {},
        "password_mode": int(result.get("password_mode") or 1),
    }
    if result.get("next") == "two_factor":
        return ConnectStep("two_factor", flow_id=await _save_flow(user, flow))
    if flow["password_mode"] == 2:
        return ConnectStep("mailbox_password", flow_id=await _save_flow(user, flow))
    return await _finish(db, user, flow, username=username, password=password)


async def two_factor(  # noqa: PLR0913 - one keyword per form field
    db: AsyncSession,
    user: User,
    flow_id: str,
    *,
    code: str,
    username: str,
    password: str,
) -> ConnectStep:
    flow = await _take_flow(user, flow_id)
    try:
        result = await _helper(
            "login_2fa",
            session=_session_part(flow["session"]),
            args={"code": code},
            timeout=READ_TIMEOUT_SECONDS,
        )
    except ProtonConnectError as error:
        if error.code == "two_factor_failed":
            await _save_flow(user, flow, flow_id)
        raise
    flow["session"] = {**flow["session"], **(result.get("session") or {})}
    if flow["password_mode"] == 2:
        return ConnectStep(
            "mailbox_password", flow_id=await _save_flow(user, flow, flow_id)
        )
    return await _finish(db, user, flow, username=username, password=password)


async def unlock(  # noqa: PLR0913 - one keyword per form field
    db: AsyncSession,
    user: User,
    flow_id: str,
    *,
    username: str,
    password: str,
    mailbox_password: str,
) -> ConnectStep:
    flow = await _take_flow(user, flow_id)
    try:
        return await _finish(
            db,
            user,
            flow,
            username=username,
            password=password,
            mailbox_password=mailbox_password,
        )
    except ProtonConnectError as error:
        if error.code == "wrong_mailbox_password":
            await _save_flow(user, flow, flow_id)
        raise


async def _finish(  # noqa: PLR0913 - one keyword per form field
    db: AsyncSession,
    user: User,
    flow: dict[str, Any],
    *,
    username: str,
    password: str,
    mailbox_password: str | None = None,
) -> ConnectStep:
    """Derive the key passphrase, then save the connection and sync it."""
    session = flow["session"]
    result = await _helper(
        "login_unlock",
        session=_session_part(session),
        args={
            "username": username,
            "password": password,
            "mailbox_password": mailbox_password,
        },
        timeout=_UNLOCK_TIMEOUT_SECONDS,
    )
    unlocked = {**session, **(result.get("session") or {})}
    account = result.get("user") or {}
    expires_in = session.get("expires_in")
    connection = await _upsert_connection(
        db,
        user=user,
        provider=CalendarProvider.PROTON.value,
        identity=ProviderIdentity(
            account_id=str(account.get("id")),
            email=account.get("email"),
            display_name=account.get("display_name") or account.get("name"),
        ),
        tokens=TokenBundle(
            access_token=str(unlocked.get("access_token")),
            refresh_token=unlocked.get("refresh_token"),
            expires_at=_utc_now() + timedelta(seconds=int(expires_in))
            if expires_in
            else None,
            scopes=list(session.get("scopes") or []),
        ),
    )
    connection.provider_secrets_encrypted = seal_secrets(
        str(unlocked.get("uid")), unlocked.get("key_passphrase")
    )
    db.add(connection)
    await db.commit()
    await _refresh_connection_calendars(
        db,
        connection,
        calendar_records(
            {"settings": result.get("settings"), "calendars": result.get("calendars")}
        ),
    )
    await _start_first_sync(db, connection)
    return ConnectStep(
        "done", connection=connection, warnings=list(result.get("warnings") or [])
    )


async def _start_first_sync(db: AsyncSession, connection: CalendarConnection) -> None:
    """Queue the first sync on worker-io and answer at once.

    A first Proton sync reads 25 months in 93-day windows and can take a
    minute or two, so the sign-in answers as soon as the connection is saved
    and the dialog shows the sync's progress. Closing the dialog never stops
    it. If the queue cannot be reached, the account waits for the next
    scheduled sync instead.
    """
    connection.sync_status = CalendarSyncStatus.SYNCING.value
    connection.sync_error = None
    connection.sync_progress_done = 0
    connection.sync_progress_total = None
    db.add(connection)
    await db.commit()
    queued = await dispatch_task_best_effort(
        "backend.worker.tasks.sync_calendar_connection_task",
        args=[connection.id],
        context=f"first sync of connection {connection.id}",
    )
    if queued is None:
        connection.sync_status = CalendarSyncStatus.IDLE.value
        connection.sync_progress_done = None
        db.add(connection)
        await db.commit()


async def end_session(db: AsyncSession, connection: CalendarConnection) -> None:
    """End the Proton session before the connection is deleted. Best effort:
    a session Proton already ended, or one Nojoin cannot read, is fine."""
    from .proton_session import run_op

    try:
        await run_op(db, connection, "logout", {}, timeout=READ_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 -- boundary: the disconnect must still complete
        logger.warning(
            "Ending the Proton session for connection %s failed: %s",
            connection.id,
            getattr(exc, "code", type(exc).__name__),
        )
