"""The model pass of the live label engine, on Nojoin's own model loaders.

Both models are the bundled ones, and both come from caches the worker already
manages: ``segmentation_model`` (pyannote/segmentation-3.0, shared with overlap
analytics) and ``embedding_core``'s WeSpeaker cache (shared with voiceprint
extraction). The worker's model-cache release frees them with everything else,
and keeps them warm while a capture uploads, as it does the live ASR model.

Per window: segmentation-3.0's powerset output decoded to hard multilabel
activations (as diart's ``PowersetAdapter`` does), then one WeSpeaker embedding
per local speaker, weighted by ``overlapped_speech_penalty`` and L2-normalised
(diart's ``OverlapAwareSpeakerEmbedding``).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .clustering import overlapped_speech_penalty
from .engine import LiveLabelConfig


def _embedding_model(device: str) -> Any:
    from backend.processing.embedding_core import (
        DEFAULT_EMBEDDING_MODEL,
        _embedding_model_cache,
        load_embedding_model,
    )

    cache_key = (DEFAULT_EMBEDDING_MODEL, device)
    if cache_key not in _embedding_model_cache:
        _embedding_model_cache[cache_key] = load_embedding_model(device)
    # The cached pyannote Inference wraps the model; the weighted forward pass
    # needs the model itself.
    return _embedding_model_cache[cache_key].model


class LiveLabelModel:
    """Callable model pass: (batch, samples) windows to activations and embeddings."""

    def __init__(self, device: str, config: LiveLabelConfig) -> None:
        import torch
        from pyannote.audio.utils.powerset import Powerset

        from backend.processing.segmentation_model import load_segmentation_model

        self._torch = torch
        self._device = torch.device(device)
        self._config = config
        self._segmentation = load_segmentation_model(device).eval()
        specifications = self._segmentation.specifications
        self._powerset = Powerset(
            len(specifications.classes), specifications.powerset_max_classes
        ).to(self._device)
        self._embedding = _embedding_model(device).eval()

    def __call__(self, windows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        torch = self._torch
        with torch.inference_mode():
            waveforms = torch.from_numpy(windows).to(self._device).unsqueeze(1)
            activations = self._powerset.to_multilabel(self._segmentation(waveforms))
            batch, frames, speakers = activations.shape
            weights = overlapped_speech_penalty(
                activations, self._config.overlap_gamma, self._config.overlap_beta
            )
            inputs = waveforms.repeat(1, speakers, 1).reshape(batch * speakers, 1, -1)
            embeddings = self._embedding(
                inputs, weights.permute(0, 2, 1).reshape(batch * speakers, frames)
            ).reshape(batch, speakers, -1)
            embeddings = embeddings / torch.norm(embeddings, p=2, dim=-1, keepdim=True)
        return activations.float().cpu().numpy(), embeddings.float().cpu().numpy()
