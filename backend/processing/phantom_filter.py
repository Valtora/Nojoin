"""
Post-diarization phantom speaker filter.

Detects and reassigns speaker segments that are likely caused by non-speech
sounds (notification chimes, background noise) rather than genuine speakers.
Uses a two-stage approach:
  1. Heuristic detection: speakers with negligible total duration / segment count.
  2. Embedding validation: confirms candidates are non-human-like audio.
"""

import logging
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from pyannote.core import Annotation, Segment

logger = logging.getLogger(__name__)

# --- Phantom Speaker Detection Thresholds ---
# Maximum total speaking duration (seconds) for a speaker to be considered a phantom candidate
PHANTOM_MAX_DURATION_S = 2.0
# Maximum segment count for a speaker to be considered a phantom candidate
PHANTOM_MAX_SEGMENTS = 3
# Below this cosine similarity to ALL established speakers, confirmed as non-speech
PHANTOM_EMBEDDING_FLOOR = 0.3
# Above this similarity to a real speaker, merge into that speaker instead of reassigning
PHANTOM_MERGE_THRESHOLD = 0.65


def _get_speaker_stats(diarization: Annotation) -> Dict[str, Dict[str, Any]]:
    """Compute total duration and segment count per speaker label."""
    stats: Dict[str, Dict[str, Any]] = {}
    for segment, _, label in diarization.itertracks(yield_label=True):
        if label not in stats:
            stats[label] = {"duration": 0.0, "count": 0, "segments": []}
        stats[label]["duration"] += segment.duration
        stats[label]["count"] += 1
        stats[label]["segments"].append(segment)
    return stats


def _find_nearest_speaker(
    target_segment: Segment,
    diarization: Annotation,
    exclude_labels: set,
) -> Optional[str]:
    """
    Find the established speaker whose segment is temporally closest
    to the target segment. Returns None if no eligible speaker exists.
    """
    best_label = None
    best_distance = float("inf")

    for segment, _, label in diarization.itertracks(yield_label=True):
        if label in exclude_labels:
            continue
        # Temporal distance: gap between segment boundaries
        distance = max(0.0, target_segment.start - segment.end, segment.start - target_segment.end)
        if distance < best_distance:
            best_distance = distance
            best_label = label
    return best_label


def filter_phantom_speakers(
    diarization: Annotation,
    audio_path: str,
    config: Optional[dict] = None,
    max_duration_s: float = PHANTOM_MAX_DURATION_S,
    max_segments: int = PHANTOM_MAX_SEGMENTS,
    embedding_floor: float = PHANTOM_EMBEDDING_FLOOR,
    merge_threshold: float = PHANTOM_MERGE_THRESHOLD,
) -> Annotation:
    """
    Detect and reassign phantom speaker segments in a diarization result.

    Two-stage filter:
      Stage 1 -- Heuristic: identify speakers with negligible presence
                 (low total duration AND low segment count).
      Stage 2 -- Embedding: for each candidate, extract an embedding and
                 compare against established speakers. If the embedding is
                 non-human-like (below floor), reassign to the nearest temporal
                 speaker. If similar to a real speaker (above merge threshold),
                 merge into that speaker.

    Args:
        diarization: Pyannote Annotation from diarization.
        audio_path:  Path to the WAV file used for diarization.
        config:      Optional config dict (for device/hf_token overrides).
        max_duration_s: Heuristic ceiling for total speaker duration.
        max_segments:   Heuristic ceiling for speaker segment count.
        embedding_floor: Cosine similarity floor for non-speech confirmation.
        merge_threshold: Cosine similarity above which a phantom is merged
                         into the matched real speaker.

    Returns:
        A new Annotation with phantom segments reassigned or merged.
    """
    stats = _get_speaker_stats(diarization)
    num_speakers = len(stats)

    if num_speakers <= 1:
        return diarization

    # --- Stage 1: Heuristic candidate detection ---
    established = set()
    candidates = set()

    for label, info in stats.items():
        if info["duration"] <= max_duration_s and info["count"] <= max_segments:
            candidates.add(label)
        else:
            established.add(label)

    # If every speaker would be a candidate, none are phantoms -- they are
    # all equally brief (e.g. a short recording with two speakers).
    if not established:
        logger.info("[PhantomFilter] No established speakers detected; skipping filter.")
        return diarization

    if not candidates:
        logger.info("[PhantomFilter] No phantom candidates detected.")
        return diarization

    logger.info(
        f"[PhantomFilter] Stage 1: {len(candidates)} phantom candidate(s) "
        f"detected out of {num_speakers} speakers: "
        + ", ".join(
            f"{lbl} ({stats[lbl]['duration']:.2f}s, {stats[lbl]['count']} seg)"
            for lbl in sorted(candidates)
        )
    )

    # --- Stage 2: Embedding validation ---
    # Lazy-import heavy dependencies inside the function (worker convention).
    from backend.processing.embedding_core import (
        load_embedding_model,
        _embedding_model_cache,
        DEFAULT_EMBEDDING_MODEL,
    )
    from backend.processing.embedding import cosine_similarity
    from backend.utils.config_manager import config_manager

    get_config = config.get if config else config_manager.get
    device_str = get_config("processing_device", "auto")
    if device_str == "auto":
        import torch
        device_str = "cuda" if torch.cuda.is_available() else "cpu"

    hf_token = get_config("hf_token")

    # Load / retrieve cached embedding model
    cache_key = (DEFAULT_EMBEDDING_MODEL, device_str)
    if cache_key not in _embedding_model_cache:
        _embedding_model_cache[cache_key] = load_embedding_model(device_str, hf_token)
    model = _embedding_model_cache[cache_key]

    # Extract a single embedding per established speaker (longest segment)
    established_embeddings: Dict[str, np.ndarray] = {}
    for label in established:
        longest_seg = max(stats[label]["segments"], key=lambda s: s.duration)
        try:
            emb = model.crop(audio_path, longest_seg)
            if hasattr(emb, "data"):
                emb = emb.data
            emb = np.array(emb)
            if len(emb.shape) == 2:
                emb = np.mean(emb, axis=0)
            established_embeddings[label] = emb
        except Exception as e:
            logger.warning(f"[PhantomFilter] Could not extract embedding for established speaker {label}: {e}")

    if not established_embeddings:
        logger.warning("[PhantomFilter] No established speaker embeddings extracted; skipping filter.")
        return diarization

    # Evaluate each candidate
    # Maps candidate label -> target label (reassign/merge destination)
    reassignment_map: Dict[str, str] = {}

    for label in candidates:
        # Extract embedding from the candidate's longest segment
        longest_seg = max(stats[label]["segments"], key=lambda s: s.duration)
        try:
            emb = model.crop(audio_path, longest_seg)
            if hasattr(emb, "data"):
                emb = emb.data
            emb = np.array(emb)
            if len(emb.shape) == 2:
                emb = np.mean(emb, axis=0)
        except Exception as e:
            logger.warning(f"[PhantomFilter] Could not extract embedding for candidate {label}: {e}")
            continue

        candidate_emb_list = emb.tolist()

        # Compare against every established speaker
        best_match_label = None
        best_score = 0.0
        for est_label, est_emb in established_embeddings.items():
            score = cosine_similarity(candidate_emb_list, est_emb.tolist())
            if score > best_score:
                best_score = score
                best_match_label = est_label

        if best_score < embedding_floor:
            # Confirmed non-speech: reassign to nearest temporal speaker
            nearest = _find_nearest_speaker(longest_seg, diarization, candidates)
            target = nearest if nearest else best_match_label
            reassignment_map[label] = target
            logger.info(
                f"[PhantomFilter] {label} confirmed as non-speech "
                f"(best similarity={best_score:.3f} < floor={embedding_floor}). "
                f"Reassigning to {target}."
            )
        elif best_score >= merge_threshold:
            # Human-like but close to an established speaker: merge
            reassignment_map[label] = best_match_label
            logger.info(
                f"[PhantomFilter] {label} merging into {best_match_label} "
                f"(similarity={best_score:.3f} >= merge={merge_threshold})."
            )
        else:
            # Ambiguous zone: human-like embedding but not clearly matching any
            # established speaker. Likely a genuine brief speaker -- leave as-is.
            logger.info(
                f"[PhantomFilter] {label} retained as legitimate speaker "
                f"(similarity={best_score:.3f}, between floor={embedding_floor} "
                f"and merge={merge_threshold})."
            )

    if not reassignment_map:
        logger.info("[PhantomFilter] No phantoms confirmed after embedding validation.")
        return diarization

    # --- Build filtered annotation ---
    filtered = Annotation(uri=diarization.uri)
    reassigned_count = 0

    for segment, track, label in diarization.itertracks(yield_label=True):
        if label in reassignment_map:
            new_label = reassignment_map[label]
            filtered[segment, track] = new_label
            reassigned_count += 1
        else:
            filtered[segment, track] = label

    logger.info(
        f"[PhantomFilter] Complete: reassigned {reassigned_count} segment(s) "
        f"from {len(reassignment_map)} phantom speaker(s)."
    )
    return filtered
