"""A user's answer to a pending name: Dismiss ("Not Alex").

Confirm needs nothing of its own: it is the rename the name already reads as
(``apply_recording_speaker_update``), which links People by exact name, locks
the speaker, offers its voiceprint and writes the user's event.

Also the People scan's link, which a dismissal refuses (``link_voice_match``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlmodel import col, select

from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent

from .anchors import longest_turn
from .events import add_event, speaker_lines
from .sources import (
    AUTHORITY,
    KIND_DISMISS,
    SOURCE_USER,
    SOURCE_VOICEPRINT,
    inferred_name_may_be_replaced,
)


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


def _refresh_clean_speaker(session: Any, speaker: RecordingSpeaker) -> None:
    """Read identity decisions after the fence without dropping staged changes."""
    state = inspect(speaker)
    if not state.persistent:
        return
    relationship_dirty = state.attrs.global_speaker.history.has_changes()
    clean = [
        column.key
        for column in state.mapper.column_attrs
        if not state.attrs[column.key].history.has_changes()
        and not (column.key == "global_speaker_id" and relationship_dirty)
    ]
    with session.no_autoflush:
        if clean:
            session.refresh(speaker, attribute_names=clean)
        if not relationship_dirty:
            session.expire(speaker, ["global_speaker"])


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
    from backend.utils.canonical_pipeline.constants import _load_transcript

    _load_transcript(session, recording_id, lock=True)
    _refresh_clean_speaker(session, speaker)
    if speaker.identity_locked:
        return shown_name(speaker)
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


def link_voice_match(
    session: Any, speaker: RecordingSpeaker, person: Any, score: float
) -> bool:
    """Link ``speaker`` to ``person`` on a People scan's voice match.

    The link is a voiceprint match like the final pass's: unconfirmed, with
    its cosine as the confidence. Strong matches show plain names, but under
    ``confirmed`` learning still teach nothing. A pending self-introduction or
    AI name ranks below it and gives way.

    Returns False, linking nobody, when the user said this speaker is not that
    person, or when the speaker keeps a name of its own: the user's (once the
    user names a speaker, voice detection stops for it), or one from before
    sources were recorded or taken as given, which the match would show as
    "Carol?" while linking Bob.
    """
    from backend.utils.canonical_pipeline.constants import _load_transcript

    previous_global_speaker_id = speaker.global_speaker_id
    _load_transcript(session, speaker.recording_id, lock=True)
    _refresh_clean_speaker(session, speaker)
    if speaker.identity_locked:
        return False
    if (
        speaker.global_speaker_id is not None
        and speaker.global_speaker_id != previous_global_speaker_id
    ):
        return False
    if str(person.name).strip().casefold() in dismissed_names(session, speaker.id):
        return False
    if inferred_name_may_be_replaced(speaker):
        speaker.local_name = None
    if speaker.local_name:
        return False
    speaker.global_speaker_id = person.id
    speaker.name = person.name
    speaker.name_source = SOURCE_VOICEPRINT
    speaker.identity_confidence = score
    session.add(speaker)
    return True
