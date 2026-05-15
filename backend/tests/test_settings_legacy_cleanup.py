from backend.api.v1.endpoints.settings import (
    SettingsUpdate,
    _build_settings_update_data,
    _get_mutable_user_settings,
)
from backend.utils.config_manager import (
    LEGACY_AUTOMATIC_AI_SETTING_KEYS,
    get_default_user_settings,
)
from backend.utils.llm_config import _merge_llm_config


def test_default_user_settings_exclude_legacy_automatic_ai_keys() -> None:
    defaults = get_default_user_settings()

    assert defaults["prefer_short_titles"] is True
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