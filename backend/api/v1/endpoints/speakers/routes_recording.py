import logging
import re
import uuid
from typing import List
from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlalchemy.orm.attributes import flag_modified

from backend.api.deps import get_db, get_current_user
from backend.models.user import User
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.recording import Recording
from backend.models.recording_public import RecordingPublicRead, RecordingSpeakerPublicRead, serialize_recording, serialize_recording_speaker
from backend.models.transcript import Transcript
from backend.models.pipeline import SpeakerCorrectionEventType, SpeakerCorrectionScope
from backend.utils.canonical_pipeline import (
    record_recording_speaker_corrections,
    update_recording_speaker_identity,
)
from backend.utils.speaker_name_suggestions import (
    SPEAKER_SUGGESTION_STATUS_ACCEPTED,
    SPEAKER_SUGGESTION_STATUS_REJECTED,
    resolve_pending_transcript_speaker_suggestion,
    supersede_pending_transcript_speaker_suggestions,
)
from backend.processing.embedding import merge_embeddings

from .router import router
from .helpers import (
    SpeakerUpdate,
    MergeRequestLabels,
    SpeakerColorUpdate,
    SpeakerSplitRequest,
    _get_owned_recording,
    _require_recording_speaker_mutations_supported,
    _canonical_transcript_writes_enabled,
    _load_segments_for_speaker_work,
    _persist_segments_for_speaker_work,
    _serialize_recording_speakers,
    _mark_pending_speaker_suggestions_superseded,
    _merge_local_speakers,
)
import backend.api.v1.endpoints.speakers as speakers_module

logger = logging.getLogger(__name__)


@router.put("/recordings/{recording_id}", response_model=List[RecordingSpeakerPublicRead])
async def update_recording_speaker(
    recording_id: str,
    update: SpeakerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update a speaker label in a recording with a name.
    
    - If the name matches an existing Global Speaker, link to it
    - Otherwise, set as local_name (local to this recording only)
    - Does NOT auto-create global speakers
    """
    logger.info(f"Updating speaker for recording {recording_id}: {update}")
    
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    if not recording:
        logger.error(f"Recording {recording_id} not found")
        raise HTTPException(status_code=404, detail="Recording not found")

    _require_recording_speaker_mutations_supported(recording)

    # Check if a Global Speaker with this name already exists
    statement = select(GlobalSpeaker).where(GlobalSpeaker.name == update.global_speaker_name, GlobalSpeaker.user_id == current_user.id)
    result = await db.execute(statement)
    global_speaker = result.scalar_one_or_none()

    if _canonical_transcript_writes_enabled() and speakers_module.recording_ready_for_canonical_backfill(recording.status):
        try:
            await db.run_sync(
                lambda sync_session: update_recording_speaker_identity(
                    sync_session,
                    recording_id=recording.id,
                    diarization_label=update.diarization_label,
                    new_speaker_name=update.global_speaker_name,
                    target_global_speaker_id=(global_speaker.id if global_speaker is not None else None),
                    actor_user_id=current_user.id,
                    merge_global_embedding_alpha=(0.3 if global_speaker is not None else None),
                    source="api",
                )
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        superseded_suggestion_count = await _mark_pending_speaker_suggestions_superseded(
            db,
            recording_id=recording.id,
            diarization_labels=[update.diarization_label],
            actor_user_id=current_user.id,
            reason="manual_name_change",
        )
        await db.commit()
        refreshed_result = await db.execute(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording.id)
            .where(RecordingSpeaker.diarization_label == update.diarization_label)
            .options(selectinload(RecordingSpeaker.global_speaker))
        )
        refreshed_speakers = refreshed_result.scalars().all()

        speakers_module.record_pipeline_metric(
            stage="speaker_correction_applied",
            recording_id=recording.id,
            payload={
                "correction_kind": "recording_speaker_rename",
                "diarization_label": update.diarization_label,
                "new_name": update.global_speaker_name,
                "matched_global_speaker": global_speaker is not None,
                "segments_repaired": True,
                "superseded_suggestion_count": superseded_suggestion_count,
            },
            log=logger,
        )

        return _serialize_recording_speakers(
            refreshed_speakers,
            recording_public_id=recording.public_id,
        )
        
    # Update RecordingSpeakers
    stmt = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == update.diarization_label
    )
    result = await db.execute(stmt)
    recording_speakers = result.scalars().all()
    
    if not recording_speakers:
        raise HTTPException(status_code=404, detail=f"No speakers found with label {update.diarization_label} in this recording")

    old_display_names = {
        rs.id: (rs.local_name or rs.name or rs.diarization_label)
        for rs in recording_speakers
    }
        
    old_names = set()
    for rs in recording_speakers:
        if rs.local_name: old_names.add(rs.local_name)
        if rs.name: old_names.add(rs.name)
        live_match = re.match(r"^LIVE_(\d+)$", rs.diarization_label or "")
        if live_match:
            old_names.add(f"Speaker {int(live_match.group(1))}")
        old_names.add(update.global_speaker_name)

    for rs in recording_speakers:
        if global_speaker:
            rs.global_speaker_id = global_speaker.id
            rs.local_name = None
            rs.name = None
            
            if rs.embedding:
                if global_speaker.embedding:
                    if not global_speaker.is_voiceprint_locked:
                        global_speaker.embedding = merge_embeddings(global_speaker.embedding, rs.embedding, alpha=0.3)
                else:
                    global_speaker.embedding = rs.embedding
                db.add(global_speaker)
        else:
            rs.local_name = update.global_speaker_name
            rs.global_speaker_id = None
            rs.name = None
        
        db.add(rs)

    # Transcript Repair
    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()

    segments_updated = False
    if transcript:
        transcript_segments = await _load_segments_for_speaker_work(
            db,
            recording=recording,
            transcript=transcript,
        )
        if transcript_segments:
            new_segments = []
            for segment in transcript_segments:
                segment_copy = dict(segment)
                current_speaker = segment_copy.get("speaker")
                
                if current_speaker in old_names:
                    segment_copy["speaker"] = update.diarization_label
                    segments_updated = True
                
                new_segments.append(segment_copy)

            if segments_updated:
                await _persist_segments_for_speaker_work(
                    db,
                    recording=recording,
                    transcript=transcript,
                    segments=new_segments,
                )

        superseded_suggestion_count = len(
            supersede_pending_transcript_speaker_suggestions(
                transcript,
                diarization_labels=[update.diarization_label],
                reason="manual_name_change",
                actor_user_id=current_user.id,
            )
        )
        if superseded_suggestion_count:
            flag_modified(transcript, "speaker_name_suggestions")
            db.add(transcript)
    else:
        superseded_suggestion_count = 0

    segments_repaired = segments_updated

    if _canonical_transcript_writes_enabled() and speakers_module.recording_ready_for_canonical_backfill(recording.status):
        event_type = (
            SpeakerCorrectionEventType.LINK_GLOBAL_SPEAKER
            if global_speaker is not None
            else SpeakerCorrectionEventType.RENAME
        )
        await db.run_sync(
            lambda sync_session: record_recording_speaker_corrections(
                sync_session,
                recording_id=recording.id,
                target_recording_speaker_ids=[rs.id for rs in recording_speakers],
                actor_user_id=current_user.id,
                event_type=event_type,
                scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
                target_global_speaker_id=global_speaker.id if global_speaker is not None else None,
                payload_by_speaker_id={
                    rs.id: {
                        "old_name": old_display_names.get(rs.id),
                        "new_name": update.global_speaker_name,
                        "matched_global_speaker": global_speaker is not None,
                        "segments_repaired": segments_repaired,
                    }
                    for rs in recording_speakers
                },
            )
        )

    await db.commit()
    speakers_module.record_pipeline_metric(
        stage="speaker_correction_applied",
        recording_id=recording.id,
        payload={
            "correction_kind": "recording_speaker_rename",
            "diarization_label": update.diarization_label,
            "new_name": update.global_speaker_name,
            "matched_global_speaker": global_speaker is not None,
            "segments_repaired": segments_repaired,
            "superseded_suggestion_count": superseded_suggestion_count,
        },
        log=logger,
    )
    
    return _serialize_recording_speakers(
        recording_speakers,
        recording_public_id=recording.public_id,
    )


@router.post("/recordings/{recording_id}/speakers/{diarization_label}/suggestions/accept", response_model=dict)
async def accept_recording_speaker_suggestion(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)
    transcript = (
        await db.execute(select(Transcript).where(Transcript.recording_id == recording.id))
    ).scalar_one_or_none()
    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")

    suggestion = resolve_pending_transcript_speaker_suggestion(
        transcript,
        diarization_label=diarization_label,
        resolution=SPEAKER_SUGGESTION_STATUS_ACCEPTED,
        actor_user_id=current_user.id,
        reason="accepted_by_user",
    )
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Pending speaker suggestion not found")

    flag_modified(transcript, "speaker_name_suggestions")
    db.add(transcript)

    await update_recording_speaker(
        recording_id,
        SpeakerUpdate(
            diarization_label=diarization_label,
            global_speaker_name=str(suggestion.get("suggested_name", "")).strip(),
        ),
        db,
        current_user,
    )

    speakers_module.record_pipeline_metric(
        stage="speaker_name_suggestion_resolved",
        recording_id=recording.id,
        payload={
            "diarization_label": diarization_label,
            "resolution": SPEAKER_SUGGESTION_STATUS_ACCEPTED,
            "suggested_name": suggestion.get("suggested_name"),
            "origin": suggestion.get("origin"),
            "source": suggestion.get("source"),
            "provider": suggestion.get("provider"),
        },
        log=logger,
    )
    return {"ok": True}


@router.post("/recordings/{recording_id}/speakers/{diarization_label}/suggestions/reject", response_model=dict)
async def reject_recording_speaker_suggestion(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)
    transcript = (
        await db.execute(select(Transcript).where(Transcript.recording_id == recording.id))
    ).scalar_one_or_none()
    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")

    suggestion = resolve_pending_transcript_speaker_suggestion(
        transcript,
        diarization_label=diarization_label,
        resolution=SPEAKER_SUGGESTION_STATUS_REJECTED,
        actor_user_id=current_user.id,
        reason="rejected_by_user",
    )
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Pending speaker suggestion not found")

    flag_modified(transcript, "speaker_name_suggestions")
    db.add(transcript)
    await db.commit()

    speakers_module.record_pipeline_metric(
        stage="speaker_name_suggestion_resolved",
        recording_id=recording.id,
        payload={
            "diarization_label": diarization_label,
            "resolution": SPEAKER_SUGGESTION_STATUS_REJECTED,
            "suggested_name": suggestion.get("suggested_name"),
            "origin": suggestion.get("origin"),
            "source": suggestion.get("source"),
            "provider": suggestion.get("provider"),
        },
        log=logger,
    )
    return {"ok": True}


@router.post("/recordings/{recording_id}/speakers/{diarization_label}/promote", response_model=RecordingSpeakerPublicRead)
async def promote_speaker_to_global(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Promote a recording speaker to the global speaker library.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    recording_speaker = result.scalar_one_or_none()
    
    if not recording_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in this recording")
    
    speaker_name = recording_speaker.local_name or recording_speaker.name or recording_speaker.diarization_label
    
    placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE)
    if placeholder_pattern.match(speaker_name):
        raise HTTPException(
            status_code=400, 
            detail="Cannot promote a speaker with a placeholder name. Please rename them first."
        )
    
    statement = select(GlobalSpeaker).where(GlobalSpeaker.name == speaker_name, GlobalSpeaker.user_id == current_user.id)
    result = await db.execute(statement)
    existing_global = result.scalar_one_or_none()
    
    if existing_global:
        target_global_speaker_id = existing_global.id
    else:
        global_speaker = GlobalSpeaker(
            name=speaker_name,
            embedding=recording_speaker.embedding,
            user_id=current_user.id
        )
        db.add(global_speaker)
        await db.flush()
        target_global_speaker_id = global_speaker.id

    if _canonical_transcript_writes_enabled() and speakers_module.recording_ready_for_canonical_backfill(recording.status):
        try:
            await db.run_sync(
                lambda sync_session: update_recording_speaker_identity(
                    sync_session,
                    recording_id=recording.id,
                    diarization_label=diarization_label,
                    new_speaker_name=speaker_name,
                    target_global_speaker_id=target_global_speaker_id,
                    actor_user_id=current_user.id,
                    merge_global_embedding_alpha=(0.5 if existing_global is not None else None),
                    event_type=(
                        SpeakerCorrectionEventType.LINK_GLOBAL_SPEAKER
                        if existing_global is not None
                        else SpeakerCorrectionEventType.PROMOTE_GLOBAL_SPEAKER
                    ),
                    source="api",
                )
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        await _mark_pending_speaker_suggestions_superseded(
            db,
            recording_id=recording.id,
            diarization_labels=[diarization_label],
            actor_user_id=current_user.id,
            reason="speaker_promoted_to_global",
        )
        await db.commit()
        refreshed_result = await db.execute(
            select(RecordingSpeaker)
            .where(RecordingSpeaker.recording_id == recording.id)
            .where(RecordingSpeaker.diarization_label == diarization_label)
            .options(selectinload(RecordingSpeaker.global_speaker))
        )
        refreshed_recording_speaker = refreshed_result.scalar_one_or_none()
        if refreshed_recording_speaker is None:
            raise HTTPException(status_code=404, detail="Speaker not found in this recording")

        return serialize_recording_speaker(
            refreshed_recording_speaker,
            recording_public_id=recording.public_id,
        )

    recording_speaker.global_speaker_id = target_global_speaker_id
    recording_speaker.local_name = None
    recording_speaker.name = None
    db.add(recording_speaker)

    transcript = (
        await db.execute(select(Transcript).where(Transcript.recording_id == recording.id))
    ).scalar_one_or_none()
    if transcript is not None:
        changed = supersede_pending_transcript_speaker_suggestions(
            transcript,
            diarization_labels=[diarization_label],
            reason="speaker_promoted_to_global",
            actor_user_id=current_user.id,
        )
        if changed:
            flag_modified(transcript, "speaker_name_suggestions")
            db.add(transcript)

    await db.commit()
    await db.refresh(recording_speaker)
    
    return serialize_recording_speaker(
        recording_speaker,
        recording_public_id=recording.public_id,
    )


@router.post("/recordings/{recording_id}/merge", response_model=RecordingPublicRead)
async def merge_recording_speakers(
    recording_id: str,
    merge_data: MergeRequestLabels,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Merge two speakers in a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)

    if merge_data.source_speaker_label == merge_data.target_speaker_label:
        raise HTTPException(status_code=400, detail="Cannot merge speaker into itself")

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == merge_data.source_speaker_label
    )
    result = await db.execute(statement)
    source_speaker = result.scalar_one_or_none()

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == merge_data.target_speaker_label
    )
    result = await db.execute(statement)
    target_speaker = result.scalar_one_or_none()

    if not source_speaker:
        raise HTTPException(status_code=404, detail=f"Source speaker '{merge_data.source_speaker_label}' not found")
    
    if not target_speaker:
        raise HTTPException(status_code=404, detail=f"Target speaker '{merge_data.target_speaker_label}' not found")

    await _merge_local_speakers(
        db, 
        recording.id,
        merge_data.source_speaker_label, 
        merge_data.target_speaker_label,
        actor_user_id=current_user.id,
    )

    await _mark_pending_speaker_suggestions_superseded(
        db,
        recording_id=recording.id,
        diarization_labels=[
            merge_data.source_speaker_label,
            merge_data.target_speaker_label,
        ],
        actor_user_id=current_user.id,
        reason="manual_speaker_merge",
    )

    await db.flush()
    await db.commit()
    
    await db.refresh(recording)
    return serialize_recording(recording)


@router.delete("/recordings/{recording_id}/speakers/{diarization_label}")
async def delete_recording_speaker(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Remove a speaker from a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)

    statement = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()

    if transcript is not None:
        transcript_segments = await _load_segments_for_speaker_work(
            db,
            recording=recording,
            transcript=transcript,
        )
        if transcript_segments:
            updated_segments = []
            changed = False
            for segment in transcript_segments:
                segment_copy = dict(segment)
                if segment_copy.get("speaker") == diarization_label:
                    segment_copy["speaker"] = "UNKNOWN"
                    changed = True
                updated_segments.append(segment_copy)

            if changed:
                await _persist_segments_for_speaker_work(
                    db,
                    recording=recording,
                    transcript=transcript,
                    segments=updated_segments,
                )

        suggestion_changes = supersede_pending_transcript_speaker_suggestions(
            transcript,
            diarization_labels=[diarization_label],
            reason="speaker_deleted",
            actor_user_id=current_user.id,
        )
        if suggestion_changes:
            flag_modified(transcript, "speaker_name_suggestions")
            db.add(transcript)

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    speaker_entry = result.scalar_one_or_none()

    if speaker_entry:
        await db.delete(speaker_entry)
    else:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")

    await db.commit()
    return {"ok": True}


@router.put("/recordings/{recording_id}/speakers/{label}/color", response_model=dict)
async def update_speaker_color(
    recording_id: str,
    label: str,
    update: SpeakerColorUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Update the color for a speaker within a single recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)

    stmt = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == label
    )
    result = await db.execute(stmt)
    recording_speaker = result.scalar_one_or_none()
    
    if not recording_speaker:
        raise HTTPException(status_code=404, detail=f"Speaker {label} not found in recording")

    recording_speaker.color = update.color
    db.add(recording_speaker)
    await db.commit()
    
    return {"status": "success", "color": update.color}


@router.post("/recordings/{recording_id}/speakers/{diarization_label}/split", response_model=List[RecordingSpeakerPublicRead])
async def split_local_speaker(
    recording_id: str,
    diarization_label: str,
    request: SpeakerSplitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Split a LOCAL speaker in a specific recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)

    stmt = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(stmt)
    source_speaker = result.scalar_one_or_none()
    if not source_speaker:
         raise HTTPException(status_code=404, detail="Source speaker not found")
         
    if not request.segments:
         raise HTTPException(status_code=400, detail="No segments provided")

    target_label = None
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording.id)
    result = await db.execute(stmt)
    all_rec_speakers = result.scalars().all()
    
    for rs in all_rec_speakers:
        if (rs.local_name == request.new_speaker_name or 
            rs.name == request.new_speaker_name or 
            rs.diarization_label == request.new_speaker_name):
            target_label = rs.diarization_label
            break
            
    if not target_label:
        target_label = f"SPLIT_{uuid.uuid4().hex[:8]}"
        new_rs = RecordingSpeaker(
            recording_id=recording.id,
            diarization_label=target_label,
            local_name=request.new_speaker_name,
        )
        db.add(new_rs)
        await db.flush()
    
    stmt = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(stmt)
    transcript = result.scalar_one_or_none()
    
    transcript_segments = await _load_segments_for_speaker_work(
        db,
        recording=recording,
        transcript=transcript,
    )
    if transcript and transcript_segments:
        segments_to_move = set()
        for s in request.segments:
            segments_to_move.add((s.recording_id, s.start, s.end))
            
        new_segments = []
        segments_updated = False
        
        for segment in transcript_segments:
            seg_copy = dict(segment)
            matches = False
            for r_id, start, end in segments_to_move:
                if r_id == recording.public_id and abs(segment["start"] - start) < 0.01 and abs(segment["end"] - end) < 0.01:
                    matches = True
                    break
            
            if matches:
                seg_copy["speaker"] = target_label
                segments_updated = True
                
            new_segments.append(seg_copy)
            
        if segments_updated:
            await _persist_segments_for_speaker_work(
                db,
                recording=recording,
                transcript=transcript,
                segments=new_segments,
            )
            
    await db.commit()
    
    stmt = select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording.id)
    result = await db.execute(stmt)
    return _serialize_recording_speakers(
        result.scalars().all(),
        recording_public_id=recording.public_id,
    )
