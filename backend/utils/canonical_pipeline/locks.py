"""Manual-edit lock management for canonical utterances.

Split from core.py, which is size-capped, but logically part of the same
write surface: releasing a lock is an audited utterance event exactly like
the edits that set it.
"""

from .core import (
    TranscriptUtterance,
    _append_utterance_event,
    _get_utterance,
    _load_transcript,
    _update_projection_segment_by_public_id,
)


def clear_utterance_manual_locks(  # noqa: PLR0913 - keyword-only edit context, matching core's write functions
    session,
    *,
    recording_id: int,
    utterance_public_id: str,
    actor_user_id: int | None = None,
    expected_revision: int | None = None,
    source: str = "api",
) -> TranscriptUtterance:
    """Release an utterance's manual-edit locks so reprocessing may
    overwrite it again.

    Text and speaker are untouched; only the locks change, and the change
    is itself an audited event, so the fact that the utterance was once
    manually edited remains in the event log even after the lock is gone.
    """
    utterance = _get_utterance(session, recording_id, utterance_public_id)
    if utterance is None:
        raise LookupError("Utterance not found")
    if expected_revision is not None and utterance.revision != expected_revision:
        raise RuntimeError("Utterance revision conflict")

    old_values = {
        "manual_text_locked": bool(utterance.manual_text_locked),
        "manual_speaker_locked": bool(utterance.manual_speaker_locked),
        "text_last_edit_source": utterance.text_last_edit_source,
        "speaker_last_edit_source": utterance.speaker_last_edit_source,
        "revision": utterance.revision,
    }
    utterance.manual_text_locked = False
    utterance.manual_speaker_locked = False
    # The lock is what the source describes, so releasing one releases the
    # other. A source kept past its lock would be state the interface can
    # never reach, since every pill is gated on the lock.
    utterance.text_last_edit_source = None
    utterance.speaker_last_edit_source = None
    utterance.revision += 1
    session.add(utterance)
    session.flush()

    _append_utterance_event(
        session,
        utterance=utterance,
        actor_user_id=actor_user_id,
        event_type="clear_manual_locks",
        source=source,
        old_values=old_values,
        new_values={
            "manual_text_locked": False,
            "manual_speaker_locked": False,
        },
        resulting_revision=utterance.revision,
    )

    transcript = _load_transcript(session, recording_id)
    if transcript is not None:
        _update_projection_segment_by_public_id(
            transcript,
            utterance.public_id,
            {
                "text_manually_edited": False,
                "speaker_manually_edited": False,
                "text_edit_source": None,
                "speaker_edit_source": None,
                "revision": utterance.revision,
                "state": utterance.state.value,
                "updated_at": utterance.updated_at.isoformat(),
            },
        )
        session.add(transcript)

    return utterance
