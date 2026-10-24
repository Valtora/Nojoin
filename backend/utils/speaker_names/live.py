"""Names a user gives live speakers while a capture runs.

A live label (``LIVE_PROVISIONAL_<n>``) is no speaker of the recording, so a
name given for it is kept only as the user's name event: anchored on the
label's longest turn so far, tied to no recording speaker, with surface
``live``. The final pass carries it as it carries any user's name
(``speaker_name_carryover``): the span goes to the final speaker holding at
least 60% of it, and a link to a person must also pass the 0.60 voiceprint
floor. Nothing is learned from it before then, and nothing reads live labels
at the final pass.

Until the final pass replaces the live lines, every reader that would show
"Speaker <n>" for the label shows the newest name given for it instead
(``live_speaker_names``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func
from sqlmodel import col, select

from backend.models.pipeline import TranscriptUtterance
from backend.models.speaker_name_event import SpeakerNameEvent

from .anchors import Line, Span, longest_turn
from .events import UserNameAct
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
    unlocked one may still move to another label. With no locked line, the
    longest turn of any. Unlabelled lines end a turn, as another voice's do.
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
        return longest_turn([*others, *locked], label)
    return longest_turn([*others, *unlocked], label)


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
    return row


def live_speaker_names(session: Any, recording_id: int) -> dict[str, str]:
    """Live label -> the newest name the user gave it.

    Only the naming route writes ``live`` rows, each a name for a live label.
    """
    rows = session.execute(
        select(SpeakerNameEvent.anchor_label, SpeakerNameEvent.name)
        .where(SpeakerNameEvent.recording_id == recording_id)
        .where(SpeakerNameEvent.surface == SURFACE_LIVE)
        .order_by(col(SpeakerNameEvent.id))
    ).all()
    return {str(label): str(name) for label, name in rows}


def with_live_speaker_names(
    session: Any, recording_id: int, speaker_map: Mapping[str, str]
) -> dict[str, str]:
    """``speaker_map`` plus the names given to live labels.

    A live label has no recording speaker, so no entry of ``speaker_map`` is
    replaced.
    """
    return {**live_speaker_names(session, recording_id), **speaker_map}
