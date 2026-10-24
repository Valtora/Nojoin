import logging
import time
from collections import defaultdict
from typing import List, Optional

from fastapi import Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

import backend.api.v1.endpoints.speakers as speakers_module
from backend.api.deps import get_current_user, get_db
from backend.core.task_dispatch import dispatch_task
from backend.models.people_tag import PeopleTag, PeopleTagLink
from backend.models.people_tag_schemas import PeopleTagRead
from backend.models.recording import (
    Recording,
    recording_supports_unified_mutations,
)
from backend.models.recording_public import LibraryVoiceprintRead
from backend.models.speaker import (
    GlobalSpeaker,
    GlobalSpeakerCreate,
    GlobalSpeakerUpdate,
    GlobalSpeakerWithCount,
    RecordingSpeaker,
)
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.embedding import (
    LIBRARY_VOICEPRINT_MIN_SPEECH_S,
    MARGIN_OF_VICTORY,
    SCAN_MATCH_THRESHOLD,
    RecordingVoiceprint,
    blend_voiceprint,
    cosine_similarity,
    is_drift_refused,
    lock_blocks_user_link,
    meets_library_evidence_floor,
    pool_recording_voiceprints,
    too_little_speech_detail,
)
from backend.processing.embedding_version import EMBEDDING_METHOD_VERSION
from backend.services.recording_identity_service import get_recordings_by_public_ids
from backend.services.tag_ownership import require_own_tag_ids
from backend.utils.canonical_pipeline import (
    recording_ready_for_canonical_backfill,
    update_recording_speaker_identity,
)
from backend.utils.embedding_audio import select_recording_audio_for_embedding
from backend.utils.speaker_names.preferences import library_learns_automatically
from backend.utils.speaker_names.review import link_voice_match

from .helpers import (
    MEASURED_EXTRACTION_TASK,
    MergeRequest,
    SegmentSelection,
    SpeakerSegment,
    SpeakerSplitRead,
    SpeakerSplitRequest,
    _canonical_transcript_writes_enabled,
    _carry_drift_refusals,
    _lift_drift_refusals,
    _load_segments_for_speaker_work,
    _merge_local_speakers,
    _persist_segments_for_speaker_work,
    _require_recordings_support_speaker_mutations,
)
from .router import router
from .split_voiceprints import (
    apply_split_to_new_person,
    apply_split_to_original,
    apply_split_to_original_speaker,
    other_recording_voiceprints,
    split_extraction_failed,
)

logger = logging.getLogger(__name__)


@router.get("/", response_model=List[GlobalSpeakerWithCount])
async def list_global_speakers(
    skip: int = 0,
    limit: int = 100,
    q: Optional[str] = None,
    tags: Optional[List[int]] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all global speakers (People) with filtering.
    """
    # Query with left join to count recordings
    query = (
        select(GlobalSpeaker, func.count(RecordingSpeaker.id).label("recording_count"))
        .outerjoin(
            RecordingSpeaker, GlobalSpeaker.id == RecordingSpeaker.global_speaker_id
        )
        .where(GlobalSpeaker.user_id == current_user.id)
    )

    if q:
        search_term = f"%{q}%"
        query = query.where(
            or_(
                GlobalSpeaker.name.ilike(search_term),
                GlobalSpeaker.email.ilike(search_term),
                GlobalSpeaker.company.ilike(search_term),
                GlobalSpeaker.notes.ilike(search_term),
                GlobalSpeaker.title.ilike(search_term),
            )
        )

    if tags:
        # Filter by tags (has ANY of the tags)
        query = query.join(GlobalSpeaker.tag_links).where(
            PeopleTagLink.tag_id.in_(tags)
        )

    query = (
        query.group_by(GlobalSpeaker.id)
        .order_by(GlobalSpeaker.name)
        .offset(skip)
        .limit(limit)
    )

    # Ensure tag_links and tag are loaded using selectinload for consistency
    query = query.options(
        selectinload(GlobalSpeaker.tag_links).selectinload(PeopleTagLink.tag)
    )

    result = await db.execute(query)
    rows = result.all()

    speakers_with_counts = []
    for row in rows:
        speaker: GlobalSpeaker = row[0]
        count = row[1]

        tag_list = []
        for link in speaker.tag_links:
            if link.tag:
                tag_list.append(
                    PeopleTagRead(
                        id=link.tag.id,
                        name=link.tag.name,
                        color=link.tag.color,
                        parent_id=link.tag.parent_id,
                    )
                )

        speakers_with_counts.append(
            GlobalSpeakerWithCount(
                id=speaker.id,
                name=speaker.name,
                color=speaker.color,
                has_voiceprint=speaker.has_voiceprint,
                is_voiceprint_locked=speaker.is_voiceprint_locked,
                recording_count=count,
                created_at=speaker.created_at.isoformat(),
                updated_at=speaker.updated_at.isoformat(),
                title=speaker.title,
                company=speaker.company,
                email=speaker.email,
                phone_number=speaker.phone_number,
                notes=speaker.notes,
                tags=tag_list,
            )
        )

    return speakers_with_counts


@router.post("/", response_model=GlobalSpeaker)
async def create_global_speaker(
    speaker_in: GlobalSpeakerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new global speaker (Person).
    """
    statement = select(GlobalSpeaker).where(
        GlobalSpeaker.name == speaker_in.name, GlobalSpeaker.user_id == current_user.id
    )
    result = await db.execute(statement)
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"A person with the name '{speaker_in.name}' already exists in your library.",
        )

    tag_ids = await require_own_tag_ids(
        db, PeopleTag, speaker_in.tag_ids, current_user.id
    )

    speaker = GlobalSpeaker(
        name=speaker_in.name,
        user_id=current_user.id,
        color=speaker_in.color,
        title=speaker_in.title,
        company=speaker_in.company,
        email=speaker_in.email,
        phone_number=speaker_in.phone_number,
        notes=speaker_in.notes,
    )
    db.add(speaker)
    await db.commit()
    await db.refresh(speaker)

    if tag_ids:
        for tag_id in tag_ids:
            link = PeopleTagLink(global_speaker_id=speaker.id, tag_id=tag_id)
            db.add(link)
        await db.commit()
        await db.refresh(speaker)

    return speaker


@router.post("/merge", response_model=GlobalSpeaker)
async def merge_speakers(
    request: MergeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Merge source speaker into target speaker.
    Reassigns all recording speakers from source to target, then deletes source.
    """
    source = await db.get(GlobalSpeaker, request.source_speaker_id)
    target = await db.get(GlobalSpeaker, request.target_speaker_id)

    if not source or not target:
        raise HTTPException(status_code=404, detail="Speaker not found")

    if source.user_id != current_user.id or target.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Cannot merge speaker into itself")

    stmt = select(RecordingSpeaker).where(
        RecordingSpeaker.global_speaker_id == source.id
    )
    result = await db.execute(stmt)
    source_recording_speakers = result.scalars().all()

    await _require_recordings_support_speaker_mutations(
        db,
        [rs.recording_id for rs in source_recording_speakers],
    )

    for rs in source_recording_speakers:
        # Check if target already has a speaker in this recording
        stmt = select(RecordingSpeaker).where(
            RecordingSpeaker.recording_id == rs.recording_id,
            RecordingSpeaker.global_speaker_id == target.id,
        )
        result = await db.execute(stmt)
        target_rs = result.scalar_one_or_none()

        if target_rs:
            # COLLISION: Target exists in this recording.
            # Merges the local speakers to prevent duplicates in the meeting view.
            if rs.id != target_rs.id:
                await _merge_local_speakers(
                    db,
                    rs.recording_id,
                    rs.diarization_label,
                    target_rs.diarization_label,
                    actor_user_id=current_user.id,
                )
        else:
            recording = await db.get(Recording, rs.recording_id)
            if (
                recording is not None
                and _canonical_transcript_writes_enabled()
                and recording_ready_for_canonical_backfill(recording.status)
            ):
                await db.run_sync(
                    lambda sync_session: update_recording_speaker_identity(
                        sync_session,
                        recording_id=rs.recording_id,
                        diarization_label=rs.diarization_label,
                        new_speaker_name=target.name,
                        target_global_speaker_id=target.id,
                        actor_user_id=current_user.id,
                        merge_global_embedding_alpha=None,
                        source="api",
                        # A confirmed speaker's name now names the target,
                        # so a later run carries it there; a pending match
                        # moves without being confirmed.
                        confirm=bool(rs.identity_locked),
                    )
                )
            else:
                rs.global_speaker_id = target.id
                rs.name = target.name
                rs.local_name = None
                db.add(rs)

    # A locked target keeps their voiceprint; one with none still takes the
    # source's, as any link the user makes would give them one.
    if source.embedding and not lock_blocks_user_link(target):
        blend_voiceprint(target, source.embedding, source.embedding_version, alpha=0.5)
    db.add(target)

    await _carry_drift_refusals(db, source_id=source.id, target_id=target.id)
    await db.delete(source)

    await db.commit()
    await db.refresh(target)
    return target


@router.put("/{speaker_id}", response_model=GlobalSpeaker)
async def update_global_speaker(
    speaker_id: int,
    speaker_in: GlobalSpeakerUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a global speaker (Person).
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    new_tag_ids: Optional[set[int]] = None
    if speaker_in.tag_ids is not None:
        new_tag_ids = set(
            await require_own_tag_ids(
                db, PeopleTag, speaker_in.tag_ids, current_user.id
            )
        )

    if speaker_in.name is not None and speaker_in.name != speaker.name:
        stmt = select(GlobalSpeaker).where(
            GlobalSpeaker.name == speaker_in.name,
            GlobalSpeaker.user_id == current_user.id,
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing and existing.id != speaker_id:
            raise HTTPException(status_code=400, detail="Speaker name already exists")
        speaker.name = speaker_in.name

        # Propagate name change to RecordingSpeakers
        stmt = select(RecordingSpeaker).where(
            RecordingSpeaker.global_speaker_id == speaker_id
        )
        result = await db.execute(stmt)
        linked_speakers = result.scalars().all()

        await _require_recordings_support_speaker_mutations(
            db,
            [rs.recording_id for rs in linked_speakers],
        )

        for rs in linked_speakers:
            rs.name = speaker_in.name
            db.add(rs)

    if speaker_in.color is not None:
        speaker.color = speaker_in.color

    # Only the lock: unlocking lets renames, links, merges and automatic
    # learning update the voiceprint again, and neither way changes it now.
    if speaker_in.is_voiceprint_locked is not None:
        speaker.is_voiceprint_locked = speaker_in.is_voiceprint_locked

    if speaker_in.title is not None:
        speaker.title = speaker_in.title
    if speaker_in.company is not None:
        speaker.company = speaker_in.company
    if speaker_in.email is not None:
        speaker.email = speaker_in.email
    if speaker_in.phone_number is not None:
        speaker.phone_number = speaker_in.phone_number
    if speaker_in.notes is not None:
        speaker.notes = speaker_in.notes

    if new_tag_ids is not None:
        stmt = select(PeopleTagLink).where(
            PeopleTagLink.global_speaker_id == speaker_id
        )
        result = await db.execute(stmt)
        existing_links = result.scalars().all()

        existing_tag_ids = {link.tag_id for link in existing_links}

        # Remove tags not in new list
        for link in existing_links:
            if link.tag_id not in new_tag_ids:
                await db.delete(link)

        # Add new tags
        for tag_id in new_tag_ids:
            if tag_id not in existing_tag_ids:
                link = PeopleTagLink(global_speaker_id=speaker_id, tag_id=tag_id)
                db.add(link)

    db.add(speaker)
    await db.commit()
    await db.refresh(speaker)
    return speaker


@router.delete("/{speaker_id}")
async def delete_global_speaker(
    speaker_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a person from the People library.

    This demotes rather than erases: the global record and its voiceprint are
    removed, while linked recording speakers keep the person's name as a
    recording-local name. No transcript or speaker attribution data changes,
    so the operation is safe for recordings of any pipeline generation.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    linked_result = await db.execute(
        select(RecordingSpeaker).where(RecordingSpeaker.global_speaker_id == speaker_id)
    )
    for recording_speaker in linked_result.scalars().all():
        if not recording_speaker.local_name:
            recording_speaker.local_name = speaker.name
            db.add(recording_speaker)

    await db.delete(speaker)
    await db.commit()
    return {"ok": True}


@router.get("/{speaker_id}/segments", response_model=List[SpeakerSegment])
async def get_speaker_segments(
    speaker_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get recent audio segments attributed to this global speaker.
    Used for manual voiceprint recalibration.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    statement = (
        select(RecordingSpeaker, Recording, Transcript)
        .join(Recording, Recording.id == RecordingSpeaker.recording_id)
        .outerjoin(Transcript, Transcript.recording_id == RecordingSpeaker.recording_id)
        .where(RecordingSpeaker.global_speaker_id == speaker_id)
        .where(Recording.is_deleted == False)
        .order_by(Recording.created_at.desc())
        .limit(20)  # Scan last 20 recordings
    )
    result = await db.execute(statement)
    rows = result.all()

    segments = []
    for rs, rec, trans in rows:
        if not trans or not trans.segments:
            continue

        labels = {rs.diarization_label}
        if rs.local_name:
            labels.add(rs.local_name)
        if rs.name:
            labels.add(rs.name)
        labels.add(speaker.name)

        rec_segments = []
        for seg in trans.segments:
            if seg.get("speaker") in labels:
                duration = seg["end"] - seg["start"]
                if duration > 10.0:
                    continue

                rec_segments.append(
                    SpeakerSegment(
                        recording_id=rec.public_id,
                        recording_name=rec.name,
                        recording_date=rec.created_at.isoformat()
                        if rec.created_at
                        else None,
                        start=seg["start"],
                        end=seg["end"],
                        text=seg["text"],
                    )
                )

        segments.extend(rec_segments)
        if len(segments) >= limit:
            break

    return segments[:limit]


@router.post("/{speaker_id}/recalibrate")
async def recalibrate_voiceprint(
    speaker_id: int,
    segments: List[SegmentSelection],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually recalibrate (reset) a speaker's voiceprint using specific audio segments.
    Locks the voiceprint to prevent auto-updates.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    if not speaker.has_voiceprint:
        raise HTTPException(
            status_code=400, detail="Speaker has no voiceprint to recalibrate"
        )

    if not segments:
        raise HTTPException(status_code=400, detail="No segments provided")

    recording_segments = defaultdict(list)
    for s in segments:
        recording_segments[s.recording_id].append((s.start, s.end))

    recordings = await get_recordings_by_public_ids(
        db,
        list(recording_segments.keys()),
        user_id=current_user.id,
    )
    recordings_by_public_id = {
        recording.public_id: recording for recording in recordings
    }

    voiceprints: list[RecordingVoiceprint] = []
    used: list[tuple[Recording, list[tuple[float, float]]]] = []
    device_str = speakers_module.config_manager.get("processing_device", "cpu")

    for recording_public_id, segs in recording_segments.items():
        rec = recordings_by_public_id.get(recording_public_id)
        if rec is None:
            continue

        user_settings = current_user.settings or {}
        hf_token = user_settings.get("hf_token") or speakers_module.config_manager.get(
            "hf_token"
        )

        target_audio = select_recording_audio_for_embedding(rec)
        if not target_audio:
            logger.warning(
                f"Skipping recalibration segments for recording {recording_public_id}: no audio file available"
            )
            continue

        task = await dispatch_task(
            MEASURED_EXTRACTION_TASK,
            args=[target_audio, segs, device_str, hf_token],
        )
        try:
            voiceprint = RecordingVoiceprint.from_task_result(
                await run_in_threadpool(task.get, timeout=120)
            )
            if voiceprint:
                voiceprints.append(voiceprint)
                if meets_library_evidence_floor(voiceprint.speech_seconds):
                    used.append((rec, segs))
        except Exception as e:  # noqa: BLE001 -- boundary: task.get re-raises whatever the worker raised; one recording must not fail recalibration
            logger.error(
                f"Failed to extract embedding for recalibration (Rec {recording_public_id}): {e}"
            )

    if not voiceprints:
        raise HTTPException(
            status_code=500,
            detail="Failed to extract embeddings from selected segments",
        )

    # A recalibration founds the voiceprint afresh, so each recording's sample
    # needs the same evidence as a first voiceprint. It is also the one place a
    # user overrides the drift guard: samples they chose and heard are used
    # whatever the guard said about their recording's link, and that link
    # counts towards the person's voiceprint again from now on.
    pool = pool_recording_voiceprints(voiceprints)
    if pool.voiceprint is None:
        raise HTTPException(status_code=400, detail=too_little_speech_detail(pool))
    await _lift_drift_refusals(db, person_id=speaker.id, sampled=used)

    speaker.embedding = pool.voiceprint.embedding
    speaker.embedding_version = EMBEDDING_METHOD_VERSION
    speaker.is_voiceprint_locked = True
    db.add(speaker)
    await db.commit()

    message = "Voiceprint recalibrated and locked."
    if pool.used < pool.offered:
        message = (
            f"Voiceprint recalibrated from {pool.used} of {pool.offered} "
            f"recordings and locked. The others held less than "
            f"{LIBRARY_VOICEPRINT_MIN_SPEECH_S:.0f} s of clear speech."
        )
    return {"success": True, "message": message}


@router.delete("/{speaker_id}/embedding")
async def delete_global_speaker_embedding(
    speaker_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete voiceprint (embedding) for a global speaker.
    Does not delete the speaker itself.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    speaker.embedding = None
    speaker.is_voiceprint_locked = False
    db.add(speaker)
    await db.commit()
    return {"ok": True}


@router.post("/{speaker_id}/split", response_model=SpeakerSplitRead)
async def split_speaker(
    speaker_id: int,
    request: SpeakerSplitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Split a global speaker into a new speaker based on selected audio segments.

    Rebuilds both people's voiceprints, the new speaker's from the selected
    segments and the original's from the remaining ones (or, when those are
    too few, from the original's other recordings), under the evidence floor
    (see ``split_voiceprints``), and says what happened to each.
    """
    original_speaker = await db.get(GlobalSpeaker, speaker_id)
    if not original_speaker or original_speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Original speaker not found")

    if not request.segments:
        raise HTTPException(
            status_code=400, detail="No segments provided for splitting"
        )

    stmt = select(GlobalSpeaker).where(
        GlobalSpeaker.name == request.new_speaker_name,
        GlobalSpeaker.user_id == current_user.id,
    )
    result = await db.execute(stmt)
    existing_new = result.scalar_one_or_none()

    new_speaker = None
    if existing_new:
        new_speaker = existing_new
    else:
        # Locked only once the split gives them a voiceprint (see
        # apply_split_to_new_person); a lock on none would only stop an
        # auto-applied name suggestion from founding one later.
        new_speaker = GlobalSpeaker(
            name=request.new_speaker_name,
            user_id=current_user.id,
        )
        db.add(new_speaker)
        await db.flush()

    recording_segments = defaultdict(list)
    for s in request.segments:
        recording_segments[s.recording_id].append(s)

    recordings = await get_recordings_by_public_ids(
        db,
        list(recording_segments.keys()),
        user_id=current_user.id,
    )
    recordings_by_public_id = {
        recording.public_id: recording for recording in recordings
    }

    await _require_recordings_support_speaker_mutations(
        db,
        [recording.id for recording in recordings],
    )

    timestamp_suffix = int(time.time())
    device_str = speakers_module.config_manager.get("processing_device", "cpu")
    user_settings = current_user.settings or {}
    hf_token = user_settings.get("hf_token") or speakers_module.config_manager.get(
        "hf_token"
    )

    new_speaker_voiceprints: list[RecordingVoiceprint] = []
    new_speaker_links: list[RecordingSpeaker] = []

    for recording_public_id, segments in recording_segments.items():
        rec = recordings_by_public_id.get(recording_public_id)
        if rec is None:
            continue
        rec_id = rec.id

        split_label = f"SPLIT_{timestamp_suffix}_{new_speaker.id}"

        stmt = select(Transcript).where(Transcript.recording_id == rec_id)
        result = await db.execute(stmt)
        transcript = result.scalar_one_or_none()

        transcript_segments = await _load_segments_for_speaker_work(
            db,
            recording=rec,
            transcript=transcript,
        )
        if transcript and transcript_segments:
            new_trans_segments = []
            segments_modified = False

            for t_seg in transcript_segments:
                is_selected = False
                t_start = t_seg["start"]
                t_end = t_seg["end"]

                for sel in segments:
                    overlap_start = max(t_start, sel.start)
                    overlap_end = min(t_end, sel.end)
                    overlap = max(0, overlap_end - overlap_start)
                    duration = t_end - t_start

                    if duration > 0 and (overlap / duration) > 0.5:
                        is_selected = True
                        break

                seg_copy = dict(t_seg)
                if is_selected:
                    seg_copy["speaker"] = split_label
                    segments_modified = True
                new_trans_segments.append(seg_copy)

            if segments_modified:
                await _persist_segments_for_speaker_work(
                    db,
                    recording=rec,
                    transcript=transcript,
                    segments=new_trans_segments,
                )

        stmt = select(RecordingSpeaker).where(
            RecordingSpeaker.recording_id == rec_id,
            RecordingSpeaker.diarization_label == split_label,
        )
        result = await db.execute(stmt)
        existing_rs = result.scalar_one_or_none()

        if not existing_rs:
            seg_tuples = [(s.start, s.end) for s in segments]
            target_audio = select_recording_audio_for_embedding(rec)

            task = await dispatch_task(
                MEASURED_EXTRACTION_TASK,
                args=[target_audio, seg_tuples, device_str, hf_token],
            )
            try:
                voiceprint = RecordingVoiceprint.from_task_result(
                    await run_in_threadpool(task.get, timeout=60)
                )
                if voiceprint:
                    new_speaker_voiceprints.append(voiceprint)
            except Exception as e:  # noqa: BLE001 -- boundary: task.get re-raises whatever the worker raised; all of it refuses the split
                logger.error(
                    f"Failed embedding extract during split (Rec {rec_id}): {e}"
                )
                raise split_extraction_failed() from e

            rs = RecordingSpeaker(
                recording_id=rec_id,
                diarization_label=split_label,
                name=new_speaker.name,
                global_speaker_id=new_speaker.id,
                embedding=voiceprint.embedding if voiceprint else None,
                embedding_version=EMBEDDING_METHOD_VERSION if voiceprint else None,
                embedding_speech_seconds=(
                    voiceprint.speech_seconds if voiceprint else None
                ),
            )
            db.add(rs)
            new_speaker_links.append(rs)

    new_update = apply_split_to_new_person(
        new_speaker, new_speaker_voiceprints, new_speaker_links
    )
    db.add(new_speaker)

    original_speaker_voiceprints: list[RecordingVoiceprint] = []

    for recording_public_id in recording_segments.keys():
        rec = recordings_by_public_id.get(recording_public_id)
        if rec is None:
            continue
        rec_id = rec.id

        stmt = select(Transcript).where(Transcript.recording_id == rec_id)
        result = await db.execute(stmt)
        transcript = result.scalar_one_or_none()
        if not transcript:
            continue

        # A row merged into another keeps its link but is not a speaker any more.
        stmt = select(RecordingSpeaker).where(
            RecordingSpeaker.recording_id == rec_id,
            RecordingSpeaker.global_speaker_id == original_speaker.id,
            RecordingSpeaker.merged_into_id.is_(None),
        )
        result = await db.execute(stmt)
        original_rss = result.scalars().all()
        valid_labels = {rs.diarization_label for rs in original_rss}

        remaining_seg_tuples = []
        transcript_segments = await _load_segments_for_speaker_work(
            db,
            recording=rec,
            transcript=transcript,
        )
        for t_seg in transcript_segments:
            if t_seg.get("speaker") in valid_labels:
                remaining_seg_tuples.append((t_seg["start"], t_seg["end"]))

        voiceprint = None
        if remaining_seg_tuples:
            target_audio = select_recording_audio_for_embedding(rec)
            task = await dispatch_task(
                MEASURED_EXTRACTION_TASK,
                args=[target_audio, remaining_seg_tuples, device_str, hf_token],
            )
            try:
                voiceprint = RecordingVoiceprint.from_task_result(
                    await run_in_threadpool(task.get, timeout=60)
                )
            except Exception as e:  # noqa: BLE001 -- boundary: task.get re-raises whatever the worker raised; all of it refuses the split
                logger.error(
                    f"Failed embedding extract for original (Rec {rec_id}): {e}"
                )
                raise split_extraction_failed() from e
        if len(original_rss) == 1:
            apply_split_to_original_speaker(original_rss[0], voiceprint)
            db.add(original_rss[0])
        # A recording whose link the drift guard refused for the original
        # stays out of their voiceprint, as it does in the rebuild. A single
        # speaker's refusal went with its old voiceprint just above.
        if voiceprint and not any(is_drift_refused(rs) for rs in original_rss):
            original_speaker_voiceprints.append(voiceprint)

    # The original's links outside the split recordings, in case what remains
    # of them inside is too little to rebuild their voiceprint from.
    split_recording_ids = [
        recordings_by_public_id[public_id].id
        for public_id in recording_segments
        if public_id in recordings_by_public_id
    ]
    other_links = (
        (
            await db.execute(
                select(RecordingSpeaker).where(
                    RecordingSpeaker.global_speaker_id == original_speaker.id,
                    RecordingSpeaker.merged_into_id.is_(None),
                    RecordingSpeaker.recording_id.notin_(split_recording_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    original_update = apply_split_to_original(
        original_speaker,
        original_speaker_voiceprints,
        other_recording_voiceprints(
            list(other_links),
            include_unconfirmed=library_learns_automatically(current_user.settings),
        ),
    )
    db.add(original_speaker)

    await db.commit()
    await db.refresh(new_speaker)
    return SpeakerSplitRead(
        id=new_speaker.id,
        name=new_speaker.name,
        has_voiceprint=new_speaker.has_voiceprint,
        is_voiceprint_locked=new_speaker.is_voiceprint_locked,
        library_voiceprint=LibraryVoiceprintRead.from_update(new_update),
        original_library_voiceprint=LibraryVoiceprintRead.from_update(original_update),
    )


@router.post("/{speaker_id}/scan-matches")
async def scan_for_matches(
    speaker_id: int,
    threshold: float = SCAN_MATCH_THRESHOLD,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Scan all 'unlinked' speakers in the library and link them to this Global Speaker
    if their embedding similarity is above the threshold and the match is unambiguous.
    """
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    if not speaker.embedding:
        raise HTTPException(
            status_code=400, detail="Speaker has no voiceprint to match against"
        )

    all_gs_stmt = select(GlobalSpeaker).where(
        GlobalSpeaker.embedding != None,
        GlobalSpeaker.user_id == current_user.id,
        GlobalSpeaker.id != speaker_id,
    )
    all_gs_result = await db.execute(all_gs_stmt)
    other_global_speakers = all_gs_result.scalars().all()

    stmt = (
        select(RecordingSpeaker)
        .join(Recording)
        .where(
            RecordingSpeaker.global_speaker_id == None,
            RecordingSpeaker.embedding != None,
            Recording.user_id == current_user.id,
        )
    )
    result = await db.execute(stmt)
    candidates = result.scalars().all()

    matches_found = 0
    recordings_updated = set()

    for cand in candidates:
        recording = await db.get(Recording, cand.recording_id)
        if recording is None or not recording_supports_unified_mutations(recording):
            continue

        if not cand.embedding:
            continue

        score = cosine_similarity(speaker.embedding, cand.embedding)
        if score < threshold:
            continue

        runner_up_score = 0.0
        for other_gs in other_global_speakers:
            if not other_gs.embedding:
                continue
            other_score = cosine_similarity(other_gs.embedding, cand.embedding)
            if other_score > runner_up_score:
                runner_up_score = other_score

        if (score - runner_up_score) < MARGIN_OF_VICTORY:
            continue

        if not await db.run_sync(
            lambda session: link_voice_match(session, cand, speaker, score)
        ):
            continue
        matches_found += 1
        recordings_updated.add(cand.recording_id)

    if matches_found > 0:
        await db.commit()

    return {
        "success": True,
        "matches_found": matches_found,
        "recordings_updated": len(recordings_updated),
    }
