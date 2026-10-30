"""Carry standing name events onto the final pass's speakers.

Runs inside ``assign_and_identify_speakers`` before any label is resolved. The
machine events of an earlier run are dropped first, because this run derives
its own; what stands after that is the user's. Each standing event goes to the
final speaker holding its span (``resolve_names``), so a name given for a turn
follows that voice even when the labels change. A name given to a live speaker
follows all of that label's live lines (``live_label_spans``).

A user's name is never dropped silently: one that lands on no speaker is
offered as pending on the speaker nearest it (``REASON_UNPLACED``), where the
LLM pass may not replace it.

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

The user's other live choices carry here too (#775), by the rule in
``backend/utils/speaker_names/live_carry.py``. A live speaker, a label or the
labels the user merged, goes to the final speaker its name goes to. A line the
user moved to a live speaker goes to that final speaker through the line's
manual speaker (``carry_live_picks``). A merge folds a final speaker into the
group's only where it is clearly the merged voice and no one else's, and
otherwise moves only the final lines the merged label's live lines cover. The
final pass's own diarisation is otherwise left as it is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from sqlmodel import select

from backend.models.pipeline import TranscriptUtterance
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.speaker_names.anchors import Line, lines_from_final_segments
from backend.utils.speaker_names.events import as_name_event
from backend.utils.speaker_names.live import (
    SURFACE_LIVE,
    live_label_spans,
    live_speaker_groups,
)
from backend.utils.speaker_names.live_carry import LiveCarry, carry_live_speakers
from backend.utils.speaker_names.resolve import NameEvent, ResolvedName, resolve_names
from backend.utils.speaker_names.sources import MACHINE_SOURCES

logger = logging.getLogger(__name__)

REASON_FLOOR_REFUSED = "floor_refused"


@dataclass
class CarriedNames:
    # Applied as the speaker's identity: it stops voiceprint identification.
    held: dict[str, ResolvedName] = field(default_factory=dict)
    # Offered as "Alex?" once the merge pass is done.
    pending: dict[str, ResolvedName] = field(default_factory=dict)
    # Where each live speaker goes, and what the user's live merges fold.
    live: LiveCarry = field(default_factory=LiveCarry)
    # Each merged live label -> the label its group shows as.
    live_groups: dict[str, str] = field(default_factory=dict)


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


def _name_events(
    rows: list[SpeakerNameEvent], groups: dict[str, str], live: LiveCarry
) -> list[NameEvent]:
    """Each row as an event; a live name follows all of its live speaker's speech.

    That is the speech of every label merged into the named one, less the
    lines the user moved, so the name lands where ``live`` sends the speaker.
    """
    labels = {
        row.id: groups.get(str(row.anchor_label), str(row.anchor_label))
        for row in rows
        if row.surface == SURFACE_LIVE and row.anchor_label
    }
    return [
        replace(as_name_event(row), anchor_spans=live.spans.get(labels[row.id], ()))
        if row.id in labels
        else as_name_event(row)
        for row in rows
    ]


def _final_line(segment: dict) -> Line | None:
    lines = lines_from_final_segments([segment])
    return lines[0] if lines else None


def _live_carry(
    session: Any, recording_id: int, final_segments: list[dict]
) -> tuple[LiveCarry, dict[str, str]]:
    # A session that cannot run a query has no live lines to read, as for
    # ``restore_name_anchors``.
    if not hasattr(session, "execute"):
        return LiveCarry(), {}
    groups = live_speaker_groups(session, recording_id)
    carry = carry_live_speakers(
        live_label_spans(session, recording_id),
        groups,
        [_final_line(segment) for segment in final_segments],
    )
    return carry, groups


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
    live, groups = _live_carry(session, recording.id, final_segments)
    carried = CarriedNames(live=live, live_groups=groups)
    if live.folds or live.moves:
        record_pipeline_metric(
            stage="live_speaker_merge_carried",
            recording_id=recording.id,
            payload={"folds": live.folds, "moved_lines": len(live.moves)},
            log=logger,
        )
    if not rows:
        return carried

    from backend.processing.embedding import IDENTIFIED_MERGE_VOICEPRINT_FLOOR

    resolved, placements = resolve_names(
        _name_events(rows, groups, live),
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


def apply_live_moves(final_segments: list[dict], live: LiveCarry) -> None:
    """Give each final line a refused fold moves its live speaker's final label."""
    from backend.utils.speaker_assignment import remap_overlapping_speakers

    for index, label in live.moves.items():
        segment = final_segments[index]
        segment["speaker"] = label
        if segment.get("overlapping_speakers"):
            segment["overlapping_speakers"] = remap_overlapping_speakers(
                segment["overlapping_speakers"], primary=label
            )


def _survivor(
    session: Any, recording_id: int, final_label: str
) -> RecordingSpeaker | None:
    speaker = session.exec(
        select(RecordingSpeaker)
        .where(RecordingSpeaker.recording_id == recording_id)
        .where(RecordingSpeaker.diarization_label == final_label)
    ).first()
    seen: set[int] = set()
    while speaker is not None and speaker.merged_into_id is not None:
        if speaker.id in seen:
            return None
        seen.add(speaker.id)
        speaker = session.get(RecordingSpeaker, speaker.merged_into_id)
    return speaker


def carry_live_picks(session: Any, recording: Any, carried: CarriedNames) -> None:
    """Point each line the user moved to a live speaker at its final speaker.

    Such a line keeps its manual speaker and names the live label it was
    moved to, with no recording speaker behind it. Once this run's speakers
    exist, the line names the final speaker that label's live speaker went to
    (after the merge passes), and finalisation carries it onto the final lines
    it covers as it carries any manual speaker. A live speaker with no final
    speaker (the final pass heard none of its own speech) leaves the line to
    the final pass, so no live label outlives the capture.
    """
    from backend.utils.canonical_pipeline.constants import ACTIVE_UTTERANCE_STATES

    if not hasattr(session, "execute"):
        return
    picks = session.exec(
        select(TranscriptUtterance)
        .where(TranscriptUtterance.recording_id == recording.id)
        .where(TranscriptUtterance.source_kind == "live")
        .where(TranscriptUtterance.state.in_(ACTIVE_UTTERANCE_STATES))
        .where(TranscriptUtterance.manual_speaker_locked.is_(True))
        .where(TranscriptUtterance.recording_speaker_id.is_(None))
    ).all()
    survivors: dict[str, RecordingSpeaker | None] = {}
    carried_ids, released = [], []
    for line in picks:
        label = str(line.speaker_label or "")
        if live_speaker_display_name(label) is None:
            continue
        final_label = carried.live.targets.get(carried.live_groups.get(label, label))
        if final_label is not None and final_label not in survivors:
            survivors[final_label] = _survivor(session, recording.id, final_label)
        survivor = survivors.get(final_label) if final_label is not None else None
        if survivor is None:
            line.manual_speaker_locked = False
            released.append(line.public_id)
        else:
            line.recording_speaker_id = survivor.id
            carried_ids.append(line.public_id)
        session.add(line)
    if picks:
        session.flush()
    if carried_ids or released:
        record_pipeline_metric(
            stage="live_speaker_picks_carried",
            recording_id=recording.id,
            payload={"carried": len(carried_ids), "released": len(released)},
            log=logger,
        )


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
