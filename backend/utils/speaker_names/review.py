"""A user's answer to a pending name: Dismiss ("Not Alex").

Confirm needs nothing of its own: it is the rename the name already reads as
(``apply_recording_speaker_update``), which links People by exact name, locks
the speaker, offers its voiceprint and writes the user's event.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent

from .anchors import longest_turn
from .events import add_event, speaker_lines
from .sources import AUTHORITY, KIND_DISMISS, SOURCE_USER


def shown_name(speaker: RecordingSpeaker) -> str:
    person = getattr(speaker, "global_speaker", None)
    return str(
        speaker.local_name
        or (person.name if person is not None else None)
        or speaker.name
        or speaker.diarization_label
    )


def dismissed_names(session: Any, recording_speaker_id: int | None) -> set[str]:
    """The names the user said this speaker is not, casefolded."""
    if recording_speaker_id is None:
        return set()
    names = session.execute(
        select(SpeakerNameEvent.name)
        .where(SpeakerNameEvent.recording_speaker_id == recording_speaker_id)
        .where(col(SpeakerNameEvent.kind) == KIND_DISMISS)
    ).scalars()
    return {str(name).strip().casefold() for name in names}


def dismiss_pending_name(
    session: Any, speaker: RecordingSpeaker, actor_user_id: int | None
) -> str:
    """Record the dismissal and put the speaker back to "Speaker N".

    The name, and a voiceprint match's provisional link, go; the speaker keeps
    its own "Speaker N" when no other speaker shows it. Returns the name now
    shown.
    """
    from backend.utils.canonical_pipeline import (
        list_active_utterances,
        refresh_transcript_projection_from_canonical,
    )
    from backend.utils.canonical_pipeline.notes_propagation import (
        propagate_speaker_rename_to_notes,
    )
    from backend.utils.canonical_pipeline.speaker import (
        ensure_recording_speaker_aliases_for_speaker,
    )
    from backend.utils.canonical_pipeline.unmerge import placeholder_speaker_name

    recording_id = speaker.recording_id
    dismissed = shown_name(speaker)
    anchor = longest_turn(speaker_lines(session, recording_id), speaker.id)
    if anchor is not None:
        add_event(
            session,
            speaker,
            SpeakerNameEvent(
                recording_id=recording_id,
                kind=KIND_DISMISS,
                name=dismissed,
                global_speaker_id=speaker.global_speaker_id,
                source=SOURCE_USER,
                authority=AUTHORITY[SOURCE_USER],
                surface="api",
                anchor_start_ms=anchor[0],
                anchor_end_ms=anchor[1],
                created_by_user_id=actor_user_id,
            ),
        )

    speakers = (
        session.execute(
            select(RecordingSpeaker).where(
                RecordingSpeaker.recording_id == recording_id
            )
        )
        .scalars()
        .all()
    )
    speaker.local_name = None
    speaker.global_speaker_id = None
    speaker.global_speaker = None
    speaker.name = placeholder_speaker_name(speaker, speakers)
    speaker.name_source = None
    speaker.identity_confidence = None
    speaker.identity_locked = False
    ensure_recording_speaker_aliases_for_speaker(session, speaker)
    session.add(speaker)
    session.flush()

    if list_active_utterances(session, recording_id):
        refresh_transcript_projection_from_canonical(session, recording_id)
    propagate_speaker_rename_to_notes(
        session, recording_id=recording_id, old_names=[dismissed], new_name=speaker.name
    )
    return speaker.name
