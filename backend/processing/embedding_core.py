import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
from pyannote.core import Segment

from backend.processing.embedding_version import (
    EMBEDDING_METHOD_VERSION,
    LEGACY_EMBEDDING_METHOD_VERSION,
)
from backend.utils.config_manager import config_manager
from backend.utils.pyannote_model_utils import resolve_local_pyannote_model

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"

# Re-exported for the worker-side callers that already import them from here.
# They are defined in a torch-free module because the API image has no torch and
# importing this one from a request path raises ModuleNotFoundError.

# Crops shorter than this carry too little evidence to be a reliable voiceprint.
EMBEDDING_MIN_CROP_S = 1.0
# Absolute floor used only when a speaker has nothing longer to offer.
EMBEDDING_FALLBACK_MIN_CROP_S = 0.5
# Long turns are split into crops of at most this length. window="whole" pools
# over the entire crop in one forward pass, so an uncapped 5-minute monologue
# would be a single very large tensor.
EMBEDDING_MAX_CROP_S = 30.0
# How many crops to average per speaker.
EMBEDDING_MAX_CROPS = 20


_embedding_model_cache = {}


def _register_pyannote_safe_globals() -> None:
    """Register PyTorch safe globals required by pyannote embedding models."""
    try:
        from pyannote.audio.core.task import Problem, Resolution, Specifications

        safe_globals_list = [Specifications, Problem, Resolution]

        try:
            from torch.torch_version import TorchVersion

            safe_globals_list.append(TorchVersion)
        except ImportError:
            pass

        torch.serialization.add_safe_globals(safe_globals_list)
    except ImportError:
        # Keep pyannote optional at module import time so non-ML tests can import
        # this module without the worker-only runtime installed.
        pass


def _load_pyannote_audio_types():
    """Load pyannote.audio lazily to avoid import-time CI/test failures."""
    _register_pyannote_safe_globals()
    from pyannote.audio import Inference, Model

    return Inference, Model


def release_embedding_model_cache():
    """Releases cached speaker embedding models from memory."""
    global _embedding_model_cache
    if _embedding_model_cache:
        logger.info(
            f"Releasing {_embedding_model_cache.keys()} from speaker embedding model cache."
        )
        _embedding_model_cache.clear()

    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load_embedding_model(device_str: str, hf_token: str = None):
    """Load pyannote embedding model."""
    try:
        Inference, Model = _load_pyannote_audio_types()
        resolved_model = resolve_local_pyannote_model(DEFAULT_EMBEDDING_MODEL)
        if resolved_model.source == "remote":
            if not hf_token:
                hf_token = config_manager.get("hf_token")

            if not hf_token:
                raise ValueError(
                    "Hugging Face token (hf_token) not found and no local embedding model is available."
                )

        # Explicitly load the model first using Model.from_pretrained
        logger.info(
            "Loading embedding model from %s source: %s",
            resolved_model.source,
            resolved_model.load_ref,
        )

        # Trusts model source (pyannote/wespeaker-voxceleb-resnet34-LM).
        # Safe globals are added at module level.
        # Note: Passing weights_only=False to Model.from_pretrained does NOT work because
        # Requires safe_globals as pyannote excludes it from torch.load.
        if resolved_model.source == "remote":
            loaded_model = Model.from_pretrained(
                resolved_model.load_ref, token=hf_token
            )
        else:
            loaded_model = Model.from_pretrained(resolved_model.load_ref)

        # window="whole" pools over the entire crop in a single forward pass,
        # which is what a speaker embedding model is designed for. The previous
        # window="sliding" setting fed the model 5s sub-windows and averaged the
        # raw outputs, which measurably depressed same-speaker similarity and
        # inflated different-speaker similarity. Callers must keep crops bounded
        # (see EMBEDDING_MAX_CROP_S).
        model = Inference(loaded_model, window="whole")
        model.to(torch.device(device_str))
        return model
    except OSError as e:
        error_msg = str(e)
        if "403" in error_msg or "forbidden" in error_msg.lower():
            logger.error(f"Permission denied for Embedding model: {e}")
            raise RuntimeError(
                "Permission denied for Embedding model. "
                "Please ensure you have accepted the terms of use on the Hugging Face model page "
                "and that your token has the correct permissions."
            ) from e
        else:
            logger.error(
                f"Failed to load embedding model (OSError): {e}", exc_info=True
            )
            raise RuntimeError(f"Could not load embedding model: {e}") from e
    except Exception as e:
        logger.error(f"Failed to load embedding model: {e}", exc_info=True)
        raise RuntimeError(
            "Could not load embedding model from the configured or bundled source."
        ) from e


def _filter_outlier_embeddings(embeddings: list) -> list:
    """
    Removes outlier embeddings that are dissimilar to the majority.
    Computes each embedding's mean cosine similarity to all others and
    excludes those falling more than 1 standard deviation below the group mean.
    Always retains at least one embedding.
    """
    n = len(embeddings)
    if n < 3:
        return embeddings

    # Pairwise cosine similarity matrix
    arr = np.array(embeddings)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    normalised = arr / norms
    sim_matrix = normalised @ normalised.T

    # Mean similarity of each embedding to all others (excluding self)
    np.fill_diagonal(sim_matrix, 0)
    mean_sims = sim_matrix.sum(axis=1) / (n - 1)

    group_mean = np.mean(mean_sims)
    group_std = np.std(mean_sims)

    cutoff = group_mean - group_std
    filtered = [emb for emb, ms in zip(embeddings, mean_sims) if ms >= cutoff]

    if not filtered:
        # Fallback: keep the single most central embedding
        best_idx = int(np.argmax(mean_sims))
        filtered = [embeddings[best_idx]]

    if len(filtered) < n:
        logger.info(
            f"Outlier filter removed {n - len(filtered)}/{n} segment embeddings "
            f"(cutoff={cutoff:.3f})"
        )

    return filtered


def _unit(vector: np.ndarray) -> Optional[np.ndarray]:
    """L2-normalise a vector, or return None when it has no direction."""
    norm = np.linalg.norm(vector)
    if not np.isfinite(norm) or norm == 0:
        return None
    return vector / norm


def _crop_embedding(model, audio_path: str, segment) -> Optional[np.ndarray]:
    """Run the embedding model over one crop and return a unit vector."""
    emb = model.crop(audio_path, segment)
    if hasattr(emb, "data"):
        emb = emb.data
    emb = np.asarray(emb, dtype=float)
    # window="whole" yields a single vector; stay tolerant of a frame axis so a
    # caller that supplies a sliding-window Inference still gets a sane result.
    if emb.ndim == 2:
        emb = np.mean(emb, axis=0)
    if emb.ndim != 1 or not np.all(np.isfinite(emb)):
        return None
    return _unit(emb)


def _split_long_segment(segment, max_length_s: float = EMBEDDING_MAX_CROP_S) -> list:
    """Chop a turn into crops of at most ``max_length_s`` seconds.

    Keeps a single forward pass bounded, and makes crops comparable in length so
    one long monologue cannot dominate the averaged voiceprint.
    """
    from pyannote.core import Segment

    if segment.duration <= max_length_s:
        return [segment]

    pieces = []
    start = segment.start
    while start < segment.end - 0.01:
        end = min(start + max_length_s, segment.end)
        pieces.append(Segment(start, end))
        start = end
    return pieces


def _select_voiceprint_crops(segments, overlap=None) -> list:
    """Pick the crops used to build one speaker's voiceprint.

    Overlapped speech is removed first: the diarization pipeline itself sets
    ``embedding_exclude_overlap: true`` for exactly this reason, and a crop
    containing two voices pulls the centroid towards a mixture of both.
    """
    from pyannote.core import Timeline

    timeline = Timeline(segments)
    if overlap is not None:
        try:
            timeline = timeline.extrude(overlap)
        except Exception as e:  # noqa: BLE001 -- boundary: overlap removal is an optimisation
            logger.warning("Could not extrude overlapped speech: %s", e)

    candidates = []
    for segment in timeline:
        candidates.extend(_split_long_segment(segment))

    usable = [s for s in candidates if s.duration >= EMBEDDING_MIN_CROP_S]
    if not usable:
        usable = [s for s in candidates if s.duration >= EMBEDDING_FALLBACK_MIN_CROP_S]
    if not usable:
        # Nothing survived overlap removal; fall back to the raw turns so a
        # speaker who only ever talks over others still gets a voiceprint.
        usable = [
            piece
            for segment in segments
            for piece in _split_long_segment(segment)
            if piece.duration >= EMBEDDING_FALLBACK_MIN_CROP_S
        ] or list(segments)

    usable.sort(key=lambda s: s.duration, reverse=True)
    return usable[:EMBEDDING_MAX_CROPS]


def _aggregate_crop_embeddings(embeddings: list) -> Optional[List[float]]:
    """Average unit crop embeddings into one unit voiceprint."""
    if not embeddings:
        return None
    if len(embeddings) >= 3:
        embeddings = _filter_outlier_embeddings(embeddings)
    mean = np.mean(np.array(embeddings), axis=0)
    unit = _unit(mean)
    if unit is None:
        return None
    return unit.tolist()


def extract_embeddings(
    audio_path: str, diarization_result, device_str: str = "auto", config: dict = None
) -> Dict[str, List[float]]:
    """
    Extracts embeddings for each speaker in the diarization result.
    Returns a dictionary mapping speaker label to embedding vector (list of floats).

    Every vector returned is unit length and carries method version
    ``EMBEDDING_METHOD_VERSION``; callers persisting these must record that
    version alongside them.
    """
    if diarization_result is None:
        logger.warning("Diarization result is None, skipping embedding extraction")
        return {}

    logger.info(f"Starting embedding extraction for {audio_path}")

    # Use provided config or fall back to system config
    get_config = config.get if config else config_manager.get
    hf_token = get_config("hf_token")

    if device_str == "auto":
        device_str = "cuda" if torch.cuda.is_available() else "cpu"

    try:
        cache_key = (DEFAULT_EMBEDDING_MODEL, device_str)
        if cache_key not in _embedding_model_cache:
            _embedding_model_cache[cache_key] = load_embedding_model(
                device_str, hf_token
            )
        model = _embedding_model_cache[cache_key]

        embeddings = {}

        speaker_segments = {}
        for turn, _, label in diarization_result.itertracks(yield_label=True):
            if label not in speaker_segments:
                speaker_segments[label] = []
            speaker_segments[label].append(turn)

        # Regions where two or more speakers are simultaneously active. Removed
        # from every speaker's crops below so no voiceprint is built from a
        # mixture of voices.
        try:
            overlap = diarization_result.get_overlap()
        except Exception as e:  # noqa: BLE001 -- boundary: not all annotations support this
            logger.warning("Could not compute overlapped speech regions: %s", e)
            overlap = None

        for label, segments in speaker_segments.items():
            crops = _select_voiceprint_crops(segments, overlap)

            speaker_embeddings = []
            for seg in crops:
                try:
                    unit_embedding = _crop_embedding(model, audio_path, seg)
                    if unit_embedding is not None:
                        speaker_embeddings.append(unit_embedding)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Failed to extract embedding for speaker {label} segment {seg}: {e}"
                    )

            # Outlier filtering drops crops unlike the rest, which is how a
            # mis-diarised turn from another speaker is kept out of the mean.
            voiceprint = _aggregate_crop_embeddings(speaker_embeddings)
            if voiceprint is not None:
                embeddings[label] = voiceprint
                logger.info(
                    "Voiceprint for %s built from %d crop(s) (method v%d).",
                    label,
                    len(speaker_embeddings),
                    EMBEDDING_METHOD_VERSION,
                )

        return embeddings

    except Exception as e:
        logger.error(f"Embedding extraction failed: {e}", exc_info=True)
        return {}


def extract_embedding_for_segments(
    audio_path: str,
    segments: List[Tuple[float, float]],
    device_str: str = "auto",
    hf_token: str = None,
) -> Optional[List[float]]:
    """
    Extract a single aggregated embedding from specific time segments.

    This is used for on-demand voiceprint creation when a user manually
    triggers voiceprint extraction for a specific speaker.

    Args:
        audio_path: Path to the audio file.
        segments: List of (start_time, end_time) tuples in seconds.
        device_str: Device to use for inference ("cpu" or "cuda").
        hf_token: Hugging Face token.

    Returns:
        Aggregated embedding vector as a list of floats, or None if extraction fails.
    """
    if not segments:
        logger.warning("No segments provided for embedding extraction")
        return None

    if device_str == "auto":
        device_str = "cuda" if torch.cuda.is_available() else "cpu"

    # fallback if not provided
    if not hf_token:
        hf_token = config_manager.get("hf_token")

    logger.info(f"Extracting embedding from {len(segments)} segments in {audio_path}")

    try:
        cache_key = (DEFAULT_EMBEDDING_MODEL, device_str)
        if cache_key not in _embedding_model_cache:
            _embedding_model_cache[cache_key] = load_embedding_model(
                device_str, hf_token
            )
        model = _embedding_model_cache[cache_key]

        # Same crop preparation as automatic extraction so a manually created
        # voiceprint lands in the same region of the space as a pipeline one and
        # the two remain directly comparable.
        crops = _select_voiceprint_crops(
            [Segment(start, end) for start, end in segments]
        )

        speaker_embeddings = []
        for seg in crops:
            try:
                unit_embedding = _crop_embedding(model, audio_path, seg)
                if unit_embedding is not None:
                    speaker_embeddings.append(unit_embedding)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"Failed to extract embedding for segment ({seg.start:.2f}, {seg.end:.2f}): {e}"
                )
                continue

        if not speaker_embeddings:
            logger.warning(
                "No embeddings could be extracted from the provided segments"
            )
            return None

        return _aggregate_crop_embeddings(speaker_embeddings)

    except Exception as e:
        logger.error(f"Embedding extraction for segments failed: {e}", exc_info=True)
        return None


__all__ = [
    "EMBEDDING_METHOD_VERSION",
    "LEGACY_EMBEDDING_METHOD_VERSION",
]
