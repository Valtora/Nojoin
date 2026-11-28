"""What an assistant may change on a calendar, and why not when it may not.

``calendar_capabilities`` is what ``list_calendars`` reports and what the
write tools check. A provider without a write backend is read-only; a
writable provider's calendar is still closed while its account needs
reconnecting, when the user can only view it, or when it is not selected.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from backend.models.calendar import (
    CalendarConnection,
    CalendarSource,
    CalendarSyncStatus,
)

# Why a calendar is not writable, in the order they are reported.
PROVIDER_READ_ONLY = "provider_read_only"
RECONNECT_REQUIRED = "reconnect_required"
CALENDAR_READ_ONLY = "calendar_read_only"
NOT_SELECTED = "not_selected"


@dataclass(frozen=True)
class WriteCapabilities:
    writable: bool
    reason: str | None = None
    create: bool = False
    update: bool = False
    cancel: bool = False
    respond: bool = False
    # "native" where the provider stores a tentative status, "marker" where
    # Nojoin marks holds itself, None where holds cannot be made.
    holds: str | None = None
    # "email" where invitations can be sent, None where they cannot.
    invitations: str | None = None
    occurrence_edits: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "create": self.create,
            "update": self.update,
            "cancel": self.cancel,
            "respond": self.respond,
            "holds": self.holds,
            "invitations": self.invitations,
            "occurrence_edits": self.occurrence_edits,
        }


READ_ONLY = WriteCapabilities(writable=False, reason=PROVIDER_READ_ONLY)


def closed(capabilities: WriteCapabilities, reason: str) -> WriteCapabilities:
    """The same provider capabilities, every one switched off for ``reason``."""
    return replace(
        capabilities,
        writable=False,
        reason=reason,
        create=False,
        update=False,
        cancel=False,
        respond=False,
        holds=None,
        invitations=None,
        occurrence_edits=False,
    )


def provider_capabilities(
    connection: CalendarConnection, source: CalendarSource
) -> WriteCapabilities:
    """What the provider's write backend supports for this calendar."""
    return READ_ONLY


def calendar_capabilities(
    connection: CalendarConnection, source: CalendarSource
) -> WriteCapabilities:
    capabilities = provider_capabilities(connection, source)
    if not capabilities.writable:
        return capabilities
    if connection.sync_status == CalendarSyncStatus.REAUTHORISATION_REQUIRED.value:
        return closed(capabilities, RECONNECT_REQUIRED)
    if source.is_read_only:
        return closed(capabilities, CALENDAR_READ_ONLY)
    if not source.is_selected:
        return closed(capabilities, NOT_SELECTED)
    return capabilities
