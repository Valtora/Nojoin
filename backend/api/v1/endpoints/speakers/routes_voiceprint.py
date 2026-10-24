import logging
import re

from fastapi import Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

import backend.api.v1.endpoints.speakers as speakers_module
from backend.api.deps import get_current_user, get_db
from backend.core.task_dispatch import dispatch_task
from backend.models.recording_public import LibraryVoiceprintRead
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.embedding import (
    MARGIN_OF_VICTORY,
    UI_SHOW_MATCH_THRESHOLD,
    UI_STRONG_MATCH_THRESHOLD,
    RecordingVoiceprint,
    cosine_similarity,
    judge_drift,
    lock_blocks_user_link,
    offer_linked_voiceprint,
)
from backend.processing.embedding_version import EMBEDDING_METHOD_VERSION
from backend.utils.embedding_audio import select_recording_audio_for_embedding
from backend.utils.speaker_names.events import UserNameAct, record_user_link

from .helpers import (
    MEASURED_EXTRACTION_TASK,
    VoiceprintAction,
    VoiceprintResult,
    _get_owned_recording,
    _load_segments_for_speaker_work,
    _require_recording_speaker_mutations_supported,
)
from .router import router

logger = logging.getLogger(__name__)


async def _own_people(db: AsyncSession, user_id: int) -> list[GlobalSpeaker]:
    """The user's own people, the only ones a voiceprint is matched against."""
    result = await db.execute(
        select(GlobalSpeaker).where(GlobalSpeaker.user_id == user_id)
    )
    return list(result.scalars().all())


async def _get_own_person(
    db: AsyncSession, person_id: int, user_id: int
) -> GlobalSpeaker:
    """Load a person of the user's own library to link a speaker to.

    A person in someone else's library answers exactly as a missing one does.
    """
    person = await db.get(GlobalSpeaker, person_id)
    if person is None or person.user_id != user_id:
        raise HTTPException(status_code=404, detail="Global Speaker not found")
    return person


async def _judge_extracted_voiceprint(
    db: AsyncSession, rec_speaker: RecordingSpeaker
) -> None:
    """Judge a speaker's freshly extracted voiceprint against its person.

    Extracting offers the voiceprint to no one: the dialog may never apply a
    link. So the drift guard judges it against the person the speaker is
    already linked to (``judge_drift``), and a voice it refused stays out of
    that person's rebuilds until a verdict lets it in. The extraction can
    take minutes, so the link is read again first: if a rename moved the
    speaker to someone else meanwhile, the verdict is for them.
    """
    # Read without flushing: extract-all commits once, after every speaker,
    # and a flush here would hold each judged speaker's row lock until then.
    # The person is read again too: extract-all loaded everyone first.
    with db.no_autoflush:
        await db.refresh(rec_speaker, ["global_speaker_id", "drift_refusals"])
        person = (
            await db.get(
                GlobalSpeaker, rec_speaker.global_speaker_id, populate_existing=True
            )
            if rec_speaker.global_speaker_id
            else None
        )
    judge_drift(rec_speaker, person)


@router.post(
    "/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/extract"
)
async def extract_voiceprint(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Extract a voiceprint (embedding) for a specific speaker in a recording.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == diarization_label,
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
        raise HTTPException(
            status_code=400, detail="No transcript segments found for this recording"
        )

    speaker_segments = []
    speaker_name = rec_speaker.name or diarization_label

    for seg in transcript_segments:
        seg_speaker = seg.get("speaker", "")
        if seg_speaker == diarization_label or seg_speaker == speaker_name:
            speaker_segments.append((seg["start"], seg["end"]))

    if not speaker_segments:
        raise HTTPException(
            status_code=400, detail="No audio segments found for this speaker"
        )

    device_str = speakers_module.config_manager.get("processing_device", "cpu")
    user_settings = current_user.settings or {}
    hf_token = user_settings.get("hf_token") or speakers_module.config_manager.get(
        "hf_token"
    )

    target_audio = select_recording_audio_for_embedding(recording)
    task = await dispatch_task(
        MEASURED_EXTRACTION_TASK,
        args=[target_audio, speaker_segments, device_str, hf_token],
    )
    try:
        voiceprint = RecordingVoiceprint.from_task_result(
            await run_in_threadpool(task.get, timeout=120)
        )
    except Exception as e:  # noqa: BLE001 -- boundary: task.get re-raises whatever the worker raised; all of it is a 504 here
        logger.error(f"Timeout or error extracting voiceprint: {e}")
        raise HTTPException(
            status_code=504,
            detail="Voiceprint extraction timed out or failed. Please try again.",
        )

    if not voiceprint:
        raise HTTPException(
            status_code=500, detail="Failed to extract voiceprint from audio segments"
        )

    embedding = voiceprint.embedding
    rec_speaker.embedding = embedding
    rec_speaker.embedding_version = EMBEDDING_METHOD_VERSION
    rec_speaker.embedding_speech_seconds = voiceprint.speech_seconds
    await _judge_extracted_voiceprint(db, rec_speaker)
    db.add(rec_speaker)
    await db.commit()
    await db.refresh(rec_speaker)

    all_global_speakers = await _own_people(db, current_user.id)

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
            "is_strong_match": best_score >= UI_STRONG_MATCH_THRESHOLD and margin_ok,
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
        "diarization_label": diarization_label,
    }


async def _record_dialog_link(
    db: AsyncSession, speaker: RecordingSpeaker, person: GlobalSpeaker, actor_id: int
) -> None:
    """The dialog's link is the user's, as a rename's is (``record_user_link``)."""
    act = UserNameAct(person.name, person.id, actor_id, "api")
    await db.run_sync(lambda session: record_user_link(session, speaker, act))


@router.post("/recordings/{recording_id}/speakers/{diarization_label}/voiceprint/apply")
async def apply_voiceprint_action(
    recording_id: str,
    diarization_label: str,
    action: VoiceprintAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Apply a voiceprint action after extraction.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == diarization_label,
    )
    result = await db.execute(statement)
    rec_speaker = result.scalar_one_or_none()

    if not rec_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")

    if not rec_speaker.embedding:
        raise HTTPException(
            status_code=400,
            detail="No voiceprint extracted. Call /voiceprint/extract first.",
        )

    if action.action == "create_new":
        if not action.new_speaker_name:
            raise HTTPException(
                status_code=400,
                detail="new_speaker_name is required for create_new action",
            )

        placeholder_pattern = re.compile(
            r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE
        )
        if placeholder_pattern.match(action.new_speaker_name):
            raise HTTPException(
                status_code=400,
                detail="Cannot use a placeholder name for Global Speaker",
            )

        existing_stmt = select(GlobalSpeaker).where(
            GlobalSpeaker.name == action.new_speaker_name,
            GlobalSpeaker.user_id == current_user.id,
        )
        existing_result = await db.execute(existing_stmt)
        if existing_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400, detail="A Global Speaker with this name already exists"
            )

        new_gs = GlobalSpeaker(name=action.new_speaker_name, user_id=current_user.id)
        library_update = offer_linked_voiceprint(new_gs, rec_speaker, alpha=0.3)
        db.add(new_gs)
        await db.commit()
        await db.refresh(new_gs)

        rec_speaker.global_speaker_id = new_gs.id
        rec_speaker.name = new_gs.name
        await _record_dialog_link(db, rec_speaker, new_gs, current_user.id)
        await db.commit()

        return VoiceprintResult(
            success=True,
            has_voiceprint=True,
            matched_speaker={"id": new_gs.id, "name": new_gs.name},
            message=f"Created new Global Speaker: {new_gs.name}",
            library_voiceprint=LibraryVoiceprintRead.from_update(library_update),
        )

    elif action.action == "link_existing" or action.action == "force_link":
        if not action.global_speaker_id:
            raise HTTPException(status_code=400, detail="global_speaker_id is required")

        gs = await _get_own_person(db, action.global_speaker_id, current_user.id)

        force = action.action == "force_link"
        alpha = 0.4 if force else 0.3
        # Force link is the user's explicit override of the lock: it folds
        # through the drift guard as an unlocked link would, and the person
        # stays locked.
        library_update = offer_linked_voiceprint(
            gs,
            rec_speaker,
            alpha=alpha,
            locked=not force and lock_blocks_user_link(gs),
        )
        if library_update.changed:
            db.add(gs)

        rec_speaker.global_speaker_id = gs.id
        rec_speaker.name = gs.name
        await _record_dialog_link(db, rec_speaker, gs, current_user.id)
        await db.commit()

        action_verb = "Force-linked" if action.action == "force_link" else "Linked"
        return VoiceprintResult(
            success=True,
            has_voiceprint=True,
            matched_speaker={"id": gs.id, "name": gs.name},
            message=f"{action_verb} to Global Speaker: {gs.name}",
            library_voiceprint=LibraryVoiceprintRead.from_update(library_update),
        )

    elif action.action == "local_only":
        return VoiceprintResult(
            success=True,
            has_voiceprint=True,
            matched_speaker=None,
            message="Voiceprint saved locally (not linked to Global Speaker)",
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action.action}")


@router.delete("/recordings/{recording_id}/speakers/{diarization_label}/voiceprint")
async def delete_voiceprint(
    recording_id: str,
    diarization_label: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete the voiceprint (embedding) from a RecordingSpeaker.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    _require_recording_speaker_mutations_supported(recording)

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording.id,
        RecordingSpeaker.diarization_label == diarization_label,
    )
    result = await db.execute(statement)
    rec_speaker = result.scalar_one_or_none()

    if not rec_speaker:
        raise HTTPException(status_code=404, detail="Speaker not found in recording")

    rec_speaker.embedding = None
    rec_speaker.embedding_speech_seconds = None
    db.add(rec_speaker)
    await db.commit()

    return {"ok": True, "message": "Voiceprint deleted"}


@router.post("/recordings/{recording_id}/voiceprints/extract-all")
async def extract_all_voiceprints(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
            "results": [],
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

    all_global_speakers = await _own_people(db, current_user.id)

    device_str = speakers_module.config_manager.get("processing_device", "cpu")
    results = []

    for rec_speaker in speakers_needing_voiceprint:
        speaker_name = rec_speaker.name or rec_speaker.diarization_label

        speaker_segments = []
        for seg in transcript_segments:
            seg_speaker = seg.get("speaker", "")
            if (
                seg_speaker == rec_speaker.diarization_label
                or seg_speaker == speaker_name
            ):
                speaker_segments.append((seg["start"], seg["end"]))

        if not speaker_segments:
            results.append(
                {
                    "diarization_label": rec_speaker.diarization_label,
                    "speaker_name": speaker_name,
                    "success": False,
                    "error": "No audio segments found",
                }
            )
            continue

        user_settings = current_user.settings or {}
        hf_token = user_settings.get("hf_token") or speakers_module.config_manager.get(
            "hf_token"
        )

        target_audio = select_recording_audio_for_embedding(recording)
        task = await dispatch_task(
            MEASURED_EXTRACTION_TASK,
            args=[target_audio, speaker_segments, device_str, hf_token],
        )
        try:
            voiceprint = RecordingVoiceprint.from_task_result(
                await run_in_threadpool(task.get, timeout=120)
            )
        except Exception as e:  # noqa: BLE001 -- boundary: task.get re-raises whatever the worker raised; the batch records the failure and continues
            logger.error(f"Timeout or error extracting voiceprint in batch: {e}")
            results.append(
                {
                    "diarization_label": rec_speaker.diarization_label,
                    "speaker_name": speaker_name,
                    "success": False,
                    "error": "Extraction timed out or failed",
                }
            )
            continue

        if not voiceprint:
            results.append(
                {
                    "diarization_label": rec_speaker.diarization_label,
                    "speaker_name": speaker_name,
                    "success": False,
                    "error": "Extraction failed",
                }
            )
            continue

        embedding = voiceprint.embedding
        rec_speaker.embedding = embedding
        rec_speaker.embedding_version = EMBEDDING_METHOD_VERSION
        rec_speaker.embedding_speech_seconds = voiceprint.speech_seconds
        await _judge_extracted_voiceprint(db, rec_speaker)
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
                "is_strong_match": best_score >= 0.65,
            }

        results.append(
            {
                "diarization_label": rec_speaker.diarization_label,
                "speaker_name": speaker_name,
                "speaker_id": rec_speaker.id,
                "success": True,
                "matched_speaker": match_info,
            }
        )

    await db.commit()

    all_speakers_list = [
        {"id": gs.id, "name": gs.name, "has_voiceprint": gs.embedding is not None}
        for gs in all_global_speakers
    ]

    return {
        "speakers_processed": len(results),
        "results": results,
        "all_global_speakers": all_speakers_list,
    }
