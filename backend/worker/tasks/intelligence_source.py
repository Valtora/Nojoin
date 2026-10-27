"""Fence derived products to the transcript publication their inputs describe."""

from sqlmodel import Session

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.transcript_settling import (
    CapturePublicationToken,
    capture_publication_token,
)


def load_intelligence_source(session: Session, recording_id: int) -> Transcript | None:
    """Read source inputs under the same fence that replaces their publication."""
    return _load_transcript(session, recording_id, lock=True)


def intelligence_source_is_current(
    session: Session, recording_id: int, source_revision: int | None
) -> bool:
    """Validate before any mutation; retain the fence until the writer commits."""
    transcript = load_intelligence_source(session, recording_id)
    current = (
        transcript is not None
        and source_revision is not None
        and transcript.change_sequence == source_revision
    )
    if not current:
        session.rollback()
    return current


def capture_meeting_edge_episode(
    session: Session, recording: Recording, transcript: Transcript
) -> CapturePublicationToken | None:
    """A live prompt may finish after more lines from its own capture arrive."""
    session.refresh(recording, attribute_names=["status", "capture_epoch"])
    state = transcript.settling_state or {}
    if (
        recording.status in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}
        and not state.get("sealed")
        and state.get("phase", "live") == "live"
    ):
        return capture_publication_token(recording, transcript)
    return None


def meeting_edge_source_is_current(
    session: Session,
    recording_id: int,
    source_revision: int | None,
    source_capture: CapturePublicationToken | None,
    source_signature: str | None = None,
) -> bool:
    """Accept live growth only within the same still-recording capture episode."""
    transcript = load_intelligence_source(session, recording_id)
    recording = session.get(Recording, recording_id)
    if recording is not None:
        session.refresh(recording, attribute_names=["status", "capture_epoch"])
    current = (
        transcript is not None
        and recording is not None
        and recording.status
        in {
            RecordingStatus.UPLOADING,
            RecordingStatus.PAUSED,
            RecordingStatus.QUEUED,
            RecordingStatus.PROCESSING,
        }
        and transcript.change_sequence == source_revision
    )
    if source_capture is not None:
        current = (
            transcript is not None
            and recording is not None
            and capture_meeting_edge_episode(session, recording, transcript)
            == source_capture
        )
    if source_signature is not None:
        current = (
            current and transcript.meeting_edge_source_signature == source_signature
        )
    if not current:
        session.rollback()
    return current


def clear_meeting_edge_error(session: Session, recording_id: int) -> None:
    """Retire transient guidance state under the existing derived-write fence.

    The lifecycle caller commits this together with its capture/terminal state.
    Clearing the signature also fences an older run if a retry keeps the words.
    """
    transcript = load_intelligence_source(session, recording_id)
    if transcript is None:
        return
    payload = (
        transcript.meeting_edge_payload
        if isinstance(transcript.meeting_edge_payload, dict)
        else {}
    )
    useful = any(
        payload.get(key)
        for key in (
            "summary",
            "questions",
            "points",
            "concepts",
            "concept_history",
            "history",
        )
    )
    transcript.meeting_edge_status = "ready" if useful else "idle"
    transcript.meeting_edge_error_message = None
    transcript.meeting_edge_source_signature = None
    session.add(transcript)
