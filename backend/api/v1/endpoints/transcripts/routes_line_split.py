"""Publish a user-requested split at a genuine canonical word boundary."""

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.models.recording_public import TranscriptPublicRead, serialize_transcript
from backend.models.user import User
from backend.utils.canonical_pipeline.split import SplitCommand, split_utterance
from backend.utils.config_manager import is_meeting_edge_enabled

from .helpers import (
    TranscriptUtteranceSplit,
    _dispatch_meeting_edge_refresh,
    _get_owned_recording,
    _get_recording_transcript,
    _require_recording_transcript_mutations_supported,
)
from .router import router


@router.post(
    "/{recording_id}/utterances/{utterance_id}/split",
    response_model=TranscriptPublicRead,
)
async def split_transcript_utterance(
    recording_id: str,
    utterance_id: str,
    update: TranscriptUtteranceSplit,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TranscriptPublicRead:
    """Split only after the service validates every source fact under its fence."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_transcript_mutations_supported(recording)
    transcript = await _get_recording_transcript(db, recording.id)
    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    try:
        await db.run_sync(
            lambda session: split_utterance(
                session,
                SplitCommand(
                    recording.id,
                    utterance_id,
                    update.before_word_id,
                    update.expected_revision,
                    current_user.id,
                ),
            )
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await db.commit()
    await db.refresh(transcript)
    await _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
    )
    return serialize_transcript(transcript, recording_public_id=recording.public_id)
