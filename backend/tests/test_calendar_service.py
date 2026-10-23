import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import pytest

from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.calendar import CalendarSyncStatus
from backend.services.calendar_service import (
    CalendarReauthorisationRequired,
    _build_google_events_query_params,
    _build_microsoft_delta_url,
    _build_sync_window,
    _can_use_incremental_sync,
    _classify_sync_failure,
    _get_meeting_url_host,
    _is_partial_microsoft_occurrence,
    _is_trusted_meeting_url,
    _iter_event_dates,
    _normalise_google_event,
    _normalise_microsoft_event,
    _to_dashboard_event,
)


def test_save_oauth_state_closes_redis_client_on_failure(monkeypatch) -> None:
    """A failed Redis write must still close the client and fall back in-memory.

    Mirrors the push-service debounce fix: redis.from_url allocates a fresh
    connection pool per call, so the client must be closed even when set()
    raises, otherwise a Redis outage on the connect path leaks pools.
    """
    import asyncio

    from backend.services.calendar_service import oauth as oauth_mod

    closed: list[bool] = []

    class _FakeRedis:
        async def set(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

        async def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(oauth_mod.redis, "from_url", lambda url: _FakeRedis())
    oauth_mod._oauth_state_fallback.clear()

    asyncio.run(oauth_mod._save_oauth_state("state-x", {"user_id": 1}))

    assert closed == [True]  # closed even though set() raised
    assert "state-x" in oauth_mod._oauth_state_fallback  # fell back in-memory


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


def test_get_meeting_url_host_normalises_hostname() -> None:
    assert (
        _get_meeting_url_host("https://Teams.Microsoft.com/l/meetup-join/abc")
        == "teams.microsoft.com"
    )


def test_is_trusted_meeting_url_allows_google_microsoft_and_zoom() -> None:
    assert _is_trusted_meeting_url("https://meet.google.com/abc-defg-hij") is True
    assert (
        _is_trusted_meeting_url("https://teams.microsoft.com/l/meetup-join/abc") is True
    )
    assert _is_trusted_meeting_url("https://company.zoom.us/j/123456789") is True


def test_is_trusted_meeting_url_rejects_other_hosts() -> None:
    assert _is_trusted_meeting_url("https://example.com/meeting") is False
    assert _is_trusted_meeting_url("https://app.zoom.evil.example/j/123") is False


def test_to_dashboard_event_marks_untrusted_links() -> None:
    event = SimpleNamespace(
        id=41,
        title="Supplier review",
        calendar_id=7,
        location_text="Boardroom 2",
        meeting_url="https://vendor.example.com/call/123",
        is_all_day=False,
        starts_at=datetime(2026, 4, 11, 9, 0, 0),
        ends_at=datetime(2026, 4, 11, 10, 0, 0),
        start_date=None,
        end_date=None,
    )
    calendar = SimpleNamespace(id=7, connection_id=9, name="Work")
    connection = SimpleNamespace(
        id=9, provider="google", email="owner@example.com", display_name=None
    )

    payload = _to_dashboard_event(event, {7: calendar}, {9: connection})

    assert payload.meeting_url_trusted is False
    assert payload.meeting_url_host == "vendor.example.com"
    assert payload.starts_at == datetime(2026, 4, 11, 9, 0, 0, tzinfo=UTC)
    assert payload.ends_at == datetime(2026, 4, 11, 10, 0, 0, tzinfo=UTC)


def test_build_google_events_query_params_uses_sync_token_without_window_filters() -> (
    None
):
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

    assert (
        _can_use_incremental_sync(
            calendar,
            datetime(2026, 4, 1, 0, 0, 0),
            datetime(2026, 5, 1, 0, 0, 0),
        )
        is True
    )
    assert (
        _can_use_incremental_sync(
            calendar,
            datetime(2026, 3, 1, 0, 0, 0),
            datetime(2026, 5, 1, 0, 0, 0),
        )
        is False
    )
    assert (
        _can_use_incremental_sync(
            SimpleNamespace(
                sync_cursor=None,
                sync_window_start=datetime(2026, 4, 1, 0, 0, 0),
                sync_window_end=datetime(2026, 5, 1, 0, 0, 0),
            ),
            datetime(2026, 4, 1, 0, 0, 0),
            datetime(2026, 5, 1, 0, 0, 0),
        )
        is False
    )


def test_iter_event_dates_expands_all_day_event_span() -> None:
    event = SimpleNamespace(
        is_all_day=True,
        start_date=date(2026, 4, 11),
        end_date=date(2026, 4, 13),
    )

    assert list(_iter_event_dates(event, "UTC")) == [
        date(2026, 4, 11),
        date(2026, 4, 12),
    ]


def test_iter_event_dates_uses_selected_timezone_for_timed_events() -> None:
    event = SimpleNamespace(
        is_all_day=False,
        starts_at=datetime(2026, 3, 31, 23, 30, 0),
        ends_at=datetime(2026, 4, 1, 0, 30, 0),
    )

    assert list(_iter_event_dates(event, "Europe/London")) == [date(2026, 4, 1)]


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
            "onlineMeeting": {
                "joinUrl": "https://teams.microsoft.com/l/meetup-join/abc"
            },
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
    assert (
        _is_partial_microsoft_occurrence(
            {
                "id": "occ-1",
                "type": "occurrence",
                "seriesMasterId": "master-1",
                "start": {"dateTime": "2026-04-14T12:00:00"},
                "end": {"dateTime": "2026-04-14T13:00:00"},
            }
        )
        is True
    )

    assert (
        _is_partial_microsoft_occurrence(
            {
                "id": "occ-2",
                "type": "occurrence",
                "seriesMasterId": "master-1",
                "subject": "Portfolio monthly",
                "start": {"dateTime": "2026-04-14T12:00:00"},
                "end": {"dateTime": "2026-04-14T13:00:00"},
            }
        )
        is False
    )


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
        CalendarReauthorisationRequired("Calendar connection requires reauthorisation")
    )

    assert status == CalendarSyncStatus.REAUTHORISATION_REQUIRED.value
    assert "reconnected" in message.lower()


def test_classify_sync_failure_marks_admin_consent_errors_as_reauthorisation_required() -> (
    None
):
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


# Token endpoint answers, as Google and Microsoft send them, for a refresh token
# that only signing in again can replace.
GOOGLE_INVALID_GRANT_BAD_REQUEST = {
    "error": "invalid_grant",
    "error_description": "Bad Request",
}
GOOGLE_INVALID_GRANT_EXPIRED_OR_REVOKED = {
    "error": "invalid_grant",
    "error_description": "Token has been expired or revoked.",
}
MICROSOFT_AADSTS_INVALID_GRANT = {
    "error": "invalid_grant",
    "error_description": (
        "AADSTS700082: The refresh token has expired due to inactivity. The "
        "token was issued on 2026-07-01T09:00:00.0000000Z and was inactive for "
        "90.00:00:00. Trace ID: 00000000-0000-0000-0000-000000000001 "
        "Correlation ID: 00000000-0000-0000-0000-000000000002 Timestamp: "
        "2026-10-23 09:00:00Z"
    ),
    "error_codes": [700082],
    "timestamp": "2026-10-23 09:00:00Z",
    "trace_id": "00000000-0000-0000-0000-000000000001",
    "correlation_id": "00000000-0000-0000-0000-000000000002",
    "error_uri": "https://login.microsoftonline.com/error?code=700082",
}
MICROSOFT_AADSTS_INTERACTION_REQUIRED = {
    "error": "interaction_required",
    "error_description": (
        "AADSTS50076: Due to a configuration change made by your administrator, "
        "or because you moved to a new location, you must use multi-factor "
        "authentication to access the resource."
    ),
    "error_codes": [50076],
    "suberror": "basic_action",
}
RECONNECT_MESSAGE = (
    "This calendar account needs to be reconnected before it can sync again."
)


def _token_endpoint_error(
    provider: str, status_code: int, body: dict | str
) -> httpx.HTTPStatusError:
    url = (
        "https://oauth2.googleapis.com/token"
        if provider == "google"
        else "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )
    request = httpx.Request("POST", url)
    if isinstance(body, dict):
        response = httpx.Response(status_code, json=body, request=request)
    else:
        response = httpx.Response(status_code, text=body, request=request)
    return httpx.HTTPStatusError(
        f"Client error '{status_code}' for url '{url}'",
        request=request,
        response=response,
    )


def _refresh_with(monkeypatch, provider: str, error: httpx.HTTPStatusError):
    """Run the access-token lookup against a token endpoint that answers ``error``."""
    from backend.services.calendar_service import oauth as oauth_mod

    async def _refuse(_runtime_config, _refresh_token):
        raise error

    monkeypatch.setattr(oauth_mod, "_refresh_google_access_token", _refuse)
    monkeypatch.setattr(oauth_mod, "_refresh_microsoft_access_token", _refuse)
    connection = SimpleNamespace(
        provider=provider,
        access_token_encrypted=None,
        refresh_token_encrypted=encrypt_secret("stored-refresh-token"),
        token_expires_at=None,
    )
    return asyncio.run(
        oauth_mod._get_access_token_for_connection(None, connection, None)
    )


@pytest.mark.parametrize(
    ("provider", "status_code", "body"),
    [
        ("google", 400, GOOGLE_INVALID_GRANT_BAD_REQUEST),
        ("google", 400, GOOGLE_INVALID_GRANT_EXPIRED_OR_REVOKED),
        ("microsoft", 400, MICROSOFT_AADSTS_INVALID_GRANT),
        ("microsoft", 400, MICROSOFT_AADSTS_INTERACTION_REQUIRED),
        ("google", 401, {"error": "login_required"}),
        ("microsoft", 400, {"error": "consent_required"}),
    ],
)
def test_refused_refresh_token_requires_reconnect(
    monkeypatch, provider, status_code, body
) -> None:
    error = _token_endpoint_error(provider, status_code, body)

    with pytest.raises(CalendarReauthorisationRequired) as raised:
        _refresh_with(monkeypatch, provider, error)

    assert raised.value.__cause__ is error
    assert body["error"] in str(raised.value)
    status, message = _classify_sync_failure(raised.value)
    assert status == CalendarSyncStatus.REAUTHORISATION_REQUIRED.value
    assert message == RECONNECT_MESSAGE


@pytest.mark.parametrize(
    ("provider", "status_code", "body"),
    [
        (
            "microsoft",
            400,
            {
                "error": "invalid_client",
                "error_description": (
                    "AADSTS7000215: Invalid client secret provided. Ensure the "
                    "secret being sent in the request is the client secret value."
                ),
            },
        ),
        ("google", 400, {"error": "invalid_request", "error_description": "Bad"}),
        ("google", 503, "<html>Service Unavailable</html>"),
        ("microsoft", 500, {"error": "invalid_grant", "error_description": "x"}),
    ],
)
def test_other_token_endpoint_failures_are_not_a_refusal(
    monkeypatch, provider, status_code, body
) -> None:
    """Only a refused grant asks the user to reconnect. A bad client secret is
    fixed by an administrator, and an outage passes; both keep syncing."""
    error = _token_endpoint_error(provider, status_code, body)

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _refresh_with(monkeypatch, provider, error)

    assert raised.value is error
    status, _message = _classify_sync_failure(error)
    assert status == CalendarSyncStatus.ERROR.value


def test_classify_sync_failure_reads_the_oauth_code_not_the_description() -> None:
    """Google's description can be just "Bad Request", which names nothing."""
    error = _token_endpoint_error("google", 400, GOOGLE_INVALID_GRANT_BAD_REQUEST)

    status, message = _classify_sync_failure(error)

    assert status == CalendarSyncStatus.REAUTHORISATION_REQUIRED.value
    assert message == RECONNECT_MESSAGE


def test_missing_refresh_token_requires_reconnect() -> None:
    from backend.services.calendar_service import oauth as oauth_mod

    connection = SimpleNamespace(
        provider="google",
        access_token_encrypted=None,
        refresh_token_encrypted=None,
        token_expires_at=None,
    )

    with pytest.raises(CalendarReauthorisationRequired):
        asyncio.run(oauth_mod._get_access_token_for_connection(None, connection, None))
