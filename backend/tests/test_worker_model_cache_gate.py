"""Guards for the warm-during-live model-cache gate.

Reloading the live ASR model on every segment costs ~9s (observed in worker
logs), so the per-task cache release is skipped while a capture is uploading.
It must still release when idle, and must always honour the ``keep_models_loaded``
operator pin.
"""

import backend.celery_app as celery_app_module
from backend.celery_app import _should_release_model_caches


def _patch_keep_models_loaded(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(
        "backend.utils.config_manager.config_manager.get",
        lambda key, default=None: value if key == "keep_models_loaded" else default,
    )


def test_releases_when_idle_and_not_pinned(monkeypatch) -> None:
    monkeypatch.setattr(celery_app_module, "_has_active_live_capture", lambda: False)
    _patch_keep_models_loaded(monkeypatch, False)
    assert _should_release_model_caches() is True


def test_skips_release_during_active_capture(monkeypatch) -> None:
    monkeypatch.setattr(celery_app_module, "_has_active_live_capture", lambda: True)
    _patch_keep_models_loaded(monkeypatch, False)
    assert _should_release_model_caches() is False


def test_keep_models_loaded_pin_wins_even_when_idle(monkeypatch) -> None:
    monkeypatch.setattr(celery_app_module, "_has_active_live_capture", lambda: False)
    _patch_keep_models_loaded(monkeypatch, True)
    assert _should_release_model_caches() is False
