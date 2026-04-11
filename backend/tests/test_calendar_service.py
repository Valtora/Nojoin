from datetime import date, datetime
from types import SimpleNamespace

import httpx

from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.calendar import CalendarSyncStatus
from backend.services.calendar_service import (
    _build_sync_window,
    _classify_sync_failure,
    _iter_event_dates,
    _normalise_google_event,
    _normalise_microsoft_event,
)


def test_encrypt_secret_round_trip() -> None:
    secret = "calendar-token"

    encrypted = encrypt_secret(secret)

    assert encrypted is not None
    assert encrypted != secret
    assert decrypt_secret(encrypted) == secret


def test_build_sync_window_is_month_aligned() -> None:
    start, end = _build_sync_window(datetime(2026, 4, 11, 15, 45, 12))

    assert start == datetime(2025, 4, 1, 0, 0, 0)
    assert end == datetime(2027, 5, 1, 0, 0, 0)


def test_normalise_google_all_day_event_preserves_exclusive_end_date() -> None:
    event = _normalise_google_event(
        {
            "id": "google-1",
            "summary": "Board off-site",
            "status": "confirmed",
            "start": {"date": "2026-04-11"},
            "end": {"date": "2026-04-13"},
            "updated": "2026-04-10T09:00:00Z",
        }
    )

    assert event is not None
    assert event.is_all_day is True
    assert event.start_date == date(2026, 4, 11)
    assert event.end_date == date(2026, 4, 13)


def test_iter_event_dates_expands_all_day_event_span() -> None:
    event = SimpleNamespace(
        is_all_day=True,
        start_date=date(2026, 4, 11),
        end_date=date(2026, 4, 13),
    )

    assert list(_iter_event_dates(event)) == [
        date(2026, 4, 11),
        date(2026, 4, 12),
    ]


def test_normalise_microsoft_timed_event_uses_utc_naive_datetimes() -> None:
    event = _normalise_microsoft_event(
        {
            "id": "ms-1",
            "subject": "Design review",
            "isAllDay": False,
            "isCancelled": False,
            "start": {"dateTime": "2026-04-11T09:30:00"},
            "end": {"dateTime": "2026-04-11T10:15:00"},
            "lastModifiedDateTime": "2026-04-10T08:00:00Z",
        }
    )

    assert event is not None
    assert event.is_all_day is False
    assert event.starts_at == datetime(2026, 4, 11, 9, 30, 0)
    assert event.ends_at == datetime(2026, 4, 11, 10, 15, 0)


def test_normalise_cancelled_microsoft_event_returns_none() -> None:
    event = _normalise_microsoft_event(
        {
            "id": "ms-2",
            "subject": "Cancelled meeting",
            "isAllDay": False,
            "isCancelled": True,
            "start": {"dateTime": "2026-04-11T09:30:00"},
            "end": {"dateTime": "2026-04-11T10:15:00"},
        }
    )

    assert event is None


def test_classify_sync_failure_marks_reauthorisation_for_refresh_token_errors() -> None:
    status, message = _classify_sync_failure(
        ValueError("Calendar connection requires reauthorisation")
    )

    assert status == CalendarSyncStatus.REAUTHORISATION_REQUIRED.value
    assert "reconnected" in message.lower()


def test_classify_sync_failure_marks_admin_consent_errors_as_reauthorisation_required() -> None:
    request = httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/calendars")
    response = httpx.Response(
        403,
        json={
            "error": {
                "code": "ErrorAccessDenied",
                "message": "Need admin approval to access this resource.",
            }
        },
        request=request,
    )
    error = httpx.HTTPStatusError(
        "Client error '403 Forbidden' for url",
        request=request,
        response=response,
    )

    status, message = _classify_sync_failure(error)

    assert status == CalendarSyncStatus.REAUTHORISATION_REQUIRED.value
    assert "admin approval" in message.lower() or "consent" in message.lower()