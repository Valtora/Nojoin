"""VAD stage of the finalize pipeline: preprocess, mute, pick each stage's audio.

Extracted from ``pipeline.py`` to keep that module within its size budget. With
VAD on, the stage keeps two files of the same length and rate, the normalised
16 kHz mono file and a copy with non-speech set to exact zeros, and hands each
later stage the one its setting asks for (``backend/processing/vad_muting.py``,
#486). Once diarisation has run, muted transcription may read a third file
instead: the muted one opened inside every diarised turn (#670), less the turns
the phantom filter confirmed as non-speech (#678), which ``run_phantom_filter``
keeps on the run's context. The heavy imports stay inside the functions, as
they were in the pipeline, so the stage tests can stub those modules per test.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.processing.engines.base import LANGUAGE_DETECTION_AUDIO, VAD_MUTED_INPUT
from backend.processing.vad_muting import (
    diarised_turns,
    muted_stages,
    write_union_muted_audio,
)
from backend.utils.reprocess_publication import (
    clear_previous_reprocess_state,
    clear_reprocess_pending,
    clear_reprocess_published,
    reprocess_publication_pending,
)

from .constants import *  # noqa: F403
from .final_asr import mark_transcript_without_speech

if TYPE_CHECKING:
    from pyannote.core import Annotation

    from .pipeline import _PipelineRunContext


@dataclass
class VadStageResult:
    """Outcome of the VAD/preprocess stage.

    ``transcription_audio_path`` feeds ASR, unless diarisation then opens it
    (``transcription_audio_after_diarization``); ``diarization_audio_path``
    feeds diarisation, the phantom filter and voiceprints. Each is the muted or
    the unmuted file (``unmuted_audio_path``), on one time base. ``finished``
    signals the "no speech detected" short-circuit: an empty transcript has
    been persisted and the recording marked PROCESSED, so the orchestrator
    returns ``None``.
    """

    transcription_audio_path: str | None
    diarization_audio_path: str | None = None
    unmuted_audio_path: str | None = None
    finished: bool = False


def _complete_without_speech(session, recording: Recording) -> None:
    """Persist an empty transcript and mark the recording PROCESSED."""
    transcript = session.exec(
        select(Transcript)
        .where(Transcript.recording_id == recording.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    recording.status = RecordingStatus.PROCESSED
    recording.client_status = ClientStatus.IDLE
    recording.processing_step = "Completed (No speech detected)"
    recording.processing_completed_at = utc_now()

    if not transcript:
        transcript = Transcript(recording_id=recording.id)

    if reprocess_publication_pending(transcript):
        clear_previous_reprocess_state(session, recording.id)
        clear_reprocess_pending(transcript)
        clear_reprocess_published(transcript)
        transcript.language_detection = None

    if (getattr(transcript, "settling_state", None) or {}).get("settled") is True:
        from backend.utils.canonical_pipeline import (
            finalize_utterances_from_segments,
            refresh_transcript_projection_from_canonical,
        )

        # An empty successful pass is still a replacement. Reconcile protected
        # human work and retire unprotected canonical lines under this fence.
        finalize_utterances_from_segments(
            session, recording_id=recording.id, segments=[], trigger_source="worker"
        )
        refresh_transcript_projection_from_canonical(session, recording.id)
        transcript.complete_transcription()
    else:
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


def run_vad_stage(
    ctx: "_PipelineRunContext", recording: Recording, audio_path: str
) -> VadStageResult:
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
    # Only this stage names the detection file, never a stored setting: with
    # VAD off the key stays unset.
    ctx.merged_config.pop(LANGUAGE_DETECTION_AUDIO, None)

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
        ctx.merged_config[LANGUAGE_DETECTION_AUDIO] = vad_output_path

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
        unmuted_audio_path=vad_input_path,
    )
    # Transcription's file is logged once diarisation has run, when it is chosen.
    logger.info(f"Diarizing {result.diarization_audio_path}")
    for path in (result.transcription_audio_path, result.diarization_audio_path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Processed audio file missing: {path}")
    return result


def run_phantom_filter(
    ctx: "_PipelineRunContext", diarization: "Annotation", audio_path: str
) -> "Annotation":
    """The phantom-filtered diarisation, keeping on ``ctx`` the turns it
    confirmed as non-speech.

    The filter relabels such a cluster to the nearest speaker rather than
    dropping it, so the transcript's attribution is unchanged, but the union
    leaves its turns muted (#678). Best effort: if the filter fails, the
    unfiltered diarisation is used and every turn opens, as before.
    """
    from backend.processing.phantom_filter import filter_phantom_speakers

    non_speech: list = []
    try:
        filtered = filter_phantom_speakers(
            diarization,
            audio_path,
            config=ctx.merged_config,
            non_speech_turns=non_speech,
        )
    except Exception as e:  # noqa: BLE001 -- boundary: phantom filter is best-effort
        logger.warning(
            f"Phantom speaker filter failed, continuing with unfiltered result: {e}"
        )
        return diarization
    ctx.non_speech_turns = tuple(non_speech)
    return filtered


def transcription_audio_after_diarization(
    ctx: "_PipelineRunContext",
    vad_result: VadStageResult,
    diarization: "Annotation | None",
) -> str:
    """The audio ASR reads, once diarisation has run (#670).

    Muted transcription hears the muted file with the unmuted audio put back
    inside every diarised turn, so it mutes only where neither VAD nor
    diarisation heard speech. A turn the phantom filter confirmed as
    non-speech opens nothing, so only VAD's own speech in it is heard (#678).
    The file keeps VAD's zeros everywhere else, so ``VAD_MUTED_INPUT`` still
    holds for it. Every other case reads the file the VAD stage picked:
    unmuted transcription, diarisation off, failed or with no turns, and
    diarisation that heard the muted file itself. If the union cannot be
    written, ASR reads the VAD-muted file, as it did before.
    """
    path, restored_s = _union_or_vad_file(ctx, vad_result, diarization)
    if restored_s is None:
        logger.info("Transcribing %s", path)
    else:
        logger.info(
            "Transcribing %s: VAD speech plus %.1f s of diarised speech VAD muted",
            path,
            restored_s,
        )
    return path


def _union_or_vad_file(
    ctx: "_PipelineRunContext",
    vad_result: VadStageResult,
    diarization: "Annotation | None",
) -> tuple[str, float | None]:
    """The union file and the seconds it put back, or the VAD stage's file and None."""
    vad_path = vad_result.transcription_audio_path
    unmuted_path = vad_result.unmuted_audio_path
    if vad_path is None or unmuted_path is None:
        raise ValueError("the VAD stage finished without audio to transcribe")
    if (
        not ctx.merged_config.get(VAD_MUTED_INPUT)
        or diarization is None
        or muted_stages(ctx.merged_config).diarization
    ):
        return vad_path, None
    turns = diarised_turns(diarization, excluding=ctx.non_speech_turns)
    if not turns:
        return vad_path, None
    union_path = f"{os.path.splitext(unmuted_path)[0]}_union.wav"
    ctx.temp_files.append(union_path)
    try:
        restored_s = write_union_muted_audio(unmuted_path, vad_path, union_path, turns)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "Could not write %s, so transcription reads the VAD-muted file: %s",
            union_path,
            exc,
        )
        return vad_path, None
    return union_path, restored_s
