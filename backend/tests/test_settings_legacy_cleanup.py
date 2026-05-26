from backend.api.v1.endpoints.settings import (
    SettingsUpdate,
    _build_settings_update_data,
    _get_mutable_user_settings,
    _merge_settings,
    _persist_install_wide_ai_settings,
)
from backend.utils.config_manager import (
    LEGACY_AUTOMATIC_AI_SETTING_KEYS,
    config_manager,
    get_default_user_settings,
    get_meeting_edge_context_level,
    is_meeting_edge_enabled,
)
from backend.utils.llm_config import _merge_llm_config
import pytest


class _FakeScalarResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeAsyncSession:
    async def execute(self, statement):
        return _FakeScalarResult(None)


def test_default_user_settings_exclude_legacy_automatic_ai_keys() -> None:
    defaults = get_default_user_settings()

    assert defaults["prefer_short_titles"] is True
    assert defaults["meeting_edge_context_level"] == 2
    for key in LEGACY_AUTOMATIC_AI_SETTING_KEYS:
        assert key not in defaults


def test_get_mutable_user_settings_strips_legacy_automatic_ai_keys() -> None:
    sanitized = _get_mutable_user_settings(
        {
            "theme": "dark",
            "prefer_short_titles": False,
            "auto_generate_notes": False,
            "auto_generate_title": False,
            "auto_infer_speakers": False,
        }
    )

    assert sanitized == {
        "theme": "dark",
        "prefer_short_titles": False,
    }


def test_build_settings_update_data_ignores_masked_sensitive_values() -> None:
    settings = SettingsUpdate.model_validate(
        {
            "prefer_short_titles": False,
            "gemini_api_key": "abc...1234",
            "auto_generate_notes": False,
        }
    )

    update_data = _build_settings_update_data(settings, is_admin=True)

    assert update_data == {"prefer_short_titles": False}


def test_merge_llm_config_strips_legacy_automatic_ai_keys() -> None:
    resolved = _merge_llm_config(
        base_config={
            "llm_provider": "openai",
            "openai_model": "gpt-test",
            "prefer_short_titles": True,
        },
        system_keys={},
        owner_settings=None,
        user_settings={
            "openai_api_key": "sk-test",
            "prefer_short_titles": False,
            "auto_generate_notes": False,
        },
    )

    assert resolved.provider == "openai"
    assert resolved.api_key == "sk-test"
    assert resolved.model == "gpt-test"
    assert resolved.merged_config["prefer_short_titles"] is False
    assert "auto_generate_notes" not in resolved.merged_config


def test_persist_install_wide_ai_settings_only_writes_install_wide_fields(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        config_manager,
        "get_all",
        lambda: {
            "worker_url": "redis://localhost:6379/0",
            "llm_provider": "gemini",
        },
    )
    monkeypatch.setattr(
        config_manager,
        "save_config",
        lambda config: captured.setdefault("saved", dict(config)),
    )
    monkeypatch.setattr(
        config_manager,
        "reload",
        lambda: captured.setdefault("reloaded", True),
    )

    _persist_install_wide_ai_settings(
        {
            "llm_provider": "openai",
            "openai_model": "gpt-4.1",
            "openai_live_model": "gpt-4.1-mini",
            "prefer_short_titles": False,
        }
    )

    assert captured["saved"] == {
        "worker_url": "redis://localhost:6379/0",
        "llm_provider": "openai",
        "openai_model": "gpt-4.1",
        "openai_live_model": "gpt-4.1-mini",
    }
    assert captured["reloaded"] is True


def test_is_meeting_edge_enabled_falls_back_to_install_wide_config(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: False if key == "enable_meeting_edge" else default,
    )

    assert is_meeting_edge_enabled({}) is False


def test_get_meeting_edge_context_level_defaults_and_clamps() -> None:
    assert get_meeting_edge_context_level(None) == 2
    assert get_meeting_edge_context_level({"meeting_edge_context_level": 5}) == 5
    assert get_meeting_edge_context_level({"meeting_edge_context_level": 99}) == 5
    assert get_meeting_edge_context_level({"meeting_edge_context_level": 0}) == 1
    assert get_meeting_edge_context_level({"meeting_edge_context_level": "nope"}) == 2


@pytest.mark.anyio
async def test_merge_settings_preserves_config_backed_meeting_edge_model(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        config_manager,
        "get_all",
        lambda: {
            "llm_provider": "openai",
            "openai_model": "gpt-4.1",
            "openai_live_model": "gpt-4.1-mini",
        },
    )

    merged = await _merge_settings({}, _FakeAsyncSession())

    assert merged["llm_provider"] == "openai"
    assert merged["openai_model"] == "gpt-4.1"
    assert merged["openai_live_model"] == "gpt-4.1-mini"