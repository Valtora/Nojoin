"""Identity of the ASR configuration a live run transcribed with.

The live lane stamps this hash and model name on the processing run it opens
for each batch of provisional utterances.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.utils.languages import resolve_transcription_language_code


def get_transcription_model_name(config: dict[str, Any] | None) -> str | None:
    normalized = dict(config or {})
    backend = str(normalized.get("transcription_backend") or "whisper")
    if backend == "parakeet":
        return str(normalized.get("parakeet_model") or "parakeet-tdt-0.6b-v3")
    if backend == "canary":
        return str(normalized.get("canary_model") or "nemo-canary-1b-v2")
    return str(normalized.get("whisper_model_size") or "turbo")


def build_asr_config_hash(config: dict[str, Any] | None) -> str:
    normalized = dict(config or {})
    backend = str(normalized.get("transcription_backend") or "whisper")
    effective_language = resolve_transcription_language_code(normalized, backend)
    payload = {
        "transcription_backend": backend,
        "model_name": get_transcription_model_name(normalized),
        "processing_device": normalized.get("processing_device", "auto"),
        "use_gpu": bool(normalized.get("use_gpu", True)),
        "transcription_language": effective_language or "auto",
    }
    digest = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    return digest
