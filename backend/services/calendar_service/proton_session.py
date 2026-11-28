"""A Proton connection's session: its secrets, refreshing it, and running ops.

Proton has no OAuth. A connection keeps the session's access and refresh
tokens in the usual token columns and its UID and the salted key passphrase in
``provider_secrets_encrypted``, all encrypted with DATA_ENCRYPTION_KEY.

Refresh tokens are single use. A refresh therefore runs under the account's
lock (the caller's, or its own), and the rotated tokens are committed in a
session of their own before anything else can fail. When the helper reports
``access_token_expired`` before it wrote anything, the session is refreshed
once and the operation retried once.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.calendar import CalendarConnection, CalendarSyncStatus

from .models_dto import UnreadableCalendarConnectionState
from .proton_helper import REFRESH_TIMEOUT_SECONDS, HelperError, call_helper
from .text_utils import _utc_now
from .tool_errors import CalendarToolError
from .write_audit import (
    ConnectionTokenStore,
    advisory_lock,
    connection_lock_key,
)

LOCK_WAIT_SECONDS = 15.0
_REFRESH_AHEAD = timedelta(minutes=2)
RECONNECT_MESSAGE = (
    "This Proton account must be reconnected in Nojoin. Ask the user to "
    "reconnect it in Settings > Integrations > Calendars."
)


def session_secrets(connection: CalendarConnection) -> dict[str, Any]:
    """The decrypted session: uid, access_token, refresh_token, key_passphrase."""
    try:
        extra = json.loads(
            decrypt_secret(connection.provider_secrets_encrypted) or "{}"
        )
        return {
            "uid": extra.get("uid"),
            "key_passphrase": extra.get("key_passphrase"),
            "access_token": decrypt_secret(connection.access_token_encrypted),
            "refresh_token": decrypt_secret(connection.refresh_token_encrypted),
        }
    except ValueError as exc:
        raise UnreadableCalendarConnectionState() from exc


def seal_secrets(uid: str, key_passphrase: str | None) -> str | None:
    return encrypt_secret(json.dumps({"uid": uid, "key_passphrase": key_passphrase}))


def _for_op(secrets: dict[str, Any], op: str) -> dict[str, Any]:
    """Only what the operation needs (PROTOCOL.md, the session table)."""
    if op in {"version", "validate", "login_start"}:
        return {}
    if op == "refresh":
        return {"uid": secrets["uid"], "refresh_token": secrets["refresh_token"]}
    session = {"uid": secrets["uid"], "access_token": secrets["access_token"]}
    if op not in {"login_2fa", "login_unlock", "logout", "list_calendars"}:
        session["key_passphrase"] = secrets["key_passphrase"]
    return session


async def mark_reconnect_required(
    db: AsyncSession, connection: CalendarConnection
) -> None:
    connection.sync_status = CalendarSyncStatus.REAUTHORISATION_REQUIRED.value
    connection.sync_error = (
        "This Proton account needs to be reconnected before it can sync again."
    )
    db.add(connection)
    await db.commit()


async def _refresh_once(
    connection: CalendarConnection, secrets: dict[str, Any]
) -> dict[str, Any]:
    result = await call_helper(
        "refresh", session=_for_op(secrets, "refresh"), timeout=REFRESH_TIMEOUT_SECONDS
    )
    session = result.get("session") or {}
    expires_in = session.get("expires_in")
    expires_at = _utc_now() + timedelta(seconds=int(expires_in)) if expires_in else None
    await ConnectionTokenStore(connection.id or 0).save(
        access_token=session.get("access_token"),
        refresh_token=session.get("refresh_token"),
        expires_at=expires_at,
    )
    secrets = {
        **secrets,
        **{k: session[k] for k in ("access_token", "refresh_token") if session.get(k)},
    }
    connection.access_token_encrypted = encrypt_secret(secrets["access_token"])
    connection.refresh_token_encrypted = encrypt_secret(secrets["refresh_token"])
    connection.token_expires_at = expires_at
    return secrets


async def refresh_session(
    db: AsyncSession, connection: CalendarConnection, secrets: dict[str, Any]
) -> dict[str, Any]:
    """Rotate the session's tokens, committing them before anything else."""
    async with advisory_lock(
        connection_lock_key(connection.id or 0), wait=LOCK_WAIT_SECONDS
    ):
        try:
            return await _refresh_once(connection, secrets)
        except HelperError as error:
            if error.code == "provider_unavailable":
                # Killed mid-refresh: the token may be spent. One more try,
                # then the account needs reconnecting (PROTOCOL.md, Tokens).
                try:
                    return await _refresh_once(connection, secrets)
                except HelperError as again:
                    error = again
            if error.code in {"auth_expired", "provider_unavailable"}:
                await mark_reconnect_required(db, connection)
                raise CalendarToolError(
                    "auth_expired",
                    RECONNECT_MESSAGE,
                    details={"reason": "refresh_refused"},
                ) from error
            raise


async def run_op(  # noqa: PLR0913 - one keyword per part of a helper call
    db: AsyncSession,
    connection: CalendarConnection,
    op: str,
    args: dict[str, Any] | None = None,
    *,
    timeout: float,
    secrets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run an operation with the connection's session, refreshing once if asked."""
    secrets = secrets or session_secrets(connection)
    if not secrets.get("uid"):
        await mark_reconnect_required(db, connection)
        raise CalendarToolError("auth_expired", RECONNECT_MESSAGE)
    expires_at = connection.token_expires_at
    if expires_at is not None and expires_at < _utc_now() + _REFRESH_AHEAD:
        secrets = await refresh_session(db, connection, secrets)
    try:
        return await call_helper(
            op, session=_for_op(secrets, op), args=args, timeout=timeout
        )
    except HelperError as error:
        if error.code != "access_token_expired":
            if error.code == "auth_expired":
                await mark_reconnect_required(db, connection)
            raise
    secrets = await refresh_session(db, connection, secrets)
    try:
        return await call_helper(
            op, session=_for_op(secrets, op), args=args, timeout=timeout
        )
    except HelperError as error:
        if error.code in {"auth_expired", "access_token_expired"}:
            await mark_reconnect_required(db, connection)
        raise
