"""Calendar push (webhook) notifications.

Push is a best-effort accelerator layered on top of the always-on 15-minute
poll. When a provider supports it and the admin has opted in, Nojoin registers a
per-calendar subscription so changes arrive by webhook within seconds; when it
is unavailable (private instance, unverified domain, expired grant) the poll
still keeps calendars fresh.

- Google Calendar: per-calendar ``events.watch`` channels (recreated to renew).
- Microsoft Graph: per-calendar ``/subscriptions`` (PATCH to renew).

Inbound notifications are authenticated by a per-channel secret (Google channel
token / Microsoft ``clientState``); the payload is treated only as a
"something changed" signal that enqueues an incremental sync.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import redis.asyncio as redis
from sqlmodel import select

from backend.core.db import async_session_maker
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.core.redis import REDIS_URL
from backend.models.calendar import (
    CalendarConnection,
    CalendarProvider,
    CalendarProviderConfig,
    CalendarPushChannel,
    CalendarPushChannelStatus,
    CalendarSource,
)
from backend.services.calendar_service import (
    MICROSOFT_GRAPH_URL,
    _build_push_notification_url,
    _get_access_token_for_connection,
    _parse_iso_datetime,
    get_provider_runtime_config,
)
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)

GOOGLE_WATCH_URL_TEMPLATE = (
    "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/watch"
)
GOOGLE_CHANNELS_STOP_URL = "https://www.googleapis.com/calendar/v3/channels/stop"

# Microsoft caps Outlook-resource subscriptions at 4230 minutes; stay under it.
MICROSOFT_SUBSCRIPTION_TTL = timedelta(minutes=4200)
GOOGLE_RENEW_WITHIN = timedelta(hours=24)
MICROSOFT_RENEW_WITHIN = timedelta(hours=6)
# Do not re-attempt a failed provisioning more often than this (avoids hammering
# a provider from an unreachable instance on every sync).
PUSH_FAILURE_BACKOFF = timedelta(minutes=30)
# Coalesce notification bursts into a single sync per connection.
NOTIFICATION_DEBOUNCE_SECONDS = 20
SYNC_DEBOUNCE_COUNTDOWN = 3
# Cap items processed from a single (unauthenticated) Microsoft notification
# batch. Real Graph batches are small; a larger one is untrusted input that would
# otherwise fan out into that many sequential per-item database lookups.
MICROSOFT_MAX_NOTIFICATIONS_PER_BATCH = 100

HTTP_NOT_FOUND = 404
HTTP_GONE = 410
STOP_OK_STATUSES = {200, 204, 404}


@dataclass
class _ReconcileContext:
    """Per-connection invariants passed through the provisioning helpers."""

    db: Any
    connection: CalendarConnection
    access_token: str
    address: str


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _google_expiration_to_datetime(value: Any) -> datetime | None:
    """Google returns channel expiration as epoch milliseconds (string)."""
    if not value:
        return None
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).replace(
        tzinfo=None
    )


def _format_graph_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def _truncate_error(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned[:500] if cleaned else None


def _needs_renewal(provider: str, channel: CalendarPushChannel, now: datetime) -> bool:
    if channel.expiration is None:
        # Unknown expiration (provider omitted it, or it failed to parse):
        # assume expired and reprovision. Otherwise an ACTIVE row with a NULL
        # expiration would never renew and would silently stop delivering once
        # the provider expires the channel on their side.
        return True
    within = (
        GOOGLE_RENEW_WITHIN
        if provider == CalendarProvider.GOOGLE.value
        else MICROSOFT_RENEW_WITHIN
    )
    return channel.expiration <= (now + within)


def _should_provision(
    provider: str, channel: CalendarPushChannel | None, now: datetime
) -> bool:
    if channel is None:
        return True
    if channel.status == CalendarPushChannelStatus.FAILED.value:
        if channel.updated_at is None:
            return True
        return (now - channel.updated_at) >= PUSH_FAILURE_BACKOFF
    if channel.status == CalendarPushChannelStatus.STOPPED.value:
        return True
    return _needs_renewal(provider, channel, now)


def _secret_matches(channel: CalendarPushChannel, provided: str | None) -> bool:
    if not provided or not channel.secret_encrypted:
        return False
    try:
        expected = decrypt_secret(channel.secret_encrypted)
    except ValueError:
        return False
    if not expected:
        return False
    return secrets.compare_digest(expected, provided)


# ---------------------------------------------------------------------------
# Provider HTTP clients
# ---------------------------------------------------------------------------


async def _google_watch(
    access_token: str,
    calendar_id: str,
    channel_id: str,
    address: str,
    token: str,
) -> dict[str, Any]:
    from urllib.parse import quote

    url = GOOGLE_WATCH_URL_TEMPLATE.format(calendar_id=quote(calendar_id, safe=""))
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "id": channel_id,
                "type": "web_hook",
                "address": address,
                "token": token,
            },
        )
        response.raise_for_status()
        return response.json()


async def _google_stop(access_token: str, channel_id: str, resource_id: str) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GOOGLE_CHANNELS_STOP_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"id": channel_id, "resourceId": resource_id},
        )
        if response.status_code not in STOP_OK_STATUSES:
            response.raise_for_status()


async def _microsoft_create_subscription(
    access_token: str,
    calendar_id: str,
    address: str,
    client_state: str,
    expiration: datetime,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{MICROSOFT_GRAPH_URL}/subscriptions",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "changeType": "created,updated,deleted",
                "notificationUrl": address,
                "resource": f"/me/calendars/{calendar_id}/events",
                "expirationDateTime": _format_graph_datetime(expiration),
                "clientState": client_state,
            },
        )
        response.raise_for_status()
        return response.json()


async def _microsoft_renew_subscription(
    access_token: str, subscription_id: str, expiration: datetime
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            f"{MICROSOFT_GRAPH_URL}/subscriptions/{subscription_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"expirationDateTime": _format_graph_datetime(expiration)},
        )
        response.raise_for_status()
        return response.json()


async def _microsoft_delete_subscription(
    access_token: str, subscription_id: str
) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.delete(
            f"{MICROSOFT_GRAPH_URL}/subscriptions/{subscription_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code not in STOP_OK_STATUSES:
            response.raise_for_status()


async def _safe_google_stop(
    access_token: str, channel_id: str | None, resource_id: str | None
) -> None:
    if not channel_id or not resource_id:
        return
    try:
        await _google_stop(access_token, channel_id, resource_id)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "Best-effort Google channel stop failed for %s: %s", channel_id, exc
        )


async def _safe_microsoft_delete(
    access_token: str, subscription_id: str | None
) -> None:
    if not subscription_id:
        return
    try:
        await _microsoft_delete_subscription(access_token, subscription_id)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "Best-effort Microsoft subscription delete failed for %s: %s",
            subscription_id,
            exc,
        )


async def _stop_provider_channel(
    access_token: str, provider: str, channel: CalendarPushChannel
) -> None:
    if provider == CalendarProvider.GOOGLE.value:
        await _safe_google_stop(
            access_token, channel.provider_channel_id, channel.resource_id
        )
    else:
        await _safe_microsoft_delete(access_token, channel.provider_channel_id)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _load_connection(db, connection_id: int) -> CalendarConnection | None:
    return (
        await db.execute(
            select(CalendarConnection).where(CalendarConnection.id == connection_id)
        )
    ).scalar_one_or_none()


async def _load_connection_channels(
    db, connection_id: int
) -> list[CalendarPushChannel]:
    return list(
        (
            await db.execute(
                select(CalendarPushChannel).where(
                    CalendarPushChannel.connection_id == connection_id
                )
            )
        )
        .scalars()
        .all()
    )


async def _load_selected_calendars(db, connection_id: int) -> list[CalendarSource]:
    return list(
        (
            await db.execute(
                select(CalendarSource).where(
                    CalendarSource.connection_id == connection_id,
                    CalendarSource.is_selected.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )


async def _find_channel(
    db, provider: str, provider_channel_id: str | None
) -> CalendarPushChannel | None:
    if not provider_channel_id:
        return None
    return (
        await db.execute(
            select(CalendarPushChannel)
            .where(
                CalendarPushChannel.provider == provider,
                CalendarPushChannel.provider_channel_id == provider_channel_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _find_channel_for_calendar(
    db, calendar_id: int, provider: str
) -> CalendarPushChannel | None:
    return (
        await db.execute(
            select(CalendarPushChannel)
            .where(
                CalendarPushChannel.calendar_id == calendar_id,
                CalendarPushChannel.provider == provider,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


async def _ensure_google_channel(
    ctx: _ReconcileContext,
    calendar: CalendarSource,
    existing: CalendarPushChannel | None,
) -> None:
    secret = secrets.token_urlsafe(32)
    channel_id = uuid.uuid4().hex
    result = await _google_watch(
        ctx.access_token,
        calendar.provider_calendar_id,
        channel_id,
        ctx.address,
        secret,
    )
    # Google channels cannot be renewed in place; a fresh watch supersedes the
    # old channel, which we then stop.
    if (
        existing is not None
        and existing.provider_channel_id
        and existing.provider_channel_id != channel_id
    ):
        await _safe_google_stop(
            ctx.access_token, existing.provider_channel_id, existing.resource_id
        )
    row = existing or CalendarPushChannel(
        connection_id=ctx.connection.id,
        calendar_id=calendar.id,
        provider=CalendarProvider.GOOGLE.value,
    )
    row.connection_id = ctx.connection.id
    row.calendar_id = calendar.id
    row.provider = CalendarProvider.GOOGLE.value
    row.provider_channel_id = channel_id
    row.resource_id = result.get("resourceId")
    row.secret_encrypted = encrypt_secret(secret)
    row.notification_url = ctx.address
    row.expiration = _google_expiration_to_datetime(result.get("expiration"))
    row.status = CalendarPushChannelStatus.ACTIVE.value
    row.last_error = None
    ctx.db.add(row)


async def _ensure_microsoft_channel(
    ctx: _ReconcileContext,
    calendar: CalendarSource,
    existing: CalendarPushChannel | None,
) -> None:
    # Renew an existing subscription in place when possible (keeps id + secret).
    # Graph's PATCH only updates the expiry, not the notificationUrl, so when the
    # webhook address has changed we must recreate instead -- otherwise Graph keeps
    # delivering to the old URL while the DB records the new one.
    if (
        existing is not None
        and existing.provider_channel_id
        and existing.status == CalendarPushChannelStatus.ACTIVE.value
        and existing.secret_encrypted
        and existing.notification_url == ctx.address
    ):
        expiration = utc_now() + MICROSOFT_SUBSCRIPTION_TTL
        try:
            result = await _microsoft_renew_subscription(
                ctx.access_token, existing.provider_channel_id, expiration
            )
            existing.notification_url = ctx.address
            existing.expiration = (
                _parse_iso_datetime(result.get("expirationDateTime")) or expiration
            )
            existing.status = CalendarPushChannelStatus.ACTIVE.value
            existing.last_error = None
            ctx.db.add(existing)
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (HTTP_NOT_FOUND, HTTP_GONE):
                raise
            # Subscription is gone; fall through and create a fresh one.

    secret = secrets.token_urlsafe(32)
    expiration = utc_now() + MICROSOFT_SUBSCRIPTION_TTL
    result = await _microsoft_create_subscription(
        ctx.access_token,
        calendar.provider_calendar_id,
        ctx.address,
        secret,
        expiration,
    )
    subscription_id = result.get("id")
    if (
        existing is not None
        and existing.provider_channel_id
        and existing.provider_channel_id != subscription_id
    ):
        await _safe_microsoft_delete(ctx.access_token, existing.provider_channel_id)
    row = existing or CalendarPushChannel(
        connection_id=ctx.connection.id,
        calendar_id=calendar.id,
        provider=CalendarProvider.MICROSOFT.value,
    )
    row.connection_id = ctx.connection.id
    row.calendar_id = calendar.id
    row.provider = CalendarProvider.MICROSOFT.value
    row.provider_channel_id = subscription_id
    row.resource_id = None
    row.secret_encrypted = encrypt_secret(secret)
    row.notification_url = ctx.address
    row.expiration = _parse_iso_datetime(result.get("expirationDateTime")) or expiration
    row.status = CalendarPushChannelStatus.ACTIVE.value
    row.last_error = None
    ctx.db.add(row)


async def _ensure_channel(
    ctx: _ReconcileContext,
    calendar: CalendarSource,
    existing: CalendarPushChannel | None,
) -> None:
    if ctx.connection.provider == CalendarProvider.MICROSOFT.value:
        await _ensure_microsoft_channel(ctx, calendar, existing)
    else:
        await _ensure_google_channel(ctx, calendar, existing)


async def _mark_channel_failed(
    ctx: _ReconcileContext,
    calendar: CalendarSource,
    existing: CalendarPushChannel | None,
    error: Exception,
) -> None:
    provider = ctx.connection.provider
    row = existing or await _find_channel_for_calendar(ctx.db, calendar.id, provider)
    if row is None:
        row = CalendarPushChannel(
            connection_id=ctx.connection.id,
            calendar_id=calendar.id,
            provider=provider,
        )
    row.status = CalendarPushChannelStatus.FAILED.value
    row.last_error = _truncate_error(str(error))
    ctx.db.add(row)


async def _stop_and_delete_channel(
    db, access_token: str | None, provider: str, channel: CalendarPushChannel
) -> None:
    if access_token:
        await _stop_provider_channel(access_token, provider, channel)
    await db.delete(channel)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def _provision_selected_calendars(
    ctx: _ReconcileContext,
    selected: list[CalendarSource],
    existing_by_calendar: dict[int, CalendarPushChannel],
    now: datetime,
) -> None:
    provider = ctx.connection.provider
    for calendar in selected:
        channel = existing_by_calendar.get(calendar.id)
        if not _should_provision(provider, channel, now):
            continue
        try:
            await _ensure_channel(ctx, calendar, channel)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to provision %s push channel for calendar %s: %s",
                provider,
                calendar.id,
                exc,
            )
            await _mark_channel_failed(ctx, calendar, channel, exc)


async def reconcile_push_channels_for_connection(
    db, connection: CalendarConnection
) -> None:
    """Bring push channels in line with the connection's current state.

    Creates channels for selected calendars, renews those near expiry, and stops
    channels for deselected calendars or when push is disabled. Commits the
    session. Individual provider failures are recorded per calendar and do not
    abort the rest.
    """
    provider = connection.provider
    runtime_config = await get_provider_runtime_config(db, provider)
    existing = await _load_connection_channels(db, connection.id)
    existing_by_calendar = {channel.calendar_id: channel for channel in existing}
    push_on = bool(runtime_config.push_enabled and runtime_config.configured)

    access_token: str | None = None
    if push_on or existing:
        try:
            access_token = await _get_access_token_for_connection(
                db, connection, runtime_config
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Push reconcile for connection %s skipped: no access token (%s)",
                connection.id,
                exc,
            )
            return

    if not push_on:
        for channel in existing:
            await _stop_and_delete_channel(db, access_token, provider, channel)
        await db.commit()
        return

    if access_token is None:
        return

    selected = await _load_selected_calendars(db, connection.id)
    selected_ids = {calendar.id for calendar in selected}
    address = _build_push_notification_url(provider)
    now = utc_now()
    ctx = _ReconcileContext(
        db=db, connection=connection, access_token=access_token, address=address
    )

    for channel in existing:
        if channel.calendar_id not in selected_ids:
            await _stop_and_delete_channel(db, access_token, provider, channel)

    await _provision_selected_calendars(ctx, selected, existing_by_calendar, now)

    await db.commit()


async def ensure_push_channels_for_connection(connection_id: int) -> None:
    """Best-effort reconcile in a fresh session; never raises."""
    try:
        async with async_session_maker() as db:
            connection = await _load_connection(db, connection_id)
            if connection is None:
                return
            await reconcile_push_channels_for_connection(db, connection)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Calendar push reconcile failed for connection %s: %s", connection_id, exc
        )


async def teardown_push_channels_for_connection(
    db, connection: CalendarConnection
) -> None:
    """Stop provider-side subscriptions for a connection being disconnected.

    The channel rows are removed by the connection's cascade delete, so this only
    needs to stop the provider side while tokens are still valid.
    """
    channels = await _load_connection_channels(db, connection.id)
    if not channels:
        return
    runtime_config = await get_provider_runtime_config(db, connection.provider)
    try:
        access_token = await _get_access_token_for_connection(
            db, connection, runtime_config
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "Cannot obtain token to stop push channels for connection %s: %s",
            connection.id,
            exc,
        )
        return
    for channel in channels:
        await _stop_provider_channel(access_token, connection.provider, channel)


async def _collect_reconcile_connection_ids() -> list[int]:
    async with async_session_maker() as db:
        push_providers = (
            select(CalendarProviderConfig.provider)
            .where(CalendarProviderConfig.push_enabled.is_(True))
            .where(CalendarProviderConfig.enabled.is_(True))
        )
        provisionable = (
            select(CalendarConnection.id)
            .join(CalendarSource, CalendarSource.connection_id == CalendarConnection.id)
            .where(CalendarSource.is_selected.is_(True))
            .where(CalendarConnection.provider.in_(push_providers))
            .distinct()
        )
        ids = set((await db.execute(provisionable)).scalars().all())
        with_channels = select(CalendarPushChannel.connection_id).distinct()
        ids |= set((await db.execute(with_channels)).scalars().all())
    return sorted(ids)


async def renew_push_channels() -> int:
    """Periodic sweep: provision, renew, and clean up push channels."""
    connection_ids = await _collect_reconcile_connection_ids()
    reconciled = 0
    for connection_id in connection_ids:
        try:
            async with async_session_maker() as db:
                connection = await _load_connection(db, connection_id)
                if connection is None:
                    continue
                await reconcile_push_channels_for_connection(db, connection)
                reconciled += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Calendar push renewal failed for connection %s: %s",
                connection_id,
                exc,
            )
    return reconciled


# ---------------------------------------------------------------------------
# Inbound notifications
# ---------------------------------------------------------------------------


async def _debounced_enqueue_sync(connection_id: int) -> None:
    should_enqueue = True
    try:
        client = redis.from_url(REDIS_URL)
        try:
            key = f"nojoin:calendar:push:debounce:{connection_id}"
            was_set = await client.set(
                key, "1", ex=NOTIFICATION_DEBOUNCE_SECONDS, nx=True
            )
            should_enqueue = bool(was_set)
        finally:
            # redis.from_url allocates a fresh connection pool per call, so the
            # client must be closed even when set() raises (e.g. Redis is
            # restarting) -- otherwise every inbound notification during an
            # outage leaks a pool until the process runs out of descriptors.
            await client.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Push debounce unavailable; enqueueing directly: %s", exc)
        should_enqueue = True

    if not should_enqueue:
        return

    try:
        from backend.celery_app import celery_app

        celery_app.send_task(
            "backend.worker.tasks.sync_calendar_connection_task",
            args=[connection_id],
            countdown=SYNC_DEBOUNCE_COUNTDOWN,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to enqueue push-triggered sync for connection %s: %s",
            connection_id,
            exc,
        )


async def handle_google_notification(
    db,
    *,
    channel_id: str | None,
    channel_token: str | None,
    resource_state: str | None,
) -> bool:
    """Process a Google push notification. Returns True if a sync was enqueued."""
    if resource_state and resource_state.strip().lower() == "sync":
        # Initial handshake confirming the channel; nothing has changed yet.
        return False
    channel = await _find_channel(db, CalendarProvider.GOOGLE.value, channel_id)
    if channel is None or channel.status != CalendarPushChannelStatus.ACTIVE.value:
        return False
    if not _secret_matches(channel, channel_token):
        logger.warning(
            "Discarding Google push notification with invalid token for connection %s",
            channel.connection_id,
        )
        return False
    await _debounced_enqueue_sync(channel.connection_id)
    return True


async def handle_microsoft_notification(db, payload: Any) -> int:
    """Process a Microsoft change-notification batch. Returns connections synced."""
    notifications = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(notifications, list):
        return 0
    if len(notifications) > MICROSOFT_MAX_NOTIFICATIONS_PER_BATCH:
        logger.warning(
            "Microsoft notification batch has %d items; processing the first %d",
            len(notifications),
            MICROSOFT_MAX_NOTIFICATIONS_PER_BATCH,
        )
        notifications = notifications[:MICROSOFT_MAX_NOTIFICATIONS_PER_BATCH]
    connection_ids: set[int] = set()
    for item in notifications:
        if not isinstance(item, dict):
            continue
        subscription_id = item.get("subscriptionId")
        channel = await _find_channel(
            db, CalendarProvider.MICROSOFT.value, subscription_id
        )
        if channel is None or channel.status != CalendarPushChannelStatus.ACTIVE.value:
            continue
        if not _secret_matches(channel, item.get("clientState")):
            logger.warning(
                "Discarding Microsoft push notification with invalid clientState "
                "for connection %s",
                channel.connection_id,
            )
            continue
        connection_ids.add(channel.connection_id)
    for connection_id in connection_ids:
        await _debounced_enqueue_sync(connection_id)
    return len(connection_ids)
