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

A live speaker the user merged into another during the capture is one live
speaker here (#775): its labels' speech is placed together, for its name and
its lines alike, and a member label's own final speaker is folded into the
group's where that is clearly the merged voice and no one else's
(``backend/utils/speaker_names/live_carry.py``). Where it is not, the member's
own lines go to the group's final speaker as lines moved by hand do.
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
    live_speaker_groups,
    manually_reassigned_live_turns,
)
from backend.utils.speaker_names.live_carry import (
    fold_merged_groups,
    group_spans,
    users_act_on,
)
from backend.utils.speaker_names.resolve import (
    NameEvent,
    Placement,
    ResolvedName,
    place_event,
    resolve_names,
)
from backend.utils.speaker_names.sources import MACHINE_SOURCES

logger = logging.getLogger(__name__)

REASON_FLOOR_REFUSED = "floor_refused"


@dataclass
class CarriedNames:
    # Applied as the speaker's identity: it stops voiceprint identification.
    held: dict[str, ResolvedName] = field(default_factory=dict)
    # Offered as "Alex?" once the merge pass is done.
    pending: dict[str, ResolvedName] = field(default_factory=dict)
    # A line moved by hand to a live speaker, by utterance id: the final label
    # it goes to, moved once the merge pass is done. A merged live label's own
    # lines join them where its final speaker could not be folded.
    live_lines: dict[int, str] = field(default_factory=dict)
    # Final label -> the final label a live merge folds it into.
    folds: dict[str, str] = field(default_factory=dict)


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


def _live_speakers_placed(
    live: dict[int, str],
    spans: dict[str, tuple[Span, ...]],
    placements: Sequence[Placement],
    lines: Sequence[Line],
) -> dict[str, Placement]:
    """Each live speaker's placement: its name's, else the same rule on its speech.

    Either is the holder, the top holder when no speaker clearly holds it, or
    the nearest speaker when processing heard none of it.
    """
    placed = {
        live[placement.event.event_id]: placement
        for placement in placements
        if placement.event.event_id in live
    }
    for label, own in spans.items():
        if label not in placed:
            placed[label] = place_event(users_act_on(own), lines)
    return placed


def _live_line_targets(
    moved: list[TranscriptUtterance],
    groups: dict[str, str],
    placed: dict[str, Placement],
    lines: Sequence[Line],
) -> dict[int, str]:
    """The final label each moved line goes to: where its live speaker's name goes.

    A label merged into another is that live speaker. One with no speech of
    its own left, every line of it moved by hand, gives each line to the
    final speaker of its own span.
    """
    targets: dict[int, str] = {}
    for row in moved:
        label = str(row.speaker_label)
        placement = placed.get(groups.get(label, label))
        target = (
            placement.label
            if placement is not None
            else place_event(users_act_on(((row.start_ms, row.end_ms),)), lines).label
        )
        if target is not None:
            targets[int(row.id)] = target
    return targets


def _merged_label_lines(
    session: Any, recording_id: int, by_line: dict[str, str]
) -> dict[int, str]:
    """A merged label's own live lines, by id, where its speaker was not folded."""
    if not by_line:
        return {}
    rows = session.execute(
        select(TranscriptUtterance.id, TranscriptUtterance.speaker_label)
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.source_kind == "live")
        .where(col(TranscriptUtterance.state).in_(ACTIVE_UTTERANCE_STATES))
        .where(col(TranscriptUtterance.manual_speaker_locked).is_(False))
        .where(col(TranscriptUtterance.speaker_label).in_(set(by_line)))
    ).all()
    return {int(row_id): by_line[str(label)] for row_id, label in rows}


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
    # A merged live label is the live speaker it joined.
    groups = live_speaker_groups(session, recording.id) if rows else {}
    live = {
        row.id: groups.get(str(row.anchor_label), str(row.anchor_label))
        for row in rows
        if row.surface == SURFACE_LIVE and row.anchor_label
    }
    # With a merge, every live speaker's speech counts: a final speaker another
    # live speaker is placed on is never folded into the group.
    own = live_label_spans(
        session,
        recording.id,
        None
        if groups
        else {*live.values(), *(str(row.speaker_label) for row in moved)},
    )
    spans = group_spans(own, groups)
    lines = lines_from_final_segments(final_segments)
    resolved, placements = resolve_names(
        _name_events(session, recording.id, rows, live, spans), lines
    )
    placed = _live_speakers_placed(live, spans, placements, lines)
    carried = CarriedNames(live_lines=_live_line_targets(moved, groups, placed, lines))
    if groups:
        merged = fold_merged_groups(own, groups, lines, placed)
        carried.folds = merged.folds
        carried.live_lines = {
            **_merged_label_lines(session, recording.id, merged.by_line),
            **carried.live_lines,
        }
        record_pipeline_metric(
            stage="live_speaker_merges_carried",
            recording_id=recording.id,
            payload={"folds": merged.folds, "by_line": merged.by_line},
            log=logger,
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


def follow_merge_chain(
    session: Any, speaker: RecordingSpeaker, label: str
) -> RecordingSpeaker:
    """Walk to the surviving speaker of a merge chain, guarding against cycles."""
    current = speaker
    visited = {current.id}
    while current.merged_into_id:
        nxt = session.get(RecordingSpeaker, current.merged_into_id)
        if not nxt:
            logger.warning(
                f"Merge chain broken for speaker {label} at ID {current.merged_into_id}"
            )
            break
        if nxt.id in visited:
            logger.warning(f"Circular merge detected for speaker {label}")
            break
        visited.add(nxt.id)
        current = nxt
    return current


def surviving_speaker(
    session: Any, recording_id: int, label: str
) -> RecordingSpeaker | None:
    """The speaker a final label ends on once the merge passes are done.

    Pending names and the lines moved to live speakers both land here, so a
    name and its lines end on one speaker.
    """
    speaker = session.exec(
        select(RecordingSpeaker)
        .where(RecordingSpeaker.recording_id == recording_id)
        .where(RecordingSpeaker.diarization_label == label)
    ).first()
    return None if speaker is None else follow_merge_chain(session, speaker, label)


def apply_pending_names(
    session: Any, recording: Any, pending: dict[str, ResolvedName]
) -> None:
    """Offer each pending name on its speaker's survivor, if nothing else named it."""
    for label, name in pending.items():
        speaker = surviving_speaker(session, recording.id, label)
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
