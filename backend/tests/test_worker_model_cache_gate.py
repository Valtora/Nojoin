"""Guards for the warm-during-live model-cache gate.

Reloading the live ASR model on every segment costs ~9s (observed in worker
logs), so the per-task cache release is skipped while a capture is uploading.
It must still release when idle.
"""

import backend.celery_app as celery_app_module
from backend.celery_app import _should_release_model_caches


def test_releases_when_idle(monkeypatch) -> None:
    monkeypatch.setattr(celery_app_module, "_has_active_live_capture", lambda: False)
    assert _should_release_model_caches() is True


def test_skips_release_during_active_capture(monkeypatch) -> None:
    monkeypatch.setattr(celery_app_module, "_has_active_live_capture", lambda: True)
    assert _should_release_model_caches() is False
