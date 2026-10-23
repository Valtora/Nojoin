"""VAD stage of the finalize pipeline: preprocess, mute, pick each stage's audio.

Extracted from ``pipeline.py`` to keep that module within its size budget. With
VAD on, the stage keeps two files of the same length and rate, the normalised
16 kHz mono file and a copy with non-speech set to exact zeros, and hands each
later stage the one its setting asks for (``backend/processing/vad_muting.py``,
#486). The heavy imports stay inside the function, as they were in the
pipeline, so the stage tests can stub those modules per test.
"""

from dataclasses import dataclass

from backend.processing.engines.base import VAD_MUTED_INPUT
from backend.processing.vad_muting import muted_stages

from .constants import *  # noqa: F403
from .final_asr import mark_transcript_without_speech


@dataclass
class VadStageResult:
    """Outcome of the VAD/preprocess stage.

    ``transcription_audio_path`` feeds ASR; ``diarization_audio_path`` feeds
    diarisation, the phantom filter and voiceprints. Each is the muted or the
    unmuted file, on one time base. ``finished`` signals the "no speech
    detected" short-circuit: an empty transcript has been persisted and the
    recording marked PROCESSED, so the orchestrator returns ``None``.
    """

    transcription_audio_path: str | None
    diarization_audio_path: str | None = None
    finished: bool = False


def _complete_without_speech(session, recording: Recording) -> None:
    """Persist an empty transcript and mark the recording PROCESSED."""
    recording.status = RecordingStatus.PROCESSED
    recording.client_status = ClientStatus.IDLE
    recording.processing_step = "Completed (No speech detected)"
    recording.processing_completed_at = utc_now()

    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording.id)
    ).first()
    if not transcript:
        transcript = Transcript(recording_id=recording.id)

    mark_transcript_without_speech(transcript)

    mark_recording_audio_chunks_ready_for_cleanup(
        session,
        recording_id=recording.id,
        upload_status="finalized",
    )
    auto_link_recording(session, recording)
    session.add(transcript)
    session.add(recording)
    session.commit()


def run_vad_stage(ctx, recording: Recording, audio_path: str) -> VadStageResult:
    """Preprocess audio to 16k mono and (when enabled) mute non-speech regions.

    On the "no speech" short-circuit, persists an empty transcript, marks the
    recording PROCESSED, and returns ``finished=True`` so the orchestrator
    returns without running ASR. VAD runs whenever ``enable_vad`` is on, even
    when no stage reads the muted file, so the short-circuit always holds.
    Otherwise returns each stage's audio path.
    """
    from backend.processing.audio_preprocessing import preprocess_audio_for_vad
    from backend.processing.vad import mute_non_speech_segments

    session = ctx.session
    device_suffix = ctx.device_suffix
    temp_files = ctx.temp_files
    recording_id = ctx.recording_id

    enable_vad = ctx.merged_config.get("enable_vad", True)
    muted = muted_stages(ctx.merged_config)
    # An engine may read exact zeros as VAD silence only in the file it reads.
    ctx.merged_config[VAD_MUTED_INPUT] = muted.transcription

    if enable_vad:
        ctx.task.update_state(state="PROCESSING", meta={"progress": 30, "stage": "VAD"})
        recording.processing_step = f"Filtering silence and noise...{device_suffix}"
        recording.processing_progress = 30
        session.add(recording)
        session.commit()

        vad_input_path = preprocess_audio_for_vad(audio_path)
        if not vad_input_path:
            raise RuntimeError("VAD preprocessing failed")
        temp_files.append(vad_input_path)

        vad_output_path = vad_input_path.replace("_vad.wav", "_vad_processed.wav")
        vad_success, speech_duration = mute_non_speech_segments(
            vad_input_path, vad_output_path, config=ctx.merged_config
        )

        if not vad_success:
            raise RuntimeError("VAD execution failed")
        temp_files.append(vad_output_path)

        if speech_duration < 1.0:
            logger.warning(
                f"No speech detected in recording {recording_id} (speech duration: {speech_duration}s)"
            )
            _complete_without_speech(session, recording)
            return VadStageResult(transcription_audio_path=None, finished=True)
    else:
        logger.info("VAD disabled, skipping silence filtering.")
        # Still need to preprocess to ensure 16k mono wav for Whisper/Pyannote
        vad_input_path = preprocess_audio_for_vad(audio_path)
        if not vad_input_path:
            raise RuntimeError("Audio preprocessing failed")
        temp_files.append(vad_input_path)
        # Nothing was muted: muted_stages is all False with VAD off.
        vad_output_path = vad_input_path

    # Use WAV for processing to avoid sample count mismatches in Pyannote
    result = VadStageResult(
        transcription_audio_path=(
            vad_output_path if muted.transcription else vad_input_path
        ),
        diarization_audio_path=vad_output_path if muted.diarization else vad_input_path,
    )
    logger.info(
        f"Transcribing {result.transcription_audio_path}; "
        f"diarizing {result.diarization_audio_path}"
    )
    for path in (result.transcription_audio_path, result.diarization_audio_path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Processed audio file missing: {path}")
    return result
