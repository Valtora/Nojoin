"""The language a settling capture transcribes in, detected once (#776).

On automatic, Whisper detects a language on every input it reads, and the live
lane hands it one on every 2 s settling step. A capture keeps one language,
like the final pass, which detects once per recording (#671). So once Whisper
has heard the same language on ``CAPTURE_LANGUAGE_AGREEMENT`` successive
steps, the capture is pinned to it and later steps transcribe in it without
detecting. Agreement guards the first steps, whose short or noisy input can be
heard as another language; until then each step detects as before.

Only Whisper on automatic detects. A set language, the recording's own or else
the owner's, never does, and Parakeet and Canary have no detection to skip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.processing.engines.base import CAPTURE_LANGUAGE
from backend.utils.languages import resolve_transcription_language_code

# Key of the capture's language in ``Transcript.settling_state``.
CAPTURE_LANGUAGE_KEY = "capture_language"
# Successive detections of one language that pin a capture to it.
CAPTURE_LANGUAGE_AGREEMENT = 3
# Where the decode records the language its result was transcribed in.
_HEARD_KEY = "_settling_heard_language"
# The decoded payload's note of what the step's detection chose, for publication
# to count; never stored with the words.
HEARD_LANGUAGE = "_heard_language"


def _detects_language(config: dict[str, Any]) -> bool:
    backend = str(config.get("transcription_backend") or "whisper").lower()
    return (
        backend == "whisper"
        and resolve_transcription_language_code(config, backend) is None
    )


@dataclass(frozen=True)
class StepLanguage:
    """The config one settling step decodes with, and whether it detects."""

    config: dict[str, Any]
    detects: bool

    def heard(self) -> str | None:
        """The language this step's own detection chose, if it detected."""
        heard = self.config.get(_HEARD_KEY)
        return str(heard) if self.detects and heard else None

    def tag(self, payload: dict[str, Any]) -> dict[str, Any]:
        """``payload`` noting what this step's detection chose, if it detected."""
        heard = self.heard()
        return {**payload, HEARD_LANGUAGE: heard} if heard else payload


def step_language(
    config: dict[str, Any], capture: dict[str, Any] | None
) -> StepLanguage:
    """A copy of ``config`` with the capture's language once it is pinned."""
    decode = dict(config)
    if not _detects_language(config):
        return StepLanguage(decode, False)
    if capture and capture.get("pinned") and capture.get("code"):
        decode[CAPTURE_LANGUAGE] = capture["code"]
        return StepLanguage(decode, False)
    return StepLanguage(decode, True)


def record_heard_language(config: dict[str, Any], result: dict[str, Any]) -> None:
    """Keep the language a decode's result was transcribed in."""
    config[_HEARD_KEY] = result.get("language")


def observe_language(capture: dict[str, Any] | None, heard: str) -> dict[str, Any]:
    """The capture's language state after a step whose detection chose ``heard``.

    Returns ``capture`` unchanged once it is pinned.
    """
    if capture and capture.get("pinned"):
        return capture
    previous = capture or {}
    count = int(previous.get("count", 0)) + 1 if previous.get("code") == heard else 1
    return {
        "code": heard,
        "count": count,
        "pinned": count >= CAPTURE_LANGUAGE_AGREEMENT,
    }
