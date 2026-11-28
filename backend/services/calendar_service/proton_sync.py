"""Syncing a Proton connection's calendars through the helper.

A full fetch reads the sync window in chunks of at most 93 days and
reconciles, keeping each stored event's id so linked recordings stay linked.
Later syncs read Proton's change feed from the cursor the full fetch took and
replace each changed series in place; a refused cursor, Proton's Refresh flag
or a moved window falls back to a full fetch. Each helper call takes the
account's lock on its own, so a write is never stuck behind a whole sync.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from backend.models.calendar import (
    CalendarConnection,
    CalendarEvent,
    CalendarSource,
    CalendarSyncStatus,
)
from backend.models.user import User
from backend.utils.timezones import get_user_timezone_name, normalise_timezone_name

from .models_dto import ProviderCalendarRecord, ProviderEventRecord
from .persistence import (
    _can_use_incremental_sync,
    _reconcile_calendar_events,
    _refresh_connection_calendars,
    _upsert_calendar_events,
)
from .proton_helper import READ_TIMEOUT_SECONDS, SYNC_TIMEOUT_SECONDS, HelperError
from .proton_mapping import chunks, records, window
from .proton_session import LOCK_WAIT_SECONDS, run_op
from .text_utils import _build_sync_window, _utc_now
from .tool_errors import CalendarToolError
from .write_audit import advisory_lock, connection_lock_key, sync_lock_key

logger = logging.getLogger(__name__)
_MAX_CHANGE_PAGES = 20
# Another sync of the same account is under way when the lock is held.
_SYNC_LOCK_WAIT_SECONDS = 0.1


class _Progress:
    """Steps done out of steps in all, stored on the connection as they go,
    so the connect dialog and the Calendars card can show them."""

    def __init__(self, db: AsyncSession, connection: CalendarConnection) -> None:
        self.db = db
        self.connection = connection
        self.done = 0
        self.total = 0

    async def save(self) -> None:
        self.connection.sync_progress_done = self.done
        self.connection.sync_progress_total = max(self.total, self.done)
        self.db.add(self.connection)
        await self.db.commit()

    async def step(self) -> None:
        self.done += 1
        await self.save()


_SLOW_DOWN = "Proton asked Nojoin to slow down. The next sync will try again."


async def _call(
    db: AsyncSession,
    connection: CalendarConnection,
    op: str,
    args: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    async with advisory_lock(
        connection_lock_key(connection.id or 0), wait=LOCK_WAIT_SECONDS
    ):
        return await run_op(db, connection, op, args, timeout=timeout)


def calendar_records(listing: dict[str, Any]) -> list[ProviderCalendarRecord]:
    """The helper's ``list_calendars`` result as calendar records."""
    settings = listing.get("settings") or {}
    found = []
    for item in listing.get("calendars") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        found.append(
            ProviderCalendarRecord(
                remote_id=str(item["id"]),
                name=item.get("name") or "Untitled calendar",
                description=item.get("description") or None,
                time_zone=item.get("time_zone") or settings.get("primary_time_zone"),
                colour=item.get("colour"),
                is_primary=bool(item.get("is_default")),
                is_read_only=not item.get("writable"),
            )
        )
    return found


def _uid_of(event: CalendarEvent) -> str:
    uid = (event.provider_data or {}).get("uid")
    return str(uid or event.provider_event_id.split("#", 1)[0])


async def replace_series(
    db: AsyncSession,
    calendar_id: int,
    uid: str,
    found: list[ProviderEventRecord],
    range_zone: str,
) -> None:
    """Make the stored rows of one UID match ``found``, updating rows in place."""
    rows = [
        row
        for row in (
            await db.execute(
                select(CalendarEvent)
                .where(CalendarEvent.calendar_id == calendar_id)
                .where(
                    (CalendarEvent.series_key == uid)
                    | (CalendarEvent.provider_event_id == uid)
                    | CalendarEvent.provider_event_id.startswith(f"{uid}#")
                )
            )
        ).scalars()
    ]
    wanted = {record.remote_id for record in found}
    gone = [row.id for row in rows if row.provider_event_id not in wanted]
    if gone:
        await db.execute(delete(CalendarEvent).where(CalendarEvent.id.in_(gone)))
    _upsert_calendar_events(
        db,
        calendar_id,
        found,
        {row.provider_event_id: row for row in rows if row.provider_event_id in wanted},
        range_zone=range_zone,
    )


async def _full_fetch(  # noqa: PLR0913 - the calendar being synced and its context
    db: AsyncSession,
    connection: CalendarConnection,
    calendar: CalendarSource,
    span: tuple[datetime, datetime],
    range_zone: str,
    started_at: datetime,
    progress: "_Progress",
) -> str | None:
    found: dict[str, ProviderEventRecord] = {}
    cursor = None
    for index, piece in enumerate(chunks(*span)):
        result = await _call(
            db,
            connection,
            "list_events",
            {
                "calendar_id": calendar.provider_calendar_id,
                "window": piece,
                "include_cursor": index == 0,
            },
            SYNC_TIMEOUT_SECONDS,
        )
        if index == 0:
            cursor = result.get("cursor")
        for record in records(result.get("events")):
            found[record.remote_id] = record
        await progress.step()
    await _reconcile_calendar_events(
        db,
        calendar.id or 0,
        list(found.values()),
        span[0],
        range_zone=range_zone,
        fetch_started_at=started_at,
    )
    return cursor


async def _restore_deleted_rows(  # noqa: PLR0913 - the calendar being synced and its context
    db: AsyncSession,
    connection: CalendarConnection,
    calendar: CalendarSource,
    deleted_ids: set[str],
    seen: set[str],
    range_zone: str,
) -> None:
    """Re-read each series a deleted Proton row belonged to (an exception's
    occurrence comes back from its master), or drop it when it is gone."""
    rows = (
        await db.execute(
            select(CalendarEvent).where(CalendarEvent.calendar_id == calendar.id)
        )
    ).scalars()
    uids = {
        _uid_of(row)
        for row in rows
        if {
            (row.provider_data or {}).get("event_id"),
            (row.provider_data or {}).get("master_event_id"),
        }
        & deleted_ids
    } - seen
    for uid in sorted(uids):
        try:
            result = await _call(
                db,
                connection,
                "get_event",
                {
                    "calendar_id": calendar.provider_calendar_id,
                    "target": {"uid": uid, "recurrence_id": None},
                    "window": window(
                        calendar.sync_window_start, calendar.sync_window_end
                    ),
                },
                READ_TIMEOUT_SECONDS,
            )
            found = records(result.get("events"))
        except HelperError as error:
            if error.code != "not_found":
                raise
            found = []
        await replace_series(db, calendar.id or 0, uid, found, range_zone)


async def _apply_changes(
    db: AsyncSession,
    connection: CalendarConnection,
    calendar: CalendarSource,
    span: tuple[datetime, datetime],
    range_zone: str,
) -> str | None:
    """Apply the change feed; the new cursor, or None when a full fetch is due."""
    cursor = calendar.sync_cursor
    seen: set[str] = set()
    deleted: set[str] = set()
    for _ in range(_MAX_CHANGE_PAGES):
        try:
            result = await _call(
                db,
                connection,
                "changes",
                {
                    "calendar_id": calendar.provider_calendar_id,
                    "cursor": cursor,
                    "window": window(*span),
                },
                SYNC_TIMEOUT_SECONDS,
            )
        except HelperError as error:
            if error.code == "not_found":
                return None
            raise
        if result.get("refresh_required"):
            return None
        for series in result.get("series") or []:
            uid = str(series.get("uid"))
            seen.add(uid)
            await replace_series(
                db, calendar.id or 0, uid, records(series.get("events")), range_zone
            )
        deleted |= {str(item) for item in result.get("deleted_event_ids") or []}
        cursor = result.get("cursor") or cursor
        if not result.get("more"):
            break
    if deleted:
        await _restore_deleted_rows(db, connection, calendar, deleted, seen, range_zone)
    return cursor


def _steps(calendar: CalendarSource, span: tuple[datetime, datetime]) -> int:
    """A change-feed read is one step; a full fetch one per window."""
    return 1 if _can_use_incremental_sync(calendar, *span) else len(chunks(*span))


async def _sync_calendar(  # noqa: PLR0913 - the calendar being synced and its context
    db: AsyncSession,
    connection: CalendarConnection,
    calendar: CalendarSource,
    range_zone: str,
    started_at: datetime,
    progress: _Progress,
) -> None:
    span = _build_sync_window()
    cursor = None
    if _can_use_incremental_sync(calendar, *span):
        cursor = await _apply_changes(db, connection, calendar, span, range_zone)
        await progress.step()
        if cursor is None:
            # The feed asked for a full fetch after all.
            progress.total += len(chunks(*span))
    if cursor is None:
        cursor = await _full_fetch(
            db, connection, calendar, span, range_zone, started_at, progress
        )
    calendar.sync_cursor = cursor
    # Only now has this calendar's own fetch completed.
    calendar.last_synced_at = _utc_now()
    calendar.sync_window_start, calendar.sync_window_end = span
    db.add(calendar)
    await db.commit()


def _failure(error: Exception) -> tuple[str, str]:
    code = getattr(error, "code", None)
    if code == "auth_expired":
        return (
            CalendarSyncStatus.REAUTHORISATION_REQUIRED.value,
            "This Proton account needs to be reconnected before it can sync again.",
        )
    if code == "rate_limited":
        return CalendarSyncStatus.ERROR.value, _SLOW_DOWN
    message = getattr(error, "message", None) or "Proton Calendar sync failed."
    return CalendarSyncStatus.ERROR.value, str(message)[:500]


async def sync_proton_connection(
    db: AsyncSession, connection: CalendarConnection
) -> None:
    """Sync every selected calendar of one Proton connection, unless another
    sync of it is already under way (the first one after sign-in, say).

    A calendar's ``last_synced_at`` moves only when its own fetch succeeded;
    the connection's only when every selected calendar's did.
    """
    try:
        async with advisory_lock(
            sync_lock_key(connection.id or 0), wait=_SYNC_LOCK_WAIT_SECONDS
        ):
            await _sync_connection(db, connection)
    except CalendarToolError as error:
        if error.code != "busy":
            raise
        logger.info("Proton connection %s is already syncing", connection.id)


async def _sync_connection(db: AsyncSession, connection: CalendarConnection) -> None:
    owner = await db.get(User, connection.user_id)
    user_zone = get_user_timezone_name(owner.settings if owner else None)
    started_at = _utc_now()
    connection.sync_status = CalendarSyncStatus.SYNCING.value
    connection.sync_error = None
    connection.last_sync_started_at = started_at
    db.add(connection)
    await db.commit()
    progress = _Progress(db, connection)
    try:
        listing = await _call(
            db, connection, "list_calendars", {}, READ_TIMEOUT_SECONDS
        )
        calendars = await _refresh_connection_calendars(
            db, connection, calendar_records(listing)
        )
        selected = [item for item in calendars if item.is_selected]
        span = _build_sync_window()
        progress.total = sum(_steps(calendar, span) for calendar in selected)
        await progress.save()
        for calendar in selected:
            range_zone = normalise_timezone_name(calendar.time_zone) or user_zone
            await _sync_calendar(
                db, connection, calendar, range_zone, started_at, progress
            )
    except (HelperError, CalendarToolError) as error:
        logger.warning(
            "Proton sync failed for connection %s: %s",
            connection.id,
            getattr(error, "code", ""),
        )
        status, message = _failure(error)
        await db.rollback()
        await db.refresh(connection)
        connection.sync_status = status
        connection.sync_error = message
        connection.sync_progress_done = connection.sync_progress_total = None
        connection.last_sync_completed_at = _utc_now()
        db.add(connection)
        await db.commit()
        return
    completed = _utc_now()
    connection.sync_status = CalendarSyncStatus.SUCCESS.value
    connection.sync_error = None
    connection.sync_progress_done = connection.sync_progress_total = None
    connection.last_sync_completed_at = completed
    connection.last_synced_at = completed
    db.add(connection)
    await db.commit()
