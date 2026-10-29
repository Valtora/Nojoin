"""Live title delay is a strictly validated preference of each account."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from backend.api.v1.endpoints import settings as settings_ep
from backend.tests.test_settings_processing_tuning import _exchange, _FakeConfigManager
from backend.utils.config_manager import get_default_user_settings
from backend.utils.meeting_brief import get_live_title_delay_minutes


def test_live_title_is_on_after_five_minutes_by_default() -> None:
    assert get_default_user_settings()["live_title_delay_minutes"] == 5
    assert get_live_title_delay_minutes({}) == 5
    assert get_live_title_delay_minutes(None) == 5


@pytest.mark.parametrize("value", [0, 1, 5, 120])
def test_valid_delay_is_preserved(value: int) -> None:
    settings = settings_ep.SettingsUpdate(live_title_delay_minutes=value)
    assert settings.live_title_delay_minutes == value
    assert get_live_title_delay_minutes({"live_title_delay_minutes": value}) == value


@pytest.mark.parametrize("value", [-1, 121, True, "5", 1.5, float("inf"), float("nan")])
def test_api_rejects_invalid_delay_and_worker_falls_back_safely(value: object) -> None:
    with pytest.raises(ValidationError):
        settings_ep.SettingsUpdate(live_title_delay_minutes=value)
    assert get_live_title_delay_minutes({"live_title_delay_minutes": value}) == 0


def test_delay_saves_for_a_member_and_does_not_change_another_members_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeConfigManager()
    monkeypatch.setattr(settings_ep, "config_manager", fake)
    responses, row = asyncio.run(
        _exchange(
            [
                ("POST", {"live_title_delay_minutes": 10}),
                ("GET", None),
            ]
        )
    )
    assert all(response.status_code == 200 for response in responses)
    assert row["live_title_delay_minutes"] == 10
    assert responses[-1].json()["live_title_delay_minutes"] == 10
    (other,), other_row = asyncio.run(_exchange([("GET", None)]))
    assert other.status_code == 200
    assert other.json()["live_title_delay_minutes"] == 5
    assert "live_title_delay_minutes" not in other_row
