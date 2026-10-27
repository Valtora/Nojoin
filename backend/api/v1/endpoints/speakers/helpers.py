import json
import logging
from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

import backend.api.v1.endpoints.speakers as speakers_module
from backend.models.recording import (
    LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL,
    Recording,
    RecordingPipelineGeneration,
    recording_supports_unified_mutations,
)
from backend.models.recording_public import (
    LibraryVoiceprintRead,
    RecordingSpeakerPublicRead,
    serialize_recording_speaker,
)
from backend.models.speaker import (
    GlobalSpeaker,
    RecordingSpeaker,
    RecordingSpeakerDriftRefusal,
)
from backend.models.transcript import Transcript
from backend.processing.embedding import (
    LibraryVoiceprintUpdate,
    RecordingVoiceprint,
    fold_recording_voiceprint,
    lift_drift_refusal,
    refuse_drift,
)
from backend.services.recording_identity_service import get_recording_by_public_id
from backend.utils.canonical_pipeline import (
    apply_compatibility_segment_replace,
    build_transcript_segments_for_read,
    merge_recording_speakers_by_label,
    recording_ready_for_canonical_backfill,
)
from backend.utils.canonical_pipeline.live_compatibility import (
    has_deferred_live_projection,
    prepare_live_compatibility_mutation,
)
from backend.utils.canonical_pipeline.public_ids import SegmentIdConflictError
from backend.utils.speaker_name_suggestions import (
    supersede_pending_transcript_speaker_suggestions,
)

logger = logging.getLogger(__name__)

# The worker task that extracts a voiceprint and measures the speech behind it.
# The older extract_embedding_task still answers with a bare vector, for an API
# older than its worker.
MEASURED_EXTRACTION_TASK = "backend.worker.tasks.extract_measured_embedding_task"


async def _get_owned_recording(
    db: AsyncSession, recording_public_id: str, user_id: int
) -> Recording:
    recording = await get_recording_by_public_id(
        db, recording_public_id, user_id=user_id
    )
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


def _canonical_transcript_writes_enabled() -> bool:
    # Read through speakers_module so tests can monkeypatch config_manager.
    return bool(
        speakers_module.config_manager.get("enable_canonical_transcript_writes", True)
    )


def _require_recording_speaker_mutations_supported(recording: Recording) -> None:
    if recording_supports_unified_mutations(recording):
        return
    raise HTTPException(
        status_code=409, detail=LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL
    )


async def _require_recordings_support_speaker_mutations(
    db: AsyncSession,
    recording_ids: list[int],
) -> None:
    unique_ids = sorted(
        {
            int(recording_id)
            for recording_id in recording_ids
            if recording_id is not None
        }
    )
    if not unique_ids:
        return

    result = await db.execute(
        select(Recording.id)
        .where(Recording.id.in_(unique_ids))
        .where(
            or_(
                Recording.pipeline_generation.is_(None),
                Recording.pipeline_generation
                != RecordingPipelineGeneration.UNIFIED.value,
            )
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409, detail=LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL
        )


def _copy_transcript_segments(raw_segments) -> list[dict]:
    if isinstance(raw_segments, str):
        try:
            raw_segments = json.loads(raw_segments)
        except json.JSONDecodeError:
            return []
    return [
        dict(segment) for segment in (raw_segments or []) if isinstance(segment, dict)
    ]


async def _load_segments_for_speaker_work(
    db: AsyncSession,
    *,
    recording: Recording,
    transcript: Transcript | None,
) -> list[dict]:
    if transcript is None:
        return []
    if _canonical_transcript_writes_enabled() and has_deferred_live_projection(
        transcript
    ):
        await db.run_sync(prepare_live_compatibility_mutation, recording.id)
    if _canonical_transcript_writes_enabled() and (
        has_deferred_live_projection(transcript)
        or recording_ready_for_canonical_backfill(recording.status)
    ):
        return await db.run_sync(
            lambda sync_session: build_transcript_segments_for_read(
                sync_session, recording.id
            )
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
    if _canonical_transcript_writes_enabled() and (
        has_deferred_live_projection(transcript)
        or recording_ready_for_canonical_backfill(recording.status)
    ):
        try:
            await db.run_sync(
                lambda sync_session: apply_compatibility_segment_replace(
                    sync_session,
                    recording_id=recording.id,
                    segments=segments,
                )
            )
        except SegmentIdConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return
    transcript.segments = segments
    flag_modified(transcript, "segments")
    db.add(transcript)


def _serialize_recording_speakers(
    speakers: list[RecordingSpeaker],
    *,
    recording_public_id: str,
    library_updates: dict[int, LibraryVoiceprintUpdate] | None = None,
) -> list[RecordingSpeakerPublicRead]:
    updates = library_updates or {}
    return [
        serialize_recording_speaker(
            speaker,
            recording_public_id=recording_public_id,
            library_update=updates.get(speaker.id) if speaker.id is not None else None,
        )
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
        await db.execute(
            select(Transcript).where(Transcript.recording_id == recording_id)
        )
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
    if (
        recording is not None
        and _canonical_transcript_writes_enabled()
        and recording_ready_for_canonical_backfill(recording.status)
    ):
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

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == source_label,
    )
    result = await db.execute(statement)
    source_speaker = result.scalar_one_or_none()

    statement = select(RecordingSpeaker).where(
        RecordingSpeaker.recording_id == recording_id,
        RecordingSpeaker.diarization_label == target_label,
    )
    result = await db.execute(statement)
    target_speaker = result.scalar_one_or_none()

    if not source_speaker or not target_speaker:
        # Callers confirm both speakers first, so a missing one is a no-op.
        return

    # Identify aliases for source to catch in transcript
    source_aliases = {source_label}
    if source_speaker.local_name:
        source_aliases.add(source_speaker.local_name)
    if source_speaker.name:
        source_aliases.add(source_speaker.name)
    if source_speaker.global_speaker_id:
        gs = await db.get(GlobalSpeaker, source_speaker.global_speaker_id)
        if gs:
            source_aliases.add(gs.name)

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

    added_seconds = None
    if source_speaker.embedding:
        person = (
            await db.get(GlobalSpeaker, target_speaker.global_speaker_id)
            if target_speaker.global_speaker_id
            else None
        )
        added_seconds = fold_recording_voiceprint(
            target_speaker,
            RecordingVoiceprint.of_speaker(source_speaker),
            alpha=0.5,
            person=person,
        )
        db.add(target_speaker)

    # Kept rather than deleted, so the merge history survives reprocessing. It
    # keeps the speech it added to the target's figure, for an unmerge.
    source_speaker.merged_into_id = target_speaker.id
    source_speaker.embedding = None
    source_speaker.embedding_speech_seconds = added_seconds
    db.add(source_speaker)


async def _carry_drift_refusals(
    db: AsyncSession, *, source_id: int, target_id: int
) -> None:
    """Keep the drift guard's refusals for a person being merged into another.

    Merging two people says they are one, and the refused links' voiceprints
    are the ones the guard judged, so each refusal for the source becomes one
    for the target instead of going with the source row (``ON DELETE
    CASCADE``). A link refused for both keeps one refusal for the target, and
    a link's refusals for anyone else stay as they are.
    """
    result = await db.execute(
        select(RecordingSpeaker)
        .join(RecordingSpeakerDriftRefusal)
        .where(RecordingSpeakerDriftRefusal.global_speaker_id == source_id)
    )
    for link in result.scalars().all():
        lift_drift_refusal(link, source_id)
        refuse_drift(link, target_id)
        db.add(link)


async def _sampled_speakers(
    db: AsyncSession, recording: Recording, samples: List[tuple[float, float]]
) -> tuple[set[int], set[str]]:
    """The speakers of the transcript lines a recording's samples cover.

    A sample covers a line when it overlaps more than half of it, the rule a
    split selects lines by. A line that names its recording speaker gives that
    speaker's id. Only a line that names none gives its speaker label, which
    on an older transcript can be a display name that several speakers carry.
    """
    transcript = (
        await db.execute(
            select(Transcript).where(Transcript.recording_id == recording.id)
        )
    ).scalar_one_or_none()
    lines = await _load_segments_for_speaker_work(
        db, recording=recording, transcript=transcript
    )
    ids: set[int] = set()
    labels: set[str] = set()
    for line in lines:
        duration = line["end"] - line["start"]
        if duration <= 0 or not any(
            (min(line["end"], end) - max(line["start"], start)) / duration > 0.5
            for start, end in samples
        ):
            continue
        if line.get("recording_speaker_id") is not None:
            ids.add(int(line["recording_speaker_id"]))
        elif line.get("speaker"):
            labels.add(line["speaker"])
    return ids, labels


async def _lift_drift_refusals(
    db: AsyncSession,
    *,
    person_id: int,
    sampled: List[tuple[Recording, List[tuple[float, float]]]],
) -> None:
    """Let the links the chosen samples came from back into the voiceprint.

    Recalibrating from a link's samples is the user saying, having heard
    them, that it is this person, which overrides the drift guard's earlier
    refusal of that link for them (see ``is_drift_refused``); its refusals for
    anyone else stand. Only the links whose lines the samples cover are lifted
    (``_sampled_speakers``): another of the person's speakers in the same
    recording was not heard, so its refusal stands.
    """
    for recording, samples in sampled:
        ids, labels = await _sampled_speakers(db, recording, samples)
        result = await db.execute(
            select(RecordingSpeaker)
            .join(RecordingSpeakerDriftRefusal)
            .where(
                RecordingSpeaker.global_speaker_id == person_id,
                RecordingSpeakerDriftRefusal.global_speaker_id == person_id,
                RecordingSpeaker.recording_id == recording.id,
            )
        )
        for link in result.scalars().all():
            names = {link.diarization_label, link.local_name, link.name}
            if link.id in ids or labels & names:
                lift_drift_refusal(link, person_id)
                db.add(link)


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


class MergedRecordingSpeakerRead(BaseModel):
    """A cluster the pipeline merged into another speaker, and what undoing
    that would restore."""

    id: int
    public_id: str
    diarization_label: str
    name: Optional[str] = None
    merged_into_id: int
    merged_into_label: str
    restorable_utterance_count: int
    has_voiceprint: bool = False


class VoiceprintAction(BaseModel):
    action: str  # "create_new", "link_existing", "local_only", "force_link"
    global_speaker_id: Optional[int] = (
        None  # Required for "link_existing" and "force_link"
    )
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
    # What the action did to the person's library voiceprint, when it had one.
    library_voiceprint: Optional[LibraryVoiceprintRead] = None


class SpeakerSplitRequest(BaseModel):
    new_speaker_name: str
    segments: List[SegmentSelection]


class SpeakerSplitRead(BaseModel):
    """The person a split moved the clips to, and what it did to both voiceprints."""

    id: int
    name: str
    has_voiceprint: bool
    is_voiceprint_locked: bool
    # The person the clips moved to.
    library_voiceprint: LibraryVoiceprintRead
    # The person they were split from.
    original_library_voiceprint: LibraryVoiceprintRead


class SpeakerColorUpdate(BaseModel):
    color: str
