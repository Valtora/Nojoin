import logging
import uuid
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.celery_app import celery_app
from backend.models.pipeline import SpeakerCorrectionScope
from backend.models.recording_public import (
    TranscriptPublicRead,
    serialize_transcript,
)
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.canonical_pipeline import (
    apply_compatibility_segment_replace,
    ensure_canonical_backfill,
    recording_ready_for_canonical_backfill,
)
from backend.utils.canonical_pipeline import (
    update_utterance_speaker as update_canonical_utterance_speaker,
)
from backend.utils.canonical_pipeline import (
    update_utterance_text as update_canonical_utterance_text,
)
from backend.utils.config_manager import is_meeting_edge_enabled
from backend.utils.speaker_assignment import (
    matches_speaker_name,
    reconcile_segment_assignment,
    segment_references_label,
)

from .helpers import (
    FindReplaceRequest,
    TranscriptSegmentSpeakerUpdate,
    TranscriptSegmentsUpdate,
    TranscriptSegmentTextUpdate,
    _apply_find_replace,
    _canonical_transcript_writes_enabled,
    _dispatch_meeting_edge_refresh,
    _get_owned_recording,
    _get_recording_speaker_display_name,
    _require_recording_transcript_mutations_supported,
)
from .router import router

logger = logging.getLogger(__name__)


@router.put("/{recording_id}/segments/{segment_index}")
async def update_segment_speaker(
    recording_id: str,
    segment_index: int,
    update: TranscriptSegmentSpeakerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the speaker for a specific transcript segment.
    Also updates the speaker embedding associations using the audio from this segment.
    """
    # 1. Fetch Recording and Transcript
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    # Fetch transcript with segments
    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    _require_recording_transcript_mutations_supported(recording)

    if segment_index < 0 or segment_index >= len(transcript.segments):
        raise HTTPException(status_code=400, detail="Invalid segment index")

    if _canonical_transcript_writes_enabled() and (
        transcript.segments[segment_index].get("id")
        or recording_ready_for_canonical_backfill(recording.status)
    ):
        await db.run_sync(
            lambda sync_session: ensure_canonical_backfill(sync_session, recording.id)
        )
        await db.commit()
        await db.refresh(transcript)
        if segment_index < 0 or segment_index >= len(transcript.segments):
            raise HTTPException(status_code=400, detail="Invalid segment index")

        canonical_segment = dict(transcript.segments[segment_index])
        utterance_id = canonical_segment.get("id")
        if not utterance_id:
            raise HTTPException(
                status_code=409, detail="Canonical utterance identifier is unavailable"
            )

        new_speaker_name = update.new_speaker_name.strip()
        if not new_speaker_name:
            raise HTTPException(status_code=400, detail="Speaker name cannot be empty")

        try:
            await db.run_sync(
                lambda sync_session: update_canonical_utterance_speaker(
                    sync_session,
                    recording_id=recording.id,
                    utterance_public_id=str(utterance_id),
                    new_speaker_name=new_speaker_name,
                    global_speaker_id=update.global_speaker_id,
                    diarization_label=update.diarization_label,
                    scope=SpeakerCorrectionScope.UTTERANCE_ONLY,
                    actor_user_id=current_user.id,
                )
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        await db.commit()
        await db.refresh(transcript)
        refreshed_segment = dict(transcript.segments[segment_index])
        record_pipeline_metric(
            stage="speaker_correction_applied",
            recording_id=recording.id,
            payload={
                "correction_kind": "segment_speaker",
                "segment_index": segment_index,
                "utterance_id": utterance_id,
                "old_label": canonical_segment.get("speaker"),
                "new_label": refreshed_segment.get("speaker"),
                "duration_s": round(
                    float(refreshed_segment.get("end", 0.0))
                    - float(refreshed_segment.get("start", 0.0)),
                    3,
                ),
            },
            log=logger,
        )
        _dispatch_meeting_edge_refresh(
            recording.id,
            enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
        )

        try:
            target_speaker_id = refreshed_segment.get("recording_speaker_id")
            if target_speaker_id is not None:
                start = refreshed_segment["start"]
                end = refreshed_segment["end"]
                duration = end - start
                if duration > 0.5:
                    celery_app.send_task(
                        "backend.worker.tasks.update_speaker_embedding_task",
                        args=[recording.id, start, end, target_speaker_id],
                    )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to dispatch embedding update task: {e}")

        return {"status": "success", "speaker": refreshed_segment.get("speaker")}

    segment = dict(transcript.segments[segment_index])
    old_label = segment.get("speaker")
    new_speaker_name = update.new_speaker_name.strip()

    if not new_speaker_name:
        raise HTTPException(status_code=400, detail="Speaker name cannot be empty")

    # 2. Resolve Target Speaker
    # Determine the diarization_label to assign to the segment.

    target_label = None
    target_recording_speaker = None

    # Check if speaker exists in this recording (by name or global name)
    # Fetch all recording speakers for name comparison
    stmt = (
        select(RecordingSpeaker)
        .where(RecordingSpeaker.recording_id == recording.id)
        .options(selectinload(RecordingSpeaker.global_speaker))
    )
    result = await db.execute(stmt)
    recording_speakers = result.scalars().all()
    target_speaker_id: Optional[int] = None
    current_recording_speaker = next(
        (
            recording_speaker
            for recording_speaker in recording_speakers
            if recording_speaker.diarization_label == old_label
        ),
        None,
    )

    current_global_speaker_id = (
        current_recording_speaker.global_speaker_id
        if current_recording_speaker
        else None
    )
    current_speaker_name = (
        _get_recording_speaker_display_name(current_recording_speaker)
        if current_recording_speaker
        else old_label
    )

    if update.diarization_label == old_label:
        return {"status": "unchanged", "speaker": old_label}

    if (
        update.global_speaker_id is not None
        and current_global_speaker_id == update.global_speaker_id
    ):
        return {"status": "unchanged", "speaker": old_label}

    if (
        update.diarization_label is None
        and update.global_speaker_id is None
        and matches_speaker_name(current_speaker_name, new_speaker_name)
    ):
        return {"status": "unchanged", "speaker": old_label}

    if update.diarization_label is not None:
        target_recording_speaker = next(
            (
                recording_speaker
                for recording_speaker in recording_speakers
                if recording_speaker.diarization_label == update.diarization_label
            ),
            None,
        )

        if not target_recording_speaker:
            raise HTTPException(
                status_code=404, detail="Speaker not found in recording"
            )

        target_label = target_recording_speaker.diarization_label
        target_speaker_id = target_recording_speaker.id

    if target_label is None and update.global_speaker_id is not None:
        stmt = select(GlobalSpeaker).where(
            GlobalSpeaker.id == update.global_speaker_id,
            GlobalSpeaker.user_id == current_user.id,
        )
        result = await db.execute(stmt)
        global_speaker = result.scalar_one_or_none()

        if not global_speaker:
            raise HTTPException(status_code=404, detail="Global speaker not found")

        target_recording_speaker = next(
            (
                rs
                for rs in recording_speakers
                if rs.global_speaker_id == global_speaker.id
            ),
            None,
        )

        if target_recording_speaker:
            target_label = target_recording_speaker.diarization_label
            target_speaker_id = target_recording_speaker.id
        else:
            target_recording_speaker = next(
                (
                    recording_speaker
                    for recording_speaker in recording_speakers
                    if recording_speaker.global_speaker_id is None
                    and (
                        matches_speaker_name(
                            recording_speaker.local_name, new_speaker_name
                        )
                        or matches_speaker_name(
                            recording_speaker.name, new_speaker_name
                        )
                    )
                ),
                None,
            )

            if target_recording_speaker:
                target_recording_speaker.global_speaker_id = global_speaker.id
                target_recording_speaker.local_name = None
                target_recording_speaker.name = None
                db.add(target_recording_speaker)
                await db.flush()
                target_label = target_recording_speaker.diarization_label
                target_speaker_id = target_recording_speaker.id
            else:
                target_label = f"MANUAL_{uuid.uuid4().hex[:8]}"
                target_recording_speaker = RecordingSpeaker(
                    recording_id=recording.id,
                    diarization_label=target_label,
                    global_speaker_id=global_speaker.id,
                    name=None,
                )
                db.add(target_recording_speaker)
                await db.flush()
                target_speaker_id = target_recording_speaker.id

    # Try to find a local match when no global speaker was explicitly selected.
    if target_label is None:
        for rs in recording_speakers:
            if matches_speaker_name(rs.local_name, new_speaker_name):
                target_label = rs.diarization_label
                target_recording_speaker = rs
                target_speaker_id = rs.id
                break

            if matches_speaker_name(rs.name, new_speaker_name):
                target_label = rs.diarization_label
                target_recording_speaker = rs
                target_speaker_id = rs.id
                break

            if matches_speaker_name(
                rs.global_speaker.name if rs.global_speaker else None,
                new_speaker_name,
            ):
                target_label = rs.diarization_label
                target_recording_speaker = rs
                target_speaker_id = rs.id
                break

    if target_label is None:
        target_label = f"MANUAL_{uuid.uuid4().hex[:8]}"
        target_recording_speaker = RecordingSpeaker(
            recording_id=recording.id,
            diarization_label=target_label,
            local_name=new_speaker_name,
            name=None,
        )
        db.add(target_recording_speaker)
        await db.flush()
        target_speaker_id = target_recording_speaker.id

    # 3. Update Transcript Segment
    updated_segments = [dict(entry) for entry in transcript.segments]
    reconcile_segment_assignment(
        updated_segments, segment_index, old_label, target_label
    )
    updated_segments[segment_index]["speaker_manually_edited"] = True
    transcript.segments = updated_segments
    flag_modified(transcript, "segments")
    db.add(transcript)

    # 5. Cleanup Old Speaker (if unused)
    if old_label and old_label != target_label:
        is_used = any(
            segment_references_label(entry, old_label) for entry in transcript.segments
        )

        if not is_used:
            # Delete the RecordingSpeaker entry
            stmt = select(RecordingSpeaker).where(
                RecordingSpeaker.recording_id == recording.id,
                RecordingSpeaker.diarization_label == old_label,
            )
            result = await db.execute(stmt)
            old_speaker_entry = result.scalar_one_or_none()

            if old_speaker_entry:
                await db.delete(old_speaker_entry)
                # Note: We don't delete the GlobalSpeaker, just the local association

    await db.commit()
    record_pipeline_metric(
        stage="speaker_correction_applied",
        recording_id=recording.id,
        payload={
            "correction_kind": "segment_speaker",
            "segment_index": segment_index,
            "old_label": old_label,
            "new_label": target_label,
            "duration_s": round(
                float(segment.get("end", 0.0)) - float(segment.get("start", 0.0)), 3
            ),
        },
        log=logger,
    )
    _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
    )

    # 6. Update Embeddings (Active Learning)
    try:
        if target_speaker_id is not None:
            start = segment["start"]
            end = segment["end"]
            duration = end - start

            if duration > 0.5:
                celery_app.send_task(
                    "backend.worker.tasks.update_speaker_embedding_task",
                    args=[
                        recording.id,
                        start,
                        end,
                        target_speaker_id,
                    ],
                )
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to dispatch embedding update task: {e}")

    return {"status": "success", "speaker": target_label}


@router.put(
    "/{recording_id}/segments/{segment_index}/text", response_model=TranscriptPublicRead
)
async def update_transcript_segment_text(
    recording_id: str,
    segment_index: int,
    update: TranscriptSegmentTextUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the text content of a specific transcript segment.
    """
    # 0. Check Ownership
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    # 1. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    _require_recording_transcript_mutations_supported(recording)

    if segment_index < 0 or segment_index >= len(transcript.segments):
        raise HTTPException(status_code=400, detail="Invalid segment index")

    if _canonical_transcript_writes_enabled() and (
        transcript.segments[segment_index].get("id")
        or recording_ready_for_canonical_backfill(recording.status)
    ):
        await db.run_sync(
            lambda sync_session: ensure_canonical_backfill(sync_session, recording.id)
        )
        await db.commit()
        await db.refresh(transcript)
        if segment_index < 0 or segment_index >= len(transcript.segments):
            raise HTTPException(status_code=400, detail="Invalid segment index")

        utterance_id = transcript.segments[segment_index].get("id")
        if not utterance_id:
            raise HTTPException(
                status_code=409, detail="Canonical utterance identifier is unavailable"
            )

        try:
            await db.run_sync(
                lambda sync_session: update_canonical_utterance_text(
                    sync_session,
                    recording_id=recording.id,
                    utterance_public_id=str(utterance_id),
                    text=update.text,
                    actor_user_id=current_user.id,
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
                "segment_index": segment_index,
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

    # 2. Update Segment
    updated_segments = [dict(entry) for entry in transcript.segments]
    updated_segments[segment_index]["text"] = update.text
    updated_segments[segment_index]["text_manually_edited"] = True
    transcript.segments = updated_segments
    flag_modified(transcript, "segments")

    # 3. Reconstruct Full Text
    full_text = " ".join([s["text"] for s in transcript.segments])
    transcript.text = full_text

    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    record_pipeline_metric(
        stage="transcript_text_correction_applied",
        recording_id=recording.id,
        payload={
            "segment_index": segment_index,
            "text_chars": len(update.text),
        },
        log=logger,
    )
    _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
    )

    return serialize_transcript(transcript, recording_public_id=recording.public_id)


@router.post("/{recording_id}/replace", response_model=TranscriptPublicRead)
async def find_and_replace(
    recording_id: str,
    replace_request: FindReplaceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Find and replace text across the entire transcript AND meeting notes.
    This ensures consistency between the diarized transcript and generated notes.
    """
    # 0. Check Ownership
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    # 1. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    _require_recording_transcript_mutations_supported(recording)

    find_text = replace_request.find_text
    replace_text = replace_request.replace_text
    case_sensitive = replace_request.case_sensitive
    use_regex = replace_request.use_regex

    if not find_text:
        raise HTTPException(status_code=400, detail="Find text cannot be empty")

    # 2. Apply find/replace to both transcript and notes
    _apply_find_replace(transcript, find_text, replace_text, case_sensitive, use_regex)

    if (
        _canonical_transcript_writes_enabled()
        and recording_ready_for_canonical_backfill(recording.status)
    ):
        await db.run_sync(
            lambda sync_session: ensure_canonical_backfill(sync_session, recording.id)
        )
        await db.run_sync(
            lambda sync_session: apply_compatibility_segment_replace(
                sync_session,
                recording_id=recording.id,
                segments=[dict(segment) for segment in (transcript.segments or [])],
            )
        )

    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
    )

    return serialize_transcript(transcript, recording_public_id=recording.public_id)


@router.put("/{recording_id}/segments", response_model=TranscriptPublicRead)
async def update_transcript_segments(
    recording_id: str,
    update: TranscriptSegmentsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bulk update all segments of a transcript.
    Useful for Undo/Redo operations involving multiple segments.
    """
    # 0. Check Ownership
    recording = await _get_owned_recording(db, recording_id, current_user.id)

    # 1. Fetch Transcript
    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")

    _require_recording_transcript_mutations_supported(recording)

    if _canonical_transcript_writes_enabled() and (
        any(segment.get("id") for segment in update.segments)
        or recording_ready_for_canonical_backfill(recording.status)
    ):
        await db.run_sync(
            lambda sync_session: ensure_canonical_backfill(sync_session, recording.id)
        )
        await db.commit()
        await db.refresh(transcript)

        canonical_segments = [dict(segment) for segment in update.segments]
        if canonical_segments and not any(
            segment.get("id") for segment in canonical_segments
        ):
            if len(canonical_segments) == len(transcript.segments):
                for index, segment in enumerate(canonical_segments):
                    segment["id"] = transcript.segments[index].get("id")

        await db.run_sync(
            lambda sync_session: apply_compatibility_segment_replace(
                sync_session,
                recording_id=recording.id,
                segments=canonical_segments,
            )
        )

        await db.commit()
        await db.refresh(transcript)
        _dispatch_meeting_edge_refresh(
            recording.id,
            enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
        )

        return serialize_transcript(transcript, recording_public_id=recording.public_id)

    # 2. Update Segments
    transcript.segments = update.segments
    flag_modified(transcript, "segments")

    # 3. Reconstruct Full Text
    full_text = " ".join([s.get("text", "") for s in transcript.segments])
    transcript.text = full_text

    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)
    _dispatch_meeting_edge_refresh(
        recording.id,
        enabled=is_meeting_edge_enabled(getattr(current_user, "settings", None)),
    )

    return serialize_transcript(transcript, recording_public_id=recording.public_id)
