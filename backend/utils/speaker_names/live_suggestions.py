"""Pending live name proposals, kept in the existing naming review state.

A proposal comes from the self-introduction rule or from asking the AI
provider (``backend.worker.tasks.live_naming_stage``); a label has at most one
pending at a time. Proposals are not naming acts: only the live rename route
writes the user's anchored event. A dismissed label stays dismissed for the
rest of the capture.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
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
    SpeakerInferenceResult,
    SpeakerInferenceSuggestion,
    build_persisted_speaker_suggestion,
    detect_rule_based_speaker_suggestions,
    is_personal_name,
    load_transcript_speaker_suggestions,
    resolve_pending_transcript_speaker_suggestion,
)
from backend.utils.transcript_settling import (
    load_capture_publication,
    publication_is_current,
)

from .live import live_speaker_names
from .sources import suggestion_source

LIVE_SUGGESTION_ORIGIN = "live_self_introduction"
LIVE_LLM_SUGGESTION_ORIGIN = "live_llm"
LIVE_PROPOSAL_ORIGINS = (LIVE_SUGGESTION_ORIGIN, LIVE_LLM_SUGGESTION_ORIGIN)
# One bookkeeping entry per label: when it was last asked about, and how much
# it had said by then.
LIVE_ASK_ORIGIN = "live_llm_ask"
# A label is asked about again once it has said this much more.
NEW_SPEECH_PER_ASK_SECONDS = 60.0


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
    """The rule's hits on unanswered labels without a pending AI proposal."""
    eligible = set(_unanswered_labels(session, transcript, _live_labels(segments)))
    eligible -= set(_pending_live_proposals(transcript, (LIVE_LLM_SUGGESTION_ORIGIN,)))
    return {
        item.diarization_label: item
        for item in detect_rule_based_speaker_suggestions(
            segments, sorted(eligible), meeting_context
        ).suggestions
    }


def _live_labels(segments: Sequence[dict[str, Any]]) -> set[str]:
    return {
        str(segment.get("speaker", ""))
        for segment in segments
        if live_speaker_display_name(segment.get("speaker")) is not None
    }


def _unanswered_labels(
    session: Any, transcript: Transcript, labels: set[str]
) -> list[str]:
    """The labels the user has neither named nor dismissed a proposal for."""
    dismissed = {
        item["diarization_label"]
        for item in load_transcript_speaker_suggestions(transcript)
        if item.get("origin") in LIVE_PROPOSAL_ORIGINS
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


def _pending_live_proposals(
    transcript: Transcript, origins: Sequence[str] = (LIVE_SUGGESTION_ORIGIN,)
) -> dict[str, str]:
    return {
        label: item["suggested_name"]
        for label, item in _pending_live_entries(transcript, origins).items()
    }


def _pending_live_entries(
    transcript: Transcript, origins: Sequence[str] = LIVE_PROPOSAL_ORIGINS
) -> dict[str, dict[str, Any]]:
    return {
        item["diarization_label"]: item
        for item in load_transcript_speaker_suggestions(transcript)
        if item.get("origin") in origins
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


def refresh_live_speaker_suggestions(
    session: Any, recording_id: int
) -> dict[str, float]:
    """Evaluate current live lines for self-introductions.

    Most checks change nothing, so the rule first runs on an unlocked read
    and the capture fence is taken only when its hits differ from the stored
    proposals. Under the fence everything is read again, after waiting for a
    rename, dismissal or Stop.

    Returns the labels due an AI ask (``_due_for_ask``), with their speech.
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
    due = _due_for_ask(session, *evidence[:2]) if evidence else {}
    session.commit()
    return due


def _due_for_ask(
    session: Any, transcript: Transcript, segments: Sequence[dict[str, Any]]
) -> dict[str, float]:
    """Unanswered labels with a rule proposal newer than their last ask, or
    ``NEW_SPEECH_PER_ASK_SECONDS`` more speech since it, with their speech."""
    asks = {
        item["diarization_label"]: item
        for item in load_transcript_speaker_suggestions(transcript)
        if item.get("origin") == LIVE_ASK_ORIGIN
    }
    introduced = {
        label
        for label, item in _pending_live_entries(
            transcript, (LIVE_SUGGESTION_ORIGIN,)
        ).items()
        if item["created_at"] > asks.get(label, {}).get("asked_at", "")
    }
    speech: dict[str, float] = {}
    for segment in segments:
        label = str(segment.get("speaker", ""))
        length = float(segment.get("end") or 0.0) - float(segment.get("start") or 0.0)
        speech[label] = speech.get(label, 0.0) + max(length, 0.0)
    return {
        label: speech.get(label, 0.0)
        for label in _unanswered_labels(session, transcript, _live_labels(segments))
        if label in introduced
        or speech.get(label, 0.0)
        - float(asks.get(label, {}).get("speech_seconds") or 0.0)
        >= NEW_SPEECH_PER_ASK_SECONDS
    }


def mark_live_asks(
    session: Any, recording_id: int
) -> tuple[list[str], list[dict[str, Any]], MeetingEventContext | None] | None:
    """Record an ask for the labels due one, under the capture fence.

    Recorded before the call, so a failing provider is tried again only after
    more speech. Returns the labels with the lines and context to ask about.
    """
    evidence = _live_evidence(session, recording_id, lock=True)
    due = _due_for_ask(session, *evidence[:2]) if evidence is not None else {}
    if evidence is not None and due:
        transcript = evidence[0]
        now = datetime.now(UTC).isoformat()
        transcript.speaker_name_suggestions = [
            item
            for item in load_transcript_speaker_suggestions(transcript)
            if not (
                item.get("origin") == LIVE_ASK_ORIGIN
                and item.get("diarization_label") in due
            )
        ] + [
            {
                "origin": LIVE_ASK_ORIGIN,
                "diarization_label": label,
                "speech_seconds": round(speech, 3),
                "asked_at": now,
            }
            for label, speech in due.items()
        ]
        session.add(transcript)
    session.commit()
    if evidence is None or not due:
        return None
    return sorted(due), evidence[1], evidence[2]


def store_live_llm_suggestions(
    session: Any,
    recording_id: int,
    result: SpeakerInferenceResult,
    provider: str | None,
) -> int:
    """Offer the model's names, under the capture fence; returns how many.

    An omitted label or a role is no proposal. A name that agrees with the
    label's pending one leaves it as it is; another replaces it.
    """
    evidence = _live_evidence(session, recording_id, lock=True)
    if evidence is None:
        session.commit()
        return 0
    transcript, segments, _ = evidence
    eligible = set(_unanswered_labels(session, transcript, _live_labels(segments)))
    pending = _pending_live_proposals(transcript, LIVE_PROPOSAL_ORIGINS)
    added = [
        item
        for item in result.suggestions
        if item.diarization_label in eligible
        and is_personal_name(item.suggested_name)
        and pending.get(item.diarization_label, "").casefold()
        != item.suggested_name.casefold()
    ]
    for item in added:
        resolve_pending_transcript_speaker_suggestion(
            transcript,
            diarization_label=item.diarization_label,
            resolution=SPEAKER_SUGGESTION_STATUS_SUPERSEDED,
            reason="replaced_by_llm",
        )
    if added:
        transcript.speaker_name_suggestions = load_transcript_speaker_suggestions(
            transcript
        ) + [
            build_persisted_speaker_suggestion(
                item, origin=LIVE_LLM_SUGGESTION_ORIGIN, provider=provider
            )
            for item in added
        ]
        session.add(transcript)
    session.commit()
    return len(added)


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
    pending = _pending_live_proposals(transcript, LIVE_PROPOSAL_ORIGINS)
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


def live_suggestion_sources(
    transcript: Transcript, labels: Sequence[str]
) -> dict[str, str]:
    """Where each shown proposal came from, as a name's source: ``heuristic``
    for a self-introduction, ``llm`` for the AI provider."""
    pending = _pending_live_entries(transcript)
    return {
        label: suggestion_source(pending[label]) for label in labels if label in pending
    }


def shown_in_transcript_read(item: Mapping[str, Any]) -> bool:
    """A pending live proposal names a live label, shown only by the live read
    while capture runs; after Stop that label no longer exists. An ask record
    is bookkeeping, not a suggestion."""
    origin = item.get("origin")
    if origin == LIVE_ASK_ORIGIN:
        return False
    return not (
        origin in LIVE_PROPOSAL_ORIGINS
        and item.get("status") == SPEAKER_SUGGESTION_STATUS_PENDING
    )
