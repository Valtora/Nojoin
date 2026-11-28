from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, List, Optional

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, Relationship, SQLModel

from backend.models.base import BaseDBModel
from backend.models.int_bounds import RowId
from backend.utils.time import utc_now


class CalendarProvider(str, Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    PROTON = "proton"


class CalendarSyncStatus(str, Enum):
    IDLE = "idle"
    SYNCING = "syncing"
    SUCCESS = "success"
    ERROR = "error"
    REAUTHORISATION_REQUIRED = "reauthorisation_required"


class CalendarPushChannelStatus(str, Enum):
    ACTIVE = "active"
    FAILED = "failed"
    STOPPED = "stopped"


class CalendarDashboardState(str, Enum):
    READY = "ready"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    NO_ACCOUNTS = "no_accounts"
    NO_SELECTED_CALENDARS = "no_selected_calendars"
    SYNC_IN_PROGRESS = "sync_in_progress"
    NO_EVENTS = "no_events"


class CalendarProviderConfig(BaseDBModel, table=True):
    __tablename__ = "calendar_provider_configs"

    provider: str = Field(
        sa_column=Column(String(32), nullable=False, unique=True, index=True)
    )
    client_id: Optional[str] = Field(
        default=None, sa_column=Column(String(512), nullable=True)
    )
    client_secret_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    tenant_id: Optional[str] = Field(
        default=None, sa_column=Column(String(255), nullable=True)
    )
    enabled: bool = Field(default=True, nullable=False)
    # Per-provider opt-in for push (webhook) notifications. Left off by default
    # because it requires the instance to be publicly reachable over HTTPS and,
    # for Google, a verified callback domain in Google Cloud.
    push_enabled: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean, nullable=False, server_default=sa.text("false")),
    )


class CalendarConnection(BaseDBModel, table=True):
    __tablename__ = "calendar_connections"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "provider_account_id",
            name="uq_calendar_connection_user_provider_account",
        ),
    )

    user_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider: str = Field(sa_column=Column(String(32), nullable=False, index=True))
    provider_account_id: str = Field(
        sa_column=Column(String(255), nullable=False, index=True)
    )
    email: Optional[str] = Field(
        default=None, sa_column=Column(String(320), nullable=True, index=True)
    )
    display_name: Optional[str] = Field(
        default=None, sa_column=Column(String(255), nullable=True)
    )
    access_token_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    refresh_token_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    granted_scopes: List[str] = Field(
        default_factory=list,
        sa_column=Column(sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
    )
    token_expires_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    sync_status: str = Field(
        default=CalendarSyncStatus.IDLE.value,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    sync_error: Optional[str] = Field(
        default=None, sa_column=Column(String(512), nullable=True)
    )
    last_sync_started_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    last_sync_completed_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    last_synced_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    # Secrets a provider needs beside its tokens, as one JSON object encrypted
    # with DATA_ENCRYPTION_KEY: for Proton, the session UID and the derived key
    # passphrase. OAuth providers leave it empty.
    provider_secrets_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    calendars: List["CalendarSource"] = Relationship(
        back_populates="connection",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class CalendarSource(BaseDBModel, table=True):
    __tablename__ = "calendar_sources"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "provider_calendar_id",
            name="uq_calendar_source_connection_remote",
        ),
    )

    connection_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("calendar_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider_calendar_id: str = Field(
        sa_column=Column(String(512), nullable=False, index=True)
    )
    name: str = Field(sa_column=Column(String(255), nullable=False))
    description: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    time_zone: Optional[str] = Field(
        default=None, sa_column=Column(String(128), nullable=True)
    )
    colour: Optional[str] = Field(
        default=None, sa_column=Column(String(32), nullable=True)
    )
    user_colour: Optional[str] = Field(
        default=None, sa_column=Column(String(32), nullable=True)
    )
    is_primary: bool = Field(default=False, nullable=False)
    is_read_only: bool = Field(default=False, nullable=False)
    is_selected: bool = Field(default=False, nullable=False, index=True)
    sync_cursor: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    last_synced_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    sync_window_start: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    sync_window_end: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )

    connection: Optional[CalendarConnection] = Relationship(back_populates="calendars")
    events: List["CalendarEvent"] = Relationship(
        back_populates="calendar",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class CalendarEvent(BaseDBModel, table=True):
    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint(
            "calendar_id",
            "provider_event_id",
            name="uq_calendar_event_calendar_remote",
        ),
        sa.Index(
            "ix_calendar_events_calendar_id_range_start", "calendar_id", "range_start"
        ),
    )

    calendar_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("calendar_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider_event_id: str = Field(
        sa_column=Column(String(512), nullable=False, index=True)
    )
    title: str = Field(sa_column=Column(String(512), nullable=False))
    status: str = Field(
        default="confirmed", sa_column=Column(String(32), nullable=False, index=True)
    )
    is_all_day: bool = Field(default=False, nullable=False, index=True)
    starts_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True, index=True)
    )
    ends_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True, index=True)
    )
    start_date: Optional[date] = Field(
        default=None, sa_column=Column(Date, nullable=True, index=True)
    )
    end_date: Optional[date] = Field(
        default=None, sa_column=Column(Date, nullable=True, index=True)
    )
    location_text: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    description: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    attendees: Optional[list] = Field(
        default=None, sa_column=Column(sa.JSON, nullable=True)
    )
    meeting_url: Optional[str] = Field(
        default=None, sa_column=Column(String(2048), nullable=True)
    )
    source_url: Optional[str] = Field(
        default=None, sa_column=Column(String(2048), nullable=True)
    )
    external_updated_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    # The IANA zone the provider says the event was written in, where it says.
    start_time_zone: Optional[str] = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    end_time_zone: Optional[str] = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    # The span the event covers, naive UTC, for ordering, overlap filters and
    # free/busy. A timed event's equals starts_at and ends_at; an all-day
    # event's runs from midnight at start_date to midnight at the exclusive
    # end_date in the calendar's zone, else the user's. fill_event_range
    # derives it on every insert and update the code did not set it on.
    range_start: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=False)
    )
    range_end: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=False)
    )
    show_as: str = Field(
        default="busy",
        sa_column=Column(String(16), nullable=False, server_default="busy"),
    )
    # {name, email, is_self}
    organiser: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(sa.JSON, nullable=True)
    )
    is_organiser: Optional[bool] = Field(
        default=None, sa_column=Column(sa.Boolean, nullable=True)
    )
    my_response: Optional[str] = Field(
        default=None, sa_column=Column(String(16), nullable=True)
    )
    # The provider's series identity, shared by every occurrence of a series.
    series_key: Optional[str] = Field(
        default=None, sa_column=Column(String(512), nullable=True, index=True)
    )
    original_start: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    original_start_date: Optional[date] = Field(
        default=None, sa_column=Column(Date, nullable=True)
    )
    occurrence_type: str = Field(
        default="single",
        sa_column=Column(String(16), nullable=False, server_default="single"),
    )
    recurrence_rule: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    provider_revision: Optional[str] = Field(
        default=None, sa_column=Column(String(255), nullable=True)
    )
    provider_data: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(sa.JSON, nullable=True)
    )

    calendar: Optional[CalendarSource] = Relationship(back_populates="events")


# UTC offsets run from -12:00 to +14:00, so midnight in any zone lies within
# this distance of midnight UTC on the same date.
_ZONE_MIDNIGHT_SPREAD = timedelta(hours=14)


def _midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time())


def _is_midnight_of(value: Optional[datetime], day: date) -> bool:
    return (
        value is not None and abs(value - _midnight_utc(day)) <= _ZONE_MIDNIGHT_SPREAD
    )


def fill_event_range(event: CalendarEvent) -> None:
    """Give an event the range_start and range_end its times imply.

    A timed event's range is its start and end. An all-day event keeps a range
    already set at midnight of its dates in some zone, which sync sets in the
    calendar's zone, and otherwise gets midnight UTC, as the migration's
    backfill does. Restores of older archives and seeded rows reach here
    without a range.
    """
    if event.starts_at is not None:
        event.range_start = event.starts_at
        event.range_end = event.ends_at or event.starts_at
        return
    if event.start_date is not None:
        end_date = event.end_date or event.start_date + timedelta(days=1)
        if not _is_midnight_of(event.range_start, event.start_date):
            event.range_start = _midnight_utc(event.start_date)
        if not _is_midnight_of(event.range_end, end_date):
            event.range_end = _midnight_utc(end_date)
        return
    fallback = event.created_at or utc_now()
    event.range_start = event.range_start or fallback
    event.range_end = event.range_end or event.range_start


@sa.event.listens_for(CalendarEvent, "before_insert")
@sa.event.listens_for(CalendarEvent, "before_update")
def _fill_event_range_before_write(_mapper: Any, _connection: Any, target: Any) -> None:
    fill_event_range(target)


class CalendarAttendeeInvite(BaseDBModel, table=True):
    """Whether Nojoin has invited one attendee to one event.

    Nojoin writes a row for every attendee of an event it creates or adds
    people to, with ``invited_at`` empty until an invitation is sent, and
    ``cancelled_at`` once a cancellation is. The provider has no such field
    and sync replaces event rows, so this lives on its own, keyed by the
    event's iCalendar UID. An event with no rows was not created through
    Nojoin, and its attendees are taken to have been invited by whoever
    created it.
    """

    __tablename__ = "calendar_attendee_invites"
    __table_args__ = (
        UniqueConstraint(
            "calendar_source_id",
            "event_uid",
            "email",
            name="uq_calendar_attendee_invite_source_uid_email",
        ),
    )

    calendar_source_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("calendar_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    event_uid: str = Field(sa_column=Column(String(512), nullable=False))
    # Lower-cased.
    email: str = Field(sa_column=Column(String(320), nullable=False))
    invited_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )
    invited_sequence: Optional[int] = Field(
        default=None, sa_column=Column(sa.Integer, nullable=True)
    )
    cancelled_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True)
    )


class CalendarPushChannel(BaseDBModel, table=True):
    """A provider push/webhook subscription for one selected calendar.

    Google uses per-calendar ``events.watch`` channels; Microsoft uses Graph
    subscriptions against ``/me/calendars/{id}/events``. A row exists only while
    push is active for that calendar. ``secret_encrypted`` holds the value we
    hand the provider (Google channel token / Microsoft ``clientState``) and
    later use to authenticate the inbound notification.
    """

    __tablename__ = "calendar_push_channels"
    __table_args__ = (
        UniqueConstraint(
            "calendar_id",
            "provider",
            name="uq_calendar_push_channel_calendar_provider",
        ),
    )

    connection_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("calendar_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    calendar_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("calendar_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider: str = Field(sa_column=Column(String(32), nullable=False, index=True))
    # Google: the channel UUID we generate. Microsoft: the subscription id Graph
    # assigns. Looked up when an inbound notification arrives. Nullable so a
    # failed provisioning attempt can be recorded (for backoff) before any
    # channel id exists.
    provider_channel_id: Optional[str] = Field(
        default=None, sa_column=Column(String(512), nullable=True, index=True)
    )
    # Google only: the opaque resourceId required to stop the channel.
    resource_id: Optional[str] = Field(
        default=None, sa_column=Column(String(512), nullable=True)
    )
    secret_encrypted: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    notification_url: Optional[str] = Field(
        default=None, sa_column=Column(String(2048), nullable=True)
    )
    expiration: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime, nullable=True, index=True)
    )
    status: str = Field(
        default=CalendarPushChannelStatus.ACTIVE.value,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    last_error: Optional[str] = Field(
        default=None, sa_column=Column(String(512), nullable=True)
    )


class CalendarProviderStatusRead(SQLModel):
    provider: str
    display_name: str
    configured: bool
    source: str
    enabled: bool
    redirect_uri: str
    client_id: Optional[str] = None
    tenant_id: Optional[str] = None
    has_client_secret: bool
    push_enabled: bool = False
    # Full webhook URL the provider must call. Surfaced so admins can register
    # it (and, for Google, verify the callback domain).
    push_notification_url: Optional[str] = None


class CalendarProviderAvailabilityRead(SQLModel):
    provider: str
    display_name: str
    configured: bool


class CalendarProviderConfigUpdate(SQLModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    tenant_id: Optional[str] = None
    enabled: Optional[bool] = None
    clear_client_secret: bool = False
    push_enabled: Optional[bool] = None


class CalendarSourceRead(SQLModel):
    id: int
    provider_calendar_id: str
    name: str
    description: Optional[str] = None
    time_zone: Optional[str] = None
    colour: Optional[str] = None
    provider_colour: Optional[str] = None
    custom_colour: Optional[str] = None
    is_primary: bool
    is_read_only: bool
    is_selected: bool
    last_synced_at: Optional[datetime] = None


class CalendarConnectionRead(SQLModel):
    id: int
    provider: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    sync_status: str
    sync_error: Optional[str] = None
    last_sync_started_at: Optional[datetime] = None
    last_sync_completed_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
    selected_calendar_count: int
    # True when at least one selected calendar has a live, unexpired push
    # channel, i.e. changes arrive by webhook rather than only the 15-min poll.
    push_active: bool = False
    calendars: List[CalendarSourceRead]


class CalendarOverviewRead(SQLModel):
    providers: List[CalendarProviderAvailabilityRead]
    connections: List[CalendarConnectionRead]


class CalendarSelectionUpdate(SQLModel):
    selected_calendar_ids: List[RowId]


class CalendarSourceColourUpdate(SQLModel):
    colour: Optional[str] = Field(default=None, max_length=32)


class CalendarActionResponse(SQLModel):
    success: bool
    detail: str


class CalendarAuthorisationStart(SQLModel):
    authorisation_url: str


class CalendarDashboardDayCountRead(SQLModel):
    date: date
    count: int


class CalendarDashboardTagRead(SQLModel):
    id: int
    name: str
    color: Optional[str] = None


class CalendarDashboardRecordingRead(SQLModel):
    id: str
    name: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    status: str
    # Tells a capture in progress, which is UPLOADING too, from a finishing upload.
    client_status: Optional[str] = None
    speaker_names: List[str] = Field(default_factory=list)
    tags: List[CalendarDashboardTagRead] = Field(default_factory=list)


class CalendarDashboardEventRead(SQLModel):
    id: int
    title: str
    provider: str
    calendar_id: int
    calendar_name: str
    calendar_colour: Optional[str] = None
    account_label: Optional[str] = None
    location: Optional[str] = None
    meeting_url: Optional[str] = None
    meeting_url_trusted: bool = False
    meeting_url_host: Optional[str] = None
    is_all_day: bool
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    linked_recordings: List[CalendarDashboardRecordingRead] = Field(
        default_factory=list
    )


class CalendarDashboardSummaryRead(SQLModel):
    month: str
    timezone: str
    state: str
    provider_configured: bool
    is_syncing: bool
    connection_count: int
    selected_calendar_count: int
    last_synced_at: Optional[datetime] = None
    day_counts: List[CalendarDashboardDayCountRead]
    agenda_items: List[CalendarDashboardEventRead]
    recording_items: List[CalendarDashboardRecordingRead] = Field(default_factory=list)
    next_event: Optional[CalendarDashboardEventRead] = None
