import logging
from typing import List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.core.task_dispatch import dispatch_task_best_effort
from backend.models.live_settling_public import LiveSettlingPublicRead
from backend.models.pipeline import SpeakerCorrectionScope
from backend.models.recording import (
    LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL,
    Recording,
    recording_supports_unified_mutations,
)
from backend.models.recording_public import (
    RecordingSpeakerPublicRead,
)
from backend.models.speaker import RecordingSpeaker
from backend.models.transcript import Transcript
from backend.services.recording_identity_service import get_recording_by_public_id
from backend.utils.canonical_pipeline.live_compatibility import (
    prepare_live_compatibility_mutation,
)
from backend.utils.config_manager import config_manager
from backend.utils.live_speaker_labels import speaker_display_name

logger = logging.getLogger(__name__)


async def _get_owned_recording(
    db: AsyncSession,
    recording_public_id: str,
    user_id: int,
    *,
    options: tuple | None = None,
) -> Recording:
    recording = await get_recording_by_public_id(
        db,
        recording_public_id,
        user_id=user_id,
        options=options,
    )
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


async def _dispatch_meeting_edge_refresh(
    recording_id: int, *, enabled: bool = True
) -> None:
    """Queue a Meeting Edge refresh. Best-effort by design.

    Runs off the event loop because publishing is a blocking socket call, and
    every caller is an `async def` handler: blocking here stalls that process's
    other in-flight requests too, not only this one.

    A failure is logged and swallowed rather than surfaced. Meeting Edge is
    derived guidance that the next transcript mutation or live segment
    recomputes from scratch, so a lost refresh costs one stale panel until the
    next edit, while failing the request would lose the edit the user actually
    made. That is a deliberate policy, not an oversight.
    """
    if not enabled:
        return

    await dispatch_task_best_effort(
        "backend.worker.tasks.refresh_meeting_edge_task",
        args=[recording_id],
        context=f"recording {recording_id}",
    )


async def _get_recording_transcript(
    db: AsyncSession, recording_id: int
) -> Transcript | None:
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    result = await db.execute(statement)
    return result.scalar_one_or_none()


def _canonical_transcript_writes_enabled() -> bool:
    return bool(config_manager.get("enable_canonical_transcript_writes", True))


async def _prepare_live_transcript_mutation(
    db: AsyncSession, recording_id: int
) -> bool:
    if not _canonical_transcript_writes_enabled():
        from backend.utils.canonical_pipeline.guards import settling_required

        if not settling_required(await _get_recording_transcript(db, recording_id)):
            return False
    return await db.run_sync(prepare_live_compatibility_mutation, recording_id)


def _require_recording_transcript_mutations_supported(recording: Recording) -> None:
    if recording_supports_unified_mutations(recording):
        return
    raise HTTPException(
        status_code=409, detail=LEGACY_RECORDING_REPROCESS_REQUIRED_DETAIL
    )


def _find_segment_index_by_public_id(
    transcript: Transcript, utterance_id: str
) -> int | None:
    for index, segment in enumerate(transcript.segments or []):
        if str(segment.get("id")) == utterance_id:
            return index
    return None


def _get_segment_revision(segment: dict) -> int:
    return int(segment.get("revision") or 1)


class TranscriptSegmentTextUpdate(BaseModel):
    text: str


class TranscriptSegmentSpeakerUpdate(BaseModel):
    new_speaker_name: str
    global_speaker_id: Optional[int] = None
    diarization_label: Optional[str] = None


class FindReplaceRequest(BaseModel):
    find_text: str
    replace_text: str
    case_sensitive: bool = False
    use_regex: bool = False


class TranscriptSegmentsUpdate(BaseModel):
    segments: List[dict]


class TranscriptUtteranceTextPatch(BaseModel):
    text: str
    expected_revision: Optional[int] = None


class TranscriptUtteranceSpeakerPatch(BaseModel):
    new_speaker_name: str
    global_speaker_id: Optional[int] = None
    diarization_label: Optional[str] = None
    scope: SpeakerCorrectionScope = (
        SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING
    )
    expected_revision: Optional[int] = None


class TranscriptUtteranceRead(LiveSettlingPublicRead):
    id: str
    start: float
    end: float
    start_ms: int
    end_ms: int
    text: str
    alignment_error: Optional[str] = None
    speaker: str
    recording_speaker_id: Optional[int] = None
    # The diariser's own cluster label for this span, kept across merges. Null
    # on live lines and on rows written before it was recorded.
    diarization_label: Optional[str] = None
    state: str
    revision: int
    speaker_state: Optional[str] = None
    segment_source: str
    provisional: bool = False
    speaker_manually_edited: bool = False
    text_manually_edited: bool = False
    # Which surface authored the edit the corresponding flag reports: "api" for
    # the web app, "mcp" for a connected assistant. None where no edit is
    # attributable, which includes every edit made before provenance was
    # recorded. The interface must show those as unattributed rather than
    # guessing at a person.
    text_edit_source: Optional[str] = None
    speaker_edit_source: Optional[str] = None
    speaker_confidence: Optional[float] = None
    # "microphone" | "system" when the live lane could attribute the audio to a
    # capture channel, else None. Provenance of the audio, not who was speaking.
    source_channel: Optional[str] = None
    text_confidence: Optional[float] = None
    speaker_assignment_source: Optional[str] = None
    speaker_assignment_authority: Optional[str] = None
    updated_at: Optional[str] = None
    overlapping_speakers: List[str] = []


class TranscriptUtteranceListRead(BaseModel):
    recording_id: str
    settling_required: bool = Field(
        default=False, exclude_if=lambda value: value is False
    )
    revision: int
    utterances: List[TranscriptUtteranceRead]
    tombstones: List[str] = []
    speakers: List[RecordingSpeakerPublicRead] = []
    # Live label -> the name the user gave it during capture. Sent whole on every
    # read, delta or not: naming a live speaker changes no utterance.
    live_speaker_names: dict[str, str] = {}


class TranscriptUtteranceSplit(BaseModel):
    """Split immediately before a current genuine word identity."""

    before_word_id: str
    expected_revision: int = Field(ge=1)


class NotesUpdate(BaseModel):
    notes: str


class MeetingEdgeFocusUpdate(BaseModel):
    meeting_edge_focus: str


class ChatRequest(BaseModel):
    message: str
    tag_ids: Optional[List[int]] = None


def _build_speaker_map(speakers) -> dict:
    """Build a mapping from diarization label to speaker name."""
    speaker_map = {}
    for rs in speakers:
        name = (
            rs.local_name
            or (rs.global_speaker.name if rs.global_speaker else None)
            or rs.name
            or rs.diarization_label
        )
        speaker_map[rs.diarization_label] = name
    return speaker_map


def _format_transcript_text(segments, speaker_map: dict) -> str:
    """Format transcript segments as text."""
    lines = []
    for seg in segments:
        speaker_label = seg.get("speaker", "Unknown")
        speaker_name = speaker_display_name(speaker_label, speaker_map)
        start = seg.get("start", 0)
        minutes = int(start // 60)
        seconds = int(start % 60)
        time_str = f"[{minutes:02d}:{seconds:02d}]"
        text = seg.get("text", "").strip()
        lines.append(f"{time_str} {speaker_name}: {text}")
    return "\n".join(lines)


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file output."""
    return "".join(
        [c for c in filename if c.isalpha() or c.isdigit() or c in (" ", "-", "_", ".")]
    ).strip()


def _get_recording_speaker_display_name(recording_speaker: RecordingSpeaker) -> str:
    return (
        recording_speaker.local_name
        or (
            recording_speaker.global_speaker.name
            if recording_speaker.global_speaker
            else None
        )
        or recording_speaker.name
        or recording_speaker.diarization_label
    )


import re2


def _apply_find_replace(
    transcript: Transcript,
    find_text: str,
    replace_text: str,
    case_sensitive: bool = False,
    use_regex: bool = False,
) -> int:
    """
    Apply find and replace to both transcript segments and notes.
    Returns the number of segment replacements made.
    """
    if len(find_text) > 1000:
        raise HTTPException(
            status_code=400, detail="Search pattern is too long (max 1000 characters)"
        )

    total_segment_replacements = 0

    # Path 1: Simple string replacement (Case Sensitive + No Regex)
    # this is the fastest and safest path
    if not use_regex and case_sensitive:
        for segment in transcript.segments:
            if find_text in segment["text"]:
                new_text = segment["text"].replace(find_text, replace_text)
                if new_text != segment["text"]:
                    segment["text"] = new_text
                    segment["text_manually_edited"] = True
                    total_segment_replacements += 1

        if transcript.notes and find_text in transcript.notes:
            transcript.notes = transcript.notes.replace(find_text, replace_text)

    else:
        # Path 2: Regex-based replacement (Case Insensitive OR Explicit Regex)

        # google-re2 does not support flags arg in compile. We must use inline flags.
        # e.g. (?i) for ignore case.

        prefix = ""
        if not case_sensitive:
            prefix = "(?i)"

        if use_regex:
            pattern = prefix + find_text
        else:
            pattern = prefix + re2.escape(find_text)

        try:
            regex = re2.compile(pattern)
        except re2.error:
            raise HTTPException(status_code=400, detail="Invalid regular expression")

        for segment in transcript.segments:
            new_text, count = regex.subn(replace_text, segment["text"])
            if count > 0:
                segment["text"] = new_text
                segment["text_manually_edited"] = True
                total_segment_replacements += 1

        if transcript.notes:
            transcript.notes = regex.sub(replace_text, transcript.notes)

    if total_segment_replacements > 0:
        flag_modified(transcript, "segments")
        full_text = " ".join([s["text"] for s in transcript.segments])
        transcript.text = full_text

    return total_segment_replacements
