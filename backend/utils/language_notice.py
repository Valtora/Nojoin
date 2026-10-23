"""The language a transcript was detected in, and the notice when it disagrees (#671).

Whisper detects a language on the first 30 s of VAD speech on every run, even
when a language is set (``whisper_engine._detect_language_on_speech``). The
pipeline stores what it heard beside the language the transcript used
(``Transcript.language_detection``), and the recording page shows a notice when
the two disagree. Either answer sets the recording's own language, which hides
the notice for good.
"""

from __future__ import annotations

from typing import Any, Mapping

from backend.utils.languages import (
    AUTO_TRANSCRIPTION_LANGUAGE,
    LANGUAGE_OPTIONS_BY_CODE,
)

# Detection must be at least this sure before it questions a set language, so a
# bilingual meeting's coin toss doesn't nag. A guess: no fixture measures it.
LANGUAGE_NOTICE_MIN_PROBABILITY = 0.5


def language_detection_record(
    transcription_result: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any] | None:
    """What the engine heard and what the transcript used, or None.

    None when no detection ran: VAD off, the detection failed, or the engine
    isn't Whisper. ``config`` is the run's merged settings, which name the
    engine.
    """
    detection = transcription_result.get("language_detection")
    if not detection:
        return None
    return {
        **detection,
        "used": transcription_result.get("language"),
        "engine": config.get("transcription_backend") or "whisper",
    }


def language_label(code: str) -> str:
    option = LANGUAGE_OPTIONS_BY_CODE.get(code)
    return option.label if option else code


def language_notice(
    *, processed: bool, recording_language: str | None, detection: Any
) -> dict[str, Any] | None:
    """The mismatch notice for a recording, or None when there is nothing to ask.

    Shown only once processing is complete, while the recording has no language
    of its own, and when detection was sure enough of a language other than the
    one the transcript used. A detected language that can't be chosen
    (``LANGUAGE_OPTIONS``) is reprocessed on ``auto``, where Whisper detects it
    again on the same speech.
    """
    if not processed or recording_language or not isinstance(detection, Mapping):
        return None
    detected = str(detection.get("detected") or "").lower()
    used = str(detection.get("used") or "").lower()
    probability = detection.get("probability")
    if not detected or not used or detected == used:
        return None
    if not isinstance(probability, (int, float)):
        return None
    if probability < LANGUAGE_NOTICE_MIN_PROBABILITY:
        return None
    return {
        "kind": "mismatch",
        "detected": detected,
        "detected_label": detection.get("detected_label") or language_label(detected),
        "used": used,
        "used_label": language_label(used),
        "engine": detection.get("engine") or "whisper",
        "reprocess_language": (
            detected
            if detected in LANGUAGE_OPTIONS_BY_CODE
            else AUTO_TRANSCRIPTION_LANGUAGE
        ),
    }
