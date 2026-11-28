"""Writes to an event already in the user's calendar: update, cancel, respond.

Each resolves the event among the user's, checks its arguments and the state
it found, previews on a dry run, then calls the provider's backend under the
account's lock through the shared steps in ``write_steps``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, func, select

from backend.models.calendar import CalendarEvent
from backend.utils.timezones import utc_naive_to_aware

from .event_view import CalendarState, event_view, owns_event
from .notifications import (
    Person,
    Plan,
    invite_records,
    notifications_object,
    people,
    plan_cancel,
    plan_respond,
    plan_update,
    record_invites,
    unchanged_plan,
)
from .reads import user_event
from .tool_errors import CalendarToolError
from .write_audit import (
    Actor,
    finish_audit,
    new_audit_row,
    save_audit,
)
from .write_backend import (
    CalendarWriteBackend,
    EventTime,
    NotificationReport,
    write_backend_for,
)
from .write_inputs import (
    CancelRequest,
    PatchPlan,
    RespondRequest,
    UpdateRequest,
    build_patch,
    first_day,
    patched_record,
    stored_zone,
)
from .write_steps import (
    _call_backend,
    _check_revision,
    _first_problem,
    _log_refusal,
    _preview,
    _require,
    _result,
    _scope_for,
    _send,
    _store_outcome,
    event_ref,
    event_uid,
    provider_context,
)

# --- update, cancel, respond --------------------------------------------------


def _nothing_asked(request: UpdateRequest) -> bool:
    named = [
        request.title,
        request.start,
        request.end,
        request.time_zone,
        request.all_day,
        request.description,
        request.location,
        request.meeting_url,
        request.status,
        request.show_as,
        request.recurrence,
    ]
    return all(value is None for value in named) and not (
        request.add_attendees or request.remove_attendees or request.clear_fields
    )


def _series_rules(
    request: UpdateRequest, event: CalendarEvent, scope: str, plan: PatchPlan, zone: str
) -> None:
    touches_rule = (
        request.recurrence is not None or "recurrence" in request.clear_fields
    )
    if touches_rule and scope == "occurrence":
        raise CalendarToolError(
            "invalid_argument",
            'Changing the recurrence needs scope "series".',
            field="scope",
        )
    if scope != "series" or request.start is None:
        return
    original = event.original_start_date or (
        first_day(EventTime(at=utc_naive_to_aware(event.original_start)), zone)
        if event.original_start
        else None
    )
    if original is not None and first_day(plan.start, zone) != original:
        raise CalendarToolError(
            "unsupported",
            "A series cannot be moved to another date this way.",
            field="start",
            details={
                "hint": "Cancel the series and create it again on the new date, or "
                "edit the occurrences one by one."
            },
        )


async def _series_count(db: AsyncSession, event: CalendarEvent) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(CalendarEvent)
                .where(CalendarEvent.calendar_id == event.calendar_id)
                .where(CalendarEvent.series_key == event.series_key)
            )
        ).scalar_one()
    )


def _update_checks(
    request: UpdateRequest,
    event: CalendarEvent,
    state: CalendarState,
    patch_plan: PatchPlan,
    zone: str,
) -> str:
    """The update's scope, once the state it found allows the change."""
    scope = _scope_for(event, request.scope, state)
    _series_rules(request, event, scope, patch_plan, zone)
    _require(state, "update")
    if patch_plan.status == "tentative" and not state.capabilities.holds:
        raise CalendarToolError("unsupported", "This calendar cannot hold time.")
    if not owns_event(event):
        raise CalendarToolError(
            "not_organiser",
            "Someone else organises this event. Use respond_to_calendar_invitation "
            "to answer it.",
        )
    _check_revision(event, request.expected_revision)
    return scope


@dataclass
class _UpdatePeople:
    kept: list[Person]
    added: list[Person]
    plan: Plan
    uid: str
    nothing: bool


async def _update_people(
    db: AsyncSession,
    event: CalendarEvent,
    state: CalendarState,
    patch_plan: PatchPlan,
    notify: bool,
) -> _UpdatePeople:
    patch = patch_plan.patch
    removed_keys = {email.casefold() for email in patch.remove_attendees}
    before = people(event.attendees, state.connection.email)
    kept = [p for p in before if p.email.casefold() not in removed_keys]
    removed = [p for p in before if p.email.casefold() in removed_keys]
    added = [Person(a.email, a.name) for a in patch.add_attendees]
    nothing = not patch.changes and not added and not removed
    uid = event_uid(event)
    records = await invite_records(db, state.source.id or 0, uid)
    plan = (
        unchanged_plan(before)
        if nothing
        else plan_update(
            kept,
            added,
            removed,
            records=records,
            notify=notify,
            hold_after=patch_plan.status == "tentative",
        )
    )
    return _UpdatePeople(kept=kept, added=added, plan=plan, uid=uid, nothing=nothing)


async def update_event(
    db: AsyncSession, actor: Actor, request: UpdateRequest
) -> dict[str, Any]:
    event, state = await user_event(db, actor.user, request.event_id)
    if _nothing_asked(request):
        raise CalendarToolError("invalid_argument", "Nothing to update.")
    patch_plan = build_patch(request, event, state, actor.user_zone)
    backend = write_backend_for(state.connection.provider)
    _first_problem(backend.validate(state.source, patch_plan.patch))
    if patch_plan.status == "tentative" and request.notify_attendees:
        raise CalendarToolError(
            "invalid_argument",
            "A hold stays private: confirm it (status confirmed) to notify its attendees.",
            field="notify_attendees",
        )
    zone = stored_zone(event, state, actor.user_zone)
    snapshot: dict[str, Any] = {
        "action": "update",
        "scope": request.scope,
        "state": state,
        "event": event,
    }
    try:
        scope = _update_checks(request, event, state, patch_plan, zone)
    except CalendarToolError as error:
        if not request.dry_run:
            await _log_refusal(db, actor, error, **snapshot)
        raise
    snapshot["scope"] = scope
    who = await _update_people(db, event, state, patch_plan, request.notify_attendees)
    if request.dry_run or who.nothing:
        preview = _preview(
            patched_record(event, patch_plan, zone),
            state,
            actor.user_zone,
            event_id=event.id,
        )
        result = _result(
            "no_change" if who.nothing else "would_update",
            dry_run=request.dry_run,
            verified=False,
            scope=scope,
            event=event_view(preview, state, user_zone=actor.user_zone, full=True),
            notifications=notifications_object(
                who.plan,
                notify_requested=request.notify_attendees,
                dry_run=request.dry_run,
            ),
            changes=patch_plan.changes,
            warnings=patch_plan.warnings,
            occurrences=await _series_count(db, event) if scope == "series" else None,
        )
        if who.nothing and not request.dry_run:
            row = await save_audit(
                db, new_audit_row(actor, outcome="succeeded", **snapshot)
            )
            result["audit_id"] = row.id
        return result
    return await _write_update(
        db, actor, request, event, state, backend, patch_plan, who, scope, snapshot
    )


async def _write_update(  # noqa: PLR0913 - the update's checked parts
    db: AsyncSession,
    actor: Actor,
    request: UpdateRequest,
    event: CalendarEvent,
    state: CalendarState,
    backend: CalendarWriteBackend,
    patch_plan: PatchPlan,
    who: _UpdatePeople,
    scope: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    row = await save_audit(db, new_audit_row(actor, **snapshot))
    ctx = provider_context(db, state, actor.user_zone)
    ref = event_ref(event, state)
    # Storing the read-back updates ``event`` itself, the same row.
    before_revision = event.provider_revision
    share = request.notify_attendees and patch_plan.status != "tentative"
    if share:
        kept = {person.email.casefold() for person in who.kept}
        patch_plan.patch.share_with = [
            mail.email
            for mail in who.plan.mails
            if mail.kind == "invitation" and mail.email.casefold() in kept
        ]
    outcome = await _call_backend(
        db,
        row,
        state,
        lambda: backend.update_event(
            ctx,
            ref,
            patch_plan.patch,
            scope=scope,
            share_with_attendees=share,
            expected_revision=request.expected_revision,
        ),
    )
    stored = await _store_outcome(db, state, outcome, actor.user_zone)
    updated = next(
        (item for item in stored if item.id == event.id), stored[0] if stored else None
    )
    report = NotificationReport()
    if request.notify_attendees:
        report = await _send(backend, ctx, ref, who.plan)
    await record_invites(
        db, state.source.id or 0, who.uid, [*who.kept, *who.added], report.emailed
    )
    await db.commit()
    changes = list(patch_plan.changes)
    if outcome.revision and outcome.revision != before_revision:
        changes.append(
            {"field": "revision", "before": before_revision, "after": outcome.revision}
        )
    notifications = notifications_object(
        who.plan,
        notify_requested=request.notify_attendees,
        dry_run=False,
        report=report,
        shown_by_provider=outcome.shown_by_provider,
    )
    result = _result(
        "updated",
        dry_run=False,
        verified=outcome.verified,
        scope=scope,
        event=event_view(updated, state, user_zone=actor.user_zone, full=True)
        if updated
        else None,
        notifications=notifications,
        changes=changes,
        warnings=[*patch_plan.warnings, *outcome.warnings],
        occurrences=outcome.occurrences_affected,
        exceptions_removed=outcome.exceptions_removed,
        audit_id=row.id,
    )
    await finish_audit(
        db,
        row,
        outcome="succeeded",
        changes=changes,
        notifications=notifications,
        event=updated,
    )
    return result


async def cancel_event(
    db: AsyncSession, actor: Actor, request: CancelRequest
) -> dict[str, Any]:
    event, state = await user_event(db, actor.user, request.event_id)
    backend = write_backend_for(state.connection.provider)
    snapshot = {
        "action": "cancel",
        "scope": request.scope,
        "state": state,
        "event": event,
    }
    try:
        scope = _scope_for(event, request.scope, state)
        snapshot["scope"] = scope
        _require(state, "cancel")
        if not owns_event(event):
            raise CalendarToolError(
                "not_organiser",
                "Someone else organises this event. To turn it down, use "
                'respond_to_calendar_invitation with response "declined".',
            )
        _check_revision(event, request.expected_revision)
    except CalendarToolError as error:
        if not request.dry_run:
            await _log_refusal(db, actor, error, **snapshot)
        raise
    uid = event_uid(event)
    records = await invite_records(db, state.source.id or 0, uid)
    attendees = people(event.attendees, state.connection.email)
    plan = plan_cancel(attendees, records=records, notify=request.notify_attendees)
    last_state = event_view(event, state, user_zone=actor.user_zone, full=True)
    last_state["status"] = "cancelled"
    occurrences = await _series_count(db, event) if scope == "series" else None
    if request.dry_run:
        return _result(
            "would_cancel",
            dry_run=True,
            verified=False,
            scope=scope,
            event=last_state,
            notifications=notifications_object(
                plan, notify_requested=request.notify_attendees, dry_run=True
            ),
            warnings=plan.warnings,
            occurrences=occurrences,
        )
    row = await save_audit(db, new_audit_row(actor, **snapshot))
    ctx = provider_context(db, state, actor.user_zone)
    ref = event_ref(event, state)
    outcome = await _call_backend(
        db,
        row,
        state,
        lambda: backend.cancel_event(
            ctx, ref, scope=scope, expected_revision=request.expected_revision
        ),
    )
    gone = delete(CalendarEvent).where(CalendarEvent.calendar_id == event.calendar_id)
    if scope == "series":
        gone = gone.where(CalendarEvent.series_key == event.series_key)
    else:
        gone = gone.where(CalendarEvent.id == event.id)
    await db.execute(gone)
    await _store_outcome(db, state, outcome, actor.user_zone)
    report = NotificationReport()
    if request.notify_attendees:
        report = await _send(backend, ctx, ref, plan)
    await record_invites(db, state.source.id or 0, uid, [], report.emailed)
    await db.commit()
    notifications = notifications_object(
        plan, notify_requested=request.notify_attendees, dry_run=False, report=report
    )
    result = _result(
        "cancelled",
        dry_run=False,
        verified=outcome.verified,
        scope=scope,
        event=last_state,
        notifications=notifications,
        changes=[{"field": "status", "before": event.status, "after": "cancelled"}],
        warnings=[
            *(plan.warnings if not request.notify_attendees else []),
            *outcome.warnings,
        ],
        occurrences=outcome.occurrences_affected
        if outcome.occurrences_affected is not None
        else occurrences,
        exceptions_removed=outcome.exceptions_removed,
        audit_id=row.id,
    )
    # The whole last state, so the user can recreate the event by hand.
    await finish_audit(
        db, row, outcome="succeeded", changes=last_state, notifications=notifications
    )
    return result


async def respond_to_invitation(
    db: AsyncSession, actor: Actor, request: RespondRequest
) -> dict[str, Any]:
    event, state = await user_event(db, actor.user, request.event_id)
    if request.note is not None and not request.notify_organiser:
        raise CalendarToolError(
            "invalid_argument",
            "note travels in the reply email, so it needs notify_organiser true.",
            field="note",
        )
    backend = write_backend_for(state.connection.provider)
    scope = "series" if event.series_key else "single"
    snapshot = {"action": "respond", "scope": scope, "state": state, "event": event}
    try:
        _require(state, "respond")
        if event.is_organiser is not False:
            raise CalendarToolError(
                "not_attendee",
                "This is the user's own event, not an invitation. Use "
                "update_calendar_event or cancel_calendar_event instead.",
            )
        _check_revision(event, request.expected_revision)
    except CalendarToolError as error:
        if not request.dry_run:
            await _log_refusal(db, actor, error, **snapshot)
        raise
    organiser = None
    if isinstance(event.organiser, dict) and event.organiser.get("email"):
        organiser = Person(str(event.organiser["email"]), event.organiser.get("name"))
    unchanged = event.my_response == request.response
    plan = plan_respond(organiser, notify=request.notify_organiser, unchanged=unchanged)
    changes = (
        []
        if unchanged
        else [
            {
                "field": "my_response",
                "before": event.my_response,
                "after": request.response,
            }
        ]
    )
    if request.dry_run or unchanged:
        result = _result(
            "no_change" if unchanged else "would_respond",
            dry_run=request.dry_run,
            verified=False,
            scope=scope,
            event=event_view(event, state, user_zone=actor.user_zone, full=True),
            notifications=notifications_object(
                plan, notify_requested=request.notify_organiser, dry_run=request.dry_run
            ),
            changes=changes,
        )
        if unchanged and not request.dry_run:
            row = await save_audit(
                db, new_audit_row(actor, outcome="succeeded", **snapshot)
            )
            result["audit_id"] = row.id
        return result
    row = await save_audit(db, new_audit_row(actor, **snapshot))
    ctx = provider_context(db, state, actor.user_zone)
    ref = event_ref(event, state)
    outcome = await _call_backend(
        db,
        row,
        state,
        lambda: backend.respond(
            ctx, ref, request.response, expected_revision=request.expected_revision
        ),
    )
    stored = await _store_outcome(db, state, outcome, actor.user_zone)
    answered = next(
        (item for item in stored if item.id == event.id), stored[0] if stored else event
    )
    report = NotificationReport()
    if request.notify_organiser:
        report = await _send(backend, ctx, ref, plan, note=request.note)
    notifications = notifications_object(
        plan,
        notify_requested=request.notify_organiser,
        dry_run=False,
        report=report,
        shown_by_provider=outcome.shown_by_provider,
    )
    result = _result(
        "responded",
        dry_run=False,
        verified=outcome.verified,
        scope=scope,
        event=event_view(answered, state, user_zone=actor.user_zone, full=True),
        notifications=notifications,
        changes=changes,
        warnings=outcome.warnings,
        audit_id=row.id,
    )
    await finish_audit(
        db,
        row,
        outcome="succeeded",
        changes=changes,
        notifications=notifications,
        event=answered,
    )
    return result
