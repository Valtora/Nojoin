"""The two per-user choices about speaker names, and how they are read."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.v1.endpoints.settings import SettingsUpdate
from backend.utils.speaker_names import preferences


@pytest.fixture
def install(monkeypatch) -> dict[str, object]:
    """The install's config.json, empty unless a test fills it."""
    values: dict[str, object] = {}
    monkeypatch.setattr(
        preferences.config_manager,
        "get",
        lambda key, default=None: values.get(key, default),
    )
    return values


def test_both_default_to_asking_the_user(install) -> None:
    assert preferences.confirm_suggested_names_enabled({}) is False
    assert preferences.library_learns_automatically({}) is False


def test_the_users_choice_wins_over_the_installs(install) -> None:
    install.update(voiceprint_learning="automatic", confirm_suggested_names=True)

    assert preferences.library_learns_automatically({}) is True
    assert preferences.confirm_suggested_names_enabled({}) is True
    user = {"voiceprint_learning": "confirmed", "confirm_suggested_names": False}
    assert preferences.library_learns_automatically(user) is False
    assert preferences.confirm_suggested_names_enabled(user) is False


@pytest.mark.parametrize("value", ["confirmed", "automatic"])
def test_voiceprint_learning_accepts_its_two_choices(value) -> None:
    assert SettingsUpdate(voiceprint_learning=value).voiceprint_learning == value


def test_voiceprint_learning_refuses_anything_else() -> None:
    with pytest.raises(ValidationError, match="voiceprint_learning"):
        SettingsUpdate(voiceprint_learning="always")
