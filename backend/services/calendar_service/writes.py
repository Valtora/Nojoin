"""Provider-neutral orchestration of the four calendar write tools.

Each write runs the same steps: resolve the calendar or event among the
user's; check the arguments (no change-log row for a call that fails here);
check the state it found (writable, organiser, scope, revision), logging a
refusal; on a dry run, stop and return what would happen; for a keyed create,
deduplicate; then, holding the account's lock, call the provider's backend,
store what it read back, send mail only when the call asked, record who was
invited, finish the change-log row and build the result.

Backends speak only the neutral model in ``write_backend``. The result shape
is the one documented in docs/MCP.md: ``outcome``, ``dry_run``, ``verified``,
``scope``, ``event``, ``affected``, ``changes``, ``notifications``,
``warnings``, ``audit_id`` and ``idempotent_replay``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.calendar import CalendarEvent
from backend.models.mcp_write_audit import McpWriteAudit

from .event_view import CalendarState, event_view
from .notifications import (
    Person,
    Plan,
    notifications_object,
    plan_create,
    record_invites,
)
from .reads import user_calendars
from .tool_errors import CalendarToolError
from .write_audit import (
    Actor,
    advisory_lock,
    connection_lock_key,
    fingerprint,
    finish_audit,
    idempotency_lock_key,
    keyed_audit,
    new_audit_row,
    save_audit,
)
from .write_backend import (
    CalendarWriteBackend,
    EventDraft,
    EventRef,
    NotificationReport,
    ProviderContext,
    WriteOutcome,
    write_backend_for,
)
from .write_inputs import (
    CreateRequest,
    build_draft,
    draft_record,
    naive_utc,
)
from .write_steps import (
    _IDEMPOTENCY_KEY,
    IDEMPOTENCY_WAIT_SECONDS,
    LOCK_WAIT_SECONDS,
    _call_backend,
    _first_problem,
    _log_refusal,
    _preview,
    _require,
    _result,
    _send,
    _store_outcome,
    derived_uid,
    event_ref,
    provider_context,
)
from .writes_existing import cancel_event, respond_to_invitation, update_event

__all__ = [
    "cancel_event",
    "create_event",
    "derived_uid",
    "respond_to_invitation",
    "update_event",
]

# --- create ------------------------------------------------------------------


def _fingerprint_of(request: CreateRequest) -> str:
    arguments = {
        name: value
        for name, value in vars(request).items()
        if name not in {"idempotency_key", "dry_run", "attendees"}
    }
    arguments["attendees"] = [vars(attendee) for attendee in request.attendees]
    return fingerprint(arguments)


async def create_event(
    db: AsyncSession, actor: Actor, request: CreateRequest
) -> dict[str, Any]:
    (state,) = await user_calendars(db, actor.user, [request.calendar_id])
    key = request.idempotency_key
    if key is not None and not _IDEMPOTENCY_KEY.match(key):
        raise CalendarToolError(
            "invalid_argument",
            "idempotency_key is 8 to 128 characters of letters, digits and . _ : -",
            field="idempotency_key",
        )
    draft, warnings = build_draft(request, state, actor.user_zone)
    backend = write_backend_for(state.connection.provider)
    _first_problem(backend.validate(state.source, draft))
    hold = draft.status == "tentative"
    scope = "series" if draft.recurrence else "single"
    snapshot = {
        "action": "create",
        "scope": scope,
        "state": state,
        "event": None,
        "title": draft.title,
        "start": naive_utc(draft.start.at),
    }
    try:
        _require(state, "create")
        if hold and not state.capabilities.holds:
            raise CalendarToolError("unsupported", "This calendar cannot hold time.")
    except CalendarToolError as error:
        if not request.dry_run:
            await _log_refusal(db, actor, error, **snapshot)
        raise
    attendees = [Person(a.email, a.name) for a in draft.attendees]
    plan = plan_create(attendees, notify=request.notify_attendees, hold=hold)
    uid = derived_uid(actor.user_id, key) if key else f"{uuid.uuid4()}@nojoin"
    if request.dry_run:
        preview = _preview(
            draft_record(draft, f"preview:{uid}", uid=uid),
            state,
            actor.user_zone,
            event_id=None,
        )
        return _result(
            "would_create",
            dry_run=True,
            verified=False,
            scope=scope,
            event=event_view(preview, state, user_zone=actor.user_zone, full=True),
            notifications=notifications_object(
                plan, notify_requested=request.notify_attendees, dry_run=True
            ),
            warnings=warnings,
        )
    job = _Create(
        db, actor, state, backend, request, draft, plan, attendees, uid, scope, warnings
    )
    if key is None:
        return await job.run(save_audit_row=new_audit_row(actor, **snapshot))
    async with advisory_lock(
        idempotency_lock_key(actor.user_id, key), wait=IDEMPOTENCY_WAIT_SECONDS
    ):
        return await job.run_keyed(key, snapshot)


@dataclass
class _Create:
    """One create past its checks: deduplicated, written, stored and logged."""

    db: AsyncSession
    actor: Actor
    state: CalendarState
    backend: CalendarWriteBackend
    request: CreateRequest
    draft: EventDraft
    plan: Plan
    attendees: list[Person]
    uid: str
    scope: str
    warnings: list[str]

    async def run_keyed(self, key: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        digest = _fingerprint_of(self.request)
        row = await keyed_audit(self.db, self.actor.user_id, key)
        if row is None:
            fresh = new_audit_row(
                self.actor, idempotency_key=key, request_fingerprint=digest, **snapshot
            )
            return await self.run(save_audit_row=fresh)
        if row.request_fingerprint != digest:
            error = CalendarToolError(
                "idempotency_conflict",
                "This idempotency_key was used for a different request. Use a new key.",
                field="idempotency_key",
            )
            await _log_refusal(self.db, self.actor, error, **snapshot)
            raise error
        if row.outcome == "succeeded" and row.response_snapshot:
            return await self._replay(row)
        if row.outcome in {"pending", "uncertain"}:
            landed = await self._find_landed()
            if landed is not None:
                return await self._finish(row, landed, replay=True)
        row.outcome = "pending"
        row.error_code = None
        return await self.run(save_audit_row=row)

    async def _replay(self, row: McpWriteAudit) -> dict[str, Any]:
        result = dict(row.response_snapshot or {})
        result["idempotent_replay"] = True
        if row.calendar_event_id is not None:
            event = await self.db.get(CalendarEvent, row.calendar_event_id)
            if event is not None:
                result["event"] = event_view(
                    event, self.state, user_zone=self.actor.user_zone, full=True
                )
        return result

    async def _find_landed(self) -> WriteOutcome | None:
        ctx = provider_context(self.db, self.state, self.actor.user_zone)
        ref = EventRef(
            calendar_id=self.state.source.id or 0,
            provider_calendar_id=self.state.source.provider_calendar_id,
            uid=self.uid,
        )
        assert self.state.connection.id is not None
        async with advisory_lock(
            connection_lock_key(self.state.connection.id), wait=LOCK_WAIT_SECONDS
        ):
            record = await self.backend.get_event(ctx, ref)
        return WriteOutcome(events=[record]) if record is not None else None

    async def run(self, *, save_audit_row: McpWriteAudit) -> dict[str, Any]:
        row = await save_audit(self.db, save_audit_row)
        ctx = provider_context(self.db, self.state, self.actor.user_zone)
        share = self.request.notify_attendees and self.draft.status != "tentative"
        try:
            outcome = await _call_backend(
                self.db,
                row,
                self.state,
                lambda: self.backend.create_event(
                    ctx, self.draft, uid=self.uid, share_with_attendees=share
                ),
            )
        except CalendarToolError as error:
            if (
                error.details.get("outcome_unknown")
                and self.request.idempotency_key is None
            ):
                raise CalendarToolError(
                    "provider_unavailable",
                    "The provider did not answer in time and the event may have been "
                    "created. List the calendar before trying again.",
                    details={"outcome_unknown": True},
                ) from error
            raise
        return await self._finish(row, outcome, ctx=ctx)

    async def _finish(
        self,
        row: McpWriteAudit,
        outcome: WriteOutcome,
        *,
        ctx: ProviderContext | None = None,
        replay: bool = False,
    ) -> dict[str, Any]:
        stored = await _store_outcome(
            self.db, self.state, outcome, self.actor.user_zone
        )
        event = stored[0] if stored else None
        report = NotificationReport()
        if (
            self.request.notify_attendees
            and not replay
            and ctx is not None
            and event is not None
        ):
            report = await _send(
                self.backend, ctx, event_ref(event, self.state), self.plan
            )
        await record_invites(
            self.db, self.state.source.id or 0, self.uid, self.attendees, report.emailed
        )
        await self.db.commit()
        notifications = notifications_object(
            self.plan,
            notify_requested=self.request.notify_attendees,
            dry_run=False,
            report=report,
            shown_by_provider=outcome.shown_by_provider,
        )
        result = _result(
            "created",
            dry_run=False,
            verified=outcome.verified,
            scope=self.scope,
            event=event_view(
                event, self.state, user_zone=self.actor.user_zone, full=True
            )
            if event
            else None,
            notifications=notifications,
            warnings=[*self.warnings, *outcome.warnings],
            audit_id=row.id,
        )
        result["idempotent_replay"] = replay
        await finish_audit(
            self.db,
            row,
            outcome="succeeded",
            changes=[],
            notifications=notifications,
            response_snapshot=result,
            event=event,
        )
        return result
