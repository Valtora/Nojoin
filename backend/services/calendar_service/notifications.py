"""Who hears about a calendar write, planned before it and reported after.

Nobody is emailed unless the call asked: ``notify_attendees`` is off by
default on create, update and cancel. Replies to an invitation are the one
exception the maintainer chose (28 Nov 2026): ``notify_organiser`` defaults
to on. Every write result, dry runs included, carries the notifications
object built here, so an agent can always tell the user who was told.

"Was invited" means Nojoin's own invite record says so, or the event was not
created through Nojoin (it has no invite records), in which case whoever
created it will have sent the invitations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.calendar import CalendarAttendeeInvite

from .text_utils import _utc_now
from .write_backend import NotificationReport, PlannedMail

NOTIFY_ATTENDEES_OFF = "notify_attendees_off"
NOTIFY_ORGANISER_OFF = "notify_organiser_off"
IS_YOU = "is_you"
HOLD_NOT_CONFIRMED = "hold_not_confirmed"
UNCHANGED = "unchanged"


@dataclass
class Plan:
    mails: list[PlannedMail] = field(default_factory=list)
    not_emailed: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def skip(self, email: str, reason: str, name: str | None = None) -> None:
        self.not_emailed.append({"email": email, "reason": reason, "name": name})


@dataclass(frozen=True)
class Person:
    email: str
    name: str | None = None
    is_you: bool = False


@dataclass(frozen=True)
class InviteRecords:
    invited: frozenset[str]
    through_nojoin: bool

    def was_invited(self, email: str) -> bool:
        return not self.through_nojoin or email.casefold() in self.invited


def people(
    entries: list[dict[str, Any]] | None, account_email: str | None
) -> list[Person]:
    own = (account_email or "").strip().casefold()
    found: list[Person] = []
    for entry in entries or []:
        email = entry.get("email") if isinstance(entry, dict) else None
        if not isinstance(email, str) or not email:
            continue
        found.append(
            Person(
                email=email,
                name=entry.get("name"),
                is_you=bool(entry.get("is_self")) or email.casefold() == own,
            )
        )
    return found


async def invite_records(db: AsyncSession, source_id: int, uid: str) -> InviteRecords:
    rows = (
        await db.execute(
            select(CalendarAttendeeInvite)
            .where(CalendarAttendeeInvite.calendar_source_id == source_id)
            .where(CalendarAttendeeInvite.event_uid == uid)
        )
    ).scalars()
    rows = list(rows)
    return InviteRecords(
        invited=frozenset(
            row.email
            for row in rows
            if row.invited_at is not None and row.cancelled_at is None
        ),
        through_nojoin=bool(rows),
    )


def plan_create(attendees: list[Person], *, notify: bool, hold: bool) -> Plan:
    plan = Plan()
    for person in attendees:
        if person.is_you:
            plan.skip(person.email, IS_YOU, person.name)
        elif hold:
            plan.skip(person.email, HOLD_NOT_CONFIRMED, person.name)
        elif notify:
            plan.mails.append(PlannedMail(person.email, "invitation", person.name))
        else:
            plan.skip(person.email, NOTIFY_ATTENDEES_OFF, person.name)
    return plan


def plan_update(  # noqa: PLR0913 - one keyword per input of the plan
    kept: list[Person],
    added: list[Person],
    removed: list[Person],
    *,
    records: InviteRecords,
    notify: bool,
    hold_after: bool,
) -> Plan:
    plan = Plan()
    for person in [*kept, *added]:
        if person.is_you:
            plan.skip(person.email, IS_YOU, person.name)
        elif hold_after:
            plan.skip(person.email, HOLD_NOT_CONFIRMED, person.name)
        elif not notify:
            plan.skip(person.email, NOTIFY_ATTENDEES_OFF, person.name)
        else:
            invited = person in kept and records.was_invited(person.email)
            kind = "update" if invited else "invitation"
            plan.mails.append(PlannedMail(person.email, kind, person.name))
    for person in removed:
        if not records.was_invited(person.email):
            plan.skip(person.email, UNCHANGED, person.name)
        elif notify:
            plan.mails.append(PlannedMail(person.email, "cancellation", person.name))
        else:
            plan.skip(person.email, NOTIFY_ATTENDEES_OFF, person.name)
    return plan


def plan_cancel(
    attendees: list[Person], *, records: InviteRecords, notify: bool
) -> Plan:
    plan = Plan()
    left_with_it = 0
    for person in attendees:
        if person.is_you:
            plan.skip(person.email, IS_YOU, person.name)
        elif not records.was_invited(person.email):
            # Never invited, so the event was never in their calendar.
            plan.skip(person.email, UNCHANGED, person.name)
        elif notify:
            plan.mails.append(PlannedMail(person.email, "cancellation", person.name))
        else:
            plan.skip(person.email, NOTIFY_ATTENDEES_OFF, person.name)
            left_with_it += 1
    if left_with_it:
        plan.warnings.append(
            f"{_count(left_with_it, 'person')} invited earlier still "
            f"{'has' if left_with_it == 1 else 'have'} this event in their calendar."
        )
    return plan


def plan_respond(organiser: Person | None, *, notify: bool, unchanged: bool) -> Plan:
    plan = Plan()
    if organiser is None:
        return plan
    if unchanged:
        plan.skip(organiser.email, UNCHANGED, organiser.name)
    elif notify:
        plan.mails.append(PlannedMail(organiser.email, "reply", organiser.name))
    else:
        plan.skip(organiser.email, NOTIFY_ORGANISER_OFF, organiser.name)
    return plan


def unchanged_plan(attendees: list[Person]) -> Plan:
    plan = Plan()
    for person in attendees:
        plan.skip(person.email, IS_YOU if person.is_you else UNCHANGED, person.name)
    return plan


def _count(number: int, noun: str) -> str:
    plural = "people" if noun == "person" else f"{noun}s"
    return f"{number} {noun if number == 1 else plural}"


def _names(entries: list[dict[str, Any]]) -> str:
    labels = [entry.get("name") or entry["email"] for entry in entries]
    if len(labels) > 3:
        return f"{len(labels)} people"
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + f" and {labels[-1]}"


def _summary(
    sent: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> str:
    parts: list[str] = []
    if sent:
        parts.append(f"{'Would email' if dry_run else 'Emailed'} {_names(sent)}.")
    else:
        parts.append("Nobody would be told." if dry_run else "Nobody was told.")
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for entry in skipped:
        by_reason.setdefault(entry["reason"], []).append(entry)
    holds = by_reason.get(HOLD_NOT_CONFIRMED, [])
    if holds:
        parts.append(
            f"{_count(len(holds), 'attendee')} {'is' if len(holds) == 1 else 'are'} "
            "on the hold and will be invited only when it is confirmed with "
            "notify_attendees true."
        )
    quiet = by_reason.get(NOTIFY_ATTENDEES_OFF, [])
    if quiet:
        verb = "is" if len(quiet) == 1 else "are"
        parts.append(
            f"{_names(quiet)} {verb} on the event but {'was' if len(quiet) == 1 else 'were'} not told."
        )
    for entry in by_reason.get(NOTIFY_ORGANISER_OFF, []):
        parts.append(
            f"The organiser, {entry.get('name') or entry['email']}, was not emailed."
        )
    if failed:
        parts.append(
            f"Mail to {_names(failed)} could not be sent; the change itself was saved."
        )
    return " ".join(parts)


def notifications_object(
    plan: Plan,
    *,
    notify_requested: bool,
    dry_run: bool,
    report: NotificationReport | None = None,
    shown_by_provider: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The ``notifications`` every write result carries."""
    if dry_run or report is None:
        sent = [{"email": m.email, "kind": m.kind, "name": m.name} for m in plan.mails]
        failed: list[dict[str, Any]] = []
    else:
        sent = [
            {"email": m.email, "kind": m.kind, "name": m.name} for m in report.emailed
        ]
        failed = [{"email": email, "error": error} for email, error in report.failed]
    shown = shown_by_provider or []
    summary = _summary(sent, plan.not_emailed, failed, dry_run=dry_run)
    if shown:
        summary += f" {_names(shown)} sees the change in their own calendar."
    return {
        "notify_requested": notify_requested,
        "anyone_notified": bool(sent and not dry_run) or bool(shown),
        "summary": summary,
        "would_email" if dry_run else "emailed": [
            {"email": entry["email"], "kind": entry["kind"]} for entry in sent
        ],
        "not_emailed": [
            {"email": entry["email"], "reason": entry["reason"]}
            for entry in plan.not_emailed
        ],
        "shown_by_provider": [{"email": entry["email"]} for entry in shown],
        "failed": failed,
    }


async def record_invites(
    db: AsyncSession,
    source_id: int,
    uid: str,
    attendees: list[Person],
    emailed: list[PlannedMail],
) -> None:
    """Note each attendee of an event Nojoin wrote, and who got which mail."""
    now = _utc_now()
    existing = {
        row.email: row
        for row in (
            await db.execute(
                select(CalendarAttendeeInvite)
                .where(CalendarAttendeeInvite.calendar_source_id == source_id)
                .where(CalendarAttendeeInvite.event_uid == uid)
            )
        ).scalars()
    }
    kinds = {mail.email.casefold(): mail.kind for mail in emailed}
    for email in {
        person.email.casefold() for person in attendees if not person.is_you
    } | set(kinds):
        row = existing.get(email) or CalendarAttendeeInvite(
            calendar_source_id=source_id, event_uid=uid, email=email
        )
        kind = kinds.get(email)
        if kind in {"invitation", "update"}:
            row.invited_at = now
            row.cancelled_at = None
        elif kind == "cancellation":
            row.cancelled_at = now
        db.add(row)
