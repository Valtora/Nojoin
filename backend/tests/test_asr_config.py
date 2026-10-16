from __future__ import annotations

from backend.utils.asr_config import get_transcription_model_name


def test_get_transcription_model_name_uses_backend_specific_config():
    assert (
        get_transcription_model_name(
            {"transcription_backend": "whisper", "whisper_model_size": "base"}
        )
        == "base"
    )
    assert (
        get_transcription_model_name(
            {"transcription_backend": "parakeet", "parakeet_model": "parakeet-v3"}
        )
        == "parakeet-v3"
    )
    assert (
        get_transcription_model_name(
            {"transcription_backend": "canary", "canary_model": "canary-v2"}
        )
        == "canary-v2"
    )
