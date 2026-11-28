"""Turning a failed provider call into the refusal a calendar tool gives.

``calendar_tool_error`` is the one mapping from exceptions to the codes in
``tool_errors``. ``_classify_sync_failure`` keeps deciding what a failure
does to the connection's sync status; this decides what the agent is told.
Provider messages are masked and capped before they reach the agent.
"""

from __future__ import annotations

import httpx

from backend.models.calendar import CalendarSyncStatus

from .models_dto import (
    CalendarReauthorisationRequired,
    UnreadableCalendarConnectionState,
)
from .provider_errors import (
    PROVIDER_ERROR_DETAIL_LIMIT,
    _mask_addresses,
    _provider_error_reason,
    _truncate_error,
)
from .text_utils import _classify_sync_failure, _extract_http_error_text
from .tool_errors import CalendarToolError

RECONNECT_MESSAGE = (
    "This calendar account must be reconnected in Nojoin. Ask the user to "
    "reconnect it in Settings > Integrations > Calendars."
)


def _retry_after_seconds(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After")
    if value and value.strip().isdigit():
        return int(value.strip())
    return None


def _http_failure(exc: httpx.HTTPStatusError) -> CalendarToolError:
    status = exc.response.status_code
    if status in (404, 410):
        return CalendarToolError(
            "not_found",
            "The event no longer exists at the provider. List the calendar again.",
        )
    if status == 412:
        return CalendarToolError(
            "conflict",
            "The event changed at the provider since it was read. Read it again "
            "and confirm the change with the user.",
        )
    if status == 429:
        return CalendarToolError(
            "rate_limited",
            "The provider asked Nojoin to slow down. Wait, then try again.",
            details={"retry_after_seconds": _retry_after_seconds(exc.response)},
        )
    if status >= 500:
        return CalendarToolError(
            "provider_unavailable",
            f"The calendar provider failed (HTTP {status}). Try again shortly.",
        )
    failure_status, _message = _classify_sync_failure(exc)
    if failure_status == CalendarSyncStatus.REAUTHORISATION_REQUIRED.value:
        return CalendarToolError("auth_expired", RECONNECT_MESSAGE)
    message = _truncate_error(
        _mask_addresses(_extract_http_error_text(exc)), PROVIDER_ERROR_DETAIL_LIMIT
    )
    return CalendarToolError(
        "provider_rejected",
        f"The calendar provider refused the request: {message}",
        details={
            "provider_code": _provider_error_reason(exc),
            "http_status": status,
        },
    )


def calendar_tool_error(exc: Exception) -> CalendarToolError | None:
    """The refusal for a failed provider call, or None for any other error.

    None means the exception is not a provider's answer, a network failure or
    a refused grant, so it is a fault to raise, not a refusal to report.
    """
    if isinstance(exc, CalendarToolError):
        return exc
    if isinstance(
        exc, (CalendarReauthorisationRequired, UnreadableCalendarConnectionState)
    ):
        return CalendarToolError("auth_expired", RECONNECT_MESSAGE)
    if isinstance(exc, httpx.HTTPStatusError):
        return _http_failure(exc)
    if isinstance(exc, httpx.HTTPError):
        return CalendarToolError(
            "provider_unavailable",
            "Nojoin could not reach the calendar provider. Try again shortly.",
            details={"outcome_unknown": False},
        )
    return None
