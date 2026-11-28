"""The provider-neutral interface every writable calendar provider implements.

``writes.py`` resolves, authorises, validates, previews, deduplicates, locks,
audits and builds results; a backend only talks to its provider. The registry
maps each provider to its backend: Google and Microsoft to
``ReadOnlyBackend``, whose capabilities say ``provider_read_only``. A
provider gains writes by registering a backend (``register_write_backend``),
with no tool changing.

Mail is a separate call, ``send_notifications``, which the neutral layer
makes only when the call asked for it, and only after the write landed.
``share_with_attendees`` tells a backend whether the provider may deliver the
event to attendees itself; it is never true unless notifying was asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.calendar import CalendarConnection, CalendarSource

from .capabilities import READ_ONLY, WriteCapabilities
from .constants import OAUTH_PROVIDERS
from .models_dto import ProviderEventRecord
from .tool_errors import CalendarToolError

Scope = Literal["single", "occurrence", "series"]
Response = Literal["accepted", "declined", "tentative"]


@dataclass(frozen=True)
class AttendeeDraft:
    email: str
    name: str | None = None
    optional: bool = False


@dataclass(frozen=True)
class EventTime:
    """A timed event's instant (aware), or an all-day event's date."""

    at: datetime | None = None
    day: date | None = None


@dataclass
class EventDraft:
    """A new event, checked and in the neutral model."""

    title: str
    start: EventTime
    end: EventTime
    # The zone the event is written in: it keeps a weekly 10:00 at 10:00.
    time_zone: str
    all_day: bool
    description: str | None = None
    location: str | None = None
    meeting_url: str | None = None
    status: str = "confirmed"
    show_as: str = "busy"
    recurrence: str | None = None
    attendees: list[AttendeeDraft] = field(default_factory=list)
    # {name, email} of the calendar's account.
    organiser: dict[str, Any] | None = None


@dataclass
class EventPatch:
    """The fields an update changes. A field named with None is cleared.

    ``changes`` keys are ``title``, ``start``, ``end`` (EventTime),
    ``time_zone``, ``all_day``, ``description``, ``location``,
    ``meeting_url``, ``status``, ``show_as`` and ``recurrence``.
    """

    changes: dict[str, Any] = field(default_factory=dict)
    add_attendees: list[AttendeeDraft] = field(default_factory=list)
    remove_attendees: list[str] = field(default_factory=list)
    # Attendees already on the event whom Nojoin's records say were never
    # invited, so a provider that delivers events itself hands it to them
    # when notifying (every attendee of a hold being confirmed, say).
    share_with: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EventRef:
    """Which event a write is for, in the provider's terms."""

    calendar_id: int
    provider_calendar_id: str
    event_id: int | None = None
    provider_event_id: str | None = None
    uid: str | None = None
    series_key: str | None = None
    original_start: datetime | None = None
    original_start_date: date | None = None
    occurrence_type: str = "single"
    revision: str | None = None
    provider_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldProblem:
    field: str
    message: str


class TokenStore(Protocol):
    """Persists rotated credentials at once, before anything else can fail."""

    async def save(
        self,
        *,
        access_token: str | None,
        refresh_token: str | None,
        expires_at: datetime | None,
        secrets: dict[str, Any] | None = None,
    ) -> None: ...


@dataclass
class ProviderContext:
    db: AsyncSession
    connection: CalendarConnection
    source: CalendarSource
    tokens: TokenStore
    user_zone: str
    secrets: dict[str, Any] = field(default_factory=dict)
    # What a backend keeps between the calls of one tool call, such as the
    # written event a later send_notifications mails.
    scratch: dict[str, Any] = field(default_factory=dict)


@dataclass
class WriteOutcome:
    """What a write left at the provider, read back where it could be."""

    events: list[ProviderEventRecord]
    deleted_keys: list[str] = field(default_factory=list)
    revision: str | None = None
    warnings: list[str] = field(default_factory=list)
    # False when the write landed and reading it back failed: ``events`` is
    # then what was sent.
    verified: bool = True
    # People the provider shows the change to without any mail.
    shown_by_provider: list[dict[str, Any]] = field(default_factory=list)
    occurrences_affected: int | None = None
    exceptions_removed: int = 0


@dataclass(frozen=True)
class PlannedMail:
    email: str
    # invitation, update, cancellation or reply
    kind: str
    name: str | None = None


@dataclass
class NotificationPlan:
    ref: EventRef
    mails: list[PlannedMail]
    note: str | None = None


@dataclass
class NotificationReport:
    emailed: list[PlannedMail] = field(default_factory=list)
    # (address, error) for mail that could not be sent; the write still stands.
    failed: list[tuple[str, str]] = field(default_factory=list)


class CalendarWriteBackend(Protocol):
    provider: str

    def capabilities(
        self, connection: CalendarConnection, source: CalendarSource
    ) -> WriteCapabilities: ...

    def validate(
        self, source: CalendarSource, draft: EventDraft | EventPatch
    ) -> list[FieldProblem]: ...

    async def get_event(
        self, ctx: ProviderContext, ref: EventRef
    ) -> ProviderEventRecord | None: ...

    async def create_event(
        self,
        ctx: ProviderContext,
        draft: EventDraft,
        *,
        uid: str,
        share_with_attendees: bool,
    ) -> WriteOutcome: ...

    async def update_event(  # noqa: PLR0913 - the interface the design fixes
        self,
        ctx: ProviderContext,
        ref: EventRef,
        patch: EventPatch,
        *,
        scope: Scope,
        share_with_attendees: bool,
        expected_revision: str | None,
    ) -> WriteOutcome: ...

    async def cancel_event(
        self,
        ctx: ProviderContext,
        ref: EventRef,
        *,
        scope: Scope,
        expected_revision: str | None,
    ) -> WriteOutcome: ...

    async def respond(
        self,
        ctx: ProviderContext,
        ref: EventRef,
        response: Response,
        *,
        expected_revision: str | None,
    ) -> WriteOutcome: ...

    async def send_notifications(
        self, ctx: ProviderContext, plan: NotificationPlan
    ) -> NotificationReport: ...


def _read_only_refusal() -> CalendarToolError:
    return CalendarToolError(
        "not_writable",
        "Assistants cannot change this provider's calendars yet.",
        details={"reason": "provider_read_only"},
    )


class ReadOnlyBackend:
    """A provider Nojoin reads but does not write: Google and Microsoft today.

    The neutral layer refuses every write before reaching a backend whose
    capabilities say ``provider_read_only``; the write methods refuse again in
    case anything calls them directly.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider

    def capabilities(
        self, connection: CalendarConnection, source: CalendarSource
    ) -> WriteCapabilities:
        return READ_ONLY

    def validate(
        self, source: CalendarSource, draft: EventDraft | EventPatch
    ) -> list[FieldProblem]:
        return []

    async def get_event(
        self, ctx: ProviderContext, ref: EventRef
    ) -> ProviderEventRecord | None:
        if self.provider not in OAUTH_PROVIDERS or not ref.provider_event_id:
            raise CalendarToolError(
                "unsupported",
                "Nojoin cannot read this provider's events fresh yet.",
                details={"hint": "Call get_calendar_event without refresh."},
            )
        from .event_refresh import fetch_oauth_event

        return await fetch_oauth_event(
            ctx.db, ctx.connection, ctx.source, ref.provider_event_id
        )

    async def create_event(
        self,
        ctx: ProviderContext,
        draft: EventDraft,
        *,
        uid: str,
        share_with_attendees: bool,
    ) -> WriteOutcome:
        raise _read_only_refusal()

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
        raise _read_only_refusal()

    async def cancel_event(
        self,
        ctx: ProviderContext,
        ref: EventRef,
        *,
        scope: Scope,
        expected_revision: str | None,
    ) -> WriteOutcome:
        raise _read_only_refusal()

    async def respond(
        self,
        ctx: ProviderContext,
        ref: EventRef,
        response: Response,
        *,
        expected_revision: str | None,
    ) -> WriteOutcome:
        raise _read_only_refusal()

    async def send_notifications(
        self, ctx: ProviderContext, plan: NotificationPlan
    ) -> NotificationReport:
        raise _read_only_refusal()


_BACKENDS: dict[str, CalendarWriteBackend] = {}


def register_write_backend(backend: CalendarWriteBackend) -> None:
    _BACKENDS[backend.provider] = backend


def _built_in(provider: str) -> CalendarWriteBackend | None:
    if provider == "proton":
        from .proton_backend import ProtonBackend

        return ProtonBackend()
    return None


def write_backend_for(provider: str) -> CalendarWriteBackend:
    """The provider's backend; a provider with none registered is read-only.

    A registered backend (a test's) wins over the built-in one.
    """
    return _BACKENDS.get(provider) or _built_in(provider) or ReadOnlyBackend(provider)


def unregister_write_backend(provider: str) -> None:
    _BACKENDS.pop(provider, None)
