"""Publish ordinary ASR immediately and settled results with canonical reconciliation."""

from typing import TYPE_CHECKING, Any

from sqlmodel import select

from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.utils.config_manager import config_manager
from backend.utils.language_notice import language_detection_record

if TYPE_CHECKING:
    from .pipeline import _PipelineRunContext


def staged_publication_required(transcript: Transcript | None) -> bool:
    """Only durable settled provenance opts into the staged final publication."""
    return (getattr(transcript, "settling_state", None) or {}).get("settled") is True


def canonical_finalization_required(transcript: Transcript) -> bool:
    """Existing settled work keeps its publication contract after a rollback switch."""
    return bool(
        config_manager.get("enable_canonical_transcript_writes", True)
    ) or staged_publication_required(transcript)


def persist_final_transcript(
    ctx: "_PipelineRunContext",
    recording: Recording,
    final_segments: list[dict[str, Any]],
    transcription_result: dict[str, Any],
    *,
    staged: bool = False,
) -> Transcript:
    """Publish ordinary ASR immediately, or prepare settled metadata under its fence.

    Canonical reconciliation must see the previous projection, including human
    work. Ordinary recordings retain their raw ASR publication before speakers.
    """
    session = ctx.session
    if staged and hasattr(session, "execute"):
        from backend.utils.canonical_pipeline.constants import _load_transcript

        transcript = _load_transcript(session, recording.id, lock=True)
    else:
        transcript = session.exec(
            select(Transcript)
            .where(Transcript.recording_id == recording.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
    if transcript is None:
        transcript = Transcript(recording_id=recording.id, text="", segments=[])
        session.add(transcript)
        session.flush()
    if not staged:
        transcript.text = transcription_result.get("text", "")
        transcript.segments = final_segments
    transcript.language_detection = language_detection_record(
        transcription_result, ctx.merged_config
    )
    transcript.complete_transcription()
    transcript.set_notes_error_message(None)
    if transcript.notes_status == "error":
        transcript.notes_status = "pending"
    session.add(transcript)
    if not staged:
        session.commit()
    return transcript
