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

A line moved by hand to a live speaker goes where that speaker's name goes:
one placement of the label's speech decides both (``_live_line_targets``).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from sqlmodel import col, select

from backend.models.pipeline import TranscriptUtterance
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.transcript import Transcript
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.canonical_pipeline.constants import ACTIVE_UTTERANCE_STATES
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.speaker_names.anchors import Line, Span, lines_from_final_segments
from backend.utils.speaker_names.events import as_name_event
from backend.utils.speaker_names.live import (
    SURFACE_LIVE,
    live_label_spans,
    manually_reassigned_live_turns,
)
from backend.utils.speaker_names.resolve import (
    NameEvent,
    Placement,
    ResolvedName,
    place_event,
    resolve_names,
)
from backend.utils.speaker_names.sources import (
    AUTHORITY,
    KIND_NAME,
    MACHINE_SOURCES,
    SOURCE_USER,
)

logger = logging.getLogger(__name__)

REASON_FLOOR_REFUSED = "floor_refused"


@dataclass
class CarriedNames:
    # Applied as the speaker's identity: it stops voiceprint identification.
    held: dict[str, ResolvedName] = field(default_factory=dict)
    # Offered as "Alex?" once the merge pass is done.
    pending: dict[str, ResolvedName] = field(default_factory=dict)
    # A line moved by hand to a live speaker, by utterance id: the final label
    # it goes to, moved once the merge pass is done.
    live_lines: dict[int, str] = field(default_factory=dict)


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
    session: Any,
    recording_id: int,
    rows: list[SpeakerNameEvent],
    live: dict[int, str],
    spans: dict[str, tuple[Span, ...]],
) -> list[NameEvent]:
    """Each row as an event; a live name follows all of its label's speech."""
    removed = manually_reassigned_live_turns(
        session, recording_id, set(live.values()) - spans.keys()
    )
    invalidated = {
        row.id
        for row in rows
        if row.id in live
        and any(
            start <= row.anchor_start_ms and end >= row.anchor_end_ms
            for start, end in removed.get(live[row.id], ())
        )
    }
    return [
        replace(
            as_name_event(row),
            anchor_spans=spans.get(live[row.id], ()),
            # Missing raw labels also follow ordinary finalisation. Only an
            # explicit correction covering the saved turn invalidates it.
            anchor_end_ms=(
                row.anchor_start_ms if row.id in invalidated else row.anchor_end_ms
            ),
        )
        if row.id in live
        else as_name_event(row)
        for row in rows
    ]


def _lines_moved_to_live_speakers(
    session: Any, recording_id: int
) -> list[TranscriptUtterance]:
    """Active lines whose hand-picked speaker is still a live label.

    Live lines take corrections only once settling has published them, so
    any other transcript has none.
    """
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()
    if (getattr(transcript, "settling_state", None) or {}).get("settled") is not True:
        return []
    rows = (
        session.execute(
            select(TranscriptUtterance)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(col(TranscriptUtterance.state).in_(ACTIVE_UTTERANCE_STATES))
            .where(col(TranscriptUtterance.manual_speaker_locked).is_(True))
        )
        .scalars()
        .all()
    )
    return [row for row in rows if live_speaker_display_name(row.speaker_label)]


def _user_act_on(spans: tuple[Span, ...]) -> NameEvent:
    """A user's act on this speech, placed as a user's name on it would be."""
    return NameEvent(
        event_id=0,
        kind=KIND_NAME,
        name="",
        global_speaker_id=None,
        source=SOURCE_USER,
        authority=AUTHORITY[SOURCE_USER],
        anchor_start_ms=spans[0][0],
        anchor_end_ms=spans[-1][1],
        anchor_spans=spans,
    )


def _live_line_targets(
    moved: list[TranscriptUtterance],
    live: dict[int, str],
    spans: dict[str, tuple[Span, ...]],
    placements: Sequence[Placement],
    lines: Sequence[Line],
) -> dict[int, str]:
    """The final label each moved line goes to: where its live speaker's name goes.

    A named label reuses its name's placement; an unnamed one is placed the
    same way on the same speech. Either is the holder, the top holder when no
    speaker clearly holds it, or the nearest speaker when processing heard
    none of it. A label with no speech of its own left, every line of it
    moved by hand, gives each line to the final speaker of its own span.
    """
    placed = {
        live[placement.event.event_id]: placement.label
        for placement in placements
        if placement.event.event_id in live
    }
    targets: dict[int, str] = {}
    for row in moved:
        label = str(row.speaker_label)
        own = spans.get(label)
        if own and label not in placed:
            placed[label] = place_event(_user_act_on(own), lines).label
        target = (
            placed[label]
            if own
            else place_event(_user_act_on(((row.start_ms, row.end_ms),)), lines).label
        )
        if target is not None:
            targets[int(row.id)] = target
    return targets


def carry_names_to_final_speakers(
    session: Any,
    recording: Any,
    final_segments: list[dict],
    speaker_voiceprints: dict[str, Any],
) -> CarriedNames:
    """Resolve the standing events, and lines moved to live speakers, against this run."""
    from backend.utils.speaker_names.durability import restore_name_anchors

    restore_name_anchors(session, recording.id)
    rows = _standing_rows_after_dropping_machine_ones(session, recording.id)
    moved = _lines_moved_to_live_speakers(session, recording.id)
    live = {
        row.id: str(row.anchor_label)
        for row in rows
        if row.surface == SURFACE_LIVE and row.anchor_label
    }
    spans = live_label_spans(
        session,
        recording.id,
        {*live.values(), *(str(row.speaker_label) for row in moved)},
    )
    lines = lines_from_final_segments(final_segments)
    resolved, placements = resolve_names(
        _name_events(session, recording.id, rows, live, spans), lines
    )
    carried = CarriedNames(
        live_lines=_live_line_targets(moved, live, spans, placements, lines)
    )
    if not rows:
        return carried

    from backend.processing.embedding import IDENTIFIED_MERGE_VOICEPRINT_FLOOR

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
