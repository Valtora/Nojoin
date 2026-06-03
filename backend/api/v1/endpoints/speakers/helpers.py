import json
import logging
from typing import List, Optional
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select
from pydantic import BaseModel, ConfigDict

from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.recording import Recording, RecordingPipelineGeneration, recording_supports_unified_mutations, LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL
from backend.models.recording_public import RecordingSpeakerPublicRead, serialize_recording_speaker
from backend.models.transcript import Transcript
from backend.services.recording_identity_service import get_recording_by_public_id
from backend.utils.canonical_pipeline import (
    apply_compatibility_segment_replace,
    build_transcript_segments_for_read,
    recording_ready_for_canonical_backfill,
    merge_recording_speakers_by_label,
)
from backend.utils.speaker_name_suggestions import supersede_pending_transcript_speaker_suggestions
from backend.processing.embedding import merge_embeddings

import backend.api.v1.endpoints.speakers as speakers_module

logger = logging.getLogger(__name__)


# Helper functions
async def _get_owned_recording(db: AsyncSession, recording_public_id: str, user_id: int) -> Recording:
    recording = await get_recording_by_public_id(db, recording_public_id, user_id=user_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


def _canonical_transcript_writes_enabled() -> bool:
    # Use speakers_module to support test monkeypatching if needed
    return bool(speakers_module.config_manager.get("enable_canonical_transcript_writes", True))


def _require_recording_speaker_mutations_supported(recording: Recording) -> None:
    if recording_supports_unified_mutations(recording):
        return
    raise HTTPException(status_code=409, detail=LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL)


async def _require_recordings_support_speaker_mutations(
    db: AsyncSession,
    recording_ids: list[int],
) -> None:
    unique_ids = sorted({int(recording_id) for recording_id in recording_ids if recording_id is not None})
    if not unique_ids:
        return

    result = await db.execute(
        select(Recording.id)
        .where(Recording.id.in_(unique_ids))
        .where(
            or_(
                Recording.pipeline_generation.is_(None),
                Recording.pipeline_generation != RecordingPipelineGeneration.UNIFIED.value,
            )
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL)


def _copy_transcript_segments(raw_segments) -> list[dict]:
    if isinstance(raw_segments, str):
        try:
            raw_segments = json.loads(raw_segments)
        except json.JSONDecodeError:
            return []
    return [dict(segment) for segment in (raw_segments or []) if isinstance(segment, dict)]


async def _load_segments_for_speaker_work(
    db: AsyncSession,
    *,
    recording: Recording,
    transcript: Transcript | None,
) -> list[dict]:
    if transcript is None:
        return []
    if _canonical_transcript_writes_enabled() and recording_ready_for_canonical_backfill(recording.status):
        return await db.run_sync(
            lambda sync_session: build_transcript_segments_for_read(sync_session, recording.id)
        )
    return _copy_transcript_segments(getattr(transcript, "segments", None))


async def _persist_segments_for_speaker_work(
    db: AsyncSession,
    *,
    recording: Recording,
    transcript: Transcript | None,
    segments: list[dict],
) -> None:
    if transcript is None:
        return
    if _canonical_transcript_writes_enabled() and recording_ready_for_canonical_backfill(recording.status):
        await db.run_sync(
            lambda sync_session: apply_compatibility_segment_replace(
                sync_session,
                recording_id=recording.id,
                segments=segments,
            )
        )
        return
    transcript.segments = segments
    flag_modified(transcript, "segments")
    db.add(transcript)


def _serialize_recording_speakers(
    speakers: list[RecordingSpeaker],
    *,
    recording_public_id: str,
) -> list[RecordingSpeakerPublicRead]:
    return [
        serialize_recording_speaker(speaker, recording_public_id=recording_public_id)
        for speaker in speakers
    ]


async def _mark_pending_speaker_suggestions_superseded(
    db: AsyncSession,
    *,
    recording_id: int,
    diarization_labels: list[str],
    actor_user_id: int | None,
    reason: str,
) -> int:
    transcript = (
        await db.execute(select(Transcript).where(Transcript.recording_id == recording_id))
    ).scalar_one_or_none()
    if transcript is None:
        return 0

    changed = supersede_pending_transcript_speaker_suggestions(
        transcript,
        diarization_labels=diarization_labels,
        reason=reason,
        actor_user_id=actor_user_id,
    )
    if not changed:
        return 0

    flag_modified(transcript, "speaker_name_suggestions")
    db.add(transcript)
    return len(changed)


async def _merge_local_speakers(
    db: AsyncSession,
    recording_id: int,
    source_label: str,
    target_label: str,
    actor_user_id: int | None = None,
):
    """
    Helper to merge two local speakers (by diarization label) within a single recording.
    Updates transcript segments, merges embeddings, and deletes source recording speaker.
    """
    recording = await db.get(Recording, recording_id)
    if recording is not None and not recording_supports_unified_mutations(recording):
        raise RuntimeError(LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL)
    if recording is not None and _canonical_transcript_writes_enabled() and recording_ready_for_canonical_backfill(recording.status):
        try:
            await db.run_sync(
                lambda sync_session: merge_recording_speakers_by_label(
                    sync_session,
                    recording_id=recording_id,
                    source_diarization_label=source_label,
                    target_diarization_label=target_label,
                    actor_user_id=actor_user_id,
                    source="api",
                )
            )
        except (LookupError, ValueError):
            return
        return

    # 1. Find the source and target speaker entries
    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == source_label
    )
    result = await db.execute(statement)
    source_speaker = result.scalar_one_or_none()

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == target_label
    )
    result = await db.execute(statement)
    target_speaker = result.scalar_one_or_none()

    if not source_speaker or not target_speaker:
        # Should not happen if confirmed before calling, but safe to return or log
        return

    # Identify aliases for source to catch in transcript
    source_aliases = {source_label}
    if source_speaker.local_name:
        source_aliases.add(source_speaker.local_name)
    if source_speaker.name:
        source_aliases.add(source_speaker.name)
    # Check global link for alias
    if source_speaker.global_speaker_id:
        gs = await db.get(GlobalSpeaker, source_speaker.global_speaker_id)
        if gs:
            source_aliases.add(gs.name)

    # 2. Update Transcript Segments
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(statement)
    transcript = result.scalar_one_or_none()

    if recording is not None and transcript is not None:
        transcript_segments = await _load_segments_for_speaker_work(
            db,
            recording=recording,
            transcript=transcript,
        )
        if transcript_segments:
            segments_updated = False
            new_segments = []
            for segment in transcript_segments:
                segment_copy = dict(segment)
                if segment_copy.get("speaker") in source_aliases:
                    segment_copy["speaker"] = target_label
                    segments_updated = True
                new_segments.append(segment_copy)

            if segments_updated:
                await _persist_segments_for_speaker_work(
                    db,
                    recording=recording,
                    transcript=transcript,
                    segments=new_segments,
                )

    # 3. Merge embeddings
    if source_speaker.embedding and target_speaker.embedding:
        target_speaker.embedding = merge_embeddings(
            target_speaker.embedding, 
            source_speaker.embedding, 
            alpha=0.5
        )
        db.add(target_speaker)
    elif source_speaker.embedding and not target_speaker.embedding:
        target_speaker.embedding = source_speaker.embedding
        db.add(target_speaker)

    # 4. Soft-Merge source speaker (set merged_into_id)
    # Instead of deleting, we keep it to preserve the merge history for reprocessing
    source_speaker.merged_into_id = target_speaker.id
    source_speaker.embedding = None # Clear embedding as it's merged
    db.add(source_speaker)


# Pydantic schemas/models
class SpeakerUpdate(BaseModel):
    diarization_label: str
    global_speaker_name: str
    model_config = ConfigDict(extra="ignore")


class MergeRequest(BaseModel):
    source_speaker_id: int
    target_speaker_id: int


class MergeRequestLabels(BaseModel):
    target_speaker_label: str
    source_speaker_label: str


class VoiceprintAction(BaseModel):
    action: str  # "create_new", "link_existing", "local_only", "force_link"
    global_speaker_id: Optional[int] = None  # Required for "link_existing" and "force_link"
    new_speaker_name: Optional[str] = None  # Required for "create_new"


class SpeakerSegment(BaseModel):
    recording_id: str
    recording_name: Optional[str] = None
    recording_date: Optional[str] = None
    start: float
    end: float
    text: str


class SegmentSelection(BaseModel):
    recording_id: str
    start: float
    end: float


class VoiceprintResult(BaseModel):
    success: bool
    has_voiceprint: bool
    matched_speaker: Optional[dict] = None  # {id, name, similarity_score}
    message: Optional[str] = None


class SpeakerSplitRequest(BaseModel):
    new_speaker_name: str
    segments: List[SegmentSelection]


class SpeakerColorUpdate(BaseModel):
    color: str
