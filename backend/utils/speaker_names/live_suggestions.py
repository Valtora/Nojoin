"""Pending live self-introductions, kept in the existing naming review state.

Proposals are not naming acts: only the live rename route writes the user's
anchored event. A dismissed label stays dismissed for the rest of the capture.
"""

from collections.abc import Sequence
from typing import Any

from sqlmodel import col, select

from backend.models.calendar import CalendarEvent
from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.utils.canonical_pipeline import build_transcript_segments_for_read
from backend.utils.canonical_pipeline.constants import ACTIVE_UTTERANCE_STATES
from backend.utils.live_speaker_labels import live_speaker_display_name
from backend.utils.meeting_notes import (
    MeetingEventContext,
    meeting_event_context_from_calendar_event,
)
from backend.utils.speaker_name_suggestions import (
    SPEAKER_SUGGESTION_STATUS_PENDING,
    SPEAKER_SUGGESTION_STATUS_REJECTED,
    SPEAKER_SUGGESTION_STATUS_SUPERSEDED,
    SpeakerInferenceSuggestion,
    build_persisted_speaker_suggestion,
    detect_rule_based_speaker_suggestions,
    load_transcript_speaker_suggestions,
    resolve_pending_transcript_speaker_suggestion,
)
from backend.utils.transcript_settling import (
    load_capture_publication,
    publication_is_current,
)

from .live import live_speaker_names

LIVE_SUGGESTION_ORIGIN = "live_self_introduction"


def suggest_live_speaker_names(
    session: Any,
    transcript: Transcript,
    segments: Sequence[dict[str, Any]],
    meeting_context: MeetingEventContext | None = None,
) -> int:
    """Review current evidence for unnamed labels, under the capture fence."""
    suggestions = load_transcript_speaker_suggestions(transcript)
    handled = {
        item["diarization_label"]
        for item in suggestions
        if item.get("origin") == LIVE_SUGGESTION_ORIGIN
        and item.get("status") == SPEAKER_SUGGESTION_STATUS_REJECTED
    }
    labels = {
        str(segment.get("speaker", ""))
        for segment in segments
        if live_speaker_display_name(segment.get("speaker")) is not None
    }
    named = live_speaker_names(session, transcript.recording_id, labels=labels)
    eligible = sorted(labels - handled - named.keys())
    candidates = {
        item.diarization_label: item
        for item in detect_rule_based_speaker_suggestions(
            segments, eligible, meeting_context
        ).suggestions
    }
    changed = _retire_stale_proposals(transcript, suggestions, candidates)
    pending = {
        item["diarization_label"]
        for item in load_transcript_speaker_suggestions(transcript)
        if item.get("origin") == LIVE_SUGGESTION_ORIGIN
        and item.get("status") == SPEAKER_SUGGESTION_STATUS_PENDING
    }
    additions = [
        build_persisted_speaker_suggestion(item, origin=LIVE_SUGGESTION_ORIGIN)
        for label, item in candidates.items()
        if label not in pending
    ]
    if additions:
        transcript.speaker_name_suggestions = (
            load_transcript_speaker_suggestions(transcript) + additions
        )
    if changed or additions:
        session.add(transcript)
    return len(additions)


def _retire_stale_proposals(
    transcript: Transcript,
    suggestions: list[dict[str, Any]],
    candidates: dict[str, SpeakerInferenceSuggestion],
) -> bool:
    """Unlocked words and labels can move; their old proposal must not linger."""
    changed = False
    for item in suggestions:
        if item.get("origin") != LIVE_SUGGESTION_ORIGIN:
            continue
        if item.get("status") != SPEAKER_SUGGESTION_STATUS_PENDING:
            continue
        label = item["diarization_label"]
        candidate = candidates.get(label)
        if candidate is not None and candidate.suggested_name == item["suggested_name"]:
            continue
        resolve_pending_transcript_speaker_suggestion(
            transcript,
            diarization_label=label,
            resolution=SPEAKER_SUGGESTION_STATUS_SUPERSEDED,
            reason="live_evidence_changed",
        )
        changed = True
    return changed


def refresh_live_speaker_suggestions(session: Any, recording_id: int) -> None:
    """Evaluate current live lines before Meeting Edge's AI-only guards.

    The fence reloads both review state and capture eligibility after waiting
    for a rename, dismissal or Stop. Commit even a no-op to release the fence
    before the potentially slow AI refresh starts.
    """
    transcript, recording = load_capture_publication(session, recording_id)
    if transcript is not None and publication_is_current(recording, transcript, None):
        segments = build_transcript_segments_for_read(
            session, recording_id, transcript=transcript
        )
        event = (
            session.get(CalendarEvent, recording.calendar_event_id)
            if recording.calendar_event_id is not None
            else None
        )
        suggest_live_speaker_names(
            session,
            transcript,
            segments,
            meeting_event_context_from_calendar_event(event),
        )
    session.commit()


def live_speaker_suggestions(
    session: Any, recording: Recording, transcript: Transcript
) -> dict[str, str]:
    """Whole pending map for current unnamed labels, including on empty deltas."""
    if not publication_is_current(recording, transcript, None):
        return {}
    pending = {
        item["diarization_label"]: item["suggested_name"]
        for item in load_transcript_speaker_suggestions(transcript)
        if item.get("origin") == LIVE_SUGGESTION_ORIGIN
        and item.get("status") == SPEAKER_SUGGESTION_STATUS_PENDING
    }
    if not pending:
        return {}
    heard = set(
        session.execute(
            select(TranscriptUtterance.speaker_label)
            .where(TranscriptUtterance.recording_id == recording.id)
            .where(col(TranscriptUtterance.state).in_(ACTIVE_UTTERANCE_STATES))
            .where(col(TranscriptUtterance.speaker_label).in_(pending))
        ).scalars()
    )
    named = live_speaker_names(session, recording.id, labels=heard)
    return {
        label: name for label, name in pending.items() if label in heard - named.keys()
    }
