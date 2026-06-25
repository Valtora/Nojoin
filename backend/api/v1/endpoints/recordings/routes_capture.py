import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

import backend.api.v1.endpoints.recordings as recordings_module
from backend.api.deps import get_current_recording_client_user, get_db
from backend.api.error_handling import sanitized_http_exception
from backend.models.pipeline import RecordingAudioChunk, RecordingAudioWindowManifest
from backend.models.recording import (
    CaptureSourceReportCreate,
    ClientStatus,
    Recording,
    RecordingCaptureLifecycleResponse,
    RecordingStatus,
)
from backend.models.recording_public import RecordingPublicRead, serialize_recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.utils.rate_limit import enforce_upload_concurrency
from backend.utils.recording_audio_sync import BROWSER_AUDIO_SEGMENT_SUFFIXES
from backend.utils.time import utc_now
from backend.utils.upload_limit import UPLOAD_LIMIT_SEGMENT, stream_and_validate_upload

from .router import router

logger = logging.getLogger(__name__)


def _serialize_capture_track(track_payload):
    if track_payload is None:
        return None
    return {
        "kind": track_payload.kind,
        "label": track_payload.label,
        "enabled": track_payload.enabled,
        "muted": track_payload.muted,
        "ready_state": track_payload.ready_state,
        "settings": track_payload.settings,
    }


@router.post("/{recording_id}/pause", response_model=RecordingCaptureLifecycleResponse)
async def pause_upload(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Pause an in-flight recording session while retaining uploaded chunks.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )

    if recording.status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        raise HTTPException(
            status_code=409, detail=recordings_module.UPLOAD_CLOSED_DETAIL
        )

    if recording.status != RecordingStatus.PAUSED:
        recording.status = RecordingStatus.PAUSED
        recording.client_status = ClientStatus.PAUSED
        recording.last_activity_at = utc_now()
        db.add(recording)
        await db.commit()
        await db.refresh(recording)

        try:
            recordings_module.record_pipeline_metric(
                stage="recording_paused",
                recording_id=recording.id,
                payload={"public_id": recording.public_id},
                log=logger,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to record pause metric for recording %s: %s",
                recording.id,
                exc,
            )

    return RecordingCaptureLifecycleResponse(
        recording_id=recording.public_id,
        status=recording.status,
        last_sequence=recordings_module._get_last_uploaded_sequence(recording.id),
    )


@router.post("/{recording_id}/resume", response_model=RecordingCaptureLifecycleResponse)
async def resume_upload(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Resume a paused recording session.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )

    if recording.status != RecordingStatus.PAUSED:
        raise HTTPException(
            status_code=409,
            detail="Recording is not paused",
        )

    recording.status = RecordingStatus.UPLOADING
    recording.client_status = ClientStatus.RECORDING
    recording.last_activity_at = utc_now()
    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    return RecordingCaptureLifecycleResponse(
        recording_id=recording.public_id,
        status=recording.status,
        last_sequence=recordings_module._get_last_uploaded_sequence(recording.id),
    )


@router.post("/{recording_id}/segment")
async def upload_segment(
    request: Request,
    recording_id: str,
    sequence: int = Query(..., description="Sequence number of the segment", ge=0),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Upload a segment for a recording.
    """
    async with enforce_upload_concurrency(
        request, "upload_segment", str(current_user.id), 5
    ):
        recording = await recordings_module._get_owned_recording(
            db, recording_id, current_user.id
        )
        segment_suffix = recordings_module._resolve_segment_upload_suffix(file)

        recordings_module._ensure_recording_accepts_uploads(recording)

        recording_temp_dir = recordings_module.recording_upload_temp_dir(
            recording.id, create=True
        )

        filename = os.path.basename(f"{int(sequence)}{segment_suffix}")
        segment_path = recording_temp_dir / filename

        try:
            await stream_and_validate_upload(
                file=file,
                dest_path=str(segment_path),
                max_size=UPLOAD_LIMIT_SEGMENT,
            )
            if segment_suffix == ".wav":
                await recordings_module._sync_recording_audio_chunks_from_directory(
                    db,
                    recording_id=recording.id,
                    source_kind="browser",
                    suffix=".wav",
                )
                await recordings_module._sync_recording_audio_window_manifests(
                    db,
                    recording_id=recording.id,
                    source_kind="browser",
                    seal_tail=False,
                )
            recording.last_activity_at = utc_now()
            db.add(recording)
            await db.commit()
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            try:
                if segment_path.exists():
                    segment_path.unlink()
            except OSError:
                pass
            raise sanitized_http_exception(
                logger=logger,
                status_code=500,
                client_message="Failed to save the uploaded segment.",
                log_message=f"Failed to save uploaded segment {sequence} for recording {recording_id}.",
                exc=e,
            )

    try:
        file_size = segment_path.stat().st_size if segment_path.exists() else 0
        recordings_module.record_pipeline_metric(
            stage="audio_chunk_uploaded",
            recording_id=recording.id,
            payload={
                "sequence": sequence,
                "bytes": file_size,
                "filename": filename,
            },
            log=logger,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to record audio chunk upload metric for recording %s segment %s: %s",
            recording.id,
            sequence,
            exc,
        )

    # Dispatch the live transcription task.
    if segment_suffix == ".wav" and recordings_module.config_manager.get(
        "enable_live_transcription"
    ):
        try:
            recordings_module.celery_app.send_task(
                "backend.processing.live_transcribe.transcribe_segment_live_task",
                args=[recording.id, sequence],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to dispatch live transcription task for recording %s segment %s: %s",
                recording.id,
                sequence,
                e,
            )
    elif segment_suffix in BROWSER_AUDIO_SEGMENT_SUFFIXES:
        try:
            recordings_module.celery_app.send_task(
                "backend.processing.segment_transcode.transcode_segment_task",
                args=[recording.id, sequence],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to dispatch segment transcode task for recording %s segment %s: %s",
                recording.id,
                sequence,
                e,
            )

    return {"status": "received", "segment": sequence}


@router.post("/{recording_id}/capture-source-report")
async def log_capture_source_report(
    recording_id: str,
    payload: CaptureSourceReportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )

    logger.info(
        (
            "Capture source report for recording %s (user %s): "
            "attempt=%s outcome=%s mode=%s requested_microphone_device_id=%s "
            "requested_microphone_label=%s shared_audio_available=%s "
            "configured_microphone_gain=%s configured_system_gain=%s "
            "configured_echo_cancellation=%s configured_noise_suppression=%s "
            "configured_auto_gain_control=%s "
            "failure_code=%s failure_message=%s available_microphones=%s "
            "browser_microphone_track=%s browser_display_audio_track=%s "
            "browser_display_video_track=%s notes=%s"
        ),
        recording.public_id,
        current_user.id,
        payload.attempt_kind,
        payload.outcome,
        payload.mode,
        payload.requested_microphone_device_id,
        payload.requested_microphone_label,
        payload.shared_audio_available,
        payload.configured_microphone_gain,
        payload.configured_system_gain,
        payload.configured_echo_cancellation,
        payload.configured_noise_suppression,
        payload.configured_auto_gain_control,
        payload.failure_code,
        payload.failure_message,
        [device.model_dump() for device in payload.available_microphones],
        _serialize_capture_track(payload.browser_microphone_track),
        _serialize_capture_track(payload.browser_display_audio_track),
        _serialize_capture_track(payload.browser_display_video_track),
        payload.notes,
    )

    return {"status": "logged"}


@router.post("/{recording_id}/finalize", response_model=RecordingPublicRead)
async def finalize_upload(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Finalize the upload, concatenate segments, and trigger processing.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )

    recordings_module._ensure_recording_can_finalize_upload(recording)

    await recordings_module._sync_recording_audio_chunks_from_directory(
        db,
        recording_id=recording.id,
        source_kind="browser",
        suffix=".wav",
    )
    await recordings_module._sync_recording_audio_window_manifests(
        db,
        recording_id=recording.id,
        source_kind="browser",
        seal_tail=True,
    )
    chunk_rows = await recordings_module._list_recording_audio_chunks(
        db,
        recording_id=recording.id,
        source_kind="browser",
    )
    pending_transcode_sequences = recordings_module._find_pending_transcode_sequences(
        recording.id,
        chunk_rows=chunk_rows,
    )
    if pending_transcode_sequences:
        await recordings_module._transcode_pending_browser_segments_for_finalize(
            recording.id,
            pending_transcode_sequences,
        )
        await recordings_module._sync_recording_audio_chunks_from_directory(
            db,
            recording_id=recording.id,
            source_kind="browser",
            suffix=".wav",
        )
        await recordings_module._sync_recording_audio_window_manifests(
            db,
            recording_id=recording.id,
            source_kind="browser",
            seal_tail=True,
        )
        chunk_rows = await recordings_module._list_recording_audio_chunks(
            db,
            recording_id=recording.id,
            source_kind="browser",
        )
        pending_transcode_sequences = (
            recordings_module._find_pending_transcode_sequences(
                recording.id,
                chunk_rows=chunk_rows,
            )
        )

    if pending_transcode_sequences:
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )
    if not chunk_rows:
        raise HTTPException(status_code=400, detail="No valid segments found")

    missing_sequences = recordings_module._find_missing_chunk_sequences(chunk_rows)
    if missing_sequences:
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )

    final_audio_path = recording.audio_path

    try:
        master_segment_paths = recordings_module._list_staged_browser_master_segments(
            recording.id
        )
        if master_segment_paths:
            final_audio_path = recordings_module._browser_master_output_path(
                recording,
                recordings_module._resolve_browser_master_suffix(master_segment_paths),
            )
            recordings_module.concatenate_media_files(
                [str(path) for path in master_segment_paths],
                final_audio_path,
            )
        else:
            segment_paths = [row.storage_path for row in chunk_rows]
            recordings_module.concatenate_wavs(segment_paths, final_audio_path)

        recordings_module._enforce_lossy_audio_bitrate_floor(final_audio_path)
        duration_seconds = recordings_module.get_audio_duration(final_audio_path)
    except HTTPException as exc:
        failed_root: Path | None = None
        try:
            failed_root = recordings_module.move_recording_upload_to_failed(
                recording.id, logger=logger
            )
        except Exception as move_error:  # noqa: BLE001
            logger.error(f"Failed to move segments to failed dir: {move_error}")
        await recordings_module._mark_recording_audio_chunks_failed(
            db,
            recording_id=recording.id,
            failed_root=failed_root,
        )
        await db.commit()

        recordings_module.delete_recording_artifacts(
            recording_id=recording.id,
            audio_path=final_audio_path,
            proxy_path=None,
            logger=logger,
        )
        await recordings_module._mark_recording_upload_error(
            db, recording, str(exc.detail)
        )
        raise
    except Exception as e:  # noqa: BLE001
        failed_root: Path | None = None
        try:
            failed_root = recordings_module.move_recording_upload_to_failed(
                recording.id, logger=logger
            )
        except Exception as move_error:  # noqa: BLE001
            logger.error(f"Failed to move segments to failed dir: {move_error}")
        await recordings_module._mark_recording_audio_chunks_failed(
            db,
            recording_id=recording.id,
            failed_root=failed_root,
        )
        await db.commit()

        recordings_module.delete_recording_artifacts(
            recording_id=recording.id,
            audio_path=final_audio_path,
            proxy_path=None,
            logger=logger,
        )

        raise sanitized_http_exception(
            logger=logger,
            status_code=500,
            client_message="Failed to finalize the uploaded recording.",
            log_message=f"Failed to finalize segmented upload for recording {recording_id}.",
            exc=e,
        )

    file_stats = os.stat(final_audio_path)
    await db.refresh(recording)
    recording.audio_path = final_audio_path
    recording.proxy_path = recordings_module.get_initial_proxy_path(final_audio_path)
    recording.file_size_bytes = file_stats.st_size
    recording.duration_seconds = duration_seconds

    if recording.status != RecordingStatus.UPLOADING:
        db.add(recording)
        await db.commit()
        await db.refresh(recording)
        raise HTTPException(
            status_code=409,
            detail=recordings_module.UPLOAD_CLOSED_DETAIL,
        )

    recording.status = RecordingStatus.QUEUED
    recording.client_status = ClientStatus.IDLE
    recording.processing_started_at = None
    recording.processing_completed_at = None

    db.add(recording)
    await db.commit()
    await db.refresh(recording)

    task = recordings_module.celery_app.send_task(
        "backend.worker.tasks.process_recording_task", args=[recording.id]
    )
    recording.celery_task_id = task.id
    db.add(recording)
    await db.commit()
    from backend.models.task import register_task_ownership

    await register_task_ownership(db, task.id, recording.user_id)

    proxy_task = recordings_module.celery_app.send_task(
        "backend.worker.tasks.generate_proxy_task", args=[recording.id]
    )
    if proxy_task:
        await register_task_ownership(db, proxy_task.id, recording.user_id)

    return serialize_recording(
        recording, has_proxy=recordings_module._recording_has_proxy(recording)
    )


@router.post("/{recording_id}/discard")
async def discard_upload(
    recording_id: str,
    reason: Optional[str] = Query(
        None, description="Optional client-provided discard reason"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Discard a recording that has not yet completed.

    This is the single graceful "give up on this meeting" path. It accepts any
    in-flight state: an active or paused live capture (``UPLOADING``/``PAUSED``),
    a meeting waiting in the processing queue (``QUEUED``), or one whose
    pipeline is actively running (``PROCESSING``). Whatever stage it is at, the
    backend revokes any running Celery task, removes every on-disk artefact, and
    deletes the recording row so no manual cancel-then-delete is required.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )

    discardable_states = {
        RecordingStatus.UPLOADING,
        RecordingStatus.PAUSED,
        RecordingStatus.QUEUED,
        RecordingStatus.PROCESSING,
    }
    if recording.status not in discardable_states:
        raise HTTPException(
            status_code=400,
            detail="Only in-flight or processing recordings can be discarded",
        )

    if reason:
        logger.info(
            "Discarding recording %s (status=%s) for user %s with reason=%s",
            recording.public_id,
            recording.status.value,
            current_user.id,
            reason,
        )
    else:
        logger.info(
            "Discarding recording %s (status=%s) for user %s",
            recording.public_id,
            recording.status.value,
            current_user.id,
        )

    # Revoke any in-flight processing task first so the worker stops touching the
    # recording before its row and files disappear. terminate=True sends SIGTERM
    # to a task that is already running; a queued-but-not-started task is simply
    # dropped. Mirrors the permanent-delete path in routes_actions.py.
    if recording.celery_task_id:
        try:
            recordings_module.celery_app.control.revoke(
                recording.celery_task_id, terminate=True
            )
        except Exception:  # noqa: BLE001
            pass

    recordings_module.delete_recording_artifacts(
        recording_id=recording.id,
        audio_path=recording.audio_path,
        proxy_path=recording.proxy_path,
        logger=logger,
    )

    await db.execute(
        delete(RecordingAudioChunk).where(
            RecordingAudioChunk.recording_id == recording.id
        )
    )
    await db.execute(
        delete(RecordingAudioWindowManifest).where(
            RecordingAudioWindowManifest.recording_id == recording.id
        )
    )
    await db.execute(delete(Transcript).where(Transcript.recording_id == recording.id))
    await db.execute(delete(Recording).where(Recording.id == recording.id))
    await db.commit()

    return {"ok": True}


@router.put("/{recording_id}/client_status", response_model=RecordingPublicRead)
async def update_client_status(
    recording_id: str,
    status: ClientStatus = Query(..., description="Current status of the client"),
    upload_progress: Optional[int] = Query(
        None, description="Upload progress percentage (0-100)"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """
    Update the client status (e.g. RECORDING, PAUSED) for a recording.
    """
    recording = await recordings_module._get_owned_recording(
        db, recording_id, current_user.id
    )

    recordings_module._ensure_recording_accepts_status_updates(recording)

    recording.client_status = status
    if upload_progress is not None:
        recording.upload_progress = upload_progress
    db.add(recording)
    await db.commit()
    await db.refresh(recording)
    return serialize_recording(
        recording, has_proxy=recordings_module._recording_has_proxy(recording)
    )
