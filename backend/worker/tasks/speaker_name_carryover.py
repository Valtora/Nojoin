"""Carry standing name events onto the final pass's speakers.

Runs inside ``assign_and_identify_speakers`` before any label is resolved. The
machine events of an earlier run are dropped first, because this run derives
its own; what stands after that is the user's. Each standing event goes to the
final speaker holding its span (``resolve_names``), so a name given for a turn
follows that voice even when the labels change.

A user's name for a person applies only where the cluster sounds like them:
when both voiceprints exist and can be compared, the cluster must score at
least ``IDENTIFIED_MERGE_VOICEPRINT_FLOOR`` (0.60) against the person's library
voiceprint, the floor #451 set for a cluster joining an identified person.
Below it the name is offered as pending ("Alex?"). Without a comparable
voiceprint on either side, the user's word stands, as the merge pass's veto
lets it (``LIBRARY_NO_VOICEPRINT``).

A held name stops identification for its speaker. A pending one is applied
only after the merge pass, and only to a speaker still unnamed and unlinked,
so it never blocks a merge, never triggers merge-by-name and never overrides a
voiceprint match.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from sqlmodel import select

from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.speaker_names.anchors import lines_from_final_segments
from backend.utils.speaker_names.events import as_name_event
from backend.utils.speaker_names.resolve import ResolvedName, resolve_names
from backend.utils.speaker_names.sources import MACHINE_SOURCES

logger = logging.getLogger(__name__)

REASON_FLOOR_REFUSED = "floor_refused"


@dataclass
class CarriedNames:
    # Applied as the speaker's identity: it stops voiceprint identification.
    held: dict[str, ResolvedName] = field(default_factory=dict)
    # Offered as "Alex?" once the merge pass is done.
    pending: dict[str, ResolvedName] = field(default_factory=dict)


def _floor_score(session: Any, global_speaker_id: int, voiceprint: Any) -> float | None:
    """Cosine of the cluster against the person, or None when not comparable."""
    from backend.processing.embedding import cosine_similarity, embedding_version_of
    from backend.processing.embedding_version import EMBEDDING_METHOD_VERSION

    embedding = getattr(voiceprint, "embedding", None)
    person = session.get(GlobalSpeaker, global_speaker_id)
    if not embedding or person is None or not person.embedding:
        return None
    if embedding_version_of(person) != EMBEDDING_METHOD_VERSION:
        return None
    return cosine_similarity(embedding, person.embedding)


def _standing_rows_after_dropping_machine_ones(
    session: Any, recording_id: int
) -> list[SpeakerNameEvent]:
    """This run derives its own machine rows, so an earlier run's go first.

    A retried or requeued run then never duplicates them. A user's Confirm or
    Dismiss that pointed at one keeps its own name and span.
    """
    rows = list(
        session.exec(
            select(SpeakerNameEvent)
            .where(SpeakerNameEvent.recording_id == recording_id)
            .order_by(SpeakerNameEvent.id)
        ).all()
    )
    kept = []
    for row in rows:
        if row.source in MACHINE_SOURCES:
            session.delete(row)
        else:
            kept.append(row)
    if len(kept) != len(rows):
        session.flush()
    return kept


def carry_names_to_final_speakers(
    session: Any,
    recording: Any,
    final_segments: list[dict],
    speaker_voiceprints: dict[str, Any],
) -> CarriedNames:
    """Resolve the standing events against this run's speakers."""
    from backend.utils.speaker_names.durability import restore_name_anchors

    restore_name_anchors(session, recording.id)
    rows = _standing_rows_after_dropping_machine_ones(session, recording.id)
    carried = CarriedNames()
    if not rows:
        return carried

    from backend.processing.embedding import IDENTIFIED_MERGE_VOICEPRINT_FLOOR

    resolved, placements = resolve_names(
        [as_name_event(row) for row in rows],
        lines_from_final_segments(final_segments),
    )
    floor_scores: dict[str, float | None] = {}
    for label, name in resolved.items():
        if name.pending:
            carried.pending[label] = name
            continue
        if name.global_speaker_id is not None:
            score = _floor_score(
                session, name.global_speaker_id, speaker_voiceprints.get(label)
            )
            floor_scores[label] = score
            if score is not None and score < IDENTIFIED_MERGE_VOICEPRINT_FLOOR:
                carried.pending[label] = replace(
                    name, pending=True, reason=REASON_FLOOR_REFUSED
                )
                continue
        carried.held[label] = name

    applied = {
        event_id: name.reason
        for name in [*carried.held.values(), *carried.pending.values()]
        for event_id in name.event_ids
    }
    for placement in placements:
        score = floor_scores.get(placement.label or "")
        record_pipeline_metric(
            stage="speaker_name_resolution",
            recording_id=recording.id,
            payload={
                "event_id": placement.event.event_id,
                "source": placement.event.source,
                "outcome": placement.outcome,
                "holder": placement.label,
                "share": round(placement.share, 4),
                "covered_ms": placement.covered_ms,
                "applied": applied.get(placement.event.event_id),
                "floor_score": None if score is None else round(score, 4),
            },
            log=logger,
        )
    return carried


def apply_pending_names(
    session: Any, recording: Any, pending: dict[str, ResolvedName]
) -> None:
    """Offer each pending name on its speaker, if nothing else named it."""
    for label, name in pending.items():
        speaker = session.exec(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording.id)
            .where(RecordingSpeaker.diarization_label == label)
        ).first()
        if speaker is None or speaker.merged_into_id is not None:
            continue
        if speaker.global_speaker_id is not None or speaker.local_name:
            continue
        logger.info(
            "Offering %s as a pending name for %s (%s)", name.name, label, name.reason
        )
        speaker.local_name = name.name
        speaker.name_source = name.source
        speaker.identity_locked = False
        session.add(speaker)
    session.flush()
