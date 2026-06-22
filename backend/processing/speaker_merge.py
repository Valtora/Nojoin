"""Post-diarization embedding-based speaker merge pass.

Detects over-clustered speakers by computing pairwise cosine similarity
between all active RecordingSpeaker embeddings within a recording. Speakers
above the merge threshold are consolidated using Union-Find, with the
highest-utterance-count speaker surviving each merge group.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import select

from backend.models.speaker import RecordingSpeaker
from backend.processing.embedding import (
    DUPLICATE_SPEAKER_MERGE_THRESHOLD,
    cosine_similarity,
)

logger = logging.getLogger(__name__)


def _find(parent: dict[int, int], speaker_id: int) -> int:
    while parent[speaker_id] != speaker_id:
        parent[speaker_id] = parent[parent[speaker_id]]
        speaker_id = parent[speaker_id]
    return speaker_id


def _union(parent: dict[int, int], rank: dict[int, int], a: int, b: int) -> int:
    root_a = _find(parent, a)
    root_b = _find(parent, b)
    if root_a == root_b:
        return root_a
    if rank[root_a] < rank[root_b]:
        root_a, root_b = root_b, root_a
    parent[root_b] = root_a
    if rank[root_a] == rank[root_b]:
        rank[root_a] += 1
    return root_a


def _count_utterances_per_speaker(
    session,
    recording_id: int,
    speaker_ids: set[int],
) -> dict[int, int]:
    from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState

    active_states = {
        TranscriptUtteranceState.PROVISIONAL.value,
        TranscriptUtteranceState.STABLE.value,
        TranscriptUtteranceState.FINALIZED.value,
    }
    rows = session.execute(
        select(
            TranscriptUtterance.recording_speaker_id,
        )
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.state.in_(active_states))
        .where(TranscriptUtterance.recording_speaker_id.in_(speaker_ids))
    ).all()

    counts: dict[int, int] = {}
    for (speaker_id,) in rows:
        if speaker_id is not None:
            counts[int(speaker_id)] = counts.get(int(speaker_id), 0) + 1
    return counts


def merge_duplicate_speakers(
    session,
    *,
    recording_id: int,
    threshold: float = DUPLICATE_SPEAKER_MERGE_THRESHOLD,
    segments: list[dict[str, Any]] | None = None,
) -> list[tuple[int, int]]:
    """Merge RecordingSpeaker rows with high embedding similarity.

    Args:
        session: Database session.
        recording_id: The recording to process.
        threshold: Cosine similarity threshold for merging.
        segments: Optional mutable list of transcript segment dicts. When
            provided, segment speaker labels are rewritten in-place to point
            to the surviving speaker's diarization label.

    Returns:
        A list of (merged_speaker_id, survivor_speaker_id) pairs.
    """
    speakers = list(
        session.execute(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording_id)
            .where(RecordingSpeaker.merged_into_id.is_(None))
        )
        .scalars()
        .all()
    )

    eligible = [s for s in speakers if s.embedding and s.id is not None]
    if len(eligible) < 2:
        return []

    speaker_ids = {int(s.id) for s in eligible}
    utterance_counts = _count_utterances_per_speaker(session, recording_id, speaker_ids)

    parent: dict[int, int] = {int(s.id): int(s.id) for s in eligible}
    rank: dict[int, int] = {int(s.id): 0 for s in eligible}

    for i, speaker_a in enumerate(eligible):
        for speaker_b in eligible[i + 1 :]:
            if speaker_a.id is None or speaker_b.id is None:
                continue
            score = cosine_similarity(speaker_a.embedding, speaker_b.embedding)
            if score >= threshold:
                logger.info(
                    "[SpeakerMerge] %s (id=%d) ~ %s (id=%d): cosine=%.3f >= %.3f",
                    speaker_a.diarization_label,
                    speaker_a.id,
                    speaker_b.diarization_label,
                    speaker_b.id,
                    score,
                    threshold,
                )
                _union(parent, rank, int(speaker_a.id), int(speaker_b.id))

    groups: dict[int, list[RecordingSpeaker]] = {}
    for speaker in eligible:
        root = _find(parent, int(speaker.id))
        groups.setdefault(root, []).append(speaker)

    merge_pairs: list[tuple[int, int]] = []
    label_remap: dict[str, str] = {}

    for root, group in groups.items():
        if len(group) < 2:
            continue

        group.sort(
            key=lambda s: (
                utterance_counts.get(int(s.id), 0),
                len(s.embedding or []),
            ),
            reverse=True,
        )

        survivor = group[0]
        for merged in group[1:]:
            merged.merged_into_id = survivor.id
            session.add(merged)
            merge_pairs.append((int(merged.id), int(survivor.id)))
            label_remap[merged.diarization_label] = survivor.diarization_label
            logger.info(
                "[SpeakerMerge] Merged %s (id=%d) -> %s (id=%d)",
                merged.diarization_label,
                merged.id,
                survivor.diarization_label,
                survivor.id,
            )

    if not merge_pairs:
        return []

    session.flush()

    if segments:
        for seg in segments:
            current_speaker = seg.get("speaker")
            if current_speaker in label_remap:
                seg["speaker"] = label_remap[current_speaker]
            overlapping = seg.get("overlapping_speakers")
            if isinstance(overlapping, list):
                seg["overlapping_speakers"] = [
                    label_remap.get(label, label) for label in overlapping
                ]

    return merge_pairs
