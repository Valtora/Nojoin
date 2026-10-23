"""The lines a speaker correction applies to, and the order it locks them in."""

from __future__ import annotations

from sqlmodel import col, select

from backend.models.pipeline import SpeakerCorrectionScope, TranscriptUtterance

from .constants import ACTIVE_UTTERANCE_STATES, _get_utterance

_KEYED_SCOPES = {
    SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
    SpeakerCorrectionScope.FROM_THIS_UTTERANCE_FORWARD,
    SpeakerCorrectionScope.MERGE_INTO_SPEAKER,
}


def _lock_active_lines(session, recording_id: int) -> list[TranscriptUtterance]:
    """Every active line, locked in id order and current as of the lock."""
    return list(
        session.execute(
            select(TranscriptUtterance)
            .where(TranscriptUtterance.recording_id == recording_id)
            .where(col(TranscriptUtterance.state).in_(ACTIVE_UTTERANCE_STATES))
            .order_by(col(TranscriptUtterance.id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )


def _lock_anchor(
    session,
    recording_id: int,
    utterance_public_id: str,
    scope: SpeakerCorrectionScope,
) -> TranscriptUtterance | None:
    """The line a correction starts from, locked and current as of the lock.

    A correction to every line of a speaker locks the active lines first, in id
    order, its own among them. Locking its own line before them would let two
    such corrections on different lines each hold a line the other waits for.
    """
    if scope in _KEYED_SCOPES:
        _lock_active_lines(session, recording_id)
    return _get_utterance(session, recording_id, utterance_public_id, lock=True)


def _select_utterances_for_scope(
    session,
    *,
    recording_id: int,
    anchor_utterance: TranscriptUtterance,
    scope: SpeakerCorrectionScope,
    current_key: int | str | None,
) -> list[TranscriptUtterance]:
    """The anchor alone, or every active line with its speaker, read locked.

    Locked in id order and current as of the lock, so a live label written
    meanwhile is waited for: its revision bump is counted, not written over.
    """
    if scope not in _KEYED_SCOPES:
        return [anchor_utterance]
    active = _lock_active_lines(session, recording_id)
    lines = sorted(
        (
            utterance
            for utterance in active
            if (utterance.recording_speaker_id or utterance.speaker_label)
            == current_key
        ),
        key=lambda utterance: (utterance.sort_key, utterance.id),
    )
    if scope == SpeakerCorrectionScope.FROM_THIS_UTTERANCE_FORWARD:
        return [u for u in lines if u.start_ms >= anchor_utterance.start_ms]
    return lines
