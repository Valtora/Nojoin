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
    candidates = _live_candidates(session, transcript, segments, meeting_context)
    changed = _retire_stale_proposals(transcript, suggestions, candidates)
    pending = _pending_live_proposals(transcript)
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


def _live_candidates(
    session: Any,
    transcript: Transcript,
    segments: Sequence[dict[str, Any]],
    meeting_context: MeetingEventContext | None,
) -> dict[str, SpeakerInferenceSuggestion]:
    """The rule's hits on live labels the user has neither named nor dismissed."""
    labels = {
        str(segment.get("speaker", ""))
        for segment in segments
        if live_speaker_display_name(segment.get("speaker")) is not None
    }
    return {
        item.diarization_label: item
        for item in detect_rule_based_speaker_suggestions(
            segments, _unanswered_labels(session, transcript, labels), meeting_context
        ).suggestions
    }


def _unanswered_labels(
    session: Any, transcript: Transcript, labels: set[str]
) -> list[str]:
    """The labels the user has neither named nor dismissed a proposal for."""
    dismissed = {
        item["diarization_label"]
        for item in load_transcript_speaker_suggestions(transcript)
        if item.get("origin") == LIVE_SUGGESTION_ORIGIN
        and item.get("status") == SPEAKER_SUGGESTION_STATUS_REJECTED
    }
    named = live_speaker_names(session, transcript.recording_id, labels=labels)
    return sorted(labels - dismissed - named.keys())


def has_unnamed_live_labels(session: Any, transcript: Transcript) -> bool:
    """Whether a current live line carries a label still open to a proposal.

    Without one, a naming check has nothing to do and is not queued.
    """
    heard = session.execute(
        select(TranscriptUtterance.speaker_label)
        .where(TranscriptUtterance.recording_id == transcript.recording_id)
        .where(col(TranscriptUtterance.state).in_(ACTIVE_UTTERANCE_STATES))
        .distinct()
    ).scalars()
    labels = {str(label) for label in heard if live_speaker_display_name(label)}
    return bool(labels and _unanswered_labels(session, transcript, labels))


def _pending_live_proposals(transcript: Transcript) -> dict[str, str]:
    return {
        item["diarization_label"]: item["suggested_name"]
        for item in load_transcript_speaker_suggestions(transcript)
        if item.get("origin") == LIVE_SUGGESTION_ORIGIN
        and item.get("status") == SPEAKER_SUGGESTION_STATUS_PENDING
    }


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
    """Evaluate current live lines for self-introductions.

    Most checks change nothing, so the rule first runs on an unlocked read
    and the capture fence is taken only when its hits differ from the stored
    proposals. Under the fence everything is read again, after waiting for a
    rename, dismissal or Stop.
    """
    evidence = _live_evidence(session, recording_id, lock=False)
    if evidence is not None:
        hits = _live_candidates(session, *evidence)
        if {label: item.suggested_name for label, item in hits.items()} != (
            _pending_live_proposals(evidence[0])
        ):
            evidence = _live_evidence(session, recording_id, lock=True)
            if evidence is not None:
                suggest_live_speaker_names(session, *evidence)
    session.commit()


def _live_evidence(
    session: Any, recording_id: int, *, lock: bool
) -> tuple[Transcript, list[dict[str, Any]], MeetingEventContext | None] | None:
    transcript, recording = load_capture_publication(session, recording_id, lock=lock)
    if transcript is None or not publication_is_current(recording, transcript, None):
        return None
    segments = build_transcript_segments_for_read(
        session, recording_id, transcript=transcript
    )
    event = (
        session.get(CalendarEvent, recording.calendar_event_id)
        if recording.calendar_event_id is not None
        else None
    )
    return transcript, segments, meeting_event_context_from_calendar_event(event)


def live_speaker_suggestions(
    session: Any, recording: Recording, transcript: Transcript
) -> dict[str, str]:
    """Whole pending map for labels a current line carries, on every read.

    Naming a label resolves its proposal in the same commit, so a named label
    has none pending.
    """
    if not publication_is_current(recording, transcript, None):
        return {}
    pending = _pending_live_proposals(transcript)
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
    return {label: name for label, name in pending.items() if label in heard}
