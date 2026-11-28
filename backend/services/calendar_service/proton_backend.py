"""Proton Calendar as a writable calendar backend, through the nojoin-proton helper.

Implements ``CalendarWriteBackend``. Every helper call here runs under the
account's advisory lock (re-entrant, so a call the neutral layer already
holds it for goes straight on) and with the session refreshed once when
Proton refuses the access token. Mail is ``send_notifications`` alone, which
the neutral layer calls only when notifying was asked for.
"""

from __future__ import annotations

from typing import Any

from backend.models.calendar import CalendarConnection, CalendarSource

from .capabilities import WriteCapabilities
from .models_dto import ProviderEventRecord
from .proton_helper import READ_TIMEOUT_SECONDS, HelperError
from .proton_mapping import records, target, tool_error, window
from .proton_session import LOCK_WAIT_SECONDS, run_op
from .rrule_limits import parse_rrule, proton_problems
from .tool_errors import CalendarToolError
from .write_audit import advisory_lock, connection_lock_key
from .write_backend import (
    EventDraft,
    EventPatch,
    EventRef,
    FieldProblem,
    ProviderContext,
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
