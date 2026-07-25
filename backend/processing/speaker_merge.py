"""Post-diarization embedding-based speaker merge pass.

Detects over-clustered speakers by computing pairwise cosine similarity
between all active RecordingSpeaker embeddings within a recording. Speakers
above the merge threshold are consolidated using Union-Find.

Every run emits a ``speaker_merge_pass`` pipeline metric -- including runs that
merge nothing and runs that cannot score anything at all. A pass that merged
nothing used to be indistinguishable from one that never ran, which made
under-merging impossible to diagnose from a worker log.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import select

from backend.models.speaker import RecordingSpeaker
from backend.processing.embedding import (
    DUPLICATE_SPEAKER_MERGE_THRESHOLD,
    cosine_similarity,
    embedding_version_of,
)
from backend.processing.pipeline_metrics import record_pipeline_metric

logger = logging.getLogger(__name__)

# Reasons the pass produced no comparison at all. Recorded on the metric so an
# empty result is self-explaining.
REASON_NO_ELIGIBLE_SPEAKERS = "fewer_than_two_speakers_with_embeddings"


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


def _merge_is_blocked(a: RecordingSpeaker, b: RecordingSpeaker) -> str | None:
    """Reject pairs the embedding score must not be allowed to overrule.

    Two speakers already resolved to different people are different people --
    whatever their voiceprints score. Identification and manual naming are
    stronger evidence than an acoustic similarity above a fixed threshold, and a
    wrong merge is far harder for a user to undo than a wrong split.
    """
    if (
        a.global_speaker_id is not None
        and b.global_speaker_id is not None
        and a.global_speaker_id != b.global_speaker_id
    ):
        return "distinct_global_speakers"

    a_local = (a.local_name or "").strip().casefold()
    b_local = (b.local_name or "").strip().casefold()
    if a_local and b_local and a_local != b_local:
        return "distinct_manual_names"

    return None


def _survivor_sort_key(
    speaker: RecordingSpeaker,
    utterance_counts: dict[int, int],
    speech_seconds: dict[str, float],
) -> tuple:
    """Rank speakers so the best-evidenced row survives a merge group.

    Identification comes first so a merge can never discard a resolved person in
    favour of an anonymous fragment. Speech duration comes before utterance
    count because the utterance table is not yet populated when this pass runs
    on an imported recording, which used to leave the choice effectively
    arbitrary.
    """
    return (
        1 if speaker.global_speaker_id is not None else 0,
        1 if (speaker.local_name or "").strip() else 0,
        float(speech_seconds.get(speaker.diarization_label, 0.0)),
        utterance_counts.get(int(speaker.id), 0),
        len(speaker.embedding or []),
        -int(speaker.id),  # deterministic final tiebreak
    )


def _score_and_union_pairs(
    eligible: list[RecordingSpeaker],
    threshold: float,
    parent: dict[int, int],
    rank: dict[int, int],
) -> list[dict[str, Any]]:
    """Score every pair, union those that qualify, and report all of them.

    Every pair is recorded, merged or not. The near-miss scores are the whole
    point: they distinguish "these are the same voice and we missed it" from
    "these really are different voices".
    """
    scored_pairs: list[dict[str, Any]] = []

    for i, speaker_a in enumerate(eligible):
        for speaker_b in eligible[i + 1 :]:
            if speaker_a.id is None or speaker_b.id is None:
                continue
            score = cosine_similarity(speaker_a.embedding, speaker_b.embedding)
            blocked = _merge_is_blocked(speaker_a, speaker_b)
            merged = score >= threshold and blocked is None

            scored_pairs.append(
                {
                    "a_label": speaker_a.diarization_label,
                    "b_label": speaker_b.diarization_label,
                    "a_id": int(speaker_a.id),
                    "b_id": int(speaker_b.id),
                    "cosine": round(float(score), 4),
                    "merged": merged,
                    "blocked_by": blocked,
                }
            )

            if blocked is not None:
                if score >= threshold:
                    logger.info(
                        "[SpeakerMerge] %s ~ %s scored %.3f but was not merged (%s).",
                        speaker_a.diarization_label,
                        speaker_b.diarization_label,
                        score,
                        blocked,
                    )
                continue

            if merged:
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

    return scored_pairs


def merge_duplicate_speakers(
    session,
    *,
    recording_id: int,
    threshold: float = DUPLICATE_SPEAKER_MERGE_THRESHOLD,
    segments: list[dict[str, Any]] | None = None,
    speech_seconds: dict[str, float] | None = None,
) -> list[tuple[int, int]]:
    """Merge RecordingSpeaker rows with high embedding similarity.

    Args:
        session: Database session.
        recording_id: The recording to process.
        threshold: Cosine similarity threshold for merging.
        segments: Optional mutable list of transcript segment dicts. When
            provided, segment speaker labels are rewritten in-place to point
            to the surviving speaker's diarization label.
        speech_seconds: Optional map of diarization label to total speech
            seconds, used to pick the survivor of each merge group.

    Returns:
        A list of (merged_speaker_id, survivor_speaker_id) pairs.
    """
    speech_seconds = speech_seconds or {}

    speakers = list(
        session.execute(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording_id)
            .where(RecordingSpeaker.merged_into_id.is_(None))
        )
        .scalars()
        .all()
    )

    with_embedding = [s for s in speakers if s.embedding and s.id is not None]

    # Only embeddings produced by the same extraction method can be compared.
    # Mixing versions would score unrelated regions of the vector space against
    # each other and produce merges that mean nothing.
    versions: dict[int, list[RecordingSpeaker]] = {}
    for speaker in with_embedding:
        versions.setdefault(embedding_version_of(speaker), []).append(speaker)
    eligible = max(versions.values(), key=len) if versions else []
    skipped_version_mismatch = len(with_embedding) - len(eligible)

    def _emit(payload: dict[str, Any]) -> None:
        record_pipeline_metric(
            stage="speaker_merge_pass",
            recording_id=recording_id,
            payload=payload,
            log=logger,
        )

    base_payload: dict[str, Any] = {
        "threshold": round(float(threshold), 4),
        "total_speaker_count": len(speakers),
        "eligible_speaker_count": len(eligible),
        "skipped_no_embedding": len(speakers) - len(with_embedding),
        "skipped_version_mismatch": skipped_version_mismatch,
        "embedding_versions_present": sorted(versions),
    }

    if len(eligible) < 2:
        # The case that previously produced no log line at all.
        _emit({**base_payload, "reason": REASON_NO_ELIGIBLE_SPEAKERS, "pairs": []})
        return []

    speaker_ids = {int(s.id) for s in eligible}
    utterance_counts = _count_utterances_per_speaker(session, recording_id, speaker_ids)

    parent: dict[int, int] = {int(s.id): int(s.id) for s in eligible}
    rank: dict[int, int] = {int(s.id): 0 for s in eligible}

    scored_pairs = _score_and_union_pairs(eligible, threshold, parent, rank)

    groups: dict[int, list[RecordingSpeaker]] = {}
    for speaker in eligible:
        root = _find(parent, int(speaker.id))
        groups.setdefault(root, []).append(speaker)

    merge_pairs: list[tuple[int, int]] = []
    label_remap: dict[str, str] = {}

    for _root, group in groups.items():
        if len(group) < 2:
            continue

        group.sort(
            key=lambda s: _survivor_sort_key(s, utterance_counts, speech_seconds),
            reverse=True,
        )

        survivor = group[0]
        for merged_speaker in group[1:]:
            merged_speaker.merged_into_id = survivor.id
            session.add(merged_speaker)
            merge_pairs.append((int(merged_speaker.id), int(survivor.id)))
            label_remap[merged_speaker.diarization_label] = survivor.diarization_label
            logger.info(
                "[SpeakerMerge] Merged %s (id=%d) -> %s (id=%d)",
                merged_speaker.diarization_label,
                merged_speaker.id,
                survivor.diarization_label,
                survivor.id,
            )

    _emit(
        {
            **base_payload,
            "reason": None,
            "pairs": scored_pairs,
            "pair_count": len(scored_pairs),
            "merged_pair_count": len(merge_pairs),
            "surviving_speaker_count": len(eligible) - len(merge_pairs),
            "max_cosine": max((p["cosine"] for p in scored_pairs), default=None),
            "speech_seconds": {
                s.diarization_label: round(
                    float(speech_seconds.get(s.diarization_label, 0.0)), 2
                )
                for s in eligible
            },
        }
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
