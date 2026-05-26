from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.processing.browser_live_audio import (
    BROWSER_LIVE_CHANNEL_COUNT,
    BROWSER_LIVE_SAMPLE_RATE_HZ,
)
from backend.processing.live_transcribe import transcribe_segment_live_task
from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.config_manager import config_manager
from backend.utils.recording_audio_sync import (
    BROWSER_AUDIO_SEGMENT_SUFFIXES,
    TRANSCODE_FAILED_SUFFIX,
    sync_recording_audio_chunks_from_directory,
    sync_recording_audio_window_manifests,
)
from backend.utils.recording_storage import recording_upload_temp_dir


logger = logging.getLogger(__name__)


def _transcode_failed_marker_path(recording_id: int, sequence: int) -> Path:
    return recording_upload_temp_dir(recording_id, create=True) / f"{sequence}{TRANSCODE_FAILED_SUFFIX}"


def _wav_segment_path(recording_id: int, sequence: int) -> Path:
    return recording_upload_temp_dir(recording_id, create=True) / f"{sequence}.wav"


def _locate_staged_browser_segment(recording_id: int, sequence: int) -> Path | None:
    temp_dir = recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        return None

    for suffix in sorted(BROWSER_AUDIO_SEGMENT_SUFFIXES):
        candidate = temp_dir / f"{sequence}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _run_ffmpeg_transcode(input_path: Path, output_path: Path) -> None:
    """Transcode browser WebM/Ogg into canonical stereo live-capture WAV."""
    command = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-ar",
        str(BROWSER_LIVE_SAMPLE_RATE_HZ),
        "-ac",
        str(BROWSER_LIVE_CHANNEL_COUNT),
        "-f",
        "wav",
        "-y",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return

    error_output = completed.stderr.strip() or completed.stdout.strip()
    if not error_output:
        error_output = f"ffmpeg exited with status {completed.returncode}"
    raise RuntimeError(error_output)


def transcode_staged_browser_segment(recording_id: int, sequence: int) -> Path:
    failure_marker = _transcode_failed_marker_path(recording_id, sequence)
    wav_path = _wav_segment_path(recording_id, sequence)
    raw_segment_path = _locate_staged_browser_segment(recording_id, sequence)

    if raw_segment_path is None:
        if wav_path.exists():
            return wav_path
        raise FileNotFoundError(
            f"No staged browser segment found for recording {recording_id} sequence {sequence}"
        )

    _run_ffmpeg_transcode(raw_segment_path, wav_path)
    try:
        raw_segment_path.unlink()
    except OSError as exc:
        logger.warning(
            "Failed to remove source segment %s after transcode: %s",
            raw_segment_path,
            exc,
        )
    try:
        failure_marker.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(
            "Failed to remove transcode marker %s: %s",
            failure_marker,
            exc,
        )

    return wav_path


@celery_app.task
def transcode_segment_task(recording_id: int, sequence: int):
    config_manager.reload()

    failure_marker = _transcode_failed_marker_path(recording_id, sequence)
    wav_path = _wav_segment_path(recording_id, sequence)
    raw_segment_path = _locate_staged_browser_segment(recording_id, sequence)

    try:
        transcode_staged_browser_segment(recording_id, sequence)

        session = get_sync_session()
        try:
            sync_recording_audio_chunks_from_directory(
                session,
                recording_id=recording_id,
                source_kind="browser",
                suffix=".wav",
                temp_dir=wav_path.parent,
            )
            sync_recording_audio_window_manifests(
                session,
                recording_id=recording_id,
                source_kind="browser",
                seal_tail=False,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        if config_manager.get("enable_live_transcription"):
            try:
                transcribe_segment_live_task.delay(recording_id, sequence)
            except Exception as exc:
                logger.warning(
                    "Failed to dispatch live transcription after transcode for recording %s segment %s: %s",
                    recording_id,
                    sequence,
                    exc,
                )

        return {"status": "received", "segment": sequence}
    except Exception as exc:
        try:
            if wav_path.exists():
                wav_path.unlink()
        except OSError as cleanup_error:
            logger.warning(
                "Failed to remove partial WAV segment %s after transcode failure: %s",
                wav_path,
                cleanup_error,
            )

        try:
            failure_marker.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        except OSError as marker_error:
            logger.warning(
                "Failed to write transcode failure marker %s: %s",
                failure_marker,
                marker_error,
            )

        try:
            record_pipeline_metric(
                stage="segment_transcode_failed",
                recording_id=recording_id,
                payload={
                    "sequence": sequence,
                    "input_path": str(raw_segment_path) if raw_segment_path is not None else None,
                    "output_path": str(wav_path),
                    "error": str(exc),
                },
                log=logger,
            )
        except Exception as metric_error:
            logger.warning(
                "Failed to record transcode failure metric for recording %s segment %s: %s",
                recording_id,
                sequence,
                metric_error,
            )

        logger.warning(
            "Segment transcode failed for recording %s segment %s: %s",
            recording_id,
            sequence,
            exc,
        )
        return {"status": "failed", "segment": sequence}