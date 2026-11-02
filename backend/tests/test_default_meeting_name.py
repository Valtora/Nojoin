# ruff: noqa: F811 -- the API fixtures are imported from test_recording_pause_resume and requested by name
"""A meeting's default name is in its owner's time zone (issue #886).

The containers run in UTC, so the server's local clock named a capture started
at 21:24 in Tokyo an "Early Afternoon Meeting", and near midnight it gave the
wrong day as well.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

import backend.tests.test_recording_pause_resume as pause_resume
from backend.api.v1.endpoints.recordings import helpers
from backend.api.v1.endpoints.recordings.helpers import generate_default_meeting_name
from backend.tests.test_recording_pause_resume import (  # noqa: F401 -- fixtures
    BASE_URL,
    TRUSTED_ORIGIN,
    api_app,
    set_session_cookie,
    test_session_maker,
)
from backend.utils.timezones import get_user_timezone_name

# 20:30 UTC on Saturday 31 October 2026 is already Sunday morning in Tokyo.
SATURDAY_EVENING_UTC = datetime(2026, 10, 31, 20, 30)
# 03:00 UTC on Sunday 1 November is still Saturday evening in Los Angeles.
SUNDAY_SMALL_HOURS_UTC = datetime(2026, 11, 1, 3, 0)
# The capture in the issue: 12:24 UTC, 21:24 in Tokyo.
SUNDAY_LUNCHTIME_UTC = datetime(2026, 11, 1, 12, 24)


@pytest.mark.parametrize(
    ("zone", "now", "expected"),
    [
        ("UTC", SATURDAY_EVENING_UTC, "Saturday 31st Oct, Late Evening Meeting"),
        ("Asia/Tokyo", SATURDAY_EVENING_UTC, "Sunday 1st Nov, Early Morning Meeting"),
        ("UTC", SUNDAY_SMALL_HOURS_UTC, "Sunday 1st Nov, Late Night Meeting"),
        (
            "America/Los_Angeles",
            SUNDAY_SMALL_HOURS_UTC,
            "Saturday 31st Oct, Late Evening Meeting",
        ),
        ("UTC", SUNDAY_LUNCHTIME_UTC, "Sunday 1st Nov, Early Afternoon Meeting"),
        ("Asia/Tokyo", SUNDAY_LUNCHTIME_UTC, "Sunday 1st Nov, Night Meeting"),
    ],
)
def test_the_name_uses_the_owners_day_and_time_of_day(
    zone: str, now: datetime, expected: str
) -> None:
    assert generate_default_meeting_name(zone, now=now) == expected


def test_the_owners_setting_wins_then_the_install_default_then_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def name_for(settings: dict) -> str:
        return generate_default_meeting_name(
            get_user_timezone_name(settings), now=SATURDAY_EVENING_UTC
        )

    monkeypatch.setenv("DEFAULT_TIMEZONE", "America/Los_Angeles")
    assert name_for({"timezone": "Asia/Tokyo"}).startswith("Sunday 1st Nov")
    assert name_for({}) == "Saturday 31st Oct, Early Afternoon Meeting"
    assert name_for({"timezone": "Not/AZone"}) == (
        "Saturday 31st Oct, Early Afternoon Meeting"
    )

    monkeypatch.delenv("DEFAULT_TIMEZONE")
    assert name_for({}) == "Saturday 31st Oct, Late Evening Meeting"


@pytest.fixture
def user_settings(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The signed-in user's settings, which a test fills in before it calls."""
    settings: dict = {}
    build_user = pause_resume.build_test_user

    def build_user_with_settings():
        user = build_user()
        user.settings = settings
        return user

    monkeypatch.setattr(pause_resume, "build_test_user", build_user_with_settings)
    return settings


@pytest.fixture
async def client(user_settings: dict, api_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=api_app), base_url=BASE_URL
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_a_capture_started_without_a_name_is_named_in_the_owners_zone(
    client: AsyncClient,
    user_settings: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_settings["timezone"] = "Asia/Tokyo"
    monkeypatch.setattr(helpers, "utc_now", lambda: SUNDAY_LUNCHTIME_UTC)
    set_session_cookie(client)

    response = await client.post(
        "/api/v1/recordings/init", headers={"Origin": TRUSTED_ORIGIN}
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Sunday 1st Nov, Night Meeting"
