from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any, Iterable, Sequence
from uuid import uuid4

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.models.pipeline import (
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
from backend.models.transcript import Transcript
from backend.processing.browser_live_audio import (
    source_channel_from_confidence_payload,
)
from backend.processing.embedding import (
    LibraryVoiceprintUpdate,
    RecordingVoiceprint,
    fold_recording_voiceprint,
)
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.speaker_assignment import (
    matches_speaker_name,
    reconcile_segment_assignment,
    remap_overlapping_speakers,
)
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

ROLLING_DIARIZATION_SPEAKER_STATE_PROVISIONAL = "provisional"
ROLLING_DIARIZATION_SPEAKER_STATE_STABLE = "stable"
ROLLING_DIARIZATION_SPEAKER_STATE_MANUAL_OVERRIDE = "manual_override"
# confidence_payload key of a live speaker label: {"locked": bool, "share": float}.
# Read only while the utterance is provisional, so finalize never sees it.
LIVE_SPEAKER_LABEL_PAYLOAD_KEY = "live_speaker_label"

SPEAKER_ASSIGNMENT_SOURCE_LEGACY = "legacy"
SPEAKER_ASSIGNMENT_SOURCE_LIVE = "live"
SPEAKER_ASSIGNMENT_SOURCE_CATCH_UP = "catch_up"
SPEAKER_ASSIGNMENT_SOURCE_FINALIZE = "finalize"
SPEAKER_ASSIGNMENT_SOURCE_BACKFILL = "backfill"
SPEAKER_ASSIGNMENT_SOURCE_COMPATIBILITY_REPLACE = "compatibility_replace"
SPEAKER_ASSIGNMENT_SOURCE_MANUAL = "manual"
# Only older rows carry the two replay sources: speaker corrections no longer
# replay diarisation window rows.
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


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
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


def _build_overlap_groups(
    segments: Sequence[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
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


def _names_no_speaker(value: str) -> bool:
    """True for a segment speaker that no ``RecordingSpeaker`` may be made from.

    A live label (``LIVE_PROVISIONAL_<n>``) is no speaker of the recording: the
    line keeps it and readers show "Speaker <n>". A row made from it would be a
    nameable speaker, linkable to a person, that the final pass never chose.
    """
    return (
        not value
        or value == UNKNOWN_SPEAKER
        or (live_speaker_display_name(value) is not None)
    )


def _get_utterance(
    session, recording_id: int, utterance_public_id: str, *, lock: bool = False
) -> TranscriptUtterance | None:
    """The utterance; ``lock`` reads it ``FOR UPDATE``, current as of the lock.

    A speaker edit locks it before checking the client's revision, so a live
    label written meanwhile fails the check instead of being overwritten.
    """
    statement = (
        select(TranscriptUtterance)
        .where(TranscriptUtterance.recording_id == recording_id)
        .where(TranscriptUtterance.public_id == utterance_public_id)
    )
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    return session.execute(statement).scalar_one_or_none()


def _load_transcript(
    session, recording_id: int, *, lock: bool = False
) -> Transcript | None:
    """The transcript; ``lock`` reads it ``FOR UPDATE``, current as of the lock."""
    statement = select(Transcript).where(Transcript.recording_id == recording_id)
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
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
    timing_changed = old_values.get("start_ms") != new_values.get(
        "start_ms"
    ) or old_values.get("end_ms") != new_values.get("end_ms")
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


def _recording_speaker_display_name(
    session, recording_speaker: RecordingSpeaker
) -> str:
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


def _segment_edit_source(
    segment: dict[str, Any], key: str, *, locked: bool
) -> str | None:
    """Read an edit source off a segment payload, gated on its lock.

    A source only means something while the lock it describes is held, so an
    unlocked segment carrying a stale source resolves to None rather than
    claiming an edit the row no longer pins. The rebuild paths in core.py read
    their provenance through here, which is what carries an assistant's
    correction onto a replacement utterance when boundaries shift.
    """
    if not locked:
        return None
    value = segment.get(key)
    return str(value) if value else None


def _segment_diarization_label(segment: dict[str, Any]) -> str | None:
    """The diariser's cluster label carried on a segment, or None.

    Finalize stamps it before speaker assignment rewrites ``speaker``; the
    projection carries it back into a backfill. UNKNOWN is no label at all.
    """
    label = str(segment.get("diarization_label") or "").strip()
    if not label or label.upper() == UNKNOWN_SPEAKER:
        return None
    return label


def _touch_recording_speaker_bounds(
    recording_speaker: RecordingSpeaker, segment: dict[str, Any]
) -> None:
    start_ms = _segment_to_ms(segment.get("start", 0.0))
    end_ms = _segment_to_ms(segment.get("end", 0.0))
    if (
        recording_speaker.first_seen_ms is None
        or start_ms < recording_speaker.first_seen_ms
    ):
        recording_speaker.first_seen_ms = start_ms
    if (
        recording_speaker.last_seen_ms is None
        or end_ms > recording_speaker.last_seen_ms
    ):
        recording_speaker.last_seen_ms = end_ms


__all__ = [name for name in globals() if not name.startswith("__")]
