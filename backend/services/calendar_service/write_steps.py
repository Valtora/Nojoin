"""The steps every calendar write shares.

Resolving the provider context and the event reference a backend needs,
refusing for the state a write found, previews, storing what a backend read
back, sending mail, and running a backend call under the account's lock with
its change-log row completed whatever happens. ``writes`` and
``writes_existing`` compose these into the four tools.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from backend.core.encryption import decrypt_secret
from backend.models.calendar import CalendarEvent, CalendarSyncStatus
from backend.models.mcp_write_audit import McpWriteAudit
from backend.utils.timezones import normalise_timezone_name

from .event_view import CalendarState
from .models_dto import ProviderEventRecord
from .notifications import (
    Plan,
)
from .persistence import _build_calendar_event_model, _upsert_calendar_events
from .tool_errors import CalendarToolError
from .tool_failures import calendar_tool_error
from .write_audit import (
    Actor,
    ConnectionTokenStore,
    advisory_lock,
    connection_lock_key,
    finish_audit,
    new_audit_row,
    save_audit,
)
from .write_backend import (
    CalendarWriteBackend,
    EventRef,
    FieldProblem,
    NotificationPlan,
    NotificationReport,
    ProviderContext,
    WriteOutcome,
)

LOCK_WAIT_SECONDS = 15.0
IDEMPOTENCY_WAIT_SECONDS = 45.0
_UID_NAMESPACE = uuid.UUID("5d8f2c1e-9a47-4b3e-8f60-2c1d7e9a4b58")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
# Refusals for the state a write found, as opposed to a provider failing.
_REFUSAL_CODES = frozenset(
    {
        "invalid_argument",
        "invalid_time",
        "ambiguous_time",
        "scope_required",
        "unsupported",
        "not_found",
        "not_writable",
        "not_organiser",
        "not_attendee",
        "conflict",
        "idempotency_conflict",
        "busy",
    }
)
_NOT_WRITABLE_MESSAGES = {
    "provider_read_only": "Assistants cannot change this provider's calendars yet.",
    "calendar_read_only": "The user can only view this calendar.",
    "not_selected": "This calendar is not selected in Nojoin, so it is not kept "
    "in sync. The user can select it in Settings > Integrations.",
    "reconnect_required": "This calendar account must be reconnected in Nojoin "
    "before it can be changed.",
}


def derived_uid(user_id: int, key: str) -> str:
    """The iCalendar UID of a keyed create, so a retry finds what landed."""
    return f"{uuid.uuid5(_UID_NAMESPACE, f'{user_id}:{key}')}@nojoin"


def event_uid(event: CalendarEvent) -> str:
    uid = (event.provider_data or {}).get("uid")
    return str(uid or event.series_key or event.provider_event_id)


def provider_context(
    db: AsyncSession, state: CalendarState, user_zone: str
) -> ProviderContext:
    """The account, its decrypted secrets and its token store, for a backend."""
    from .config import _load_connection_token_bundle

    connection = state.connection
    access_token, refresh_token = _load_connection_token_bundle(connection)
    secrets: dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
    if connection.provider_secrets_encrypted:
        try:
            secrets.update(
                json.loads(
                    decrypt_secret(connection.provider_secrets_encrypted) or "{}"
                )
            )
        except ValueError as exc:
            raise CalendarToolError(
                "auth_expired",
                "This calendar account must be reconnected in Nojoin.",
            ) from exc
    assert connection.id is not None
    return ProviderContext(
        db=db,
        connection=connection,
        source=state.source,
        tokens=ConnectionTokenStore(connection.id),
        user_zone=user_zone,
        secrets=secrets,
    )


def event_ref(event: CalendarEvent, state: CalendarState) -> EventRef:
    return EventRef(
        calendar_id=state.source.id or 0,
        provider_calendar_id=state.source.provider_calendar_id,
        event_id=event.id,
        provider_event_id=event.provider_event_id,
        uid=event_uid(event),
        series_key=event.series_key,
        original_start=event.original_start,
        original_start_date=event.original_start_date,
        occurrence_type=event.occurrence_type,
        revision=event.provider_revision,
        provider_data=dict(event.provider_data or {}),
    )


def _outcome_of(error: CalendarToolError) -> str:
    if error.details.get("outcome_unknown"):
        return "uncertain"
    return "refused" if error.code in _REFUSAL_CODES else "failed"


def _require(state: CalendarState, action: str) -> None:
    capabilities = state.capabilities
    if not capabilities.writable:
        reason = capabilities.reason or "provider_read_only"
        raise CalendarToolError(
            "not_writable",
            _NOT_WRITABLE_MESSAGES.get(reason, "This calendar cannot be changed."),
            details={"reason": reason},
        )
    if not getattr(capabilities, action):
        raise CalendarToolError(
            "unsupported",
            f"This calendar does not support {action} from an assistant.",
            details={"hint": "Ask the user to make this change in their calendar app."},
        )


def _first_problem(problems: list[FieldProblem]) -> None:
    if problems:
        raise CalendarToolError(
            "invalid_argument", problems[0].message, field=problems[0].field
        )


def _scope_for(
    event: CalendarEvent, requested: str | None, state: CalendarState
) -> str:
    if event.series_key:
        if requested is None:
            raise CalendarToolError(
                "scope_required",
                "This event is one occurrence of a recurring series. Ask the user "
                "whether the change is for this occurrence or the whole series, then "
                'pass scope "occurrence" or "series".',
                field="scope",
                details={"choices": ["occurrence", "series"]},
            )
        if requested == "occurrence" and not state.capabilities.occurrence_edits:
            raise CalendarToolError(
                "unsupported",
                "This calendar cannot change one occurrence on its own.",
                details={"hint": 'Use scope "series", or ask the user to edit it.'},
            )
        return requested
    if requested is not None:
        raise CalendarToolError(
            "invalid_argument",
            "This event is not part of a series; leave scope out.",
            field="scope",
        )
    return "single"


def _check_revision(event: CalendarEvent, expected: str | None) -> None:
    if expected is not None and expected != event.provider_revision:
        raise CalendarToolError(
            "conflict",
            "The event changed since it was read. Read it again and confirm the "
            "change with the user.",
            details={"current_revision": event.provider_revision},
        )


def _result(  # noqa: PLR0913 - one keyword per field of the result
    outcome: str,
    *,
    dry_run: bool,
    verified: bool,
    scope: str,
    event: dict[str, Any] | None,
    notifications: dict[str, Any],
    changes: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    occurrences: int | None = None,
    exceptions_removed: int = 0,
    audit_id: int | None = None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "dry_run": dry_run,
        "verified": verified,
        "scope": scope,
        "event": event,
        "affected": {
            "occurrences": occurrences,
            "exceptions_removed": exceptions_removed,
        },
        "changes": changes or [],
        "notifications": notifications,
        "warnings": warnings or [],
        "audit_id": audit_id,
        "idempotent_replay": False,
    }


def _range_zone(state: CalendarState, user_zone: str) -> str:
    return normalise_timezone_name(state.source.time_zone) or user_zone


def _preview(
    record: ProviderEventRecord,
    state: CalendarState,
    user_zone: str,
    *,
    event_id: int | None,
) -> CalendarEvent:
    """A transient event built from a record, never added to the session."""
    model = _build_calendar_event_model(
        state.source.id or 0, record, range_zone=_range_zone(state, user_zone)
    )
    model.id = event_id
    return model


async def _store_outcome(
    db: AsyncSession, state: CalendarState, outcome: WriteOutcome, user_zone: str
) -> list[CalendarEvent]:
    """Save what the provider read back; the stored rows, in its order."""
    calendar_id = state.source.id
    assert calendar_id is not None
    if outcome.deleted_keys:
        await db.execute(
            delete(CalendarEvent)
            .where(CalendarEvent.calendar_id == calendar_id)
            .where(CalendarEvent.provider_event_id.in_(outcome.deleted_keys))
        )
    remote_ids = [record.remote_id for record in outcome.events]
    existing: dict[str, CalendarEvent] = {}
    if remote_ids:
        rows = await db.execute(
            select(CalendarEvent)
            .where(CalendarEvent.calendar_id == calendar_id)
            .where(CalendarEvent.provider_event_id.in_(remote_ids))
        )
        existing = {row.provider_event_id: row for row in rows.scalars()}
    _upsert_calendar_events(
        db,
        calendar_id,
        outcome.events,
        existing,
        range_zone=_range_zone(state, user_zone),
    )
    await db.commit()
    if not remote_ids:
        return []
    stored = await db.execute(
        select(CalendarEvent)
        .where(CalendarEvent.calendar_id == calendar_id)
        .where(CalendarEvent.provider_event_id.in_(remote_ids))
    )
    by_id = {row.provider_event_id: row for row in stored.scalars()}
    return [by_id[remote_id] for remote_id in remote_ids if remote_id in by_id]


async def _send(
    backend: CalendarWriteBackend,
    ctx: ProviderContext,
    ref: EventRef,
    plan: Plan,
    *,
    note: str | None = None,
) -> NotificationReport:
    """Send the planned mail; a failure is reported, the write still stands."""
    if not plan.mails:
        return NotificationReport()
    try:
        return await backend.send_notifications(
            ctx, NotificationPlan(ref=ref, mails=plan.mails, note=note)
        )
    except CalendarToolError as error:
        return NotificationReport(
            failed=[(mail.email, error.code) for mail in plan.mails]
        )


async def _log_refusal(
    db: AsyncSession,
    actor: Actor,
    error: CalendarToolError,
    **snapshot: Any,
) -> None:
    row = new_audit_row(actor, outcome="refused", **snapshot)
    row.error_code = error.code
    await save_audit(db, row)


async def _call_backend(
    db: AsyncSession, row: McpWriteAudit, state: CalendarState, call: Any
) -> WriteOutcome:
    """Run one backend write under the account's lock, logging a failure.

    A provider failure the backend let through is mapped to its code here, so
    the change-log row is completed whatever went wrong; a refused sign-in
    also marks the account for reconnecting, as a failed sync would.
    """
    assert state.connection.id is not None
    try:
        async with advisory_lock(
            connection_lock_key(state.connection.id), wait=LOCK_WAIT_SECONDS
        ):
            return await call()
    except CalendarToolError as error:
        await _record_failure(db, row, state, error)
        raise
    except Exception as exc:
        mapped = calendar_tool_error(exc)
        if mapped is None:
            await finish_audit(db, row, outcome="failed", error_code="internal")
            raise
        await _record_failure(db, row, state, mapped)
        raise mapped from exc


async def _record_failure(
    db: AsyncSession, row: McpWriteAudit, state: CalendarState, error: CalendarToolError
) -> None:
    if error.code == "auth_expired":
        connection = state.connection
        connection.sync_status = CalendarSyncStatus.REAUTHORISATION_REQUIRED.value
        connection.sync_error = (
            "This calendar account needs to be reconnected before it can sync again."
        )
        db.add(connection)
    await finish_audit(db, row, outcome=_outcome_of(error), error_code=error.code)
