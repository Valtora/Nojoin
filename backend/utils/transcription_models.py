"""The model ids each transcription engine knows, and the engine override check."""

import logging
from collections.abc import Mapping
from typing import Any

from backend.utils.config_manager import TRANSCRIPTION_BACKENDS, WHISPER_MODEL_SIZES

logger = logging.getLogger(__name__)

# The model ids the onnx-asr engines know, mapped to the id onnx-asr loads.
# ParakeetEngine and CanaryEngine take their maps from here.
PARAKEET_MODEL_IDS = {"parakeet-tdt-0.6b-v3": "nemo-parakeet-tdt-0.6b-v3"}
CANARY_MODEL_IDS = {"nemo-canary-1b-v2": "nemo-canary-1b-v2"}

# The values a recording's engine override may hold, by key. A reprocess picks
# from these and nothing else.
ENGINE_OVERRIDE_CHOICES: dict[str, tuple[str, ...]] = {
    "transcription_backend": tuple(TRANSCRIPTION_BACKENDS),
    "whisper_model_size": tuple(WHISPER_MODEL_SIZES),
    "parakeet_model": tuple(PARAKEET_MODEL_IDS),
    "canary_model": tuple(CANARY_MODEL_IDS),
}


def known_engine_override(override: Mapping[str, Any] | None) -> dict[str, str]:
    """The entries of an engine override that name a known engine or model.

    Any other key, and any value outside :data:`ENGINE_OVERRIDE_CHOICES`, is left
    out with a warning.
    """
    if not override:
        return {}
    known = {
        key: value
        for key, value in override.items()
        if key in ENGINE_OVERRIDE_CHOICES
        and isinstance(value, str)
        and value in ENGINE_OVERRIDE_CHOICES[key]
    }
    ignored = sorted(set(override) - set(known))
    if ignored:
        logger.warning("Engine override entries ignored as unknown: %s", ignored)
    logger.info("Engine override applied: %s", known)
    return known
