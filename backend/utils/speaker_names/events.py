"""Read and write ``speaker_name_events`` on a sync session.

Rows are only added. Each new act supersedes the newest row of the speaker it
names, so the speaker's history reads as a chain.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlmodel import col, select

from backend.models.pipeline import TranscriptUtterance
from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent

from .anchors import Line, longest_turn
from .resolve import NameEvent
from .sources import AUTHORITY, KIND_NAME, SOURCE_USER


def as_name_event(row: SpeakerNameEvent) -> NameEvent:
    assert row.id is not None
    return NameEvent(
        event_id=row.id,
        kind=row.kind,
        name=row.name,
        global_speaker_id=row.global_speaker_id,
        source=row.source,
        authority=row.authority,
        anchor_start_ms=row.anchor_start_ms,
        anchor_end_ms=row.anchor_end_ms,
        supersedes_event_id=row.supersedes_event_id,
    )


def newest_event_id(session: Any, recording_speaker_id: int | None) -> int | None:
    """The speaker's newest row.

    Every act on a speaker supersedes the one before, so its rows form one
    chain and the newest is the one standing.
    """
    if recording_speaker_id is None:
        return None
    return session.execute(
        select(func.max(SpeakerNameEvent.id)).where(
            SpeakerNameEvent.recording_speaker_id == recording_speaker_id
        )
    ).scalar()


def speaker_lines(session: Any, recording_id: int) -> list[Line]:
    """Every active line as (recording speaker id, start ms, end ms)."""
    from backend.utils.canonical_pipeline.constants import ACTIVE_UTTERANCE_STATES

    rows = session.execute(
        select(
            TranscriptUtterance.recording_speaker_id,
            TranscriptUtterance.start_ms,
            TranscriptUtterance.end_ms,
        )
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(col(TranscriptUtterance.state).in_(ACTIVE_UTTERANCE_STATES))
    ).all()
    return [(row[0], int(row[1]), int(row[2])) for row in rows if row[0] is not None]


def add_event(
    session: Any, speaker: RecordingSpeaker, row: SpeakerNameEvent
) -> SpeakerNameEvent:
    """Add ``row`` as the speaker's newest act.

    Fills what follows from the speaker and the source: the recording, the
    label at the time, the authority, and the row it supersedes.
    """
    row.recording_id = speaker.recording_id
    row.recording_speaker_id = speaker.id
    row.anchor_label = speaker.diarization_label
    row.authority = AUTHORITY[row.source]
    row.supersedes_event_id = newest_event_id(session, speaker.id)
    session.add(row)
    session.flush()
    return row


@dataclass(frozen=True)
class UserNameAct:
    """A rename, a link to a person or an Add to People."""

    name: str
    global_speaker_id: int | None
    actor_user_id: int | None
    # "api" for the web app, "mcp" for a connected assistant.
    surface: str


def record_user_name_events(
    session: Any,
    recording_id: int,
    speakers: Sequence[RecordingSpeaker],
    act: UserNameAct,
) -> list[SpeakerNameEvent]:
    """Record a user's act on each speaker, anchored on its longest turn so far.

    A speaker with no lines has no turn for the name to follow, so it gets no
    row; its name still applies as before.
    """
    line_list = speaker_lines(session, recording_id)
    written: list[SpeakerNameEvent] = []
    for speaker in speakers:
        anchor = longest_turn(line_list, speaker.id)
        if anchor is None:
            continue
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
            created_by_user_id=act.actor_user_id,
        )
        written.append(add_event(session, speaker, row))
    return written


def record_user_link(
    session: Any,
    speaker: RecordingSpeaker,
    act: UserNameAct,
) -> None:
    """Mark a link the user made outside a rename as the user's, as a rename does.

    The voiceprint dialog, a line moved to a person and the legacy Add to
    People set the link themselves. Without this the speaker would keep a
    machine source and no lock: it would still read as pending, and the next
    inference could replace the name and drop the link.
    """
    speaker.local_name = None
    speaker.name_source = SOURCE_USER
    speaker.identity_locked = True
    session.add(speaker)
    record_user_name_events(session, speaker.recording_id, [speaker], act)
