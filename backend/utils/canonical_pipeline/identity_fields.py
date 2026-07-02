"""Shared identity-field mutation core for recording speakers.

Extracted so the manual rename path (``update_recording_speaker_identity`` in
``speaker.py``) and the worker's speaker-suggestion auto-apply share one
implementation of the global-link / local-name assignment, embedding merge,
and alias sync instead of drifting copies.
"""

from __future__ import annotations

from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.processing.embedding import merge_embeddings


def apply_recording_speaker_identity_fields(
    session,
    recording_speaker: RecordingSpeaker,
    *,
    new_speaker_name: str,
    target_global_speaker: GlobalSpeaker | None = None,
    merge_global_embedding_alpha: float | None = None,
    identity_confidence: float | None = None,
    identity_locked: bool | None = None,
) -> None:
    """Mutate one recording speaker's identity fields in place.

    Voiceprint-locked global speakers never receive embedding updates,
    whether merging into an existing voiceprint or seeding an empty one.
    ``identity_confidence`` and ``identity_locked`` are written only when a
    value is provided, so callers control whether the identity is asserted as
    human-confirmed.
    """
    # Imported lazily: speaker.py imports this module at load time, and the
    # alias helper lives there.
    from .speaker import ensure_recording_speaker_aliases_for_speaker

    if target_global_speaker is not None:
        recording_speaker.global_speaker_id = target_global_speaker.id
        recording_speaker.global_speaker = target_global_speaker
        recording_speaker.local_name = None
        if merge_global_embedding_alpha is not None and recording_speaker.embedding:
            if target_global_speaker.embedding:
                if not target_global_speaker.is_voiceprint_locked:
                    target_global_speaker.embedding = merge_embeddings(
                        target_global_speaker.embedding,
                        recording_speaker.embedding,
                        alpha=merge_global_embedding_alpha,
                    )
            else:
                if not target_global_speaker.is_voiceprint_locked:
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
