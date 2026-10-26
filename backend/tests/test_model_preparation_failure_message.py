"""A failed model preparation reports what failed and where to retry it.

The progress message is what the setup wizard's Finish step and the Model
downloads card show. It used to carry the exception itself, so a server
without internet access showed "Model preparation failed: <urlopen error
[Errno -3] Temporary failure in name resolution>". The exception still goes to
the worker log.
"""

from __future__ import annotations

import logging
import urllib.error

import pytest

from backend import preload_models

OFFLINE = urllib.error.URLError("[Errno -3] Temporary failure in name resolution")


@pytest.fixture
def progress(monkeypatch) -> list[dict]:
    reports: list[dict] = []

    def _record(progress, message, *args, status="downloading", stage=None):
        reports.append({"message": message, "status": status, "stage": stage})
        return True

    monkeypatch.setattr(preload_models, "clear_download_progress", lambda: None)
    monkeypatch.setattr(preload_models, "set_download_progress", _record)
    monkeypatch.setattr(preload_models, "_release_validation_caches", lambda: None)
    return reports


def _fail(*args, **kwargs):
    raise OFFLINE


def test_an_offline_whisper_download_names_the_model_and_the_retry(
    monkeypatch, progress, caplog
) -> None:
    monkeypatch.setattr(preload_models, "_prepare_whisper_model", _fail)

    with caplog.at_level(logging.ERROR, logger=preload_models.logger.name):
        with pytest.raises(urllib.error.URLError):
            preload_models.download_models(
                transcription_backend="whisper", whisper_model_size="turbo"
            )

    error = progress[-1]
    assert error["status"] == "error"
    assert error["message"] == (
        "Could not download the Whisper transcription model. Check that the "
        "server can reach the internet and has free disk space, then retry "
        "from Model downloads under Advanced in Settings > AI providers."
    )
    assert "urlopen" not in error["message"]
    # The detail an operator needs to diagnose it stays in the worker log.
    assert "Temporary failure in name resolution" in caplog.text


def test_a_failure_after_whisper_names_the_speaker_models(
    monkeypatch, progress
) -> None:
    monkeypatch.setattr(preload_models, "_prepare_whisper_model", lambda size: None)
    monkeypatch.setattr(preload_models, "_prepare_pyannote_models", _fail)

    with pytest.raises(urllib.error.URLError):
        preload_models.download_models(
            transcription_backend="whisper", whisper_model_size="turbo"
        )

    assert progress[-1]["message"].startswith("Could not download the speaker models.")


@pytest.mark.parametrize(
    ("stage", "subject"),
    [
        ("parakeet", "the Parakeet transcription model"),
        ("canary", "the Canary transcription model"),
        (None, "the transcription and speaker models"),
        ("queued", "the transcription and speaker models"),
    ],
)
def test_each_stage_has_a_plain_subject(stage, subject) -> None:
    assert preload_models.preparation_failure_message(stage).startswith(
        f"Could not download {subject}."
    )
