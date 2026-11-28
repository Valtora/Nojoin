"""The change log, the locks and the token store behind calendar writes.

- **Change log.** One ``mcp_write_audit`` row per write past its argument
  checks, committed as ``pending`` before any provider call when the write is
  keyed, so a retry finds it, and completed with the outcome after.
- **Locks.** Every provider call that touches an account's session holds that
  account's lock, and a keyed create holds one on (user, key), so a retry
  waits for the first call. On Postgres these are session advisory locks on a
  connection of their own, held for the whole call; elsewhere, which only the
  tests use, an in-process lock stands in.
- **Tokens.** ``ConnectionTokenStore`` commits rotated credentials in a
  session of its own the moment a backend has them, so a refresh token the
  provider rotated is never lost to a later failure.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, func, select

from backend.core.encryption import encrypt_secret
from backend.models.calendar import CalendarConnection, CalendarEvent
from backend.models.mcp_write_audit import McpWriteAudit
from backend.models.oauth import OAuthClient, OAuthRefreshToken
from backend.models.user import User

from .event_view import CalendarState
from .text_utils import _add_months, _utc_now
from .tool_errors import CalendarToolError

AUDIT_RETENTION_MONTHS = 12
_LOCK_POLL_SECONDS = 0.2
# Namespaces keep the two kinds of advisory lock from ever sharing a key.
_CONNECTION_LOCK = 0x4E4A01
_IDEMPOTENCY_LOCK = 0x4E4A02
_local_locks: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class Actor:
    """Who asked for a write: the user, and the assistant's grant and tool."""

    user: User
    user_zone: str
    tool: str
    grant_id: str | None = None
    client_name: str | None = None

    @property
    def user_id(self) -> int:
        assert self.user.id is not None
        return self.user.id


async def client_name_for_grant(db: AsyncSession, grant_id: str | None) -> str | None:
    """The registered name of the app a grant belongs to, as it stood."""
    if not grant_id:
        return None
    return (
        await db.execute(
            select(OAuthClient.client_name)
            .join(
                OAuthRefreshToken, OAuthRefreshToken.client_id == OAuthClient.client_id
            )
            .where(OAuthRefreshToken.grant_id == grant_id)
            .limit(1)
        )
    ).scalar_one_or_none()


def _session_maker() -> Any:
    from backend.core import db as core_db

    return core_db.async_session_maker


def _lock_key(namespace: int, value: str) -> int:
    digest = int.from_bytes(hashlib.sha256(value.encode()).digest()[:5], "big")
    key = (namespace << 40) | digest
    return key - (1 << 64) if key >= 1 << 63 else key


def connection_lock_key(connection_id: int) -> int:
    return _lock_key(_CONNECTION_LOCK, str(connection_id))


def idempotency_lock_key(user_id: int, key: str) -> int:
    return _lock_key(_IDEMPOTENCY_LOCK, f"{user_id}:{key}")


def _busy() -> CalendarToolError:
    return CalendarToolError(
        "busy",
        "Another change or a sync is using this calendar account. Try again in "
        "a moment.",
    )


@asynccontextmanager
async def _local_lock(key: int, wait: float) -> AsyncIterator[None]:
    lock = _local_locks.setdefault(key, asyncio.Lock())
    try:
        await asyncio.wait_for(lock.acquire(), timeout=wait)
    except TimeoutError as exc:
        raise _busy() from exc
    try:
        yield
    finally:
        lock.release()


@asynccontextmanager
async def advisory_lock(key: int, *, wait: float) -> AsyncIterator[None]:
    """Hold an advisory lock for the block, or refuse with ``busy``."""
    maker = _session_maker()
    bind = maker.kw.get("bind")
    if bind is None or bind.dialect.name != "postgresql":
        async with _local_lock(key, wait):
            yield
        return
    async with maker() as lock_session:
        deadline = time.monotonic() + wait
        while not (
            await lock_session.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            )
        ).scalar():
            if time.monotonic() >= deadline:
                raise _busy()
            await asyncio.sleep(_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            # The same connection: the session's transaction has held it.
            await lock_session.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": key}
            )
            await lock_session.commit()


class ConnectionTokenStore:
    """Persists a connection's rotated credentials immediately."""

    def __init__(self, connection_id: int) -> None:
        self.connection_id = connection_id

    async def save(
        self,
        *,
        access_token: str | None,
        refresh_token: str | None,
        expires_at: datetime | None,
        secrets: dict[str, Any] | None = None,
    ) -> None:
        async with _session_maker()() as session:
            connection = await session.get(CalendarConnection, self.connection_id)
            if connection is None:
                return
            if access_token is not None:
                connection.access_token_encrypted = encrypt_secret(access_token)
            if refresh_token is not None:
                connection.refresh_token_encrypted = encrypt_secret(refresh_token)
            connection.token_expires_at = expires_at
            if secrets is not None:
                connection.provider_secrets_encrypted = encrypt_secret(
                    json.dumps(secrets)
                )
            session.add(connection)
            await session.commit()


def fingerprint(arguments: dict[str, Any]) -> str:
    """A digest of a request's normalised arguments, for idempotency checks."""
    payload = json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def new_audit_row(  # noqa: PLR0913 - one keyword per snapshot the row keeps
    actor: Actor,
    *,
    action: str,
    scope: str | None,
    state: CalendarState,
    event: CalendarEvent | None,
    outcome: str = "pending",
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
    title: str | None = None,
    start: datetime | None = None,
) -> McpWriteAudit:
    return McpWriteAudit(
        user_id=actor.user_id,
        grant_id=actor.grant_id,
        client_name=actor.client_name,
        area="calendar",
        tool=actor.tool,
        action=action,
        scope=scope,
        connection_id=state.connection.id,
        calendar_source_id=state.source.id,
        calendar_event_id=event.id if event is not None else None,
        provider=state.connection.provider,
        calendar_name=state.source.name,
        provider_event_key=event.provider_event_id if event is not None else None,
        event_title=title or (event.title if event is not None else None),
        event_start=start or (event.range_start if event is not None else None),
        outcome=outcome,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )


async def save_audit(db: AsyncSession, row: McpWriteAudit) -> McpWriteAudit:
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def finish_audit(  # noqa: PLR0913 - one keyword per completed field
    db: AsyncSession,
    row: McpWriteAudit,
    *,
    outcome: str,
    error_code: str | None = None,
    changes: Any = None,
    notifications: Any = None,
    response_snapshot: Any = None,
    event: CalendarEvent | None = None,
) -> McpWriteAudit:
    row.outcome = outcome
    row.error_code = error_code
    row.completed_at = _utc_now()
    if changes is not None:
        row.changes = changes
    if notifications is not None:
        row.notifications = notifications
    if response_snapshot is not None:
        row.response_snapshot = response_snapshot
    if event is not None and event.id is not None:
        row.calendar_event_id = event.id
        row.provider_event_key = event.provider_event_id
        row.event_title = event.title
        row.event_start = event.range_start
    return await save_audit(db, row)


async def keyed_audit(
    db: AsyncSession, user_id: int, idempotency_key: str
) -> McpWriteAudit | None:
    return (
        await db.execute(
            select(McpWriteAudit)
            .where(McpWriteAudit.user_id == user_id)
            .where(McpWriteAudit.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()


async def prune_audit(db: AsyncSession, now: datetime | None = None) -> int:
    """Delete change-log rows older than the retention period."""
    cutoff = _add_months(now or _utc_now(), -AUDIT_RETENTION_MONTHS)
    result = await db.execute(
        delete(McpWriteAudit).where(McpWriteAudit.created_at < cutoff)
    )
    await db.commit()
    return int(result.rowcount or 0)


async def list_changes(
    db: AsyncSession,
    user_id: int,
    *,
    connection_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[McpWriteAudit], int]:
    """One page of the user's change log, newest first, and its total."""
    statement = select(McpWriteAudit).where(McpWriteAudit.user_id == user_id)
    if connection_id is not None:
        statement = statement.where(McpWriteAudit.connection_id == connection_id)
    total = (
        await db.execute(select(func.count()).select_from(statement.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            statement.order_by(McpWriteAudit.created_at.desc(), McpWriteAudit.id.desc())
            .offset(skip)
            .limit(limit)
        )
    ).scalars()
    return list(rows), int(total)
