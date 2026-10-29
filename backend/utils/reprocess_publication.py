"""Retain an ordinary publication while its replacement is being computed."""

from sqlalchemy import delete
from sqlmodel import Session, select

from backend.models.chat import ChatMessage
from backend.models.context_chunk import ContextChunk
from backend.models.pipeline import (
    DiarizationWindowResult,
    ProcessingRun,
    SpeakerCorrectionEvent,
    TranscriptUtterance,
)
from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.transcript import Transcript
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.transcript_change import change_sequence_bump


def reprocess_publication_pending(transcript: Transcript | None) -> bool:
    """An explicit durable marker distinguishes retained work from live drafts."""
    return (getattr(transcript, "settling_state", None) or {}).get(
        "reprocess_pending"
    ) is True


def reprocess_replacement_running(transcript: Transcript | None) -> bool:
    """A retained publication whose replacement has neither published nor failed."""
    return (
        transcript is not None
        and transcript.transcript_status == "pending"
        and reprocess_publication_pending(transcript)
    )


REPROCESS_RUNNING_MESSAGE = (
    "This recording is being reprocessed. Try again once processing has finished."
)


def reprocess_publication_complete(transcript: Transcript | None) -> bool:
    """A replacement remains readable during optional post-publication work."""
    return (
        (getattr(transcript, "settling_state", None) or {}).get("reprocess_published")
        is True
        and transcript is not None
        and transcript.transcript_status == "completed"
    )


def mark_reprocess_pending(transcript: Transcript) -> None:
    """Store the independent marker without enabling settled edit semantics."""
    transcript.settling_state = {
        **(transcript.settling_state or {}),
        "reprocess_pending": True,
    }


def _drop_marker(transcript: Transcript, key: str) -> None:
    state = dict(transcript.settling_state or {})
    state.pop(key, None)
    transcript.settling_state = state or None


def clear_reprocess_pending(transcript: Transcript) -> None:
    """Retire the marker in the same transaction that publishes its replacement."""
    _drop_marker(transcript, "reprocess_pending")


def clear_reprocess_published(transcript: Transcript) -> None:
    """Retire the post-publication marker once the run has finished."""
    _drop_marker(transcript, "reprocess_published")


def restore_retained_publication(session: Session, recording_id: int) -> None:
    """Hand a failed replacement's recording back the publication it retained.

    Requeue set the retained transcript to pending. Left so, the failed
    recording would still read as a running reprocess: notes generation would
    stay refused, and the next status update would move it to QUEUED with no
    task to finish it.
    Requeue only retains a completed transcript, so that is what it goes back to.
    A failed transcription keeps its error and the marker: the recording reports
    the failure over the retained transcript, and a later Retry retains it again.
    """
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()
    if transcript is not None and reprocess_replacement_running(transcript):
        clear_reprocess_pending(transcript)
        transcript.transcript_status = "completed"
        session.add(transcript)


def clear_previous_reprocess_state(session: Session, recording_id: int) -> None:
    """Retire old generated dependencies under the publication fence, without committing.

    The caller replaces speakers, canonical rows and projection before committing.
    A rollback therefore restores the whole previous publication. Notes, title,
    suggestions and Meeting Edge retain their individual regeneration boundaries.
    """
    transcript = _load_transcript(session, recording_id, lock=True)
    session.execute(delete(ChatMessage).where(ChatMessage.recording_id == recording_id))
    session.execute(
        delete(ContextChunk)
        .where(ContextChunk.recording_id == recording_id)
        .where(ContextChunk.document_id.is_(None))
    )
    for model in (
        SpeakerCorrectionEvent,
        SpeakerNameEvent,
        TranscriptUtterance,
        RecordingSpeaker,
        ProcessingRun,
        DiarizationWindowResult,
    ):
        session.execute(delete(model).where(model.recording_id == recording_id))
    if transcript is not None:
        # Capture eligibility is runtime state. Leaving it authoritative after
        # canonical-off publication would hide the new compatibility projection.
        if reprocess_publication_pending(transcript):
            transcript.settling_state = {
                "reprocess_pending": True,
                "reprocess_published": True,
            }
        defaults = Transcript(recording_id=recording_id)
        for field in (
            "analytics_payload",
            "analytics_status",
            "analytics_error_message",
            "analytics_ai_status",
            "analytics_ai_error_message",
        ):
            setattr(transcript, field, getattr(defaults, field))
        session.add(transcript)
    # Canonical-off and no-speech replacements write no utterances, so the
    # publication itself tells finished pages the transcript changed.
    session.execute(change_sequence_bump(recording_id))
