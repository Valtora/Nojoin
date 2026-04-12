from datetime import date, datetime
from types import SimpleNamespace

import httpx

from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.calendar import CalendarSyncStatus
from backend.services.calendar_service import (
    _build_google_events_query_params,
    _build_microsoft_delta_url,
    _build_sync_window,
    _can_use_incremental_sync,
    _classify_sync_failure,
    _is_partial_microsoft_occurrence,
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


def test_normalise_google_event_extracts_location_and_meeting_url() -> None:
    event = _normalise_google_event(
        {
            "id": "google-2",
            "summary": "Founder catch-up",
            "status": "confirmed",
            "start": {"dateTime": "2026-04-11T09:30:00Z"},
            "end": {"dateTime": "2026-04-11T10:15:00Z"},
            "location": "Boardroom 4",
            "description": "Zoom fallback https://us02web.zoom.us/j/123456789",
            "hangoutLink": "https://meet.google.com/abc-defg-hij",
            "updated": "2026-04-10T09:00:00Z",
        }
    )

    assert event is not None
    assert event.location_text == "Boardroom 4"
    assert event.meeting_url == "https://meet.google.com/abc-defg-hij"


def test_build_google_events_query_params_uses_sync_token_without_window_filters() -> None:
    params = _build_google_events_query_params(
        datetime(2026, 4, 1, 0, 0, 0),
        datetime(2026, 5, 1, 0, 0, 0),
        sync_cursor="google-sync-token",
        page_token="page-2",
    )

    assert params["syncToken"] == "google-sync-token"
    assert params["pageToken"] == "page-2"
    assert "timeMin" not in params
    assert "timeMax" not in params


def test_build_microsoft_delta_url_preserves_calendar_and_window() -> None:
    url = _build_microsoft_delta_url(
        "team/calendar@example.com",
        datetime(2026, 4, 1, 0, 0, 0),
        datetime(2026, 5, 1, 0, 0, 0),
    )

    assert "team%2Fcalendar%40example.com" in url
    assert "startDateTime=2026-04-01T00%3A00%3A00Z" in url
    assert "endDateTime=2026-05-01T00%3A00%3A00Z" in url
    assert "%24select=" in url
    assert "onlineMeetingUrl" in url
    assert "seriesMasterId" in url


def test_can_use_incremental_sync_requires_matching_window_and_cursor() -> None:
    calendar = SimpleNamespace(
        sync_cursor="opaque-cursor",
        sync_window_start=datetime(2026, 4, 1, 0, 0, 0),
        sync_window_end=datetime(2026, 5, 1, 0, 0, 0),
    )

    assert _can_use_incremental_sync(
        calendar,
        datetime(2026, 4, 1, 0, 0, 0),
        datetime(2026, 5, 1, 0, 0, 0),
    ) is True
    assert _can_use_incremental_sync(
        calendar,
        datetime(2026, 3, 1, 0, 0, 0),
        datetime(2026, 5, 1, 0, 0, 0),
    ) is False
    assert _can_use_incremental_sync(
        SimpleNamespace(
            sync_cursor=None,
            sync_window_start=datetime(2026, 4, 1, 0, 0, 0),
            sync_window_end=datetime(2026, 5, 1, 0, 0, 0),
        ),
        datetime(2026, 4, 1, 0, 0, 0),
        datetime(2026, 5, 1, 0, 0, 0),
    ) is False


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
            "location": {"displayName": "Microsoft Teams Meeting"},
            "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/abc"},
            "lastModifiedDateTime": "2026-04-10T08:00:00Z",
        }
    )

    assert event is not None
    assert event.is_all_day is False
    assert event.starts_at == datetime(2026, 4, 11, 9, 30, 0)
    assert event.ends_at == datetime(2026, 4, 11, 10, 15, 0)
    assert event.location_text == "Microsoft Teams Meeting"
    assert event.meeting_url == "https://teams.microsoft.com/l/meetup-join/abc"


def test_normalise_microsoft_event_extracts_meeting_url_from_body_when_needed() -> None:
    event = _normalise_microsoft_event(
        {
            "id": "ms-3",
            "subject": "Client review",
            "isAllDay": False,
            "isCancelled": False,
            "start": {"dateTime": "2026-04-11T09:30:00"},
            "end": {"dateTime": "2026-04-11T10:15:00"},
            "body": {
                "contentType": "html",
                "content": '<p>Join here <a href="https://app.zoom.us/wc/join/555">Zoom</a></p>',
            },
            "lastModifiedDateTime": "2026-04-10T08:00:00Z",
        }
    )

    assert event is not None
    assert event.meeting_url == "https://app.zoom.us/wc/join/555"


def test_is_partial_microsoft_occurrence_detects_stripped_delta_occurrence() -> None:
    assert _is_partial_microsoft_occurrence(
        {
            "id": "occ-1",
            "type": "occurrence",
            "seriesMasterId": "master-1",
            "start": {"dateTime": "2026-04-14T12:00:00"},
            "end": {"dateTime": "2026-04-14T13:00:00"},
        }
    ) is True

    assert _is_partial_microsoft_occurrence(
        {
            "id": "occ-2",
            "type": "occurrence",
            "seriesMasterId": "master-1",
            "subject": "Gotmoves monthly",
            "start": {"dateTime": "2026-04-14T12:00:00"},
            "end": {"dateTime": "2026-04-14T13:00:00"},
        }
    ) is False


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