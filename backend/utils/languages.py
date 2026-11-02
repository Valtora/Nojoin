from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from backend.prompts import render_prompt

AUTO_TRANSCRIPTION_LANGUAGE = "auto"
DEFAULT_NOTES_LANGUAGE = "english"
SAME_AS_TRANSCRIPTION_LANGUAGE = "same_as_transcription"
CUSTOM_NOTES_LANGUAGE = "custom"
MAX_CUSTOM_NOTES_LANGUAGE_INSTRUCTION_LENGTH = 300


@dataclass(frozen=True)
class LanguageOption:
    code: str
    label: str
    canary_language: str | None = None

    @property
    def notes_instruction(self) -> str:
        return render_prompt("shared.language_named", language=self.label)


# Canary 1B v2's 25 supported European languages are also supported by Whisper.
# Keeping one shared set makes every selectable forced language honest for both
# engines that expose forced-language operation.
LANGUAGE_OPTIONS: tuple[LanguageOption, ...] = (
    LanguageOption("en", "English", "en"),
    LanguageOption("bg", "Bulgarian", "bg"),
    LanguageOption("hr", "Croatian", "hr"),
    LanguageOption("cs", "Czech", "cs"),
    LanguageOption("da", "Danish", "da"),
    LanguageOption("nl", "Dutch", "nl"),
    LanguageOption("et", "Estonian", "et"),
    LanguageOption("fi", "Finnish", "fi"),
    LanguageOption("fr", "French", "fr"),
    LanguageOption("de", "German", "de"),
    LanguageOption("el", "Greek", "el"),
    LanguageOption("hu", "Hungarian", "hu"),
    LanguageOption("it", "Italian", "it"),
    LanguageOption("lv", "Latvian", "lv"),
    LanguageOption("lt", "Lithuanian", "lt"),
    LanguageOption("mt", "Maltese", "mt"),
    LanguageOption("pl", "Polish", "pl"),
    LanguageOption("pt", "Portuguese", "pt"),
    LanguageOption("ro", "Romanian", "ro"),
    LanguageOption("ru", "Russian", "ru"),
    LanguageOption("sk", "Slovak", "sk"),
    LanguageOption("sl", "Slovenian", "sl"),
    LanguageOption("es", "Spanish", "es"),
    LanguageOption("sv", "Swedish", "sv"),
    LanguageOption("uk", "Ukrainian", "uk"),
)

LANGUAGE_OPTIONS_BY_CODE = {option.code: option for option in LANGUAGE_OPTIONS}
# The language Canary transcribes in when none is set (onnx-asr's default prompt).
CANARY_DEFAULT_LANGUAGE = "en"
NOTES_LANGUAGE_VALUES = frozenset(
    {
        DEFAULT_NOTES_LANGUAGE,
        "english_british",
        "english_american",
        SAME_AS_TRANSCRIPTION_LANGUAGE,
        CUSTOM_NOTES_LANGUAGE,
        *LANGUAGE_OPTIONS_BY_CODE,
    }
)

_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ResolvedLanguagePreferences:
    transcription_language_code: str | None
    notes_language_instruction: str
    notes_language_label: str


def validate_transcription_language(value: str) -> str:
    normalized = str(value).strip().lower()
    if (
        normalized != AUTO_TRANSCRIPTION_LANGUAGE
        and normalized not in LANGUAGE_OPTIONS_BY_CODE
    ):
        raise ValueError("Unsupported transcription language.")
    return normalized


def validate_notes_language(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in NOTES_LANGUAGE_VALUES:
        raise ValueError("Unsupported notes language.")
    return normalized


def validate_custom_notes_language_instruction(value: str | None) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > MAX_CUSTOM_NOTES_LANGUAGE_INSTRUCTION_LENGTH:
        raise ValueError(
            "Custom notes language instruction must be at most "
            f"{MAX_CUSTOM_NOTES_LANGUAGE_INSTRUCTION_LENGTH} characters."
        )
    if _CONTROL_CHARACTER_PATTERN.search(normalized):
        raise ValueError(
            "Custom notes language instruction must not contain control characters."
        )
    return normalized


def validate_language_settings(settings: Mapping[str, Any]) -> None:
    transcription_language = settings.get(
        "transcription_language",
        AUTO_TRANSCRIPTION_LANGUAGE,
    )
    notes_language = settings.get("notes_language", DEFAULT_NOTES_LANGUAGE)
    custom_instruction = settings.get("notes_language_custom_instruction", "")

    validate_transcription_language(str(transcription_language))
    normalized_notes_language = validate_notes_language(str(notes_language))
    normalized_custom_instruction = validate_custom_notes_language_instruction(
        None if custom_instruction is None else str(custom_instruction)
    )
    if (
        normalized_notes_language == CUSTOM_NOTES_LANGUAGE
        and not normalized_custom_instruction
    ):
        raise ValueError(
            "Custom notes language instruction is required when notes language is custom."
        )


def apply_recording_language(
    settings: Mapping[str, Any] | None, recording: Any
) -> dict[str, Any]:
    """``settings`` with the recording's transcription language over the owner's.

    The language resolves recording first, then the owner's setting, then
    ``auto`` (#671): a recording with no language of its own (NULL) follows
    the setting. Returns a copy; ``settings`` is not changed.
    """
    resolved = dict(settings or {})
    language = getattr(recording, "transcription_language", None)
    if language:
        resolved["transcription_language"] = language
    return resolved


def resolve_transcription_language_code(
    settings: Mapping[str, Any] | None,
    transcription_backend: str | None = None,
) -> str | None:
    normalized_settings = dict(settings or {})
    selected = validate_transcription_language(
        str(
            normalized_settings.get("transcription_language")
            or AUTO_TRANSCRIPTION_LANGUAGE
        )
    )
    backend = str(
        transcription_backend
        or normalized_settings.get("transcription_backend")
        or "whisper"
    ).lower()
    if backend == "parakeet":
        return None
    if backend == "canary":
        # Canary can't detect a language: without one, onnx-asr prompts it with
        # English. Name that, so the result and the ASR hash say English.
        if selected == AUTO_TRANSCRIPTION_LANGUAGE:
            selected = CANARY_DEFAULT_LANGUAGE
        return LANGUAGE_OPTIONS_BY_CODE[selected].canary_language
    if selected == AUTO_TRANSCRIPTION_LANGUAGE:
        return None
    return selected


def resolve_language_preferences(
    settings: Mapping[str, Any] | None,
    *,
    transcription_backend: str | None = None,
    detected_transcription_language: str | None = None,
) -> ResolvedLanguagePreferences:
    normalized_settings = dict(settings or {})
    validate_language_settings(normalized_settings)

    transcription_language_code = resolve_transcription_language_code(
        normalized_settings,
        transcription_backend,
    )
    notes_language = validate_notes_language(
        str(normalized_settings.get("notes_language") or DEFAULT_NOTES_LANGUAGE)
    )

    if notes_language == "english_british":
        instruction = render_prompt("shared.language_british")
        label = "English (British)"
    elif notes_language == "english_american":
        instruction = render_prompt("shared.language_american")
        label = "English (American)"
    elif notes_language == CUSTOM_NOTES_LANGUAGE:
        custom_instruction = validate_custom_notes_language_instruction(
            normalized_settings.get("notes_language_custom_instruction")
        )
        instruction = render_prompt(
            "shared.language_custom", custom_instruction=custom_instruction
        )
        label = "Custom"
    elif notes_language == SAME_AS_TRANSCRIPTION_LANGUAGE:
        detected_code = str(detected_transcription_language or "").strip().lower()
        source_code = transcription_language_code or detected_code
        source_option = LANGUAGE_OPTIONS_BY_CODE.get(source_code)
        if source_option is not None:
            instruction = source_option.notes_instruction
            label = f"Same as transcription ({source_option.label})"
        else:
            instruction = render_prompt("shared.language_transcript")
            label = "Same as transcription"
    elif notes_language in LANGUAGE_OPTIONS_BY_CODE:
        option = LANGUAGE_OPTIONS_BY_CODE[notes_language]
        instruction = option.notes_instruction
        label = option.label
    else:
        instruction = render_prompt("shared.language_english")
        label = "English"

    return ResolvedLanguagePreferences(
        transcription_language_code=transcription_language_code,
        notes_language_instruction=instruction,
        notes_language_label=label,
    )


def build_output_language_prompt_section(instruction: str | None) -> str:
    normalized = str(instruction or "").strip()
    if not normalized:
        normalized = resolve_language_preferences({}).notes_language_instruction
    return render_prompt("shared.output_language", normalized=normalized)


def get_language_registry_payload() -> dict[str, Any]:
    transcription_languages = [
        {
            "code": AUTO_TRANSCRIPTION_LANGUAGE,
            "label": "Auto-detect",
            "forced_engines": [],
        }
    ]
    transcription_languages.extend(
        {
            "code": option.code,
            "label": option.label,
            "forced_engines": ["whisper", "canary"],
        }
        for option in LANGUAGE_OPTIONS
    )

    notes_languages = [
        {"code": DEFAULT_NOTES_LANGUAGE, "label": "English"},
        {"code": "english_british", "label": "English (British)"},
        {"code": "english_american", "label": "English (American)"},
        {"code": SAME_AS_TRANSCRIPTION_LANGUAGE, "label": "Same as transcription"},
    ]
    notes_languages.extend(
        {"code": option.code, "label": option.label}
        for option in LANGUAGE_OPTIONS
        if option.code != "en"
    )
    notes_languages.append({"code": CUSTOM_NOTES_LANGUAGE, "label": "Custom"})

    return {
        "transcription_languages": transcription_languages,
        "notes_languages": notes_languages,
        "custom_instruction_max_length": MAX_CUSTOM_NOTES_LANGUAGE_INSTRUCTION_LENGTH,
        "engine_capabilities": {
            "whisper": {
                "forced_language": True,
                "auto_detect": True,
                "guidance": "Whisper supports auto-detection or a forced language.",
            },
            "canary": {
                "forced_language": True,
                "auto_detect": False,
                "guidance": (
                    "Canary can't detect a language, so it needs one set. Until you "
                    "choose one, it transcribes as English."
                ),
            },
            "parakeet": {
                "forced_language": False,
                "auto_detect": True,
                "guidance": (
                    "Parakeet uses multilingual auto-detection; forced language selection "
                    "is not available for this engine."
                ),
            },
        },
    }
