"""Names a user gives live speakers while a capture runs.

A live label (``LIVE_PROVISIONAL_<n>``) is no speaker of the recording, so a
name given for it is kept only as the user's name event: anchored on the
label's longest turn so far, tied to no recording speaker, with surface
``live``. The final pass carries it as it carries any user's name
(``speaker_name_carryover``), measured on all of the label's live lines
(``live_label_spans``) rather than that one turn: the name goes to the final
speaker holding at least 60% of the label's speech, and a link to a person
must also pass the 0.60 voiceprint floor. The final transcript may not cover
the turn the name was given on (it can start later than the live one), and
the user named the voice on every line, not one turn. Nothing is learned from
it before then.

Until the final pass replaces the live lines, every reader that would show
"Speaker <n>" for the label shows the newest name given for it instead
(``live_speaker_names``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import func
from sqlmodel import col, select

from backend.models.pipeline import (
    TranscriptUtterance,
    TranscriptUtteranceEvent,
    TranscriptUtteranceState,
)
from backend.models.speaker_name_event import SpeakerNameEvent

from .anchors import Line, Span, longest_turn, turns_of
from .events import UserNameAct
from .resolve import MIN_COVERED_MS
from .sources import AUTHORITY, KIND_NAME, SOURCE_USER

SURFACE_LIVE = "live"
_UNLABELLED = "UNKNOWN"


def _live_label_locked(payload: dict[str, Any] | None) -> bool:
    from backend.utils.canonical_pipeline.constants import (
        LIVE_SPEAKER_LABEL_PAYLOAD_KEY,
    )

    stored = (payload or {}).get(LIVE_SPEAKER_LABEL_PAYLOAD_KEY)
    return isinstance(stored, dict) and stored.get("locked") is True


def live_label_anchor(session: Any, recording_id: int, label: str) -> Span | None:
    """The label's longest turn so far, from the lines whose label has locked.

    A locked line keeps its label, so its turn is the voice the user meant; an
    unlocked one may still move to another label. With no locked line, or none
    lasting ``MIN_COVERED_MS`` (a shorter span names nobody at the final pass),
    the longest turn of any. Unlabelled lines end a turn, as another voice's do.
    """
    from backend.utils.canonical_pipeline.constants import ACTIVE_UTTERANCE_STATES

    rows = session.execute(
        select(
            TranscriptUtterance.speaker_label,
            TranscriptUtterance.start_ms,
            TranscriptUtterance.end_ms,
            TranscriptUtterance.confidence_payload,
        )
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(col(TranscriptUtterance.state).in_(ACTIVE_UTTERANCE_STATES))
    ).all()
    others: list[Line] = []
    locked: list[Line] = []
    unlocked: list[Line] = []
    for speaker_label, start_ms, end_ms, payload in rows:
        line = (speaker_label or _UNLABELLED, int(start_ms), int(end_ms))
        if line[0] != label:
            others.append(line)
        elif _live_label_locked(payload):
            locked.append(line)
        else:
            unlocked.append(line)
    if locked:
        turn = longest_turn([*others, *locked], label)
        if turn is not None and turn[1] - turn[0] >= MIN_COVERED_MS:
            return turn
    return longest_turn([*others, *locked, *unlocked], label)


def live_label_spans(
    session: Any, recording_id: int, labels: Iterable[str]
) -> dict[str, tuple[Span, ...]]:
    """Each live label's reference speech, as a union of its live lines.

    Superseded rows count: the final pass reads the live lines before it
    replaces them, and a reprocess reads them after. Manually assigned rows
    do not vote: a correction must follow the label's other speech.
    """
    wanted = set(labels)
    if not wanted:
        return {}
    rows = session.execute(
        select(
            TranscriptUtterance.speaker_label,
            TranscriptUtterance.start_ms,
            TranscriptUtterance.end_ms,
        )
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.source_kind == "live")
        .where(col(TranscriptUtterance.manual_speaker_locked).is_(False))
        .where(TranscriptUtterance.state != TranscriptUtteranceState.DELETED)
        .where(col(TranscriptUtterance.speaker_label).in_(wanted))
    ).all()
    spans: dict[str, tuple[Span, ...]] = {}
    for label in wanted:
        lines = [
            (label, int(start), int(end)) for key, start, end in rows if key == label
        ]
        # A 1 ms gap joins touching and overlapping lines, so no speech counts twice.
        merged = tuple((start, end) for _, start, end in turns_of(lines, gap_ms=1))
        if merged:
            spans[label] = merged
    return spans


def manually_reassigned_live_turns(
    session: Any, recording_id: int, labels: Iterable[str]
) -> dict[str, tuple[Span, ...]]:
    """Turns explicitly moved away from each live label by a human correction.

    The audit's old label survives finalisation rewriting the row's label.
    Turn gaps match the saved naming anchor's gaps, including short silences.
    """
    wanted = set(labels)
    if not wanted:
        return {}
    rows = session.execute(
        select(
            TranscriptUtteranceEvent.old_values,
            TranscriptUtteranceEvent.new_values,
            TranscriptUtterance.start_ms,
            TranscriptUtterance.end_ms,
        )
        .join(
            TranscriptUtterance,
            col(TranscriptUtterance.id) == col(TranscriptUtteranceEvent.utterance_id),
        )
        .where(TranscriptUtteranceEvent.recording_id == recording_id)
        .where(TranscriptUtteranceEvent.event_type == "update_speaker")
        .where(TranscriptUtterance.source_kind == "live")
        .where(col(TranscriptUtterance.manual_speaker_locked).is_(True))
    ).all()
    changed: list[Line] = []
    for old, new, start, end in rows:
        label = (old or {}).get("speaker_label")
        if label in wanted and (new or {}).get("speaker_label") != label:
            changed.append((label, int(start), int(end)))
    turns = turns_of(changed)
    return {
        label: tuple((start, end) for key, start, end in turns if key == label)
        for label in wanted
    }


def record_live_speaker_name(
    session: Any, recording_id: int, label: str, act: UserNameAct
) -> SpeakerNameEvent | None:
    """Record the user's name for a live label; None when no line carries it.

    The row supersedes the label's previous live name, so only the newest one
    stands at the final pass. ``act.surface`` is ``live``.
    """
    anchor = live_label_anchor(session, recording_id, label)
    if anchor is None:
        return None
    previous = session.execute(
        select(func.max(SpeakerNameEvent.id))
        .where(SpeakerNameEvent.recording_id == recording_id)
        .where(SpeakerNameEvent.surface == SURFACE_LIVE)
        .where(SpeakerNameEvent.anchor_label == label)
    ).scalar()
    row = SpeakerNameEvent(
        recording_id=recording_id,
        kind=KIND_NAME,
        name=act.name,
        global_speaker_id=act.global_speaker_id,
        source=SOURCE_USER,
        authority=AUTHORITY[SOURCE_USER],
        surface=act.surface,
        anchor_start_ms=anchor[0],
        anchor_end_ms=anchor[1],
        anchor_label=label,
        supersedes_event_id=previous,
        created_by_user_id=act.actor_user_id,
    )
    session.add(row)
    session.flush()
    from .durability import persist_name_anchors

    persist_name_anchors(session, recording_id)
    return row


def live_speaker_names(
    session: Any, recording_id: int, *, labels: Iterable[str] | None = None
) -> dict[str, str]:
    """Live label -> the newest name the user gave it, while a line carries it.

    Only the naming route writes ``live`` rows, each a name for a live label.
    Once the final pass has replaced the live lines no current line carries a
    live label, so the map is empty and no read shows one. Readers with a
    canonical-first projection supply its effective ``labels`` so fallback
    reads resolve names without requiring matching canonical rows.
    """
    rows = session.execute(
        select(SpeakerNameEvent.anchor_label, SpeakerNameEvent.name)
        .where(SpeakerNameEvent.recording_id == recording_id)
        .where(SpeakerNameEvent.surface == SURFACE_LIVE)
        .order_by(col(SpeakerNameEvent.id))
    ).all()
    if not rows:
        return {}
    if labels is not None:
        heard_labels = set(labels)
        return {str(label): str(name) for label, name in rows if label in heard_labels}
    from backend.utils.canonical_pipeline.constants import ACTIVE_UTTERANCE_STATES

    heard = set(
        session.execute(
            select(TranscriptUtterance.speaker_label)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(col(TranscriptUtterance.state).in_(ACTIVE_UTTERANCE_STATES))
            .where(col(TranscriptUtterance.speaker_label).in_({r[0] for r in rows}))
            .distinct()
        ).scalars()
    )
    return {str(label): str(name) for label, name in rows if label in heard}


def with_live_speaker_names(
    session: Any,
    recording_id: int,
    speaker_map: Mapping[str, str],
    *,
    labels: Iterable[str] | None = None,
) -> dict[str, str]:
    """``speaker_map`` plus the names given to live labels.

    A live label has no recording speaker, so no entry of ``speaker_map`` is
    replaced.
    """
    return {**live_speaker_names(session, recording_id, labels=labels), **speaker_map}
