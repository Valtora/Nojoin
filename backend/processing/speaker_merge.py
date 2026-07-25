"""Post-diarization embedding-based speaker merge pass.

Detects over-clustered speakers by computing pairwise cosine similarity
between all active RecordingSpeaker embeddings within a recording. Speakers
above the merge threshold are consolidated using Union-Find, with the
highest-utterance-count speaker surviving each merge group.

Every run emits a ``speaker_merge_pass`` pipeline metric, including the runs
that merge nothing. A pass that scored every pair below the threshold and a
pass that never had embeddings to score are both legitimate outcomes, and
without the metric they are indistinguishable from each other -- and from a
pass that never ran -- which makes an over-clustering report undiagnosable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import select

from backend.models.speaker import RecordingSpeaker
from backend.processing.embedding import (
    DUPLICATE_SPEAKER_MERGE_THRESHOLD,
    cosine_similarity,
)
from backend.processing.pipeline_metrics import record_pipeline_metric

logger = logging.getLogger(__name__)

# Upper bound on pair scores carried in the metric payload. Pairs are recorded
# highest-score-first, so truncation only ever drops the least interesting
# ones -- a near-miss is by definition at the top of the list.
MAX_RECORDED_PAIR_SCORES = 25

# Nothing to merge: fewer than two active speakers in the recording.
SKIP_SINGLE_ACTIVE_SPEAKER = "single_active_speaker"
# Two or more active speakers, but fewer than two carry a voiceprint, so the
# pass cannot score anything. Anomalous whenever diarization found multiple
# speakers -- it means the merge safety net is silently inactive.
SKIP_INSUFFICIENT_EMBEDDINGS = "insufficient_embeddings"


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


@dataclass
class _MergePassOutcome:
    """What one run of the pass saw and did, for the metric payload."""

    threshold: float
    speaker_count: int
    eligible_count: int
    scored_pairs: list[dict[str, Any]] = field(default_factory=list)
    merge_pairs: list[tuple[int, int]] = field(default_factory=list)
    skip_reason: str | None = None


def _record_merge_metric(recording_id: int, outcome: _MergePassOutcome) -> None:
    """Emit the ``speaker_merge_pass`` metric for one run of the pass.

    Carries recording-speaker ids and diarization labels only. Resolved names
    can be real participants, and these lines are the ones users are asked to
    paste into public issue reports.
    """
    scored_pairs = outcome.scored_pairs
    ranked_pairs = sorted(scored_pairs, key=lambda pair: pair["score"], reverse=True)
    record_pipeline_metric(
        stage="speaker_merge_pass",
        recording_id=recording_id,
        payload={
            "threshold": round(float(outcome.threshold), 4),
            "speaker_count": outcome.speaker_count,
            "eligible_count": outcome.eligible_count,
            "pairs_considered": len(scored_pairs),
            "pairs_recorded": min(len(scored_pairs), MAX_RECORDED_PAIR_SCORES),
            "pairs_omitted": max(0, len(scored_pairs) - MAX_RECORDED_PAIR_SCORES),
            "pairs": ranked_pairs[:MAX_RECORDED_PAIR_SCORES],
            "merged_count": len(outcome.merge_pairs),
            "merged_pairs": [
                [merged_id, survivor_id]
                for merged_id, survivor_id in outcome.merge_pairs
            ],
            "skip_reason": outcome.skip_reason,
        },
        log=logger,
    )


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
        skip_reason = (
            SKIP_INSUFFICIENT_EMBEDDINGS
            if len(speakers) >= 2
            else SKIP_SINGLE_ACTIVE_SPEAKER
        )
        if skip_reason == SKIP_INSUFFICIENT_EMBEDDINGS:
            logger.warning(
                "[SpeakerMerge] Recording %d has %d active speakers but only %d "
                "voiceprint(s); the duplicate-speaker merge pass cannot run. "
                "Over-clustered speakers will not be collapsed.",
                recording_id,
                len(speakers),
                len(eligible),
            )
        _record_merge_metric(
            recording_id,
            _MergePassOutcome(
                threshold=threshold,
                speaker_count=len(speakers),
                eligible_count=len(eligible),
                skip_reason=skip_reason,
            ),
        )
        return []

    speaker_ids = {int(s.id) for s in eligible}
    utterance_counts = _count_utterances_per_speaker(session, recording_id, speaker_ids)

    parent: dict[int, int] = {int(s.id): int(s.id) for s in eligible}
    rank: dict[int, int] = {int(s.id): 0 for s in eligible}

    scored_pairs: list[dict[str, Any]] = []

    for i, speaker_a in enumerate(eligible):
        for speaker_b in eligible[i + 1 :]:
            if speaker_a.id is None or speaker_b.id is None:
                continue
            score = cosine_similarity(speaker_a.embedding, speaker_b.embedding)
            scored_pairs.append(
                {
                    "a_id": int(speaker_a.id),
                    "a_label": speaker_a.diarization_label,
                    "b_id": int(speaker_b.id),
                    "b_label": speaker_b.diarization_label,
                    "score": round(float(score), 4),
                    "above_threshold": bool(score >= threshold),
                }
            )
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

    _record_merge_metric(
        recording_id,
        _MergePassOutcome(
            threshold=threshold,
            speaker_count=len(speakers),
            eligible_count=len(eligible),
            scored_pairs=scored_pairs,
            merge_pairs=merge_pairs,
        ),
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
