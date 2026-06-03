from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence
from uuid import uuid4

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.models.pipeline import (
    DiarizationWindowResult,
    DiarizationWindowTurn,
    ProcessingRun,
    ProcessingRunKind,
    ProcessingRunStatus,
    RecordingSpeakerAlias,
    RecordingSpeakerAliasType,
    SpeakerCorrectionEvent,
    SpeakerCorrectionEventType,
    SpeakerCorrectionScope,
    TranscriptUtterance,
    TranscriptUtteranceEvent,
    TranscriptUtteranceState,
)
from backend.models.recording import (
    Recording,
    RecordingPipelineGeneration,
    RecordingStatus,
)
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.models.transcript import Transcript
from backend.processing.embedding import merge_embeddings
from backend.utils.speaker_assignment import matches_speaker_name, reconcile_segment_assignment
from backend.utils.time import utc_now


IN_FLIGHT_RECORDING_STATUSES = {
    RecordingStatus.UPLOADING.value,
    RecordingStatus.QUEUED.value,
    RecordingStatus.PROCESSING.value,
}

ACTIVE_UTTERANCE_STATES = {
    TranscriptUtteranceState.PROVISIONAL.value,
    TranscriptUtteranceState.STABLE.value,
    TranscriptUtteranceState.FINALIZED.value,
}

TOMBSTONE_UTTERANCE_STATES = {
    TranscriptUtteranceState.SUPERSEDED.value,
    TranscriptUtteranceState.DELETED.value,
}

UNKNOWN_SPEAKER = "UNKNOWN"
LABEL_PATTERN = re.compile(r"^(LIVE_\d+|SPEAKER_\d+|MANUAL_[A-Za-z0-9]+)$")
GENERIC_SPEAKER_DISPLAY_PATTERN = re.compile(r"^Speaker\s+\d+$", re.IGNORECASE)

ROLLING_DIARIZATION_MANUAL_WEIGHT = 6.0
ROLLING_DIARIZATION_CONTINUITY_WEIGHT = 4.0
ROLLING_DIARIZATION_UTTERANCE_WEIGHT = 2.0
ROLLING_DIARIZATION_EMBEDDING_WEIGHT = 2000.0
ROLLING_DIARIZATION_GLOBAL_WEIGHT = 1500.0
ROLLING_DIARIZATION_MIN_UTTERANCE_OVERLAP_MS = 250
ROLLING_DIARIZATION_MIN_TURN_MATCH_MARGIN = 150.0
ROLLING_DIARIZATION_CONFIDENCE_FLOOR = 0.50
ROLLING_DIARIZATION_EXISTING_CONFIDENCE_MARGIN = 0.15
ROLLING_DIARIZATION_STABLE_WINDOW_COUNT = 2
ROLLING_DIARIZATION_MERGE_BOUNDARY_GAP_MS = 150
ROLLING_DIARIZATION_DISTINCT_LOCAL_SPEAKER_EMBEDDING_THRESHOLD = 0.72
ROLLING_DIARIZATION_DISTINCT_LOCAL_SPEAKER_MIN_DURATION_MS = 1000
# Diarization-boundary splitting (used when the word-level splitter cannot
# split an utterance, e.g. because some words have no overlapping turn).
ROLLING_DIARIZATION_BOUNDARY_SPLIT_MIN_OVERLAP_MS = 250
ROLLING_DIARIZATION_BOUNDARY_SPLIT_MIN_RATIO = 0.15
# Ambiguity flagging on the max-overlap fallback path. When two speakers each
# hold at least this fraction of the utterance, the assignment is considered
# a boundary/transition utterance and its confidence is dampened.
ROLLING_DIARIZATION_BOUNDARY_AMBIGUITY_RATIO = 0.30
ROLLING_DIARIZATION_BOUNDARY_CONFIDENCE_DAMPENER = 0.6
ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL = "provisional"
ROLLING_DIARIZATION_SPEAKER_STATE_STABLE = "stable"
ROLLING_DIARIZATION_SPEAKER_STATE_MANUAL_OVERRIDE = "manual_override"

SPEAKER_ASSIGNMENT_SOURCE_LEGACY = "legacy"
SPEAKER_ASSIGNMENT_SOURCE_LIVE = "live"
SPEAKER_ASSIGNMENT_SOURCE_CATCH_UP = "catch_up"
SPEAKER_ASSIGNMENT_SOURCE_FINALIZE = "finalize"
SPEAKER_ASSIGNMENT_SOURCE_BACKFILL = "backfill"
SPEAKER_ASSIGNMENT_SOURCE_COMPATIBILITY_REPLACE = "compatibility_replace"
SPEAKER_ASSIGNMENT_SOURCE_MANUAL = "manual"
SPEAKER_ASSIGNMENT_SOURCE_CORRECTION_REPLAY = "speaker_correction_replay"
SPEAKER_ASSIGNMENT_SOURCE_IDENTITY_REPLAY = "identity_replay"

SPEAKER_ASSIGNMENT_AUTHORITY_PROVISIONAL = "provisional"
SPEAKER_ASSIGNMENT_AUTHORITY_FINALIZED = "finalized"
SPEAKER_ASSIGNMENT_AUTHORITY_MANUAL = "manual"
SPEAKER_ASSIGNMENT_AUTHORITY_RANK = {
    SPEAKER_ASSIGNMENT_AUTHORITY_PROVISIONAL: 0,
    SPEAKER_ASSIGNMENT_AUTHORITY_FINALIZED: 1,
    SPEAKER_ASSIGNMENT_AUTHORITY_MANUAL: 2,
}

logger = logging.getLogger(__name__)



def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None



def _to_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None



def _range_overlap_ms(
    start_a: int,
    end_a: int,
    start_b: int,
    end_b: int,
) -> int:
    return max(0, min(int(end_a), int(end_b)) - max(int(start_a), int(start_b)))



def _segment_to_ms(value: Any) -> int:
    return int(round(float(value or 0.0) * 1000.0))



def _sort_key_for_index(index: int) -> str:
    return f"{index:012d}"



def _segments_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_start = float(first.get("start", 0.0))
    first_end = float(first.get("end", 0.0))
    second_start = float(second.get("start", 0.0))
    second_end = float(second.get("end", 0.0))
    return first_start < second_end and second_start < first_end



def _build_overlap_groups(segments: Sequence[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    adjacency: dict[int, set[int]] = defaultdict(set)
    for index, segment in enumerate(segments):
        for other_index in range(index + 1, len(segments)):
            other_segment = segments[other_index]
            if _segments_overlap(segment, other_segment):
                adjacency[index].add(other_index)
                adjacency[other_index].add(index)

    groups: dict[int, dict[str, Any]] = {}
    visited: set[int] = set()
    for index in range(len(segments)):
        if index in visited or not adjacency.get(index):
            continue
        stack = [index]
        members: list[int] = []
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            members.append(current)
            stack.extend(adjacency.get(current, []))
        members.sort(key=lambda item: (float(segments[item].get("start", 0.0)), item))
        group_id = str(uuid4())
        for rank, member in enumerate(members):
            groups[member] = {"group_id": group_id, "rank": rank, "members": members}
    return groups



def _alias_type_for_value(value: str) -> RecordingSpeakerAliasType:
    if value.startswith("LIVE_"):
        return RecordingSpeakerAliasType.LIVE_LABEL
    if value.startswith("MANUAL_"):
        return RecordingSpeakerAliasType.MANUAL_LABEL
    if value.startswith("SPEAKER_"):
        return RecordingSpeakerAliasType.DIARIZATION_LABEL
    return RecordingSpeakerAliasType.IMPORT_LABEL



def _speaker_kind_for_label(label: str, source_segment: dict[str, Any]) -> str:
    if label.startswith("LIVE_"):
        return "live"
    if label.startswith("MANUAL_"):
        return "manual"
    if str(source_segment.get("segment_source") or "") == "import":
        return "imported"
    return "automated"



def _get_utterance(session, recording_id: int, utterance_public_id: str) -> TranscriptUtterance | None:
    statement = (
        select(TranscriptUtterance)
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.public_id == utterance_public_id)
    )
    return session.execute(statement).scalar_one_or_none()



def _load_transcript(session, recording_id: int) -> Transcript | None:
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    return session.execute(statement).scalar_one_or_none()



def _load_recording_speakers(session, recording_id: int) -> list[RecordingSpeaker]:
    statement = (
        select(RecordingSpeaker)
        .where(RecordingSpeaker.recording_id == recording_id)
        .options(selectinload(RecordingSpeaker.global_speaker))
    )
    return list(session.execute(statement).scalars().all())



def _creation_event_type(
    *,
    source: str,
    state: TranscriptUtteranceState | str | None,
) -> str:
    if source == "backfill":
        return "backfilled"
    state_value = state.value if hasattr(state, "value") else str(state or "")
    if source == "finalize" or state_value == TranscriptUtteranceState.FINALIZED.value:
        return "finalize"
    return "created"



def _compatibility_replace_event_type(
    old_values: dict[str, Any],
    new_values: dict[str, Any],
) -> str:
    timing_changed = (
        old_values.get("start_ms") != new_values.get("start_ms")
        or old_values.get("end_ms") != new_values.get("end_ms")
    )
    text_changed = old_values.get("text") != new_values.get("text")
    speaker_changed = old_values.get("speaker") != new_values.get("speaker")

    if timing_changed and not text_changed and not speaker_changed:
        return "update_timing"
    if text_changed and not timing_changed and not speaker_changed:
        return "update_text"
    if speaker_changed and not timing_changed and not text_changed:
        return "update_speaker"
    return "compatibility_replace"



def _event_type_for_scope(scope: SpeakerCorrectionScope) -> SpeakerCorrectionEventType:
    if scope == SpeakerCorrectionScope.MERGE_INTO_SPEAKER:
        return SpeakerCorrectionEventType.MERGE_SPEAKERS
    if scope == SpeakerCorrectionScope.FROM_THIS_UTTERANCE_FORWARD:
        return SpeakerCorrectionEventType.ASSIGN_FROM_NOW_ON
    if scope == SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING:
        return SpeakerCorrectionEventType.ASSIGN_RECORDING_SPEAKER
    return SpeakerCorrectionEventType.ASSIGN_UTTERANCE

def _recording_speaker_display_name(session, recording_speaker: RecordingSpeaker) -> str:
    if recording_speaker.local_name:
        return recording_speaker.local_name
    global_speaker = getattr(recording_speaker, "global_speaker", None)
    if global_speaker is None and recording_speaker.global_speaker_id:
        global_speaker = session.get(GlobalSpeaker, recording_speaker.global_speaker_id)
    if global_speaker is not None and getattr(global_speaker, "name", None):
        return global_speaker.name
    if recording_speaker.name:
        return recording_speaker.name
    return recording_speaker.diarization_label or UNKNOWN_SPEAKER



def _utterance_ranges_overlap(
    first: TranscriptUtterance,
    second: TranscriptUtterance,
) -> bool:
    if first.start_ms == second.start_ms and first.end_ms == second.end_ms:
        return True
    return first.start_ms < second.end_ms and second.start_ms < first.end_ms



def _append_utterance_event(
    session,
    *,
    utterance: TranscriptUtterance,
    event_type: str,
    source: str,
    old_values: dict[str, Any] | None,
    new_values: dict[str, Any] | None,
    resulting_revision: int,
    processing_run_id: int | None = None,
    actor_user_id: int | None = None,
) -> TranscriptUtteranceEvent:
    event = TranscriptUtteranceEvent(
        recording_id=utterance.recording_id,
        utterance_id=utterance.id,
        processing_run_id=processing_run_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        source=source,
        old_values=old_values,
        new_values=new_values,
        resulting_revision=resulting_revision,
    )
    session.add(event)
    session.flush()
    utterance.last_utterance_event_id = event.id
    session.add(utterance)
    return event



def _record_manual_lock_events(
    session,
    *,
    utterance: TranscriptUtterance,
    old_text_locked: bool,
    old_speaker_locked: bool,
    source: str,
    actor_user_id: int | None = None,
    processing_run_id: int | None = None,
) -> None:
    if not old_text_locked and utterance.manual_text_locked:
        _append_utterance_event(
            session,
            utterance=utterance,
            event_type="manual_lock_text",
            source=source,
            old_values={"manual_text_locked": False},
            new_values={"manual_text_locked": True},
            resulting_revision=utterance.revision,
            processing_run_id=processing_run_id,
            actor_user_id=actor_user_id,
        )
    if not old_speaker_locked and utterance.manual_speaker_locked:
        _append_utterance_event(
            session,
            utterance=utterance,
            event_type="manual_lock_speaker",
            source=source,
            old_values={"manual_speaker_locked": False},
            new_values={"manual_speaker_locked": True},
            resulting_revision=utterance.revision,
            processing_run_id=processing_run_id,
            actor_user_id=actor_user_id,
        )




__all__ = [name for name in globals() if not name.startswith('__')]
