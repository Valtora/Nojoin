from datetime import date, datetime
from enum import Enum
from typing import List, Optional

import sqlalchemy as sa
from sqlalchemy import BigInteger, Column, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from backend.models.base import BaseDBModel


class CalendarProvider(str, Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"


class CalendarSyncStatus(str, Enum):
    IDLE = "idle"
    SYNCING = "syncing"
    SUCCESS = "success"
    ERROR = "error"
    REAUTHORISATION_REQUIRED = "reauthorisation_required"


class CalendarDashboardState(str, Enum):
    READY = "ready"
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    NO_ACCOUNTS = "no_accounts"
    NO_SELECTED_CALENDARS = "no_selected_calendars"
    SYNC_IN_PROGRESS = "sync_in_progress"
    NO_EVENTS = "no_events"


class CalendarProviderConfig(BaseDBModel, table=True):
    __tablename__ = "calendar_provider_configs"

    provider: str = Field(sa_column=Column(String(32), nullable=False, unique=True, index=True))
    client_id: Optional[str] = Field(default=None, sa_column=Column(String(512), nullable=True))
    client_secret_encrypted: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    tenant_id: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True))
    enabled: bool = Field(default=True, nullable=False)


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
    provider_account_id: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    email: Optional[str] = Field(default=None, sa_column=Column(String(320), nullable=True, index=True))
    display_name: Optional[str] = Field(default=None, sa_column=Column(String(255), nullable=True))
    access_token_encrypted: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    refresh_token_encrypted: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    granted_scopes: List[str] = Field(
        default_factory=list,
        sa_column=Column(sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
    )
    token_expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    sync_status: str = Field(
        default=CalendarSyncStatus.IDLE.value,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    sync_error: Optional[str] = Field(default=None, sa_column=Column(String(512), nullable=True))
    last_sync_started_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    last_sync_completed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    last_synced_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))

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
    provider_calendar_id: str = Field(sa_column=Column(String(512), nullable=False, index=True))
    name: str = Field(sa_column=Column(String(255), nullable=False))
    description: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    time_zone: Optional[str] = Field(default=None, sa_column=Column(String(128), nullable=True))
    colour: Optional[str] = Field(default=None, sa_column=Column(String(32), nullable=True))
    is_primary: bool = Field(default=False, nullable=False)
    is_read_only: bool = Field(default=False, nullable=False)
    is_selected: bool = Field(default=False, nullable=False, index=True)
    last_synced_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    sync_window_start: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    sync_window_end: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))

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
    )

    calendar_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("calendar_sources.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider_event_id: str = Field(sa_column=Column(String(512), nullable=False, index=True))
    title: str = Field(sa_column=Column(String(512), nullable=False))
    status: str = Field(default="confirmed", sa_column=Column(String(32), nullable=False, index=True))
    is_all_day: bool = Field(default=False, nullable=False, index=True)
    starts_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True, index=True))
    ends_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True, index=True))
    start_date: Optional[date] = Field(default=None, sa_column=Column(Date, nullable=True, index=True))
    end_date: Optional[date] = Field(default=None, sa_column=Column(Date, nullable=True, index=True))
    source_url: Optional[str] = Field(default=None, sa_column=Column(String(2048), nullable=True))
    external_updated_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))

    calendar: Optional[CalendarSource] = Relationship(back_populates="events")


class CalendarProviderStatusRead(SQLModel):
    provider: str
    display_name: str
    configured: bool
    source: str
    enabled: bool
    client_id: Optional[str] = None
    tenant_id: Optional[str] = None
    has_client_secret: bool


class CalendarProviderConfigUpdate(SQLModel):
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    tenant_id: Optional[str] = None
    enabled: Optional[bool] = None
    clear_client_secret: bool = False


class CalendarSourceRead(SQLModel):
    id: int
    provider_calendar_id: str
    name: str
    description: Optional[str] = None
    time_zone: Optional[str] = None
    colour: Optional[str] = None
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
    calendars: List[CalendarSourceRead]


class CalendarOverviewRead(SQLModel):
    providers: List[CalendarProviderStatusRead]
    connections: List[CalendarConnectionRead]


class CalendarSelectionUpdate(SQLModel):
    selected_calendar_ids: List[int]


class CalendarActionResponse(SQLModel):
    success: bool
    detail: str


class CalendarAuthorisationStart(SQLModel):
    authorisation_url: str


class CalendarDashboardDayCountRead(SQLModel):
    date: date
    count: int


class CalendarDashboardEventRead(SQLModel):
    id: int
    title: str
    provider: str
    calendar_name: str
    account_label: Optional[str] = None
    is_all_day: bool
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class CalendarDashboardSummaryRead(SQLModel):
    month: str
    state: str
    provider_configured: bool
    is_syncing: bool
    connection_count: int
    selected_calendar_count: int
    last_synced_at: Optional[datetime] = None
    day_counts: List[CalendarDashboardDayCountRead]
    agenda_items: List[CalendarDashboardEventRead]
    next_event: Optional[CalendarDashboardEventRead] = None