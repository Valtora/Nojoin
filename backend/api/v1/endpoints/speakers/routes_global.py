import logging
import time
from collections import defaultdict
from typing import List, Optional

import numpy as np
from fastapi import Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

import backend.api.v1.endpoints.speakers as speakers_module
from backend.api.deps import get_current_user, get_db
from backend.models.people_tag_schemas import PeopleTagRead
from backend.models.recording import (
    Recording,
    recording_supports_unified_mutations,
)
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
    MARGIN_OF_VICTORY,
    SCAN_MATCH_THRESHOLD,
    cosine_similarity,
    merge_embeddings,
)
from backend.services.recording_identity_service import get_recordings_by_public_ids
from backend.utils.canonical_pipeline import (
    recording_ready_for_canonical_backfill,
    update_recording_speaker_identity,
)
from backend.utils.embedding_audio import select_recording_audio_for_embedding

from .helpers import (
    MergeRequest,
    SegmentSelection,
    SpeakerSegment,
    SpeakerSplitRequest,
    _canonical_transcript_writes_enabled,
    _load_segments_for_speaker_work,
    _merge_local_speakers,
    _persist_segments_for_speaker_work,
    _require_recordings_support_speaker_mutations,
)
from .router import router

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
    from backend.models.people_tag import PeopleTagLink

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

    # Build response
    speakers_with_counts = []
    for row in rows:
        speaker: GlobalSpeaker = row[0]
        count = row[1]

        # Build tag list
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
    # Check if exists
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

    import backend.models.people_tag as pt

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

    if speaker_in.tag_ids:
        for tag_id in speaker_in.tag_ids:
            link = pt.PeopleTagLink(global_speaker_id=speaker.id, tag_id=tag_id)
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
    # 1. Get speakers
    source = await db.get(GlobalSpeaker, request.source_speaker_id)
    target = await db.get(GlobalSpeaker, request.target_speaker_id)

    if not source or not target:
        raise HTTPException(status_code=404, detail="Speaker not found")

    if source.user_id != current_user.id or target.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    if source.id == target.id:
        raise HTTPException(status_code=400, detail="Cannot merge speaker into itself")

    # 2. Reassign all recording speakers
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
            if rs.id != target_rs.id:  # Sanity check
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
                    )
                )
            else:
                # No collision: Just reassign
                rs.global_speaker_id = target.id
                rs.name = target.name
                rs.local_name = None
                db.add(rs)

    # 3. Merge embeddings
    if source.embedding and target.embedding:
        target.embedding = merge_embeddings(
            target.embedding, source.embedding, alpha=0.5
        )
    elif source.embedding and not target.embedding:
        target.embedding = source.embedding
    db.add(target)

    # 4. Delete source speaker
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

    if speaker_in.name is not None and speaker_in.name != speaker.name:
        # Check name uniqueness
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

    # CRM Fields
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

    if speaker_in.tag_ids is not None:
        # Update tags: Clear existing and add new
        from backend.models.people_tag import PeopleTagLink

        stmt = select(PeopleTagLink).where(
            PeopleTagLink.global_speaker_id == speaker_id
        )
        result = await db.execute(stmt)
        existing_links = result.scalars().all()

        existing_tag_ids = {link.tag_id for link in existing_links}
        new_tag_ids = set(speaker_in.tag_ids)

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
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    linked_result = await db.execute(
        select(RecordingSpeaker.recording_id).where(
            RecordingSpeaker.global_speaker_id == speaker_id
        )
    )
    await _require_recordings_support_speaker_mutations(
        db,
        [recording_id for recording_id in linked_result.scalars().all()],
    )

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
    # Verify speaker
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    # Fetch recordings with this speaker
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

        # Determine labels to look for
        labels = {rs.diarization_label}
        if rs.local_name:
            labels.add(rs.local_name)
        if rs.name:
            labels.add(rs.name)
        labels.add(speaker.name)

        # Find matching segments
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
    # 1. Verify speaker
    speaker = await db.get(GlobalSpeaker, speaker_id)
    if not speaker or speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Speaker not found")

    if not speaker.has_voiceprint:
        raise HTTPException(
            status_code=400, detail="Speaker has no voiceprint to recalibrate"
        )

    if not segments:
        raise HTTPException(status_code=400, detail="No segments provided")

    # 2. Group segments by recording
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

    all_embeddings = []
    device_str = speakers_module.config_manager.get("processing_device", "cpu")

    # 3. Extract embeddings
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

        task = speakers_module.celery_app.send_task(
            "backend.worker.tasks.extract_embedding_task",
            args=[target_audio, segs, device_str, hf_token],
        )
        try:
            emb = await run_in_threadpool(task.get, timeout=120)  # 2 min timeout
            if emb:
                all_embeddings.append(emb)
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"Failed to extract embedding for recalibration (Rec {recording_public_id}): {e}"
            )

    if not all_embeddings:
        raise HTTPException(
            status_code=500,
            detail="Failed to extract embeddings from selected segments",
        )

    # 4. Average embeddings (equal-weight arithmetic mean across all recordings)
    final_emb = np.mean(np.array(all_embeddings), axis=0).tolist()

    # 5. Update and Lock
    speaker.embedding = final_emb
    speaker.is_voiceprint_locked = True
    db.add(speaker)
    await db.commit()

    return {"success": True, "message": "Voiceprint recalibrated and locked."}


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


@router.post("/{speaker_id}/split", response_model=GlobalSpeaker)
async def split_speaker(
    speaker_id: int,
    request: SpeakerSplitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Split a global speaker into a new speaker based on selected audio segments.
    Recalibrates both the new speaker (using selected segments) and the original speaker (using remaining segments).
    """
    # 1. Verify Source Speaker
    original_speaker = await db.get(GlobalSpeaker, speaker_id)
    if not original_speaker or original_speaker.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Original speaker not found")

    if not request.segments:
        raise HTTPException(
            status_code=400, detail="No segments provided for splitting"
        )

    # 2. Create New Speaker
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
        new_speaker = GlobalSpeaker(
            name=request.new_speaker_name,
            user_id=current_user.id,
            is_voiceprint_locked=True,
        )
        db.add(new_speaker)
        await db.flush()

    # Group segments by recording
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

    # 3. Process each affected recording
    timestamp_suffix = int(time.time())
    device_str = speakers_module.config_manager.get("processing_device", "cpu")
    user_settings = current_user.settings or {}
    hf_token = user_settings.get("hf_token") or speakers_module.config_manager.get(
        "hf_token"
    )

    new_speaker_embeddings = []

    for recording_public_id, segments in recording_segments.items():
        rec = recordings_by_public_id.get(recording_public_id)
        if rec is None:
            continue
        rec_id = rec.id

        split_label = f"SPLIT_{timestamp_suffix}_{new_speaker.id}"

        # Update Transcript Segments
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

            task = speakers_module.celery_app.send_task(
                "backend.worker.tasks.extract_embedding_task",
                args=[target_audio, seg_tuples, device_str, hf_token],
            )
            try:
                emb = await run_in_threadpool(task.get, timeout=60)
                if emb:
                    new_speaker_embeddings.append(emb)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"Failed embedding extract during split (Rec {rec_id}): {e}"
                )
                emb = None

            rs = RecordingSpeaker(
                recording_id=rec_id,
                diarization_label=split_label,
                name=new_speaker.name,
                global_speaker_id=new_speaker.id,
                embedding=emb,
            )
            db.add(rs)

    # 4. Update New Speaker Voiceprint
    if new_speaker_embeddings:
        final_new_emb = np.mean(np.array(new_speaker_embeddings), axis=0).tolist()

        if new_speaker.embedding:
            new_speaker.embedding = merge_embeddings(
                new_speaker.embedding, final_new_emb
            )
        else:
            new_speaker.embedding = final_new_emb

        new_speaker.is_voiceprint_locked = True
        db.add(new_speaker)

    # 5. Recalibrate Original Speaker
    original_speaker_embeddings = []

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

        stmt = select(RecordingSpeaker).where(
            RecordingSpeaker.recording_id == rec_id,
            RecordingSpeaker.global_speaker_id == original_speaker.id,
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

        if remaining_seg_tuples:
            target_audio = select_recording_audio_for_embedding(rec)
            task = speakers_module.celery_app.send_task(
                "backend.worker.tasks.extract_embedding_task",
                args=[target_audio, remaining_seg_tuples, device_str, hf_token],
            )
            try:
                emb = await run_in_threadpool(task.get, timeout=60)
                if emb:
                    original_speaker_embeddings.append(emb)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"Failed embedding extract for original (Rec {rec_id}): {e}"
                )

    if original_speaker_embeddings:
        final_orig_emb = np.mean(np.array(original_speaker_embeddings), axis=0).tolist()
        original_speaker.embedding = final_orig_emb
        original_speaker.is_voiceprint_locked = True
        db.add(original_speaker)

    await db.commit()
    await db.refresh(new_speaker)
    return new_speaker


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

        cand.global_speaker_id = speaker.id
        cand.name = speaker.name
        db.add(cand)
        matches_found += 1
        recordings_updated.add(cand.recording_id)

    if matches_found > 0:
        await db.commit()

    return {
        "success": True,
        "matches_found": matches_found,
        "recordings_updated": len(recordings_updated),
    }
