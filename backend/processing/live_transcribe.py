# backend/processing/live_transcribe.py
# Live transcription lane: a Celery task that transcribes recording segments
# as they arrive, producing provisional transcript segments. A sequence-gated
# buffer re-imposes ordering on concurrently uploaded segments and carries the
# trailing (incomplete) utterance forward across runs.

import json
import logging
import os

from backend.celery_app import celery_app
from backend.utils.config_manager import config_manager
from backend.utils.recording_storage import recording_upload_temp_dir

logger = logging.getLogger(__name__)

# Tolerance (seconds) for treating a speech region as touching the buffer end.
TRAIL_EPS = 0.20
# Maximum length (seconds) of a trailing utterance before a forced cut.
FORCED_MAX = 30.0
# Sample rate of the live audio buffer.
LIVE_SAMPLE_RATE = 16000
# Silence threshold (ms) for the live lane: longer than the batch default so
# normal inter-phrase pauses do not fragment the live transcript.
LIVE_MIN_SILENCE_MS = 700

_STATE_FILENAME = "state.json"
_BUFFER_FILENAME = "buffer.wav"


def read_live_state(live_dir) -> dict:
    """Read the live lane state, returning defaults when absent or unreadable."""
    state_path = os.path.join(str(live_dir), _STATE_FILENAME)
    default = {"next_expected": 1, "buffer_abs_start": 0.0}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "next_expected": int(data.get("next_expected", 1)),
            "buffer_abs_start": float(data.get("buffer_abs_start", 0.0)),
        }
    except (FileNotFoundError, ValueError, OSError):
        return default


def write_live_state(live_dir, state: dict) -> None:
    """Persist the live lane state to disk."""
    state_path = os.path.join(str(live_dir), _STATE_FILENAME)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "next_expected": int(state["next_expected"]),
                "buffer_abs_start": float(state["buffer_abs_start"]),
            },
            f,
        )


def classify_speech(speech: list[dict], combined_len: float) -> tuple[list[dict], float]:
    """Split detected speech regions into completed regions and a carry-over cut point.

    Returns (complete_segments, cut_point) where cut_point is the buffer offset
    (seconds) from which unconsumed audio should be carried into the next run.
    """
    if not speech:
        # No speech: drop the silent buffer entirely.
        return [], combined_len

    last = speech[-1]
    trailing_incomplete = last["end"] >= combined_len - TRAIL_EPS

    if trailing_incomplete and (combined_len - last["start"]) >= FORCED_MAX:
        # Trailing utterance has run too long; treat it as complete now.
        return speech, combined_len
    if trailing_incomplete:
        # Carry the trailing utterance forward from its start.
        return speech[:-1], last["start"]
    # Last region ended with silence; everything is complete.
    return speech, last["end"]


def _build_live_config() -> dict:
    """Build a minimal config dict for the live transcription engine call."""
    backend = config_manager.get("live_transcription_backend", "parakeet")
    return {
        "transcription_backend": backend,
        "live_transcription_backend": backend,
        "parakeet_model": config_manager.get("parakeet_model", "parakeet-tdt-0.6b-v3"),
        "whisper_model_size": config_manager.get("whisper_model_size", "turbo"),
        "processing_device": config_manager.get("processing_device", "auto"),
    }


@celery_app.task(bind=True)
def transcribe_segment_live_task(self, recording_id: int, sequence: int):
    """Transcribe an uploaded recording segment in the live lane.

    Sequence-gated: only the task holding next_expected drains the contiguous
    run of segments on disk. Any failure is logged and the lane still advances;
    the final processing pipeline recovers everything.
    """
    import torch

    from backend.core.db import get_sync_session
    from backend.models.recording import Recording, RecordingStatus
    from backend.processing.vad import detect_speech_segments, safe_read_audio
    from backend.processing.transcribe import transcribe_audio

    config_manager.reload()

    # The live lane is only meaningful while the recording is uploading. Once
    # finalize() runs, the API deletes the upload temp dir; a live task that is
    # still queued must stop here rather than recreate an orphan dir or crash on
    # the vanished buffer/state files. The final pipeline is authoritative.
    session = get_sync_session()
    try:
        recording = session.get(Recording, recording_id)
        if not recording or recording.status != RecordingStatus.UPLOADING:
            return
    finally:
        session.close()

    temp_dir = recording_upload_temp_dir(recording_id, create=False)
    live_dir = temp_dir / "live"
    live_dir.mkdir(parents=True, exist_ok=True)

    state = read_live_state(live_dir)
    next_expected = state["next_expected"]
    buffer_abs_start = state["buffer_abs_start"]

    # --- Gating ---
    if sequence < next_expected:
        # Already consumed by an earlier run.
        return
    if sequence > next_expected:
        # Gap: this segment waits on disk until the run reaches it.
        return

    # --- Drain: contiguous run starting at next_expected ---
    run = []
    n = next_expected
    while os.path.exists(str(temp_dir / f"{n}.wav")):
        run.append(n)
        n += 1
    if not run:
        # Defensive: the triggering segment should exist; nothing to do.
        return

    buffer_path = str(live_dir / _BUFFER_FILENAME)

    try:
        # --- Build combined buffer ---
        parts = []
        if os.path.exists(buffer_path):
            parts.append(safe_read_audio(buffer_path, sampling_rate=LIVE_SAMPLE_RATE))
        for seg_n in run:
            parts.append(
                safe_read_audio(str(temp_dir / f"{seg_n}.wav"), sampling_rate=LIVE_SAMPLE_RATE)
            )

        combined = torch.cat(parts) if parts else torch.zeros(0)
        combined_len = combined.numel() / LIVE_SAMPLE_RATE
        combined_abs_start = buffer_abs_start

        # --- Detect speech and classify ---
        speech = detect_speech_segments(combined, min_silence_duration_ms=LIVE_MIN_SILENCE_MS)
        complete, cut_point = classify_speech(speech, combined_len)

        # --- Transcribe each completed speech region ---
        live_config = _build_live_config()
        new_segments = []
        for sp in complete:
            start_sample = int(sp["start"] * LIVE_SAMPLE_RATE)
            end_sample = int(sp["end"] * LIVE_SAMPLE_RATE)
            clip = combined[start_sample:end_sample]
            if clip.numel() == 0:
                continue

            clip_path = str(live_dir / "clip.wav")
            try:
                import silero_vad

                tensor = clip if clip.ndim > 1 else clip.unsqueeze(0)
                silero_vad.save_audio(clip_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
                result = transcribe_audio(clip_path, config=live_config)
            finally:
                if os.path.exists(clip_path):
                    try:
                        os.remove(clip_path)
                    except OSError:
                        pass

            if not result:
                continue
            text = (result.get("text") or "").strip()
            if not text:
                continue

            new_segments.append(
                {
                    "start": combined_abs_start + sp["start"],
                    "end": combined_abs_start + sp["end"],
                    "speaker": "UNKNOWN",
                    "text": text,
                    "provisional": True,
                }
            )

        # --- Carry over the unconsumed trailing audio ---
        new_buffer = combined[int(cut_point * LIVE_SAMPLE_RATE):]
        if new_buffer.numel() > 0:
            tensor = new_buffer if new_buffer.ndim > 1 else new_buffer.unsqueeze(0)
            import silero_vad

            silero_vad.save_audio(buffer_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
        elif os.path.exists(buffer_path):
            try:
                os.remove(buffer_path)
            except OSError:
                pass
        new_abs_start = combined_abs_start + cut_point

        # --- Persist provisional segments (race-guarded) ---
        if new_segments:
            from sqlalchemy.orm.attributes import flag_modified

            session = get_sync_session()
            try:
                recording = session.get(Recording, recording_id)
                if recording and recording.status == RecordingStatus.UPLOADING:
                    transcript = recording.transcript
                    if transcript is not None:
                        transcript.segments = (transcript.segments or []) + new_segments
                        flag_modified(transcript, "segments")
                        session.add(transcript)
                        session.commit()
            finally:
                session.close()

        # --- Advance the lane ---
        state["next_expected"] = run[-1] + 1
        state["buffer_abs_start"] = new_abs_start
        write_live_state(live_dir, state)

    except Exception as exc:
        # Non-fatal: log, advance past the run, do not re-raise. The final
        # processing pipeline re-transcribes everything from the source audio.
        logger.error(
            "Live transcription failed for recording %s run %s: %s",
            recording_id,
            run,
            exc,
            exc_info=True,
        )
        # Best-effort advance: if the live dir vanished (recording finalized
        # mid-run) there is nothing left to advance — the final pipeline owns
        # the transcript now.
        try:
            state["next_expected"] = run[-1] + 1
            write_live_state(live_dir, state)
        except OSError:
            pass
