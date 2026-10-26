"""Shared identity-field mutation core for recording speakers.

Extracted so the manual rename path (``update_recording_speaker_identity`` in
``speaker.py``) and the worker's speaker-suggestion auto-apply share one
implementation of the global-link / local-name assignment, embedding merge,
and alias sync instead of drifting copies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.processing.embedding import (
    LibraryVoiceprintUpdate,
    lock_blocks_user_link,
    offer_linked_voiceprint,
)

if TYPE_CHECKING:
    from sqlmodel import Session


def apply_recording_speaker_identity_fields(
    session: Session,
    recording_speaker: RecordingSpeaker,
    *,
    new_speaker_name: str,
    target_global_speaker: GlobalSpeaker | None = None,
    merge_global_embedding_alpha: float | None = None,
    identity_confidence: float | None = None,
    identity_locked: bool | None = None,
    user_link: bool = False,
    name_source: str | None = None,
) -> LibraryVoiceprintUpdate | None:
    """Mutate one recording speaker's identity fields in place.

    An automated link (the default, as for speaker-suggestion auto-apply)
    never changes a voiceprint-locked person's voiceprint, nor gives them a
    first one. A ``user_link`` (a rename or promote) takes the rule of every
    link the user makes (``lock_blocks_user_link``): the lock blocks a change
    to an existing voiceprint, but a locked person with none still gets one.
    Either way a link the lock keeps out is still judged against the
    person's voiceprint. ``identity_confidence`` and ``identity_locked`` are
    written only when a value is provided, so callers control whether the
    identity is asserted as human-confirmed; so is ``name_source``, the source
    that gave the name (``backend/utils/speaker_names/sources.py``).

    Returns what happened to the person's library voiceprint, or None when no
    voiceprint was offered to it (no person, no merge weight, or no recording
    voiceprint).
    """
    # Imported lazily: speaker.py imports this module at load time, and the
    # alias helper lives there.
    from .constants import _load_transcript
    from .speaker import ensure_recording_speaker_aliases_for_speaker

    _load_transcript(session, recording_speaker.recording_id, lock=True)

    library_update = None
    if target_global_speaker is not None:
        recording_speaker.global_speaker_id = target_global_speaker.id
        recording_speaker.global_speaker = target_global_speaker
        recording_speaker.local_name = None
        if merge_global_embedding_alpha is not None and recording_speaker.embedding:
            library_update = offer_linked_voiceprint(
                target_global_speaker,
                recording_speaker,
                alpha=merge_global_embedding_alpha,
                locked=(
                    lock_blocks_user_link(target_global_speaker)
                    if user_link
                    else bool(target_global_speaker.is_voiceprint_locked)
                ),
            )
            if library_update.changed:
                session.add(target_global_speaker)
    else:
        recording_speaker.global_speaker_id = None
        recording_speaker.global_speaker = None
        recording_speaker.local_name = new_speaker_name

    recording_speaker.name = None
    if identity_confidence is not None:
        recording_speaker.identity_confidence = identity_confidence
    if identity_locked is not None:
        recording_speaker.identity_locked = identity_locked
    if name_source is not None:
        recording_speaker.name_source = name_source
    ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)
    session.add(recording_speaker)
    return library_update
