"""Post-diarization embedding-based speaker merge pass.

Detects over-clustered speakers by computing pairwise cosine similarity
between all active RecordingSpeaker embeddings within a recording. Clusters
that score at or above the merge threshold are consolidated using Union-Find.

Linkage is complete: a cluster joins a group only when it clears the threshold
against every member of that group. Under single linkage a small cluster of
short backchannel lines, whose blended voiceprint scores moderately against two
different people, bridged those two people into one speaker although they
scored far below the threshold against each other. The direct score between two
clusters is the evidence that they are the same voice, and no chain of third
parties is allowed to overrule it.

Every run emits a ``speaker_merge_pass`` pipeline metric -- including runs that
merge nothing and runs that cannot score anything at all. A pass that merged
nothing used to be indistinguishable from one that never ran, which made
under-merging impossible to diagnose from a worker log.
"""

from __future__ import annotations

import logging
from itertools import combinations
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

# Recorded on a pair that cleared the threshold but whose union would have put
# two clusters scoring below it into one group.
BLOCKED_CROSS_PAIR_BELOW_THRESHOLD = "cross_pair_below_threshold"


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


def _pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


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


def _local_name_key(speaker: RecordingSpeaker) -> str:
    return (speaker.local_name or "").strip().casefold()


def _union_is_blocked(
    globals_a: set[int],
    names_a: set[str],
    globals_b: set[int],
    names_b: set[str],
) -> str | None:
    """Reject a link whose two *components* carry conflicting identities.

    ``_merge_is_blocked`` only ever sees the pair in front of it, so it cannot
    stop an anonymous speaker acting as a bridge: X scoring above the threshold
    against both A and B drags two confirmed people into one component even
    though the direct A~B link was refused. The guard has to run against the
    identities of the whole components a union would join, not the endpoints.
    """
    if len(globals_a | globals_b) > 1:
        return "distinct_global_speakers_in_component"
    if len(names_a | names_b) > 1:
        return "distinct_manual_names_in_component"
    return None


def _weakest_cross_pair(
    members_a: set[int],
    members_b: set[int],
    scores: dict[tuple[int, int], float],
) -> tuple[float, int, int]:
    """The lowest-scoring pair across two components, with its speaker ids."""
    weakest: tuple[float, int, int] | None = None
    for a in members_a:
        for b in members_b:
            score = scores[_pair_key(a, b)]
            if weakest is None or score < weakest[0]:
                weakest = (score, a, b)
    assert weakest is not None  # both components hold at least one speaker
    return weakest


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


_Candidate = tuple[float, dict[str, Any], RecordingSpeaker, RecordingSpeaker]


def _score_pairs(
    eligible: list[RecordingSpeaker],
    threshold: float,
) -> tuple[list[dict[str, Any]], dict[tuple[int, int], float], list[_Candidate]]:
    """Score every pair and report all of them.

    Every pair is recorded, merged or not. The near-miss scores are the whole
    point: they distinguish "these are the same voice and we missed it" from
    "these really are different voices". Returns the scored pairs, every score
    keyed by id pair, and the pairs that qualify for linking.
    """
    scored_pairs: list[dict[str, Any]] = []
    scores: dict[tuple[int, int], float] = {}
    candidates: list[_Candidate] = []

    for i, speaker_a in enumerate(eligible):
        for speaker_b in eligible[i + 1 :]:
            if speaker_a.id is None or speaker_b.id is None:
                continue
            score = float(cosine_similarity(speaker_a.embedding, speaker_b.embedding))
            scores[_pair_key(int(speaker_a.id), int(speaker_b.id))] = score
            blocked = _merge_is_blocked(speaker_a, speaker_b)

            pair: dict[str, Any] = {
                "a_label": speaker_a.diarization_label,
                "b_label": speaker_b.diarization_label,
                "a_id": int(speaker_a.id),
                "b_id": int(speaker_b.id),
                "cosine": round(score, 4),
                "merged": False,
                "blocked_by": blocked,
            }
            scored_pairs.append(pair)

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

            if score >= threshold:
                candidates.append((score, pair, speaker_a, speaker_b))

    return scored_pairs, scores, candidates


def _score_and_union_pairs(
    eligible: list[RecordingSpeaker],
    threshold: float,
    parent: dict[int, int],
    rank: dict[int, int],
) -> tuple[list[dict[str, Any]], dict[tuple[int, int], float]]:
    """Score every pair, union those that qualify, and report all of them.

    Linking is a second pass over the qualifying pairs in descending score
    order. Union-find is order-sensitive once a link can be refused: taking the
    pairs in whatever order the nested loop happened to produce would let an
    arbitrary weaker link claim a component and push a stronger one out. The
    strongest evidence should win, so it is applied first.

    A link is refused when any pair across the two components it would join
    scores below the threshold (complete linkage), so two clusters only ever
    share a group when they score as the same voice against each other.
    Returns the scored pairs and the score of every pair, keyed by id pair.
    """
    scored_pairs, scores, candidates = _score_pairs(eligible, threshold)
    labels = {int(speaker.id): speaker.diarization_label for speaker in eligible}

    # The identities and members each component currently carries. Read
    # through _find, so only the live root's entry is ever consulted.
    component_globals: dict[int, set[int]] = {}
    component_names: dict[int, set[str]] = {}
    members: dict[int, set[int]] = {}
    for speaker in eligible:
        speaker_id = int(speaker.id)
        component_globals[speaker_id] = (
            {int(speaker.global_speaker_id)}
            if speaker.global_speaker_id is not None
            else set()
        )
        name_key = _local_name_key(speaker)
        component_names[speaker_id] = {name_key} if name_key else set()
        members[speaker_id] = {speaker_id}

    # Descending score, with the id pair as a deterministic tiebreak so two
    # equally-scoring links never resolve on dict or list ordering.
    candidates.sort(key=lambda c: (-c[0], c[1]["a_id"], c[1]["b_id"]))

    for score, pair, speaker_a, speaker_b in candidates:
        root_a = _find(parent, int(speaker_a.id))
        root_b = _find(parent, int(speaker_b.id))

        if root_a == root_b:
            # Already the same component by transitivity; nothing to check.
            pair["merged"] = True
            continue

        blocked = _union_is_blocked(
            component_globals[root_a],
            component_names[root_a],
            component_globals[root_b],
            component_names[root_b],
        )
        if blocked is not None:
            pair["blocked_by"] = blocked
            logger.info(
                "[SpeakerMerge] %s ~ %s scored %.3f but was not merged (%s).",
                speaker_a.diarization_label,
                speaker_b.diarization_label,
                score,
                blocked,
            )
            continue

        weakest, weak_a, weak_b = _weakest_cross_pair(
            members[root_a], members[root_b], scores
        )
        if weakest < threshold:
            pair["blocked_by"] = BLOCKED_CROSS_PAIR_BELOW_THRESHOLD
            pair["weakest_cross_cosine"] = round(weakest, 4)
            logger.info(
                "[SpeakerMerge] %s ~ %s scored %.3f but was not merged: "
                "%s ~ %s scores %.3f, below %.3f.",
                speaker_a.diarization_label,
                speaker_b.diarization_label,
                score,
                labels[weak_a],
                labels[weak_b],
                weakest,
                threshold,
            )
            continue

        logger.info(
            "[SpeakerMerge] %s (id=%d) ~ %s (id=%d): cosine=%.3f >= %.3f",
            speaker_a.diarization_label,
            speaker_a.id,
            speaker_b.diarization_label,
            speaker_b.id,
            score,
            threshold,
        )
        merged_globals = component_globals[root_a] | component_globals[root_b]
        merged_names = component_names[root_a] | component_names[root_b]
        merged_members = members[root_a] | members[root_b]
        new_root = _union(parent, rank, int(speaker_a.id), int(speaker_b.id))
        component_globals[new_root] = merged_globals
        component_names[new_root] = merged_names
        members[new_root] = merged_members
        pair["merged"] = True

    return scored_pairs, scores


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
        "linkage": "complete",
        "total_speaker_count": len(speakers),
        "eligible_speaker_count": len(eligible),
        "skipped_no_embedding": len(speakers) - len(with_embedding),
        "skipped_version_mismatch": skipped_version_mismatch,
        "embedding_versions_present": sorted(versions),
    }

    if len(eligible) < 2:
        # Logged even when nothing is comparable, so an empty pass is visible.
        _emit(
            {
                **base_payload,
                "reason": REASON_NO_ELIGIBLE_SPEAKERS,
                "pairs": [],
                "groups": [],
            }
        )
        return []

    speaker_ids = {int(s.id) for s in eligible}
    utterance_counts = _count_utterances_per_speaker(session, recording_id, speaker_ids)

    parent: dict[int, int] = {int(s.id): int(s.id) for s in eligible}
    rank: dict[int, int] = {int(s.id): 0 for s in eligible}

    scored_pairs, scores = _score_and_union_pairs(eligible, threshold, parent, rank)

    groups: dict[int, list[RecordingSpeaker]] = {}
    for speaker in eligible:
        root = _find(parent, int(speaker.id))
        groups.setdefault(root, []).append(speaker)

    merge_pairs: list[tuple[int, int]] = []
    label_remap: dict[str, str] = {}
    # One entry per merged group: who survived, who was absorbed, and the
    # weakest and mean score inside the group, so a log reader can judge the
    # group without recomputing it from the pairs.
    group_payloads: list[dict[str, Any]] = []

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

        group_scores = [
            scores[_pair_key(int(a.id), int(b.id))] for a, b in combinations(group, 2)
        ]
        group_payloads.append(
            {
                "survivor_id": int(survivor.id),
                "survivor_label": survivor.diarization_label,
                "merged_ids": [int(s.id) for s in group[1:]],
                "merged_labels": [s.diarization_label for s in group[1:]],
                "min_cosine": round(min(group_scores), 4),
                "mean_cosine": round(sum(group_scores) / len(group_scores), 4),
            }
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
            "groups": group_payloads,
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
