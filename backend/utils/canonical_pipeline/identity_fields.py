"""Shared identity-field mutation core for recording speakers.

Extracted so the manual rename path (``update_recording_speaker_identity`` in
``speaker.py``) and the worker's speaker-suggestion auto-apply share one
implementation of the global-link / local-name assignment, embedding merge,
and alias sync instead of drifting copies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.processing.embedding import merge_embeddings

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
    respect_voiceprint_lock: bool = True,
) -> None:
    """Mutate one recording speaker's identity fields in place.

    When ``respect_voiceprint_lock`` is True (the default, used by automated
    callers such as speaker-suggestion auto-apply), a voiceprint-locked global
    speaker never receives an embedding update — neither merging into an
    existing voiceprint nor seeding an empty one. The manual rename/promote
    path passes False so an explicit human-initiated link still updates the
    voiceprint, matching the documented intent that locking blocks *automated*
    updates only. ``identity_confidence`` and ``identity_locked`` are written
    only when a value is provided, so callers control whether the identity is
    asserted as human-confirmed.
    """
    # Imported lazily: speaker.py imports this module at load time, and the
    # alias helper lives there.
    from .speaker import ensure_recording_speaker_aliases_for_speaker

    if target_global_speaker is not None:
        recording_speaker.global_speaker_id = target_global_speaker.id
        recording_speaker.global_speaker = target_global_speaker
        recording_speaker.local_name = None
        embedding_locked = (
            respect_voiceprint_lock and target_global_speaker.is_voiceprint_locked
        )
        if (
            merge_global_embedding_alpha is not None
            and recording_speaker.embedding
            and not embedding_locked
        ):
            if target_global_speaker.embedding:
                target_global_speaker.embedding = merge_embeddings(
                    target_global_speaker.embedding,
                    recording_speaker.embedding,
                    alpha=merge_global_embedding_alpha,
                )
            else:
                target_global_speaker.embedding = list(recording_speaker.embedding)
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
    ensure_recording_speaker_aliases_for_speaker(session, recording_speaker)
    session.add(recording_speaker)
