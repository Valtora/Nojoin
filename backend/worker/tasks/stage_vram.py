"""Freeing one finalize stage's models before the next stage loads its own.

Extracted from ``pipeline.py`` to keep that module within its size budget.
Diarisation runs first, then ASR, then voiceprints (#670). Each release runs
only on a GPU host, where VRAM is the contended resource: ONNX Runtime's arena
grows with the transcription window and never shrinks, and pyannote keeps its
pipeline until released, so on a small card a finished stage can leave the
next with nothing to allocate. The models reload lazily on the next task.
"""

import gc
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _release_on_gpu(release: Callable[[], None], models: str) -> None:
    from backend.processing.onnx_providers import gpu_is_present

    if not gpu_is_present():
        return

    import torch

    try:
        release()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Released %s VRAM.", models)
    except Exception as e:  # noqa: BLE001 -- boundary: VRAM release is best-effort
        logger.error("Error releasing %s VRAM: %s", models, e)


def release_diarization_vram() -> None:
    """Free the diarisation pipeline once it has run, before ASR loads."""

    def release() -> None:
        from backend.processing.diarize import release_pipeline_cache

        release_pipeline_cache()

    _release_on_gpu(release, "diarization")


def release_asr_vram() -> None:
    """Free the ASR models once transcription is done, before voiceprints."""

    def release() -> None:
        from backend.processing.transcribe import release_model_cache

        release_model_cache()

    _release_on_gpu(release, "ASR")
