"""Confirm a pending speaker name, or dismiss an unconfirmed name.

A pending name (``name_is_pending``) shows as "Alex?". Confirm is the rename
the name already reads as, so it does what a rename does: links People by
exact name, locks the speaker, offers the voiceprint and writes the user's
name event. Dismiss also accepts an unlocked voiceprint name, writes a
dismissal and puts the speaker back to "Speaker N".
"""

import logging
from typing import List

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

import backend.api.v1.endpoints.speakers as speakers_module
from backend.api.deps import get_current_user, get_db
from backend.models.recording import Recording
from backend.models.recording_public import RecordingSpeakerPublicRead
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.speaker_name_suggestions import (
    SPEAKER_SUGGESTION_STATUS_ACCEPTED,
    SPEAKER_SUGGESTION_STATUS_REJECTED,
    resolve_pending_transcript_speaker_suggestion,
)
from backend.utils.speaker_names.review import dismiss_pending_name, shown_name
from backend.utils.speaker_names.sources import SOURCE_VOICEPRINT, name_is_pending

from .helpers import (
    SpeakerUpdate,
    _canonical_transcript_writes_enabled,
    _get_owned_recording,
    _require_recording_speaker_mutations_supported,
    _serialize_recording_speakers,
)
from .router import router
from .routes_recording import apply_recording_speaker_update

logger = logging.getLogger(__name__)


async def _pending_speaker(
    db: AsyncSession,
    recording: Recording,
    diarization_label: str,
    *,
    allow_unlocked_voiceprint: bool = False,
) -> RecordingSpeaker:
    speaker = (
        await db.execute(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording.id)
            .where(RecordingSpeaker.diarization_label == diarization_label)
            .where(RecordingSpeaker.merged_into_id.is_(None))
            .options(selectinload(RecordingSpeaker.global_speaker))
        )
    ).scalar_one_or_none()
    if speaker is None:
        raise HTTPException(
            status_code=404, detail="Speaker not found in this recording"
        )
    if not name_is_pending(speaker) and not (
        allow_unlocked_voiceprint
        and speaker.name_source == SOURCE_VOICEPRINT
        and not speaker.identity_locked
    ):
        raise HTTPException(
            status_code=409, detail="This speaker has no name awaiting confirmation"
        )
    return speaker


async def _resolve_suggestion(
    db: AsyncSession,
    recording: Recording,
    diarization_label: str,
    resolution: str,
    actor_user_id: int,
) -> None:
    """Close the suggestion behind the name, so its audit entry says what the user did."""
    transcript = (
        await db.execute(
            select(Transcript).where(Transcript.recording_id == recording.id)
        )
    ).scalar_one_or_none()
    if transcript is None:
        return
    resolved = resolve_pending_transcript_speaker_suggestion(
        transcript,
        diarization_label=diarization_label,
        resolution=resolution,
        actor_user_id=actor_user_id,
        reason=f"user_{resolution}",
    )
    if resolved is not None:
        # The helper assigns a new list, which marks the column changed.
        db.add(transcript)


def _record_review(
    recording: Recording, label: str, resolution: str, source: str | None
) -> None:
    speakers_module.record_pipeline_metric(
        stage="speaker_name_reviewed",
        recording_id=recording.id,
        payload={
            "diarization_label": label,
            "resolution": resolution,
            "source": source,
        },
        log=logger,
    )


@router.post(
    "/recordings/{recording_id}/speakers/{diarization_label}/suggestions/accept",
    response_model=List[RecordingSpeakerPublicRead],
)
async def confirm_speaker_name(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Confirm a pending name: the user's rename to the name it shows."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    await _require_recording_speaker_mutations_supported(db, recording)
    speaker = await _pending_speaker(db, recording, diarization_label)
    name, source = shown_name(speaker), speaker.name_source
    # Before the rename, which would otherwise mark it superseded.
    await _resolve_suggestion(
        db,
        recording,
        diarization_label,
        SPEAKER_SUGGESTION_STATUS_ACCEPTED,
        current_user.id,
    )
    confirmed = await apply_recording_speaker_update(
        recording_id,
        SpeakerUpdate(diarization_label=diarization_label, global_speaker_name=name),
        db=db,
        current_user=current_user,
        source="api",
    )
    _record_review(recording, diarization_label, "confirmed", source)
    return confirmed


@router.post(
    "/recordings/{recording_id}/speakers/{diarization_label}/suggestions/reject",
    response_model=List[RecordingSpeakerPublicRead],
)
async def dismiss_speaker_name(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dismiss an unconfirmed name ("Not Alex"); put back "Speaker N"."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    await _require_recording_speaker_mutations_supported(db, recording)
    speaker = await _pending_speaker(
        db, recording, diarization_label, allow_unlocked_voiceprint=True
    )
    source = speaker.name_source
    await _resolve_suggestion(
        db,
        recording,
        diarization_label,
        SPEAKER_SUGGESTION_STATUS_REJECTED,
        current_user.id,
    )
    # The dismissal is anchored on the speaker's canonical lines, which a
    # recording from before the canonical transcript gets here, as a rename
    # gives it.
    backfill = _canonical_transcript_writes_enabled() and (
        speakers_module.recording_ready_for_canonical_backfill(recording.status)
    )

    def _dismiss(sync_session) -> None:
        if backfill:
            from backend.utils.canonical_pipeline.startup import (
                ensure_canonical_backfill,
            )

            ensure_canonical_backfill(sync_session, recording.id)
        dismiss_pending_name(sync_session, speaker, current_user.id)

    await db.run_sync(_dismiss)
    await db.commit()
    _record_review(recording, diarization_label, "dismissed", source)
    refreshed = (
        (
            await db.execute(
                select(RecordingSpeaker)
                .where(RecordingSpeaker.recording_id == recording.id)
                .where(RecordingSpeaker.diarization_label == diarization_label)
                .options(selectinload(RecordingSpeaker.global_speaker))
            )
        )
        .scalars()
        .all()
    )
    return _serialize_recording_speakers(
        list(refreshed), recording_public_id=recording.public_id
    )
