"""Publish first-time ASR immediately; stage settled and retained reprocess results."""

from typing import TYPE_CHECKING, Any

from sqlmodel import select

from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.utils.config_manager import config_manager
from backend.utils.language_notice import language_detection_record
from backend.utils.reprocess_publication import (
    clear_previous_reprocess_state,
    clear_reprocess_pending,
    mark_reprocess_pending,
    reprocess_publication_pending,
)

if TYPE_CHECKING:
    from .pipeline import _PipelineRunContext


def _completed_reprocess_publication(transcript: Transcript | None) -> bool:
    state = getattr(transcript, "settling_state", None) or {}
    return (
        transcript is not None
        and state.get("reprocess_published") is True
        and state.get("settled") is not True
        and transcript.transcript_status == "completed"
    )


def begin_transcript_attempt(session: Any, recording_id: int) -> Transcript | None:
    """Restage a redelivered attempt whose transcript already published before notes.

    A worker can stop after publication while Recording is still PROCESSING.
    Startup reclaim and direct task redelivery must retain that completed result
    while their new candidate resolves fresh identity, just like manual requeue.
    """
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()
    if not _completed_reprocess_publication(transcript):
        return transcript
    from backend.utils.canonical_pipeline.constants import _load_transcript

    transcript = _load_transcript(session, recording_id, lock=True)
    if transcript is not None and _completed_reprocess_publication(transcript):
        mark_reprocess_pending(transcript)
        transcript.transcript_status = "pending"
        session.add(transcript)
    # Release the publication fence before long-running inference begins.
    session.commit()
    return transcript


def staged_publication_required(transcript: Transcript | None) -> bool:
    """Settled work and retained reprocesses publish after identity resolution."""
    return (getattr(transcript, "settling_state", None) or {}).get(
        "settled"
    ) is True or reprocess_publication_pending(transcript)


def canonical_finalization_required(transcript: Transcript) -> bool:
    """Existing settled work keeps its publication contract after a rollback switch."""
    return (
        bool(config_manager.get("enable_canonical_transcript_writes", True))
        or (getattr(transcript, "settling_state", None) or {}).get("settled") is True
    )


def prepare_reprocess_speakers(
    ctx: "_PipelineRunContext",
    recording: Recording,
    transcript: Transcript | None,
    diarization_result: Any,
) -> None:
    """Finish extraction before retiring the previous run's local identity state."""
    if not reprocess_publication_pending(transcript):
        return
    from .speaker_assignment import _extract_speaker_voiceprints

    ctx.speaker_voiceprints = _extract_speaker_voiceprints(
        ctx, recording, diarization_result
    )
    clear_previous_reprocess_state(ctx.session, recording.id)


def finish_reprocess_projection(
    transcript: Transcript,
    final_segments: list[dict[str, Any]],
    transcription_result: dict[str, Any],
) -> None:
    """Replace a retained projection directly when canonical writes are disabled."""
    if not reprocess_publication_pending(transcript):
        return
    if not canonical_finalization_required(transcript):
        transcript.text = transcription_result.get("text", "")
        transcript.segments = final_segments
    clear_reprocess_pending(transcript)


def persist_final_transcript(
    ctx: "_PipelineRunContext",
    recording: Recording,
    final_segments: list[dict[str, Any]],
    transcription_result: dict[str, Any],
    *,
    staged: bool = False,
) -> Transcript:
    """Publish first-time ASR immediately, or prepare staged metadata under its fence.

    Canonical reconciliation must see the previous projection, including human
    work. First-time recordings retain their raw ASR publication before speakers.
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
