"""Proton Calendar as a writable calendar backend, through the nojoin-proton helper.

Implements ``CalendarWriteBackend``. Every helper call here runs under the
account's advisory lock (re-entrant, so a call the neutral layer already
holds it for goes straight on) and with the session refreshed once when
Proton refuses the access token. Mail is ``send_notifications`` alone, which
the neutral layer calls only when notifying was asked for.
"""

from __future__ import annotations

from typing import Any

from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.utils.timezones import utc_naive_to_timezone

from .capabilities import WriteCapabilities
from .models_dto import ProviderEventRecord
from .proton_helper import READ_TIMEOUT_SECONDS, WRITE_TIMEOUT_SECONDS, HelperError
from .proton_mapping import (
    attendee_value,
    event_fields,
    records,
    target,
    time_value,
    tool_error,
    window,
    with_hold_prefix,
    without_hold_prefix,
)
from .proton_session import LOCK_WAIT_SECONDS, run_op
from .rrule_limits import parse_rrule, proton_problems
from .tool_errors import CalendarToolError
from .write_audit import advisory_lock, connection_lock_key
from .write_backend import (
    EventDraft,
    EventPatch,
    EventRef,
    FieldProblem,
    NotificationPlan,
    NotificationReport,
    PlannedMail,
    ProviderContext,
    Response,
    Scope,
    WriteOutcome,
)

PROTON_CAPABILITIES = WriteCapabilities(
    writable=True,
    create=True,
    update=True,
    cancel=True,
    respond=True,
    holds="marker",
    invitations="email",
    occurrence_edits=True,
)
# Proton's own field limits (its web client's constants).
_LIMITS = {"title": 255, "description": 3000, "location": 255}
MAX_ATTENDEES = 100


def source_window(source: CalendarSource) -> dict[str, str] | None:
    return window(source.sync_window_start, source.sync_window_end)


async def helper_call(
    ctx: ProviderContext, op: str, args: dict[str, Any], *, timeout: float
) -> dict[str, Any]:
    """One helper operation for this account, its refusals mapped."""
    connection = ctx.connection
    try:
        async with advisory_lock(
            connection_lock_key(connection.id or 0), wait=LOCK_WAIT_SECONDS
        ):
            return await run_op(ctx.db, connection, op, args, timeout=timeout)
    except HelperError as error:
        raise tool_error(error) from error


def _text_problems(values: dict[str, Any]) -> list[FieldProblem]:
    problems = []
    for name, limit in _LIMITS.items():
        value = values.get(name)
        if isinstance(value, str) and len(value) > limit:
            problems.append(
                FieldProblem(
                    name, f"Proton keeps at most {limit:,} characters of {name}."
                )
            )
    return problems


class ProtonBackend:
    provider = "proton"

    def capabilities(
        self, connection: CalendarConnection, source: CalendarSource
    ) -> WriteCapabilities:
        return PROTON_CAPABILITIES

    def validate(
        self, source: CalendarSource, draft: EventDraft | EventPatch
    ) -> list[FieldProblem]:
        if isinstance(draft, EventDraft):
            values: dict[str, Any] = {
                "title": draft.title,
                "description": draft.description,
                "location": draft.location,
            }
            attendees = len(draft.attendees)
            rule = draft.recurrence
            first = draft.start.day or (
                draft.start.at.date() if draft.start.at else None
            )
        else:
            values = draft.changes
            attendees = len(draft.add_attendees)
            rule = draft.changes.get("recurrence")
            first = None
        problems = _text_problems(values)
        if attendees > MAX_ATTENDEES:
            problems.append(
                FieldProblem(
                    "attendees", f"Proton allows at most {MAX_ATTENDEES} attendees."
                )
            )
        if rule:
            problems += [
                FieldProblem("recurrence", message)
                for message in proton_problems(parse_rrule(rule), first)
            ]
        return problems

    async def get_event(
        self, ctx: ProviderContext, ref: EventRef
    ) -> ProviderEventRecord | None:
        args = {
            "calendar_id": ctx.source.provider_calendar_id,
            "target": target(ref),
            "window": source_window(ctx.source),
        }
        try:
            result = await helper_call(
                ctx, "get_event", args, timeout=READ_TIMEOUT_SECONDS
            )
        except CalendarToolError as error:
            if error.code == "not_found":
                return None
            raise
        found = records(result.get("events"))
        if ref.provider_event_id:
            exact = [item for item in found if item.remote_id == ref.provider_event_id]
            if exact:
                return exact[0]
        return found[0] if found else None

    async def create_event(
        self,
        ctx: ProviderContext,
        draft: EventDraft,
        *,
        uid: str,
        share_with_attendees: bool,
    ) -> WriteOutcome:
        args = {
            "calendar_id": ctx.source.provider_calendar_id,
            "uid": uid,
            "event": event_fields(draft),
            "share_with_attendees": share_with_attendees,
            "window": source_window(ctx.source),
        }
        result = await helper_call(
            ctx, "create_event", args, timeout=WRITE_TIMEOUT_SECONDS
        )
        return _remember(ctx, result)

    async def update_event(  # noqa: PLR0913 - the interface the design fixes
        self,
        ctx: ProviderContext,
        ref: EventRef,
        patch: EventPatch,
        *,
        scope: Scope,
        share_with_attendees: bool,
        expected_revision: str | None,
    ) -> WriteOutcome:
        row = await ctx.db.get(CalendarEvent, ref.event_id) if ref.event_id else None
        args = {
            "calendar_id": ctx.source.provider_calendar_id,
            "target": target(ref),
            "scope": scope,
            "patch": patch_fields(patch, row, _zone(ctx, row, patch)),
            "share_with_attendees": share_with_attendees,
            "share_with": patch.share_with if share_with_attendees else [],
            "expected_revision": expected_revision,
            "window": source_window(ctx.source),
        }
        result = await helper_call(
            ctx, "update_event", args, timeout=WRITE_TIMEOUT_SECONDS
        )
        return _remember(ctx, result)

    async def cancel_event(
        self,
        ctx: ProviderContext,
        ref: EventRef,
        *,
        scope: Scope,
        expected_revision: str | None,
    ) -> WriteOutcome:
        place = target(ref)
        args = {
            "calendar_id": ctx.source.provider_calendar_id,
            "target": place,
            "scope": scope,
            "expected_revision": expected_revision,
            "window": source_window(ctx.source),
        }
        result = await helper_call(
            ctx, "cancel_event", args, timeout=WRITE_TIMEOUT_SECONDS
        )
        if scope == "occurrence":
            ctx.scratch["recurrence_id"] = place["recurrence_id"]
        return _remember(ctx, result, before=True)

    async def respond(
        self,
        ctx: ProviderContext,
        ref: EventRef,
        response: Response,
        *,
        expected_revision: str | None,
    ) -> WriteOutcome:
        # An answer covers the whole series: one occurrence alone is not in v1.
        args = {
            "calendar_id": ctx.source.provider_calendar_id,
            "target": {**target(ref), "recurrence_id": None},
            "response": response,
            "expected_revision": expected_revision,
            "window": source_window(ctx.source),
        }
        result = await helper_call(ctx, "respond", args, timeout=WRITE_TIMEOUT_SECONDS)
        ctx.scratch["response"] = response
        return _remember(ctx, result)

    async def send_notifications(
        self, ctx: ProviderContext, plan: NotificationPlan
    ) -> NotificationReport:
        report = NotificationReport()
        itip = ctx.scratch.get("itip") or {}
        if not itip.get("ics"):
            report.failed = [(mail.email, "nothing_to_send") for mail in plan.mails]
            return report
        for method, mails in _by_method(plan.mails).items():
            args = itip_args(ctx, method, mails, itip, note=plan.note)
            result = await helper_call(
                ctx, "send_itip", args, timeout=WRITE_TIMEOUT_SECONDS
            )
            sent = {
                str(item.get("email", "")).casefold()
                for item in result.get("sent") or []
            }
            report.emailed += [mail for mail in mails if mail.email.casefold() in sent]
            report.failed += [
                (str(item.get("email")), str(item.get("error") or "provider_rejected"))
                for item in result.get("failed") or []
            ]
        return report


_WARNINGS = {
    "read_back_failed": "The change was saved at Proton, but reading it back failed.",
    "personal_part_not_updated": "The answer was saved, but Proton's own reminder "
    "settings for this event were not updated.",
}


def _warning_text(code: str) -> str:
    name, _, count = code.partition(":")
    if name == "attendee_keys_unavailable":
        return (
            f"{count} Proton attendee(s) could not be handed the event, so it will "
            "not appear in their Proton calendar until they open the invitation."
        )
    if name == "exceptions_removed":
        return f"{count} occurrence(s) edited on their own were reset to the series."
    return _WARNINGS.get(name, code)


def _remember(
    ctx: ProviderContext, result: dict[str, Any], *, before: bool = False
) -> WriteOutcome:
    """The neutral outcome, keeping what send_notifications needs later."""
    events = records(result.get("events"))
    shown = records(result.get("before")) if before else events
    if result.get("itip"):
        ctx.scratch["itip"] = result["itip"]
    if shown:
        ctx.scratch["summary"] = shown[0]
    stats = result.get("stats") or {}
    return WriteOutcome(
        events=events,
        deleted_keys=[str(key) for key in result.get("deleted_keys") or []],
        revision=result.get("revision"),
        warnings=[_warning_text(str(code)) for code in result.get("warnings") or []],
        verified=bool(result.get("verified", True)),
        shown_by_provider=list(result.get("shown_by_provider") or []),
        occurrences_affected=stats.get("occurrences"),
        exceptions_removed=int(stats.get("exceptions_removed") or 0),
    )


def _zone(ctx: ProviderContext, row: CalendarEvent | None, patch: EventPatch) -> str:
    return str(
        patch.changes.get("time_zone")
        or (row.start_time_zone if row is not None else None)
        or ctx.source.time_zone
        or ctx.user_zone
    )


_PATCH_NAMES = {
    "title": "title",
    "description": "description",
    "location": "location",
    "meeting_url": "url",
    "all_day": "all_day",
    "show_as": "show_as",
    "recurrence": "recurrence_rule",
}


def patch_fields(
    patch: EventPatch, row: CalendarEvent | None, zone: str
) -> dict[str, Any]:
    """A neutral patch as the helper's, with the hold prefix kept in step."""
    changes = patch.changes
    fields: dict[str, Any] = {
        helper_name: changes[name]
        for name, helper_name in _PATCH_NAMES.items()
        if name in changes
    }
    for name in ("start", "end"):
        if name in changes:
            fields[name] = time_value(changes[name], zone)
    was_hold = row is not None and row.status == "tentative"
    hold = changes["status"] == "tentative" if "status" in changes else was_hold
    if "status" in changes:
        fields["hold"] = hold
    base = changes.get("title", row.title if row is not None else None)
    if base is not None:
        title = with_hold_prefix(base) if hold else without_hold_prefix(base)
        if title != (row.title if row is not None else None) or "title" in changes:
            fields["title"] = title
    if patch.add_attendees:
        fields["add_attendees"] = [attendee_value(a) for a in patch.add_attendees]
    if patch.remove_attendees:
        fields["remove_attendees"] = list(patch.remove_attendees)
    return fields


_METHODS = {
    "invitation": "REQUEST",
    "update": "REQUEST",
    "cancellation": "CANCEL",
    "reply": "REPLY",
}


def _by_method(mails: list[PlannedMail]) -> dict[str, list[PlannedMail]]:
    groups: dict[str, list[PlannedMail]] = {}
    for mail in mails:
        groups.setdefault(_METHODS[mail.kind], []).append(mail)
    return groups


def _when(record: ProviderEventRecord | None, zone: str) -> str:
    if record is None:
        return ""
    if record.start_date is not None:
        return record.start_date.strftime("%a %-d %b")
    moment = utc_naive_to_timezone(record.starts_at, zone)
    return moment.strftime("%a %-d %b %H:%M") if moment else ""


def itip_args(
    ctx: ProviderContext,
    method: str,
    mails: list[PlannedMail],
    itip: dict[str, Any],
    *,
    note: str | None,
) -> dict[str, Any]:
    """The send_itip request: Python writes the subject and body, in the
    user's time zone, from the event as written."""
    record = ctx.scratch.get("summary")
    title = without_hold_prefix(record.title) if record is not None else "an event"
    when = _when(record, ctx.user_zone)
    sender = ctx.connection.display_name or ctx.connection.email or "Someone"
    kinds = {mail.kind for mail in mails}
    response = ctx.scratch.get("response")
    if method == "REPLY":
        verb = {
            "accepted": "Accepted",
            "declined": "Declined",
            "tentative": "Tentative",
        }
        subject = f"{verb.get(str(response), 'Reply')}: {title}"
        body = f"{sender} has answered {title}: {response}."
    elif method == "CANCEL":
        subject = f"Cancelled: {title} @ {when}"
        body = f"{sender} has cancelled {title} on {when}."
    elif kinds == {"update"}:
        subject = f"Updated invitation: {title} @ {when}"
        body = f"{sender} has updated {title}, now on {when}."
    else:
        subject = f"Invitation: {title} @ {when}"
        body = f"{sender} has invited you to {title} on {when}."
    return {
        "calendar_id": ctx.source.provider_calendar_id,
        "method": method,
        "ics": itip["ics"],
        "recurrence_id": ctx.scratch.get("recurrence_id")
        if method == "CANCEL"
        else None,
        "recipients": [{"email": mail.email, "name": mail.name} for mail in mails],
        "subject": subject[:255],
        "body": body,
        "response": response if method == "REPLY" else None,
        "note": note if method == "REPLY" else None,
    }
