"""Internal provider-facing dataclasses and sync exceptions.

These lightweight data-transfer objects normalise Google and Microsoft payloads
into a provider-agnostic shape before persistence. Dependency leaf: no sibling
imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class ProviderRuntimeConfig:
    provider: str
    client_id: str | None
    client_secret: str | None
    tenant_id: str | None
    enabled: bool
    source: str
    push_enabled: bool = False

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.client_id and self.client_secret)


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: list[str]


@dataclass
class ProviderIdentity:
    account_id: str
    email: str | None
    display_name: str | None


@dataclass
class ProviderCalendarRecord:
    remote_id: str
    name: str
    description: str | None
    time_zone: str | None
    colour: str | None
    is_primary: bool
    is_read_only: bool


@dataclass
class ProviderEventRecord:
    remote_id: str
    title: str
    status: str
    is_all_day: bool
    starts_at: datetime | None
    ends_at: datetime | None
    start_date: date | None
    end_date: date | None
    source_url: str | None = None
    location_text: str | None = None
    meeting_url: str | None = None
    description: str | None = None
    attendees: list | None = None
    external_updated_at: datetime | None = None


@dataclass
class ProviderEventSyncResult:
    events: list[ProviderEventRecord]
    deleted_remote_ids: list[str]
    cursor: str | None


class IncrementalSyncResetRequired(Exception):
    pass


class UnreadableCalendarConnectionState(Exception):
    pass
