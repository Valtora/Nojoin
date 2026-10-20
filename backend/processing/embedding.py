import logging
import re
from typing import Any, List, Optional

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
# Duplicate merge pass: minimum cosine between a cluster and an identified
# person's library voiceprint before the cluster may join that person's group.
# The same floor as DRIFT_GUARD_THRESHOLD, which decides whether an observation
# is the same person when blending it into a library voiceprint. On this
# project's own library no two people's recording voiceprints scored above
# 0.55, while the same person's sat at a median of 0.87 with 5% below 0.73.
IDENTIFIED_MERGE_VOICEPRINT_FLOOR = 0.60
# Default threshold for the scan-matches endpoint
SCAN_MATCH_THRESHOLD = 0.75


def embedding_version_of(obj) -> int:
    """Read the extraction method version off a speaker row.

    Rows written before versioning existed carry ``NULL`` and are legacy by
    definition, so the absent value maps to version 1 rather than to the
    current version.
    """
    return _method_version(getattr(obj, "embedding_version", None))


def _method_version(value) -> int:
    """A stored ``embedding_version`` value as a method version."""
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

    merged = (1 - alpha) * curr_arr + alpha * new_arr

    return merged.tolist()


def blend_voiceprint(
    row: Any,
    embedding: List[float],
    version: Optional[int],
    *,
    alpha: float,
) -> None:
    """Write a voiceprint into a speaker row and keep its version true.

    ``row`` is a ``GlobalSpeaker`` or a ``RecordingSpeaker``, and ``version`` is
    the extraction method version ``embedding`` was produced with (``None`` for
    a voiceprint that predates versioning). Every path that writes a voiceprint
    it did not extract itself goes through here, because identification only
    compares voiceprints of one version: a row whose version is wrong is
    ignored, or compared with vectors it can't be compared with.

    A row without a voiceprint takes a copy of ``embedding`` and its version.
    A row of the same version has ``embedding`` folded in at ``alpha`` by
    ``merge_embeddings``, drift guard included. Two versions are never blended,
    since the average of two unrelated regions of the space matches neither: a
    current-version ``embedding`` replaces a stale row voiceprint, and a stale
    ``embedding`` leaves a current row as it is.
    """
    if not row.embedding:
        row.embedding = list(embedding)
        row.embedding_version = version
        return
    incoming = _method_version(version)
    if embedding_version_of(row) == incoming:
        row.embedding = merge_embeddings(row.embedding, embedding, alpha=alpha)
        return
    if incoming == EMBEDDING_METHOD_VERSION:
        row.embedding = list(embedding)
        row.embedding_version = version


_PLACEHOLDER_NAME_PATTERN = re.compile(
    r"^(SPEAKER_\d+|Speaker \d+|Unknown|New Voice .*)$", re.IGNORECASE
)


def _rank_global_speakers(
    embedding: List[float],
    global_speakers: List,
    method_version: Optional[int],
) -> tuple[List[tuple[Any, float]], int]:
    """The ranking, plus how many stale voiceprints it had to leave out."""
    if method_version is None:
        method_version = EMBEDDING_METHOD_VERSION

    ranked: List[tuple[Any, float]] = []
    skipped_stale = 0
    for gs in global_speakers:
        if not gs.embedding or _PLACEHOLDER_NAME_PATTERN.match(gs.name):
            continue
        if embedding_version_of(gs) != method_version:
            skipped_stale += 1
            continue
        ranked.append((gs, cosine_similarity(embedding, gs.embedding)))

    # A stable sort keeps the first of two equal scores first, as before.
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked, skipped_stale


def rank_global_speakers(
    embedding: List[float],
    global_speakers: List,
    method_version: Optional[int] = None,
) -> List[tuple[Any, float]]:
    """Score ``embedding`` against every comparable saved person, best first.

    People with a placeholder name or no voiceprint are left out, and so are
    voiceprints from another extraction method, because a cross-version cosine
    is not a similarity. ``find_matching_global_speaker`` decides on this
    ranking; the pipeline records it, so a miss can say who came closest.
    """
    ranked, _skipped_stale = _rank_global_speakers(
        embedding, global_speakers, method_version
    )
    return ranked


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
    ranking, skipped_stale = _rank_global_speakers(
        embedding, global_speakers, method_version
    )
    if skipped_stale:
        logger.info(
            "Skipped %d global speaker(s) whose voiceprint predates extraction "
            "method v%d. Re-extract voiceprints to make them matchable again.",
            skipped_stale,
            method_version or EMBEDDING_METHOD_VERSION,
        )
    if not ranking:
        return None, 0.0

    best_match, best_score = ranking[0]
    # Scores at or below zero never counted as a rival, so neither do they here.
    second_best_score = max(ranking[1][1], 0.0) if len(ranking) > 1 else 0.0

    if best_score >= threshold:
        # Check for ambiguity using the margin of victory
        if (best_score - second_best_score) >= margin:
            return best_match, best_score
        # It's an ambiguous match, better to return nothing than a false positive
        return None, 0.0

    return None, max(best_score, 0.0)
