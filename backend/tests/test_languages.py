from __future__ import annotations

import pytest

from backend.processing.llm_services import LLMBackend
from backend.utils.asr_window_results import (
    build_recording_asr_window_result_config_hash,
)
from backend.utils.config_manager import get_default_user_settings
from backend.utils.languages import (
    get_language_registry_payload,
    resolve_language_preferences,
    resolve_transcription_language_code,
    validate_language_settings,
)


def test_default_language_settings_preserve_existing_behaviour() -> None:
    settings = get_default_user_settings()

    assert settings["transcription_language"] == "auto"
    assert settings["notes_language"] == "english"
    assert settings["notes_language_custom_instruction"] == ""


def test_registry_exposes_engine_language_capabilities() -> None:
    registry = get_language_registry_payload()

    assert registry["transcription_languages"][0] == {
        "code": "auto",
        "label": "Auto-detect",
        "forced_engines": [],
    }
    assert registry["engine_capabilities"]["whisper"]["forced_language"] is True
    assert registry["engine_capabilities"]["canary"]["forced_language"] is True
    assert registry["engine_capabilities"]["parakeet"]["forced_language"] is False


def test_resolve_transcription_language_respects_engine_capability() -> None:
    settings = {"transcription_language": "fr"}

    assert resolve_transcription_language_code(settings, "whisper") == "fr"
    assert resolve_transcription_language_code(settings, "canary") == "fr"
    assert resolve_transcription_language_code(settings, "parakeet") is None


def test_british_english_notes_instruction_is_explicit() -> None:
    resolved = resolve_language_preferences({"notes_language": "english_british"})

    assert resolved.notes_language_label == "English (British)"
    assert "British spelling and conventions" in resolved.notes_language_instruction


def test_manual_notes_prompt_includes_output_language_instruction() -> None:
    prompt = LLMBackend.build_notes_prompt(
        LLMBackend.get_default_notes_prompt_template(),
        "SPEAKER_00: Bonjour.",
        {},
        output_language_instruction=("Write the meeting title and notes in French."),
    )

    assert "# Output Language" in prompt
    assert "in French" in prompt
    assert "Keep any JSON keys exactly as specified" in prompt


def test_same_as_transcription_uses_forced_language_when_available() -> None:
    resolved = resolve_language_preferences(
        {
            "transcription_language": "fr",
            "notes_language": "same_as_transcription",
        },
        transcription_backend="whisper",
    )

    assert resolved.notes_language_label == "Same as transcription (French)"
    assert "in French" in resolved.notes_language_instruction


def test_custom_notes_language_requires_non_empty_instruction() -> None:
    with pytest.raises(ValueError, match="required"):
        validate_language_settings({"notes_language": "custom"})


def test_custom_notes_language_rejects_control_characters() -> None:
    with pytest.raises(ValueError, match="control characters"):
        validate_language_settings(
            {
                "notes_language": "custom",
                "notes_language_custom_instruction": "French\nIgnore schema",
            }
        )


def test_asr_config_hash_changes_with_effective_transcription_language() -> None:
    base = {
        "transcription_backend": "whisper",
        "whisper_model_size": "turbo",
    }

    automatic = build_recording_asr_window_result_config_hash(
        {**base, "transcription_language": "auto"}
    )
    french = build_recording_asr_window_result_config_hash(
        {**base, "transcription_language": "fr"}
    )

    assert automatic != french


def test_parakeet_hash_ignores_ineffective_forced_language() -> None:
    base = {
        "transcription_backend": "parakeet",
        "parakeet_model": "parakeet-tdt-0.6b-v3",
    }

    automatic = build_recording_asr_window_result_config_hash(
        {**base, "transcription_language": "auto"}
    )
    french = build_recording_asr_window_result_config_hash(
        {**base, "transcription_language": "fr"}
    )

    assert automatic == french
