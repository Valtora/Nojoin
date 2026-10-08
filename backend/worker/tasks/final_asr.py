"""Failure handling for the finalize pipeline's transcription stage.

The engines raise ``TranscriptionError`` instead of returning ``None`` (see
``backend/processing/engines/errors.py``). This module holds what the pipeline
does with that: retry a GPU out-of-memory once, persist a failure that survives
the retry as a failed transcript rather than an empty, completed one, and clear
such a failure when a later run finds no speech at all.
"""

import logging
from collections.abc import Callable

from sqlmodel import select

from backend.models.transcript import Transcript
from backend.processing.engines.errors import TranscriptionError

logger = logging.getLogger(__name__)


def transcribe_with_gpu_oom_retry(
    audio_path: str, config: dict, *, free_gpu: Callable[[], None]
) -> dict:
    """Run the configured engine, retrying once if the GPU ran out of memory.

    An OOM can come from memory this process still holds rather than from the
    work itself: onnxruntime's arena grows with the largest window and never
    shrinks, torch's caching allocator keeps freed blocks, and with
    ``keep_models_loaded`` the diarisation and embedding models stay resident.
    ``free_gpu`` releases all of that, best-effort, before one more attempt. A
    second OOM means the card is too small for the job and is raised.

    Raises:
        TranscriptionError: The engine failed, or ran out of GPU memory twice.
    """
    from backend.processing.transcribe import transcribe_audio

    try:
        return transcribe_audio(audio_path, config=config)
    except TranscriptionError as exc:
        if not exc.gpu_out_of_memory:
            raise
        logger.warning("%s Freeing GPU memory and retrying once.", exc)
    free_gpu()
    return transcribe_audio(audio_path, config=config)


def mark_transcript_failed(session, recording_id: int, message: str) -> None:
    """Persist a failed transcription so the recording surfaces it.

    Sets ``transcript_status`` to ``"error"`` with ``message`` as the
    ``error_message``, the state ``update_recording_status`` maps to
    ``RecordingStatus.ERROR``. Text already on the row (provisional live
    segments) is kept: it is the only transcript the user has until a retry.
    """
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()
    if transcript is None:
        transcript = Transcript(recording_id=recording_id, text="", segments=[])
    transcript.fail_transcription(message)
    session.add(transcript)
    session.commit()


def mark_transcript_without_speech(transcript: Transcript) -> None:
    """Complete a transcript as empty because the audio held no speech.

    A previous transcription failure is cleared; a notes error is not
    (``Transcript.complete_transcription``). ``notes_status`` is left alone:
    there is nothing to summarise, so no notes run follows, which is also what
    the success path does when meeting intelligence skips an empty transcript.
    """
    transcript.text = ""  # Empty string to prevent hallucinations
    transcript.segments = []
    transcript.complete_transcription()
