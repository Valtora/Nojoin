import logging
import os
import re
from typing import Optional
from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_db, get_current_user
from backend.models.user import User
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.processing.embedding import (
    cosine_similarity,
    merge_embeddings,
    UI_SHOW_MATCH_THRESHOLD,
    UI_STRONG_MATCH_THRESHOLD,
    MARGIN_OF_VICTORY,
)

from .router import router
from .helpers import (
    VoiceprintAction,
    VoiceprintResult,
    _get_owned_recording,
    _require_recording_speaker_mutations_supported,
    _load_segments_for_speaker_work,
)
import backend.api.v1.endpoints.speakers as speakers_module
from backend.utils.embedding_audio import select_recording_audio_for_embedding

logger = logging.getLogger(__name__)


@router.post("/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/extract")
async def extract_voiceprint(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Extract a voiceprint (embedding) for a specific speaker in a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)
    
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    rec_speaker = result.scalar_one_or_none()
    
    if not rec_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")
    
    statement = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()
    
    transcript_segments = await _load_segments_for_speaker_work(
        db,
        recording=recording,
        transcript=transcript,
    )
    if not transcript or not transcript_segments:
        raise HTTPException(status_code=400, detail="No transcript segments found for this recording")
    
    speaker_segments = []
    speaker_name = rec_speaker.name or diarization_label
    
    for seg in transcript_segments:
        seg_speaker = seg.get("speaker", "")
        if seg_speaker == diarization_label or seg_speaker == speaker_name:
            speaker_segments.append((seg["start"], seg["end"]))
    
    if not speaker_segments:
        raise HTTPException(status_code=400, detail="No audio segments found for this speaker")
    
    device_str = speakers_module.config_manager.get("processing_device", "cpu")
    user_settings = current_user.settings or {}
    hf_token = user_settings.get("hf_token") or speakers_module.config_manager.get("hf_token")
    
    target_audio = select_recording_audio_for_embedding(recording)
    task = speakers_module.celery_app.send_task(
        "backend.worker.tasks.extract_embedding_task",
        args=[target_audio, speaker_segments, device_str, hf_token]
    )
    try:
        embedding = await run_in_threadpool(task.get, timeout=120)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Timeout or error extracting voiceprint: {e}")
        raise HTTPException(
            status_code=504,
            detail="Voiceprint extraction timed out or failed. Please try again."
        )
    
    if not embedding:
        raise HTTPException(status_code=500, detail="Failed to extract voiceprint from audio segments")
    
    rec_speaker.embedding = embedding
    db.add(rec_speaker)
    await db.commit()
    await db.refresh(rec_speaker)
    
    all_global_stmt = select(GlobalSpeaker)
    all_global_result = await db.execute(all_global_stmt)
    all_global_speakers = all_global_result.scalars().all()

    matched_speaker = None
    best_score = 0.0
    second_best_score = 0.0

    for gs in all_global_speakers:
        if gs.embedding:
            score = cosine_similarity(embedding, gs.embedding)
            if score > best_score:
                second_best_score = best_score
                best_score = score
                matched_speaker = gs
            elif score > second_best_score:
                second_best_score = score

    match_info = None
    if matched_speaker and best_score >= UI_SHOW_MATCH_THRESHOLD:
        margin_ok = (best_score - second_best_score) >= MARGIN_OF_VICTORY
        match_info = {
            "id": matched_speaker.id,
            "name": matched_speaker.name,
            "similarity_score": round(best_score, 3),
            "is_strong_match": best_score >= UI_STRONG_MATCH_THRESHOLD and margin_ok
        }
    
    all_speakers_list = [
        {"id": gs.id, "name": gs.name, "has_voiceprint": gs.embedding is not None}
        for gs in all_global_speakers
    ]
    
    return {
        "embedding_extracted": True,
        "matched_speaker": match_info,
        "all_global_speakers": all_speakers_list,
        "speaker_id": rec_speaker.id,
        "diarization_label": diarization_label
    }


@router.post("/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/apply")
async def apply_voiceprint_action(
    recording_id: str,
    diarization_label: str,
    action: VoiceprintAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Apply a voiceprint action after extraction.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)
    
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    rec_speaker = result.scalar_one_or_none()
    
    if not rec_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")
    
    if not rec_speaker.embedding:
        raise HTTPException(status_code=400, detail="No voiceprint extracted. Call /voiceprint/extract first.")
    
    embedding = rec_speaker.embedding
    
    if action.action == "create_new":
        if not action.new_speaker_name:
            raise HTTPException(status_code=400, detail="new_speaker_name is required for create_new action")
        
        placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE)
        if placeholder_pattern.match(action.new_speaker_name):
            raise HTTPException(status_code=400, detail="Cannot use a placeholder name for Global Speaker")
        
        existing_stmt = select(GlobalSpeaker).where(GlobalSpeaker.name == action.new_speaker_name)
        existing_result = await db.execute(existing_stmt)
        if existing_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="A Global Speaker with this name already exists")
        
        new_gs = GlobalSpeaker(name=action.new_speaker_name, embedding=embedding)
        db.add(new_gs)
        await db.commit()
        await db.refresh(new_gs)
        
        rec_speaker.global_speaker_id = new_gs.id
        rec_speaker.name = new_gs.name
        db.add(rec_speaker)
        await db.commit()
        
        return VoiceprintResult(
            success=True,
            has_voiceprint=True,
            matched_speaker={"id": new_gs.id, "name": new_gs.name},
            message=f"Created new Global Speaker: {new_gs.name}"
        )
    
    elif action.action == "link_existing" or action.action == "force_link":
        if not action.global_speaker_id:
            raise HTTPException(status_code=400, detail="global_speaker_id is required")
        
        gs = await db.get(GlobalSpeaker, action.global_speaker_id)
        if not gs:
            raise HTTPException(status_code=404, detail="Global Speaker not found")
        
        alpha = 0.4 if action.action == "force_link" else 0.3
        if gs.embedding:
            gs.embedding = merge_embeddings(gs.embedding, embedding, alpha=alpha)
        else:
            gs.embedding = embedding
        db.add(gs)
        
        rec_speaker.global_speaker_id = gs.id
        rec_speaker.name = gs.name
        db.add(rec_speaker)
        await db.commit()
        
        action_verb = "Force-linked" if action.action == "force_link" else "Linked"
        return VoiceprintResult(
            success=True,
            has_voiceprint=True,
            matched_speaker={"id": gs.id, "name": gs.name},
            message=f"{action_verb} to Global Speaker: {gs.name}"
        )
    
    elif action.action == "local_only":
        return VoiceprintResult(
            success=True,
            has_voiceprint=True,
            matched_speaker=None,
            message="Voiceprint saved locally (not linked to Global Speaker)"
        )
    
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action.action}")


@router.delete("/recordings/{recording_id}/speakers/{diarization_label}/voiceprint")
async def delete_voiceprint(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete the voiceprint (embedding) from a RecordingSpeaker.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == diarization_label
    )
    result = await db.execute(statement)
    rec_speaker = result.scalar_one_or_none()
    
    if not rec_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")
    
    rec_speaker.embedding = None
    db.add(rec_speaker)
    await db.commit()
    
    return {"ok": True, "message": "Voiceprint deleted"}


@router.post("/recordings/{recording_id}/voiceprints/extract-all")
async def extract_all_voiceprints(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Extract voiceprints for all speakers in a recording that don't have one.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)
    
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id
    )
    result = await db.execute(statement)
    all_speakers = result.scalars().all()
    
    speakers_needing_voiceprint = [s for s in all_speakers if not s.embedding]
    
    if not speakers_needing_voiceprint:
        return {
            "message": "All speakers already have voiceprints",
            "speakers_processed": 0,
            "results": []
        }
    
    statement = select(Transcript).where(Transcript.recording_id == recording.id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()
    
    transcript_segments = await _load_segments_for_speaker_work(
        db,
        recording=recording,
        transcript=transcript,
    )
    if not transcript or not transcript_segments:
        raise HTTPException(status_code=400, detail="No transcript segments found")
    
    all_global_stmt = select(GlobalSpeaker)
    all_global_result = await db.execute(all_global_stmt)
    all_global_speakers = list(all_global_result.scalars().all())
    
    device_str = speakers_module.config_manager.get("processing_device", "cpu")
    results = []
    
    for rec_speaker in speakers_needing_voiceprint:
        speaker_name = rec_speaker.name or rec_speaker.diarization_label
        
        speaker_segments = []
        for seg in transcript_segments:
            seg_speaker = seg.get("speaker", "")
            if seg_speaker == rec_speaker.diarization_label or seg_speaker == speaker_name:
                speaker_segments.append((seg["start"], seg["end"]))
        
        if not speaker_segments:
            results.append({
                "diarization_label": rec_speaker.diarization_label,
                "speaker_name": speaker_name,
                "success": False,
                "error": "No audio segments found"
            })
            continue
        
        user_settings = current_user.settings or {}
        hf_token = user_settings.get("hf_token") or speakers_module.config_manager.get("hf_token")
        
        target_audio = select_recording_audio_for_embedding(recording)
        task = speakers_module.celery_app.send_task(
            "backend.worker.tasks.extract_embedding_task",
            args=[target_audio, speaker_segments, device_str, hf_token]
        )
        try:
            embedding = await run_in_threadpool(task.get, timeout=120)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Timeout or error extracting voiceprint in batch: {e}")
            results.append({
                "diarization_label": rec_speaker.diarization_label,
                "speaker_name": speaker_name,
                "success": False,
                "error": "Extraction timed out or failed"
            })
            continue
        
        if not embedding:
            results.append({
                "diarization_label": rec_speaker.diarization_label,
                "speaker_name": speaker_name,
                "success": False,
                "error": "Extraction failed"
            })
            continue
        
        rec_speaker.embedding = embedding
        db.add(rec_speaker)
        
        matched_speaker = None
        best_score = 0.0
        
        for gs in all_global_speakers:
            if gs.embedding:
                score = cosine_similarity(embedding, gs.embedding)
                if score > best_score:
                    best_score = score
                    matched_speaker = gs
        
        match_info = None
        if matched_speaker and best_score >= 0.5:
            match_info = {
                "id": matched_speaker.id,
                "name": matched_speaker.name,
                "similarity_score": round(best_score, 3),
                "is_strong_match": best_score >= 0.65
            }
        
        results.append({
            "diarization_label": rec_speaker.diarization_label,
            "speaker_name": speaker_name,
            "speaker_id": rec_speaker.id,
            "success": True,
            "matched_speaker": match_info
        })
    
    await db.commit()
    
    all_speakers_list = [
        {"id": gs.id, "name": gs.name, "has_voiceprint": gs.embedding is not None}
        for gs in all_global_speakers
    ]
    
    return {
        "speakers_processed": len(results),
        "results": results,
        "all_global_speakers": all_speakers_list
    }
