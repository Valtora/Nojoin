"""Loader and cache for the ``pyannote/segmentation-3.0`` model.

Overlap analytics (:mod:`backend.processing.audio_overlap`) runs this model over
a recording's audio, and model preparation loads it once to validate the
download. Cached per resolved model and device, like the diarisation pipeline
and the embedding model, and released with them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import torch

from backend.utils.config_manager import config_manager
from backend.utils.pyannote_model_utils import resolve_local_pyannote_model

if TYPE_CHECKING:
    from pyannote.audio import Model

logger = logging.getLogger(__name__)

SEGMENTATION_MODEL = "pyannote/segmentation-3.0"

_model_cache: dict[tuple[str, str], Model] = {}


def release_segmentation_model_cache() -> None:
    """Drop cached segmentation models (mirrors ``diarize.release_pipeline_cache``)."""
    if _model_cache:
        logger.info(
            "Releasing %s from segmentation model cache.", list(_model_cache.keys())
        )
        _model_cache.clear()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load_segmentation_model(device_str: str, hf_token: str | None = None) -> Model:
    from pyannote.audio import Model

    resolved_model = resolve_local_pyannote_model(SEGMENTATION_MODEL)
    cache_key = (resolved_model.load_ref, device_str)
    cached = _model_cache.get(cache_key)
    if cached is not None:
        return cached

    if resolved_model.source == "remote":
        if not hf_token:
            hf_token = config_manager.get("hf_token")
        if not hf_token:
            raise ValueError(
                "Hugging Face token (hf_token) not found and no local segmentation model is available."
            )
        model = Model.from_pretrained(resolved_model.load_ref, token=hf_token)
    else:
        logger.info(
            "Loading segmentation model from %s source: %s",
            resolved_model.source,
            resolved_model.load_ref,
        )
        model = Model.from_pretrained(resolved_model.load_ref)

    model.to(torch.device(device_str))
    _model_cache[cache_key] = model
    return model
