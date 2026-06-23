import logging
from typing import Optional

from fastapi import Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.models.recording_public import (
    TranscriptPublicRead,
    serialize_recording_speaker,
    serialize_transcript,
)
from backend.models.speaker import RecordingSpeaker
from backend.models.user import User
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.canonical_pipeline import (
    build_transient_utterance_payloads_from_segments,
    ensure_canonical_backfill,
    filter_recording_speakers_for_public_read,
    list_active_utterances,
    serialize_canonical_delta,
)
from backend.utils.canonical_pipeline import (
    update_utterance_speaker as update_canonical_utterance_speaker,
)
from backend.utils.canonical_pipeline import (
    update_utterance_text as update_canonical_utterance_text,
)
from backend.utils.config_manager import is_meeting_edge_enabled

from .helpers import (
    TranscriptSegmentSpeakerUpdate,
    TranscriptSegmentTextUpdate,
    TranscriptUtteranceListRead,
    TranscriptUtteranceRead,
    TranscriptUtteranceSpeakerPatch,
    TranscriptUtteranceTextPatch,
    _canonical_transcript_writes_enabled,
    _dispatch_meeting_edge_refresh,
    _find_segment_index_by_public_id,
    _get_owned_recording,
    _get_recording_transcript,
    _get_segment_revision,
    _require_recording_transcript_mutations_supported,
)
from .router import router
from .routes_segments import update_segment_speaker, update_transcript_segment_text

logger = logging.getLogger(__name__)


@router.get("/{recording_id}/utterances", response_model=TranscriptUtteranceListRead)
async def get_transcript_utterances(
    recording_id: str,
    after_revision: Optional[int] = Query(default=None, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript = await _get_recording_transcript(db, recording.id)

    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")

    if not _canonical_transcript_writes_enabled():
        utterances = build_transient_utterance_payloads_from_segments(transcript)
        if after_revision is not None and after_revision >= 0:
            utterances = []

        speakers_result = await db.execute(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording.id)
            .options(selectinload(RecordingSpeaker.global_speaker))
        )
        speakers = speakers_result.scalars().all()
        speakers = await db.run_sync(
            lambda sync_session: filter_recording_speakers_for_public_read(
                sync_session,
                recording.id,
                speakers,
            )
        )

        return TranscriptUtteranceListRead(
            recording_id=recording.public_id,
            revision=0,
            utterances=[TranscriptUtteranceRead(**payload) for payload in utterances],
            tombstones=[],
            speakers=[
                serialize_recording_speaker(
                    speaker,
                    recording_public_id=recording.public_id,
                )
                for speaker in speakers
            ],
        )

    revision, utterances, tombstones = await db.run_sync(
        lambda sync_session: (
            ensure_canonical_backfill(sync_session, recording.id),
            serialize_canonical_delta(
                sync_session,
                recording.id,
                after_revision=after_revision,
            ),
        )[1]
    )
    await db.commit()
    await db.refresh(transcript)

    if not utterances and transcript.segments:
        utterances = build_transient_utterance_payloads_from_segments(transcript)
        if after_revision is not None and after_revision >= 0:
            utterances = []
    elif after_revision is not None and revision and after_revision >= revision:
        utterances = []
        tombstones = []

    speakers_result = await db.execute(
        select(RecordingSpeaker)
        .where(RecordingSpeaker.recording_id == recording.id)
        .options(selectinload(RecordingSpeaker.global_speaker))
    )
    speakers = speakers_result.scalars().all()
    speakers = await db.run_sync(
        lambda sync_session: filter_recording_speakers_for_public_read(
            sync_session,
            recording.id,
            speakers,
        )
    )

    return TranscriptUtteranceListRead(
        recording_id=recording.public_id,
        revision=revision,
        utterances=[TranscriptUtteranceRead(**payload) for payload in utterances],
        tombstones=tombstones,
        speakers=[
            serialize_recording_speaker(
                speaker,
                recording_public_id=recording.public_id,
            )
            for speaker in speakers
        ],
    )


@router.patch(
    "/{recording_id}/utterances/{utterance_id}/text",
    response_model=TranscriptPublicRead,
)
async def update_transcript_utterance_text(
    recording_id: str,
    utterance_id: str,
    update: TranscriptUtteranceTextPatch,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript = await _get_recording_transcript(db, recording.id)

    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")

    _require_recording_transcript_mutations_supported(recording)

    if not _canonical_transcript_writes_enabled():
        segment_index = _find_segment_index_by_public_id(transcript, utterance_id)
        if segment_index is None:
            raise HTTPException(status_code=404, detail="Utterance not found")
        segment = dict(transcript.segments[segment_index])
        if (
            update.expected_revision is not None
            and _get_segment_revision(segment) != update.expected_revision
        ):
            raise HTTPException(status_code=409, detail="Utterance revision conflict")
        return await update_transcript_segment_text(
            recording_id,
            segment_index,
            TranscriptSegmentTextUpdate(text=update.text),
            db,
            current_user,
        )

    await db.run_sync(
        lambda sync_session: ensure_canonical_backfill(sync_session, recording.id)
    )
    if not await db.run_sync(
        lambda sync_session: bool(list_active_utterances(sync_session, recording.id))
    ):
        raise HTTPException(
            status_code=409,
            detail="Canonical utterances are not available for this recording",
        )

    try:
        await db.run_sync(
            lambda sync_session: update_canonical_utterance_text(
                sync_session,
                recording_id=recording.id,
                utterance_public_id=utterance_id,
                text=update.text,
                actor_user_id=current_user.id,
                expected_revision=update.expected_revision,
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(transcript)
    record_pipeline_metric(
        stage="transcript_text_correction_applied",
        recording_id=recording.id,
        payload={
            "utterance_id": utterance_id,
            "text_chars": len(update.text),
        },
        log=logger,
    )
    _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
    )
    return serialize_transcript(transcript, recording_public_id=recording.public_id)


@router.patch(
    "/{recording_id}/utterances/{utterance_id}/speaker",
    response_model=TranscriptPublicRead,
)
async def update_transcript_utterance_speaker(
    recording_id: str,
    utterance_id: str,
    update: TranscriptUtteranceSpeakerPatch,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript = await _get_recording_transcript(db, recording.id)

    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if not update.new_speaker_name.strip():
        raise HTTPException(status_code=400, detail="Speaker name cannot be empty")

    _require_recording_transcript_mutations_supported(recording)

    if not _canonical_transcript_writes_enabled():
        segment_index = _find_segment_index_by_public_id(transcript, utterance_id)
        if segment_index is None:
            raise HTTPException(status_code=404, detail="Utterance not found")
        segment = dict(transcript.segments[segment_index])
        if (
            update.expected_revision is not None
            and _get_segment_revision(segment) != update.expected_revision
        ):
            raise HTTPException(status_code=409, detail="Utterance revision conflict")
        await update_segment_speaker(
            recording_id,
            segment_index,
            TranscriptSegmentSpeakerUpdate(
                new_speaker_name=update.new_speaker_name,
                global_speaker_id=update.global_speaker_id,
                diarization_label=update.diarization_label,
            ),
            db,
            current_user,
        )
        refreshed_transcript = await _get_recording_transcript(db, recording.id)
        if refreshed_transcript is None:
            raise HTTPException(status_code=404, detail="Transcript not found")
        return serialize_transcript(
            refreshed_transcript, recording_public_id=recording.public_id
        )

    await db.run_sync(
        lambda sync_session: ensure_canonical_backfill(sync_session, recording.id)
    )
    if not await db.run_sync(
        lambda sync_session: bool(list_active_utterances(sync_session, recording.id))
    ):
        raise HTTPException(
            status_code=409,
            detail="Canonical utterances are not available for this recording",
        )

    try:
        await db.run_sync(
            lambda sync_session: update_canonical_utterance_speaker(
                sync_session,
                recording_id=recording.id,
                utterance_public_id=utterance_id,
                new_speaker_name=update.new_speaker_name.strip(),
                global_speaker_id=update.global_speaker_id,
                diarization_label=update.diarization_label,
                scope=update.scope,
                actor_user_id=current_user.id,
                expected_revision=update.expected_revision,
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(transcript)
    updated_segment = next(
        (
            segment
            for segment in (transcript.segments or [])
            if str(segment.get("id")) == utterance_id
        ),
        None,
    )
    record_pipeline_metric(
        stage="speaker_correction_applied",
        recording_id=recording.id,
        payload={
            "correction_kind": "utterance_speaker",
            "utterance_id": utterance_id,
            "scope": update.scope.value,
            "new_label": updated_segment.get("speaker") if updated_segment else None,
        },
        log=logger,
    )
    _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
    )
    return serialize_transcript(transcript, recording_public_id=recording.public_id)
