import logging
from typing import List, Optional

import numpy as np

from backend.processing.embedding_version import (
    EMBEDDING_METHOD_VERSION,
    LEGACY_EMBEDDING_METHOD_VERSION,
)

logger = logging.getLogger(__name__)

# --- Speaker Identification Thresholds ---
# Minimum cosine similarity to consider an automatic speaker match during processing
IDENTIFICATION_THRESHOLD = 0.75
# Minimum cosine similarity required before auto-updating a global speaker's embedding
AUTO_UPDATE_THRESHOLD = 0.85
# Minimum margin between best and second-best match to avoid ambiguous assignments
MARGIN_OF_VICTORY = 0.05
# Minimum similarity between current and incoming embedding to permit a merge
DRIFT_GUARD_THRESHOLD = 0.6
# UI: minimum similarity to display a potential match to the user
UI_SHOW_MATCH_THRESHOLD = 0.50
# UI: minimum similarity (with margin check) to flag a match as "strong"
UI_STRONG_MATCH_THRESHOLD = 0.75
# Embedding-based speaker deduplication: cosine similarity above which two
# RecordingSpeaker rows within the same recording are merged automatically.
DUPLICATE_SPEAKER_MERGE_THRESHOLD = 0.70
# Default threshold for the scan-matches endpoint
SCAN_MATCH_THRESHOLD = 0.75


def embedding_version_of(obj) -> int:
    """Read the extraction method version off a speaker row.

    Rows written before versioning existed carry ``NULL`` and are legacy by
    definition, so the absent value maps to version 1 rather than to the
    current version.
    """
    value = getattr(obj, "embedding_version", None)
    if value is None:
        return LEGACY_EMBEDDING_METHOD_VERSION
    try:
        return int(value)
    except (TypeError, ValueError):
        return LEGACY_EMBEDDING_METHOD_VERSION


def embeddings_are_comparable(a, b) -> bool:
    """True when two speaker rows' voiceprints may be scored against each other.

    Cosine similarity between embeddings produced by different extraction
    methods is not meaningful -- the vectors occupy different regions of the
    space -- so a cross-version score must never be used to merge speakers or
    identify a person. Stale voiceprints are repaired by re-extraction, not by
    comparing them anyway.
    """
    return embedding_version_of(a) == embedding_version_of(b)


def cosine_similarity(v1: Optional[List[float]], v2: Optional[List[float]]) -> float:
    """Compute cosine similarity between two vectors."""
    if v1 is None or v2 is None:
        return 0.0

    # Check for None values inside the lists which can cause numpy errors
    if any(x is None for x in v1) or any(x is None for x in v2):
        return 0.0

    try:
        a = np.array(v1, dtype=float)
        b = np.array(v2, dtype=float)
    except (ValueError, TypeError):
        return 0.0

    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def merge_embeddings(
    current_embedding: List[float],
    new_embedding: List[float],
    alpha: float = 0.1,
    drift_guard: bool = True,
) -> List[float]:
    """
    Merges a new embedding into an existing one using a weighted moving average.

    Args:
        current_embedding: The existing embedding vector.
        new_embedding: The new embedding vector to merge.
        alpha: The weight of the new embedding (0.0 to 1.0).
               Higher alpha means the new embedding has more influence.
        drift_guard: If True, reject the merge when the new embedding is too
                     dissimilar to the current one (below DRIFT_GUARD_THRESHOLD).

    Returns:
        The merged embedding vector, or the original if drift guard rejects the merge.
    """
    if not current_embedding:
        return new_embedding

    # Reject merges that would corrupt the embedding with unrelated voice data
    if drift_guard:
        similarity = cosine_similarity(current_embedding, new_embedding)
        if similarity < DRIFT_GUARD_THRESHOLD:
            logger.warning(
                f"Drift guard rejected embedding merge (similarity={similarity:.3f}, "
                f"threshold={DRIFT_GUARD_THRESHOLD}). Returning original embedding."
            )
            return current_embedding

    curr_arr = np.array(current_embedding)
    new_arr = np.array(new_embedding)

    # Weighted average
    merged = (1 - alpha) * curr_arr + alpha * new_arr

    return merged.tolist()


def find_matching_global_speaker(
    embedding: List[float],
    global_speakers: List,
    threshold: float = IDENTIFICATION_THRESHOLD,
    margin: float = MARGIN_OF_VICTORY,
    method_version: Optional[int] = None,
):
    """
    Find the best matching GlobalSpeaker for a given embedding.

    Args:
        embedding: The embedding vector to match.
        global_speakers: List of GlobalSpeaker objects with embeddings.
        threshold: Minimum similarity score to consider a match.
        margin: The minimum difference required between the best and second best match
                to avoid ambiguous assignments.
        method_version: Extraction method version of ``embedding``. Global
                speakers stored under a different version are skipped, because a
                cross-version cosine score is not a meaningful similarity.
                Defaults to the current extraction version.

    Returns:
        Tuple of (best_matching_speaker, similarity_score).
        Returns (None, 0.0) if no match above threshold or if match is ambiguous.
    """
    import re

    if method_version is None:
        method_version = EMBEDDING_METHOD_VERSION

    placeholder_pattern = re.compile(
        r"^(SPEAKER_\d+|Speaker \d+|Unknown|New Voice .*)$", re.IGNORECASE
    )

    best_match = None
    best_score = 0.0
    second_best_score = 0.0
    skipped_stale = 0

    for gs in global_speakers:
        # Skip placeholder names and speakers without embeddings
        if not gs.embedding or placeholder_pattern.match(gs.name):
            continue

        if embedding_version_of(gs) != method_version:
            skipped_stale += 1
            continue

        score = cosine_similarity(embedding, gs.embedding)

        if score > best_score:
            second_best_score = best_score
            best_score = score
            best_match = gs
        elif score > second_best_score:
            second_best_score = score

    if skipped_stale:
        logger.info(
            "Skipped %d global speaker(s) whose voiceprint predates extraction "
            "method v%d. Re-extract voiceprints to make them matchable again.",
            skipped_stale,
            method_version,
        )

    if best_match and best_score >= threshold:
        # Check for ambiguity using the margin of victory
        if (best_score - second_best_score) >= margin:
            return best_match, best_score
        else:
            # It's an ambiguous match, better to return nothing than a false positive
            return None, 0.0

    return None, best_score
